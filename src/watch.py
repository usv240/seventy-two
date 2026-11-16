"""Watch my trip: a saved trip that is checked against every new exposure window the moment it is stored.

Nobody has to come back and check. The flow is event-driven, with no polling:

  ingest writes a new WINDOW# item to DynamoDB
    -> DynamoDB Streams (filtered to INSERTs of WINDOW# keys, so re-reads and every other write are ignored)
    -> this function's on_new_windows, which reads only the saved stops indexed under the window's state and
       days (TRIPSTOP#state#day), compares each with the window (trips.compare), and reads a trip only on a match
    -> a match is written into the trip's item, which flips its page, sends a notification to the person's
       devices, and adds an event, with an alarm, to the trip's calendar feed.

The work for one new window grows with the stops saved in that state on those days, never with every trip.

A saved trip deletes itself (DynamoDB TTL) 21 days after its last stop, the longest any exposure
could still matter. Window ids are made from the notice's content, so reading a page again is a
MODIFY, never an INSERT: only a window that is really new reaches the matcher.
"""
import json
import time
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from zoneinfo import ZoneInfo

import merge
import obs
import rules
import store
import trips

_DESER = TypeDeserializer()
ses = boto3.client("sesv2")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "")


def now_utc():
    return datetime.now(timezone.utc)


def stop_keys(stop):
    """Index keys for one saved stop: its state and each local day it covers, plus the day before and after,
    so a stop and a window near midnight, or in neighbouring time zones, still meet."""
    tz = ZoneInfo(stop.get("timezone") or "UTC")
    first = rules.parse_iso(stop["start_utc"]).astimezone(tz).date() - timedelta(days=1)
    last = rules.parse_iso(stop["end_utc"]).astimezone(tz).date() + timedelta(days=1)
    state = (stop.get("state") or "XX").upper()[:2]
    days = [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)][:40]
    return [f"TRIPSTOP#{state}#{d}" for d in days]


def window_keys(w):
    """The index keys a new window looks up: its state on each local day it covers, and stops with no state."""
    tz = ZoneInfo(w.get("timezone") or "UTC")
    first = rules.parse_iso(w["start_utc"]).astimezone(tz).date()
    last = rules.parse_iso(w["end_utc"]).astimezone(tz).date()
    days = [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)][:40]
    states = {(w.get("state") or "XX").upper()[:2], "XX"}
    return [f"TRIPSTOP#{s}#{d}" for s in sorted(states) for d in days]


def index_items(watch_id, parsed, expires, ttl, drill=False):
    """One small item per saved stop and day, holding the stop itself, so a new window can be compared with the
    stops in its state and days without reading a single trip."""
    out = []
    for si, s in enumerate(parsed):
        for key in stop_keys(s):
            out.append({"pk": f"WATCH#{watch_id}", "sk": f"STOP#{si:02d}#{key.rsplit('#', 1)[1]}", "trip_id": watch_id,
                        "gsi1pk": key, "gsi1sk": rules.iso(expires), "ttl": ttl, "stop": s, **({"drill": True} if drill else {})})
    return out


def candidate_watches(w, now, stats=None):
    """The saved trips with a stop that matches this window. One index query per state and day; each stop found
    is compared right there, and a trip is read only when one of its stops matches. So the work grows with the
    stops saved in that state on those days, not with every trip."""
    ids, seen = set(), 0
    for key in window_keys(w):
        kw = {"IndexName": "gsi1", "KeyConditionExpression": Key("gsi1pk").eq(key) & Key("gsi1sk").gte(rules.iso(now))}
        while True:
            page = store.table.query(**kw)
            for item in store.from_ddb(page["Items"]):
                seen += 1
                if bool(item.get("drill")) != bool(w.get("drill")):  # a drill window only ever meets a drill trip
                    continue
                if "stop" not in item or trips.compare(item["stop"], w, now)[0] == "match":
                    ids.add(item["trip_id"])  # (an index item without its stop is read the old way)
            if "LastEvaluatedKey" not in page:
                break
            kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    found = store.get_many([f"WATCH#{i}" for i in sorted(ids)]) if ids else []
    if stats is not None:
        stats.update(index_items=seen, trips_read=len(found))
    return found


def create(stops, windows, drill=False, site_url=""):
    """Save a trip. Windows already on the board are matched now; later ones by the stream."""
    parsed = trips.parse_stops(stops)
    if not parsed:
        return None
    now = now_utc()
    expires = trips.watch_expires(parsed)
    if expires <= now:
        return {"error": "Every place on this trip is more than 21 days ago, so there is nothing left to watch for."}
    watch_id = uuid.uuid4().hex[:24]  # the id is the only key to the trip: unguessable, never listed
    found, _ = trips.check(parsed, windows, now)
    matches = {m["window"]["id"]: {**m, "found_at": rules.iso(now), "already_on_board": True} for m in found}
    ttl = int((now + timedelta(days=1)).timestamp()) if drill else int(expires.timestamp())
    store.put({"pk": f"WATCH#{watch_id}", "sk": "META", "id": watch_id, "stops": parsed, "matches": matches,
               "created": rules.iso(now), "expires": rules.iso(expires), "ttl": ttl,
               "site_url": (site_url or "")[:200], **({"drill": True} if drill else {})})
    # one small index item per stop and day, so a new window finds only the stops it could match
    with store.table.batch_writer() as batch:
        for item in index_items(watch_id, parsed, expires, ttl, drill):
            batch.put_item(Item=store.to_ddb(item))
    return {"id": watch_id, "expires": rules.iso(expires), "matches": len(matches)}


def get(watch_id):
    w = store.get(f"WATCH#{watch_id}")
    if not w:
        return None
    now = now_utc()
    matches = sorted(w.get("matches", {}).values(), key=lambda m: m["found_at"], reverse=True)
    for m in matches:  # status is recomputed on every view, so the hours left stay true
        d = {k: rules.parse_iso(v) for k, v in m["deadlines"].items()}
        m["status"] = rules.status(d, now)
    matcher = store.get("MATCHER", "STATE") or {}
    return {"id": w["id"], "stops": w["stops"], "matches": matches, "created": w["created"], "expires": w["expires"],
            "as_of": rules.iso(now), "site_url": w.get("site_url", ""), "drill": bool(w.get("drill")), "alerts": len(w.get("push") or []), "matcher": {k: matcher.get(k) for k in ("last_run", "windows_in_last_run", "trips_checked")}}


def delete(watch_id):
    """The trip and its index items, all of them, now."""
    items = store.table.query(KeyConditionExpression=Key("pk").eq(f"WATCH#{watch_id}"), ProjectionExpression="pk, sk")["Items"]
    with store.table.batch_writer() as batch:
        for i in items:
            batch.delete_item(Key={"pk": i["pk"], "sk": i["sk"]})


def on_new_windows(event, _ctx):
    """DynamoDB Streams handler. The event source mapping's filter only passes INSERTs of WINDOW# items."""
    now = now_utc()
    new = []
    for rec in event.get("Records", []):
        img = rec.get("dynamodb", {}).get("NewImage")
        if img:
            new.append((store.from_ddb(_DESER.deserialize({"M": img})), rec["dynamodb"].get("ApproximateCreationDateTime")))
    if not new:
        return {"windows": 0}
    hits, checked, items_read = 0, set(), 0
    for w, stored_at in new:
        stats, before = {}, hits
        with obs.Timer() as timer, obs.span("match-window", window=w["id"], state=w.get("state"), drill=bool(w.get("drill"))):
            watches = candidate_watches(w, now, stats)
            checked.update(t["id"] for t in watches)
            hits += _match_one(w, watches, now)
        items_read += stats.get("index_items", 0)
        stored_ms = round(time.time() * 1000 - float(stored_at) * 1000) if stored_at else None
        obs.metric(MatchWindowMs=timer.ms, MatcherIndexItems=stats.get("index_items", 0),
                   MatcherTripsRead=stats.get("trips_read", 0), StoredToMatchedMs=stored_ms)
        obs.log(matcher="window", window=w["id"], state=w.get("state"), drill=bool(w.get("drill")), ms=timer.ms,
                index_items=stats.get("index_items", 0), trips_read=stats.get("trips_read", 0), matches=hits - before,
                stored_to_matched_ms=stored_ms)
    # one small record of the matcher's own last run, shown on every trip page
    store.put({"pk": "MATCHER", "sk": "STATE", "gsi1pk": "MATCHER", "gsi1sk": rules.iso(now), "last_run": rules.iso(now),
               "windows_in_last_run": len(new), "trips_checked": len(checked), "stops_checked": items_read,
               "matches_in_last_run": hits})
    obs.metric(TripMatches=hits)
    return {"windows": len(new), "trips": len(checked), "stops": items_read, "matches": hits}


def _match_one(w, watches, now):
    """Record the window on every trip it matches (once, even if the batch is retried). Returns the new matches.
    An exposure at an airport can match hundreds of trips, so the matches are written, and their notifications
    sent, 16 at a time."""
    found = []
    for t in watches:
        if bool(w.get("drill")) != bool(t.get("drill")):  # a drill window only ever meets a drill trip
            continue
        if w["id"] in (t.get("matches") or {}):
            continue
        # the same exposure posted by a second department is not a second alert
        if any(merge.same(m["window"], w) for m in (t.get("matches") or {}).values() if m.get("window")):
            continue
        for s in t["stops"]:
            kind, m = trips.compare(s, w, now)
            if kind != "match":
                continue
            found.append((t, {**m, "found_at": rules.iso(now), "already_on_board": False,
                              "seconds_after_stored": round((now - rules.parse_iso(w.get("first_seen") or rules.iso(now))).total_seconds(), 1)}))
            break
    if not found:
        return 0
    if len(found) == 1:
        return _record_match(w, *found[0])
    with ThreadPoolExecutor(min(16, len(found))) as ex:
        return sum(ex.map(lambda tm: _record_match(w, *tm), found))


def _record_match(w, t, m, tries=8):
    """Write one match into its trip, then tell the person, at most once per exposure.

    Two departments can post the same exposure, and their windows can be matched at the same moment in
    parallel stream batches. So the write is optimistic: read the trip's matches (strongly consistent),
    skip if this window or the same exposure is already there, and write only if the trip's revision is still
    the one just read. If another window got in first, read again and check again. A retried batch is a no-op."""
    # the resource's client: safe to share between threads, and (like the Table) it takes plain Python values
    client = store.table.meta.client
    key = {"pk": f"WATCH#{t['id']}", "sk": "META"}
    for _ in range(tries):
        got = client.get_item(TableName=store.table.name, Key=key, ConsistentRead=True,
                              ProjectionExpression="#m, #r, #t", ExpressionAttributeNames={"#m": "matches", "#r": "rev", "#t": "ttl"}).get("Item")
        if not got:
            return 0   # the trip was deleted meanwhile
        cur = store.from_ddb(got)
        if cur.get("ttl") and int(cur["ttl"]) < time.time():
            return 0   # past its TTL: gone, even before DynamoDB removes it
        have = cur.get("matches") or {}
        if w["id"] in have or any(merge.same(x["window"], w) for x in have.values() if x.get("window")):
            return 0   # this window, or another department's report of the same exposure, is already recorded
        rev = int(cur.get("rev") or 0)
        try:
            client.update_item(
                TableName=store.table.name, Key=key,
                UpdateExpression="SET #m.#w = :m, #r = :next",
                ConditionExpression="attribute_exists(pk) AND (attribute_not_exists(#r) OR #r = :rev)",
                ExpressionAttributeNames={"#m": "matches", "#w": w["id"], "#r": "rev"},
                ExpressionAttributeValues={":m": store.to_ddb(m), ":next": rev + 1, ":rev": rev})
        except client.exceptions.ConditionalCheckFailedException:
            continue   # another window was recorded on this trip in between: read it and check again
        t.setdefault("matches", {})[w["id"]] = m
        _email(t, m)
        _push(t, m)
        return 1
    return 0


def _push(t, m):
    """A notification on every device the person turned alerts on for, with what to do now."""
    subs = t.get("push") or []
    if not subs:
        return
    import push
    url = f"{t.get('site_url') or ''}/watch/?id={t['id']}"
    alive = push.send_all(subs, f"{rules.D.ALERT_TITLE}: {m['window']['place'][:60]}", m["status"]["next_step"], url)
    if len(alive) != len(subs):
        store.table.update_item(Key={"pk": f"WATCH#{t['id']}", "sk": "META"}, UpdateExpression="SET push = :p",
                                ExpressionAttributeValues={":p": alive})


def add_push(pk, sub, title, body, url):
    """Keep a device's subscription on a trip or a clock (at most 3 devices), and confirm with a first notification."""
    import push
    clean = push.valid_subscription(sub)
    if not clean:
        return None
    item = store.get(pk)
    if not item:
        return None
    subs = [s for s in item.get("push") or [] if s["endpoint"] != clean["endpoint"]][-2:] + [clean]
    store.table.update_item(Key={"pk": pk, "sk": "META"}, UpdateExpression="SET push = :p", ExpressionAttributeValues={":p": subs})
    status = push.send(clean, title, body, url)
    for wait in (1.5, 3):  # a brand-new subscription is sometimes not ready at the push service for a moment
        if 200 <= status < 300 or status in (404, 410):
            break
        print(json.dumps({"push_first_try": status}))
        time.sleep(wait)
        status = push.send(clean, title, body, url)
    return status


def _email(t, m):
    if not (FROM_EMAIL and t.get("email")):
        return
    try:
        ses.send_email(FromEmailAddress=FROM_EMAIL, Destination={"ToAddresses": [t["email"]]},
                       Content={"Simple": {"Subject": {"Data": f"Seventy-Two: a new {rules.DISEASE} notice matches your trip ({m['window']['place']})"},
                                           "Body": {"Text": {"Data": f"{m['status']['next_step']}\n\nNot medical advice."}}}})
    except Exception as e:
        print(json.dumps({"level": "warn", "email_failed": str(e)[:200]}))


# ---------- the trip's calendar feed (subscribe once; the calendar app fetches it again on its own) ----------

def _t(iso):
    return rules.parse_iso(iso).strftime("%Y%m%dT%H%M%SZ")


def _esc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _event(uid, start_iso, minutes, title, desc, alarm=None, url=""):
    end = rules.iso(rules.parse_iso(start_iso) + timedelta(minutes=minutes))
    lines = ["BEGIN:VEVENT", f"UID:{uid}@seventytwo", f"DTSTAMP:{_t(rules.iso(now_utc()))}",
             f"DTSTART:{_t(start_iso)}", f"DTEND:{_t(end)}", f"SUMMARY:{_esc(title)}",
             f"DESCRIPTION:{_esc(desc + ' Not medical advice.')}"] + ([f"URL:{url}"] if url else [])
    if alarm is not None:
        lines += ["BEGIN:VALARM", f"TRIGGER:{alarm}", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(title)}", "END:VALARM"]
    return lines + ["END:VEVENT"]


def calendar(watch_id):
    w = get(watch_id)
    if not w:
        return None
    page = f"{w.get('site_url') or ''}/watch/?id={watch_id}"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Seventy-Two//Watch my trip//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:Seventy-Two: my trip", "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
             "X-PUBLISHED-TTL:PT1H"]
    lines += _event(f"{watch_id}-watching", w["created"], 30, f"Seventy-Two is watching your trip for {rules.DISEASE} notices",
                    f"If a new notice matches a place on your trip, an event appears in this calendar with what to do. "
                    f"Watching until {w['expires'][:10]}. {page}", url=page)
    for m in w["matches"]:
        win, d = m["window"], m["deadlines"]
        wid = win["id"]
        call = f" Call {win['phone']}." if win.get("phone") else ""
        lines += _event(f"{watch_id}-{wid}-match", max(m["found_at"], rules.iso(now_utc() - timedelta(minutes=1))), 30,
                        f"{rules.D.ALERT_TITLE}: {win['place']}",
                        f"{m['status']['next_step']}{call} {page}", alarm="PT0M", url=page)
        for key, title in ((a["deadline"], a["window_title"]) for a in rules.ACTIONS):
            if rules.parse_iso(d[key]) > now_utc():
                lines += _event(f"{watch_id}-{wid}-{key}", d[key], 30, f"{title} ({win['place']})",
                                f"From the exposure on your trip.{call} {page}", alarm="-PT12H", url=page)
        lines += _event(f"{watch_id}-{wid}-watch", d["watch_end"], 30, f"Watch for symptoms ends ({win['place']})",
                        "If you have had no symptoms, no action is needed.", url=page)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
