"""Ingest: one health-department source per invocation (run by a Step Functions Distributed Map).

fetch -> skip if unchanged -> archive to S3 by content hash -> Claude reads -> code checks -> DynamoDB.

A source with a "discover" pattern is also an index: every link on it that matches the
pattern and has not been read before is read as a new notice, newest first, a few per run.
That is how a press release published at a brand-new URL reaches the board within 30 minutes.
"""
import hashlib
import http.client
import ipaddress
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import board
import obs
import reader
import rules
import store
from textutil import html_to_text, links

UA = "Mozilla/5.0 (compatible; SeventyTwo/1.0; reads public health exposure notices)"
MAX_NEW = 3            # new notices read per index per run; the rest wait for the next run
KEEP_MS = 70_000       # stop discovering when less than this much Lambda time is left
REREAD_DAYS = 21       # a discovered notice is re-checked this long, in case the department adds places to it
published_from_url = reader.published_from_url


class SourceUnreachable(Exception):
    """A health department page could not be fetched. Step Functions retries this error with backoff,
    and the Distributed Map tolerates it, so one broken website never stops the run."""


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, check):
        super().__init__()
        self.check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        err = self.check(newurl)  # every hop is checked, not only the first address
        if err:
            raise ValueError(err)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_ip(ip):
    a = ipaddress.ip_address(ip)
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified)


def _check_peer(sock):
    """The address the socket really connected to. A name can be looked up as public and then, a moment later,
    resolve to a private address (DNS rebinding); checking the connected peer closes that gap."""
    if not public_ip(sock.getpeername()[0]):
        sock.close()
        raise ValueError("That address is not a public web page.")


class _PeerCheckedHTTP(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        _check_peer(self.sock)


class _PeerCheckedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        _check_peer(self.sock)


class _PeerCheckedHandlers(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    def __init__(self):
        urllib.request.HTTPHandler.__init__(self)
        urllib.request.HTTPSHandler.__init__(self)

    def http_open(self, req):
        return self.do_open(_PeerCheckedHTTP, req)

    def https_open(self, req):
        return self.do_open(_PeerCheckedHTTPS, req, context=self._context)


def fetch(url, timeout=20, limit=3_000_000, check=None, trace_as=None):
    """A page's text. check: a visitor's link (the Lab), so every redirect and the connected address are checked.
    trace_as names the call in X-Ray instead of its host."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/rss+xml"})
    opener = (urllib.request.build_opener(_CheckedRedirects(check), _PeerCheckedHandlers()) if check
              else urllib.request.build_opener())
    with obs.remote(trace_as or urlsplit(url).netloc), opener.open(req, timeout=timeout) as r:
        raw = r.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("page larger than 3 MB")
        return raw.decode(r.headers.get_content_charset() or "utf-8", "replace")


metric = obs.metric  # Embedded Metric Format log lines; the alarm on SourceFailed is defined in template.yaml


def norm_url(url):
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", p.query, ""))


def url_key(url):
    return f"URL#{hashlib.sha256(norm_url(url).encode()).hexdigest()[:24]}"


def handler(event, ctx):
    """event: {source: one item of sources.json, attempt: Step Functions' retry count, last_attempt}"""
    src = event.get("source", event)
    final = int(event.get("attempt", 0)) >= int(event.get("last_attempt", 0))
    with obs.span("read-source", source=src.get("id"), state=src.get("state"), attempt=int(event.get("attempt", 0))):
        out = process(src, final_attempt=final)
    if src.get("discover") and out.get("_html"):
        out["discovered"] = discover(src, out["_html"], ctx)
    out.pop("_html", None)
    return out


def process(src, final_attempt=True):
    now = datetime.now(timezone.utc)
    drill = bool(src.get("drill"))  # scripts/drill.py: the same path end to end, filed where no visitor ever sees it
    state_key = {"pk": f"SOURCE#{src['id']}", "sk": "STATE", "gsi1pk": "DRILL" if drill else "SOURCE", "gsi1sk": src["id"]}
    prev = store.get(state_key["pk"], "STATE") or {}
    about = {"id": src["id"], "url": src["url"], "state": src.get("state"), "kind": src.get("kind", "notice"),
             **({"found_on": src["found_on"]} if src.get("found_on") else {})}

    try:
        page = fetch(src["url"])
    except Exception as e:  # raised so Step Functions retries it; after the last retry it counts as a failed run
        if final_attempt:
            store.put({**state_key, **about, "issuer": src.get("issuer"), "last_error": str(e)[:300], "last_try": rules.iso(now),
                       "last_hash": prev.get("last_hash"), "last_ok": prev.get("last_ok"),
                       "failures": int(prev.get("failures", 0)) + 1, "windows": prev.get("windows", 0)})
            metric(SourceFailed=1, SourceRead=0)
        raise SourceUnreachable(f"{src['id']}: {str(e)[:200]}") from e

    if src.get("kind") == "index":  # an index is only for finding notices, never read by the model itself
        store.put({**prev, **state_key, **about, "issuer": src.get("issuer"), "last_try": rules.iso(now),
                   "last_ok": rules.iso(now), "failures": 0, "last_error": None, "windows": 0})
        metric(SourceFailed=0, SourceRead=0)
        return {"id": src["id"], "status": "index", "_html": page}

    text = html_to_text(page)
    digest = hashlib.sha256(text.encode()).hexdigest()
    if digest == prev.get("last_hash"):
        store.put({**prev, **state_key, **about, "last_try": rules.iso(now), "last_ok": rules.iso(now), "failures": 0, "last_error": None})
        metric(SourceFailed=0, SourceRead=0)
        return {"id": src["id"], "status": "unchanged", "_html": page}

    key = f"raw/{src['id']}/{digest}"
    store.s3.put_object(Bucket=store.ARCHIVE_BUCKET, Key=key + ".html", Body=page.encode(), ContentType="text/html")
    store.s3.put_object(Bucket=store.ARCHIVE_BUCKET, Key=key + ".txt", Body=text.encode(), ContentType="text/plain")

    try:
        result, usage = reader.read(text, url=src["url"], source_hint=src.get("issuer", ""))
    except Exception as e:  # the model failing is a failed read, retried and counted like a fetch that failed
        if final_attempt:
            store.put({**prev, **state_key, **about, "issuer": src.get("issuer"), "last_error": f"read: {str(e)[:280]}",
                       "last_try": rules.iso(now), "failures": int(prev.get("failures", 0)) + 1})
            metric(SourceFailed=1, SourceRead=0)
        raise SourceUnreachable(f"{src['id']}: model: {str(e)[:200]}") from e
    windows, rejected = reader.validate(result, text, src["id"], src["url"]) if result.get("is_exposure_notice") else ([], [])
    metric(SourceFailed=0, SourceRead=1)

    published = reader.published(result, text, src["url"])
    new_windows = 0
    for w in windows:
        w.update({
            "pk": f"WINDOW#{w['id']}", "sk": "META", "gsi1pk": "DRILL" if drill else "WINDOW", "gsi1sk": w["end_utc"],
            **({"drill": True, "ttl": int(now.timestamp()) + 7200} if drill else {}),
            "issuer": result.get("issuer") or src.get("issuer"), "phone": result.get("phone") or src.get("phone"),
            "published_date": published, "notice_sha": digest, "first_seen": rules.iso(now),
            "source_kind": src.get("kind", "notice"),
        })
        if published:
            pub = datetime.strptime(published, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            start = rules.parse_iso(w["start_utc"])
            w["lag_days"] = max(0.0, rules.lag_days(start, pub))
            w.update({f"left_at_publication_{k}": v for k, v in rules.window_left_at_publication(start, pub).items()})
        existing = store.get(w["pk"])
        if existing:  # keep the first time we saw it, so "notice-to-board" stays honest
            w["first_seen"] = existing.get("first_seen", w["first_seen"])
        else:
            new_windows += 1
        store.put(w)
    metric(WindowsFound=new_windows, WindowsRejected=len(rejected))

    superseded = supersede(src, windows, now) if src.get("kind", "notice") == "notice" and windows and not drill else 0
    store.put({"pk": f"NOTICE#{digest}", "sk": "META", "source_id": src["id"], "url": src["url"],
               "fetched_at": rules.iso(now), "s3_key": key, "is_exposure_notice": bool(result.get("is_exposure_notice")),
               "published_date": published, "windows": [w["id"] for w in windows], "rejected": rejected,
               "usage": usage})
    store.put({**state_key, **about, "issuer": result.get("issuer") or src.get("issuer"),
               "last_hash": digest, "last_ok": rules.iso(now), "last_try": rules.iso(now),
               "last_change": rules.iso(now), "failures": 0, "last_error": None, "windows": len(windows),
               "rejected": len(rejected), "cost_usd": usage["cost_usd"],
               "is_exposure_notice": bool(result.get("is_exposure_notice"))})
    return {"id": src["id"], "status": "read", "windows": len(windows), "rejected": len(rejected), "superseded": superseded,
            "cost_usd": usage["cost_usd"], "_html": page}


def supersede(src, windows, now):
    """A press release that was changed and read again is the department's latest word. Windows it no longer
    lists move out of the board's index (kept, not deleted, so the record stays checkable). Live list pages
    are left alone, because they drop old exposures when they expire, not because they were wrong."""
    keep = {w["id"] for w in windows}
    gone = [w for w in store.windows() if w.get("source_id") == src["id"] and w["id"] not in keep]
    for w in gone:
        store.table.update_item(Key={"pk": f"WINDOW#{w['id']}", "sk": "META"},
                                UpdateExpression="SET gsi1pk = :s, superseded_at = :t",
                                ExpressionAttributeValues={":s": "SUPERSEDED", ":t": rules.iso(now)})
    return len(gone)


def watched_urls():
    """URLs already in the source list, so discovery never reads them a second time."""
    try:
        body = store.s3.get_object(Bucket=store.ARCHIVE_BUCKET, Key="config/sources.json")["Body"].read()
        return {norm_url(s["url"]) for s in json.loads(body)}
    except Exception:
        return set()


def discover(src, page, ctx):
    pattern = re.compile(src["discover"], re.I)
    found = {}
    for url, title in links(page, src["url"]):
        if pattern.search(url) and any(word in f"{url} {title}".lower() for word in rules.D.DISCOVER_WORDS):
            found.setdefault(norm_url(url), title)
    watched = watched_urls()
    known = {u: store.get(url_key(u)) for u in found if u not in watched}
    new = [u for u, k in known.items() if not k]
    # notices found in the last 21 days are checked again (hash-gated, so an unchanged one costs a fetch),
    # because departments often add places to a release after it goes up
    cutoff = rules.iso(datetime.now(timezone.utc) - timedelta(days=REREAD_DAYS))
    recent = [u for u, k in known.items() if k and k.get("found_at", "") >= cutoff]
    read = []
    for url in new[:MAX_NEW] + recent:
        if ctx and ctx.get_remaining_time_in_millis() < KEEP_MS:
            break
        child = {"id": f"{src['id']}-{hashlib.sha256(url.encode()).hexdigest()[:8]}", "url": url,
                 "issuer": src.get("issuer"), "state": src.get("state"), "phone": src.get("phone"),
                 "kind": "notice", "found_on": src["id"]}
        try:
            r = process(child)
        except Exception as e:  # one bad read never blocks the others; a new notice is tried again next run
            metric(SourceFailed=1, SourceRead=0)   # and it counts toward the sources-failing alarm
            read.append({"id": child["id"], "status": "read_failed", "error": str(e)[:200]})
            continue
        if not known.get(url):
            store.put({"pk": url_key(url), "sk": "META", "url": url, "title": found[url][:200],
                       "source_id": child["id"], "found_on": src["id"],
                       "found_at": rules.iso(datetime.now(timezone.utc))})
        read.append({k: r.get(k) for k in ("id", "status", "windows")})
    return {"links": len(found), "new": len(new), "rechecked": len(recent), "read": read}


def snapshot(_event, _ctx):
    """After every ingest, publish the board to S3 so the site still works if the API fails.
    It is built by the same function as GET /api/exposures, so the failover serves the same rows."""
    data = {**board.board(datetime.now(timezone.utc)), "snapshot": True}
    body = json.dumps(data, default=str).encode()
    for key in ("data/exposures.json", "api/exposures"):  # second key lets CloudFront fail over to S3 on the same path
        store.s3.put_object(Bucket=store.SITE_BUCKET, Key=key, Body=body, ContentType="application/json",
                            CacheControl="max-age=60")
    return {"windows": len(data["windows"])}
