"""Put real load on the live stack and write down what happened. Every number on the Ops page's "Scale tests"
comes from here, and each run is appended to docs/scale.json with its time.

    python scripts/scale.py trips      save 10,000 test trips (about 20,000 stops) across 50 states and DC
    python scripts/scale.py matcher    send 8 new windows through the real stream, one at a time, and read the
                                       matcher's own log line for each: stops read, trips read, matches, time
    python scripts/scale.py burst      store 200 new windows at once; how long until the last one is matched
    python scripts/scale.py edge       2,000 requests, 40 at a time, to the site and its cached API
    python scripts/scale.py clocks     start 50 real-speed clocks at once whose windows close in 4 minutes
    python scripts/scale.py baseline   the same 8 windows, two ways, from this laptop: the index, and the obvious
                                       design that reads every saved trip (Seventy-Two's own first version)
    python scripts/scale.py cleanup    delete every test trip, stop and window this script made
    python scripts/scale.py all        trips, matcher, burst, cleanup, edge, clocks

Test data never reaches the public board or a real visitor: trips and windows are drills (a drill window only
ever meets a drill trip), windows are filed under their own index key, and everything carries a TTL of a day
as well as being deleted at the end. Cost of a full run: about $0.30 of DynamoDB writes and a few cents of
Lambda and Step Functions. Claude is never called.
"""
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]
STACK = os.environ.get("STACK", "seventytwo")
REGION = os.environ.get("REGION", "us-east-1")
s = boto3.Session(region_name=REGION)
outs = {o["OutputKey"]: o["OutputValue"] for o in s.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}
os.environ.update(TABLE=outs["TableName"], AWS_DEFAULT_REGION=REGION)
sys.path.insert(0, str(ROOT / "src"))
import evidence  # noqa: E402
import rules  # noqa: E402
import store  # noqa: E402
import trips  # noqa: E402
import watch  # noqa: E402

LOG = ROOT / "docs" / "scale.json"
STATE = ROOT / "build" / "scale-state.json"   # what this run made, so cleanup can delete exactly that
UTC = timezone.utc
N_TRIPS = int(os.environ.get("SCALE_TRIPS", "10000"))
HOT = 300   # trips that all passed through one airport gate area on the same evening
STATES = sorted(k for k in evidence.STATE_ZONES if len(k) == 2)
MAIN_ZONE = {"AK": "America/Anchorage", "IN": "America/Indiana/Indianapolis", "KY": "America/New_York",
             "MI": "America/Detroit", "FL": "America/New_York", "ID": "America/Boise", "AZ": "America/Phoenix",
             "KS": "America/Chicago", "NE": "America/Chicago", "ND": "America/Chicago", "NV": "America/Los_Angeles"}
ZONE = {st: MAIN_ZONE.get(st) or sorted(evidence.STATE_ZONES[st])[0] for st in STATES}
VENUES = 40          # per state; each venue name is one word, so two venues never look alike to the matcher
_local = threading.local()


def now():
    return datetime.now(UTC)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def record(entry):
    runs = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    runs.append(entry)
    LOG.write_text(json.dumps(runs, indent=1), encoding="utf-8")
    print(json.dumps(entry, indent=1))


def table():
    if not hasattr(_local, "t"):
        _local.t = boto3.Session(region_name=REGION).resource(   # back off and retry when DynamoDB is still scaling up
            "dynamodb", config=Config(retries={"mode": "adaptive", "max_attempts": 20})).Table(outs["TableName"])
    return _local.t


def write_all(items, workers=8):
    """Batch-write items from several threads (boto3's batch writer resends anything DynamoDB throttles)."""
    chunks = [items[i:i + 500] for i in range(0, len(items), 500)]

    def put(chunk):
        with table().batch_writer() as b:
            for it in chunk:
                b.put_item(Item=store.to_ddb(it))
        return len(chunk)

    with ThreadPoolExecutor(workers) as ex:
        return sum(ex.map(put, chunks))


def delete_all(keys, workers=8):
    chunks = [keys[i:i + 500] for i in range(0, len(keys), 500)]

    def rm(chunk):
        with table().batch_writer() as b:
            for k in chunk:
                b.delete_item(Key=k)
        return len(chunk)

    with ThreadPoolExecutor(workers) as ex:
        return sum(ex.map(rm, chunks))


def venue(st, v):
    return f"Scalehall{st}{v:02d}"


def stop(st, v, day, hour, hours):
    return {"place": venue(st, v), "city": None, "state": st, "date": day.isoformat(),
            "start_time": f"{hour:02d}:00", "end_time": f"{min(23, hour + hours):02d}:00", "timezone": ZONE[st]}


def make_trips():
    """10,000 trips of 1 to 3 stops, 80% of a trip in one home state, over the last 14 days. 300 of them share
    one evening at one airport, the way an exposure at an airport reaches hundreds of people."""
    rng = random.Random(72)
    today = now().astimezone(UTC).date()
    days = [today - timedelta(days=d) for d in range(1, 15)]
    hot_day = days[4]
    out = []
    for n in range(N_TRIPS):
        home = rng.choice(STATES)
        stops = []
        for _ in range(rng.choice((1, 1, 2, 2, 2, 3))):
            st = home if rng.random() < 0.8 else rng.choice(STATES)
            stops.append(stop(st, rng.randrange(1, VENUES), rng.choice(days), rng.randrange(8, 20), rng.choice((1, 2, 3))))
        if n < HOT:
            stops[0] = stop("PA", 0, hot_day, 18, 2)
        out.append({"id": f"scale{n:05d}", "stops": stops})
    return out, hot_day


def trips_cmd():
    t0 = time.time()
    made, hot_day = make_trips()
    items, created = [], iso(now())
    for t in made:
        parsed = trips.parse_stops(t["stops"])
        expires = trips.watch_expires(parsed)
        ttl = int((now() + timedelta(days=1)).timestamp())
        items.append({"pk": f"WATCH#{t['id']}", "sk": "META", "id": t["id"], "stops": parsed, "matches": {},
                      "created": created, "expires": iso(expires), "ttl": ttl, "site_url": outs["SiteUrl"], "drill": True,
                      # only for the baseline: every test trip in one index partition, the way the first version kept them
                      "gsi1pk": "SCALEALL", "gsi1sk": iso(expires)})
        items += watch.index_items(t["id"], parsed, expires, ttl, drill=True)
    n = write_all(items)
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"trip_ids": [t["id"] for t in made], "keys": [{"pk": i["pk"], "sk": i["sk"]} for i in items],
                                 "hot_day": hot_day.isoformat(), "windows": []}), encoding="utf-8")
    stops = sum(len(t["stops"]) for t in made)
    print(json.dumps({"trips": len(made), "stops": stops, "items_written": n, "seconds": round(time.time() - t0, 1)}))


def state():
    return json.loads(STATE.read_text(encoding="utf-8"))


def save_state(st):
    STATE.write_text(json.dumps(st), encoding="utf-8")


def window(wid, st, v, day, hour, hours):
    tz = ZONE[st]
    a = datetime.strptime(f"{day} {hour:02d}:00", "%Y-%m-%d %H:%M").replace(tzinfo=__import__("zoneinfo").ZoneInfo(tz))
    b = a + timedelta(hours=hours)
    return {"pk": f"WINDOW#{wid}", "sk": "META", "gsi1pk": "DRILL", "gsi1sk": iso(b.astimezone(UTC)), "drill": True,
            "id": wid, "place": venue(st, v), "city": None, "state": st, "timezone": tz,
            "start_utc": iso(a.astimezone(UTC)), "end_utc": iso(b.astimezone(UTC)), "time_clear": True, "flags": [],
            "local_label": "Scale test", "quote": "Scale test.", "url": outs["SiteUrl"], "issuer": "Seventy-Two scale test",
            "first_seen": iso(now()), "ttl": int((now() + timedelta(days=1)).timestamp())}


def matcher_group():
    res = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in s.client("cloudformation").describe_stack_resources(StackName=STACK)["StackResources"]}
    cfg = s.client("lambda").get_function_configuration(FunctionName=res["MatcherFunction"])
    return cfg.get("LoggingConfig", {}).get("LogGroup", f"/aws/lambda/{res['MatcherFunction']}")


def matcher_lines(group, since_ms, want, timeout=120):
    """The matcher's own log line for each window id in want, with the time CloudWatch stamped it."""
    logs, found, t0 = s.client("logs"), {}, time.time()
    while time.time() - t0 < timeout and len(found) < len(want):
        token, kw = None, {"logGroupName": group, "startTime": since_ms, "filterPattern": '{ $.matcher = "window" }'}
        while True:
            page = logs.filter_log_events(**kw, **({"nextToken": token} if token else {}))
            for e in page["events"]:
                try:
                    d = json.loads(e["message"])
                except ValueError:
                    continue
                if d.get("window") in want:
                    found[d["window"]] = {**d, "logged_ms": e["timestamp"]}
            token = page.get("nextToken")
            if not token:
                break
        if len(found) < len(want):
            time.sleep(3)
    return found


def runs_started(group, since_ms, until_ms):
    page = s.client("logs").filter_log_events(logGroupName=group, startTime=since_ms, endTime=until_ms, filterPattern='"START RequestId"')
    return len(page["events"])


def visited(rng, made, n):
    """n (state, venue, day, hour) slots that real test trips visited, outside the airport evening."""
    picks = []
    for t in rng.sample(made[HOT:], n):
        sp = t["stops"][0]
        picks.append((sp["state"], int(sp["place"][-2:]), sp["date"], int(sp["start_time"][:2])))
    return picks


def matcher_plan(made, hot_day):
    rng = random.Random(7)
    plan = [("PA", 0, hot_day.isoformat(), 17, 4)]                     # the airport evening: 300 trips were there
    plan += [(a, v, d, h, 2) for a, v, d, h in visited(rng, made, 5)]   # places a few test trips visited
    plan += [("CA", 99, (hot_day - timedelta(days=2)).isoformat(), 12, 3), ("TX", 98, hot_day.isoformat(), 9, 2)]  # nobody went
    return plan


def matcher_cmd():
    st = state()
    made, hot_day = make_trips()
    plan = matcher_plan(made, hot_day)
    group, rows = matcher_group(), []
    for i, (a, v, d, h, hrs) in enumerate(plan):
        w = window(f"scalew{int(time.time())}{i}", a, v, d, h, hrs)
        since = int(time.time() * 1000) - 2000
        store.put(w)
        st["windows"].append(w["id"])
        save_state(st)
        got = matcher_lines(group, since, {w["id"]}).get(w["id"])
        if not got:
            print("no matcher line for", w["id"])
            continue
        rows.append({"state": a, "day": d, "stops_read": got.get("index_items"), "trips_read": got.get("trips_read"),
                     "matches": got.get("matches"), "ms": got.get("ms"), "stored_to_matched_ms": got.get("stored_to_matched_ms")})
        print(rows[-1])
    record({"test": "matcher", "at": iso(now()), "saved_trips": len(made), "saved_stops": sum(len(t["stops"]) for t in made),
            "states": len(STATES), "windows": rows,
            "how": "each window stored by this script, carried by DynamoDB Streams to the matcher; numbers from the matcher's own log line"})


def burst_cmd(n=200):
    st = state()
    made, _ = make_trips()
    rng = random.Random(11)
    slots = visited(rng, made, n)
    ws = [window(f"scaleb{int(time.time())}{i:03d}", a, v, d, h, 2) for i, (a, v, d, h) in enumerate(slots)]
    group = matcher_group()
    st["windows"] += [w["id"] for w in ws]
    save_state(st)
    since = int(time.time() * 1000) - 2000
    written = {}
    t0 = time.time()

    def put(chunk):
        with table().batch_writer() as b:
            for w in chunk:
                b.put_item(Item=store.to_ddb(w))
        at = time.time() * 1000
        for w in chunk:
            written[w["id"]] = at
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(put, [ws[i:i + 25] for i in range(0, len(ws), 25)]))
    write_ms = round((time.time() - t0) * 1000)
    got = matcher_lines(group, since, set(written), timeout=180)
    first = min(written.values())
    last = max((g["logged_ms"] for g in got.values()), default=None)
    waits = sorted(g["logged_ms"] - written[k] for k, g in got.items())
    record({"test": "burst", "at": iso(now()), "windows": len(ws), "matched": len(got), "saved_trips": len(made),
            "write_ms": write_ms, "drained_ms": round(last - first) if last else None,
            "matcher_runs": runs_started(group, since, int(last) + 1000) if last else None,
            "slowest_stored_to_matched_ms": round(waits[-1]) if waits else None,
            "median_stored_to_matched_ms": round(statistics.median(waits)) if waits else None,
            "matches": sum(g.get("matches") or 0 for g in got.values()),
            "how": "times from this laptop's clock (when each batch write returned) to CloudWatch's stamp on the matcher's log line"})


def baseline_cmd():
    """Both designs on the same windows and the same 10,000 trips, timed from this laptop, with the read
    capacity DynamoDB reports for each. Both must find the same matches."""
    from boto3.dynamodb.conditions import Key
    made, hot_day = make_trips()
    res = boto3.Session(region_name=REGION).resource("dynamodb")
    tbl, now_s, rows = res.Table(outs["TableName"]), iso(now()), []

    def query_all(key):
        kw = {"IndexName": "gsi1", "KeyConditionExpression": Key("gsi1pk").eq(key) & Key("gsi1sk").gte(now_s),
              "ReturnConsumedCapacity": "TOTAL"}
        items, cap = [], 0.0
        while True:
            page = tbl.query(**kw)
            items += store.from_ddb(page["Items"])
            cap += page["ConsumedCapacity"]["CapacityUnits"]
            if "LastEvaluatedKey" not in page:
                return items, cap
            kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    for a, v, d, h, hrs in matcher_plan(made, hot_day):
        w, t_now = window("baseline", a, v, d, h, hrs), now()
        # the index: only the stops filed under this window's state and days, then only the trips that matched
        t0, items, cap, ids = time.perf_counter(), 0, 0.0, set()
        for key in watch.window_keys(w):
            got, c = query_all(key)
            items, cap = items + len(got), cap + c
            ids |= {i["trip_id"] for i in got if i.get("drill") and trips.compare(i["stop"], w, t_now)[0] == "match"}
        keys = [{"pk": f"WATCH#{i}", "sk": "META"} for i in sorted(ids)]
        for k in range(0, len(keys), 100):
            r = res.batch_get_item(RequestItems={tbl.name: {"Keys": keys[k:k + 100]}}, ReturnConsumedCapacity="TOTAL")
            cap += sum(x["CapacityUnits"] for x in r.get("ConsumedCapacity", []))
        index = {"ms": round((time.perf_counter() - t0) * 1000), "items_read": items + len(keys), "read_units": round(cap, 1), "matches": len(ids)}
        # the obvious design: read every saved trip and compare every stop
        t0 = time.perf_counter()
        everyone, cap = query_all("SCALEALL")
        hit = {t["id"] for t in everyone for sp in t["stops"] if trips.compare(sp, w, t_now)[0] == "match"}
        every = {"ms": round((time.perf_counter() - t0) * 1000), "items_read": len(everyone), "read_units": round(cap, 1), "matches": len(hit)}
        rows.append({"state": a, "day": d, "index": index, "every_trip": every, "same_matches": hit == ids})
        print(rows[-1])
    ru = 0.125 / 1e6   # on-demand price of one read request unit
    record({"test": "baseline", "at": iso(now()), "saved_trips": len(made), "windows": rows,
            "usd_per_million_windows": {"index": round(sum(r["index"]["read_units"] for r in rows) / len(rows) * ru * 1e6, 2),
                                        "every_trip": round(sum(r["every_trip"]["read_units"] for r in rows) / len(rows) * ru * 1e6, 2)},
            "how": "both designs run from this laptop against the live table, one after the other, for each window"})


def cleanup_cmd():
    if not STATE.exists():
        print("nothing to clean up")
        return
    st = state()
    keys = st["keys"] + [{"pk": f"WINDOW#{w}", "sk": "META"} for w in st["windows"]]
    print(json.dumps({"deleted": delete_all(keys)}))
    STATE.unlink()


def edge_cmd(n=2000, conc=40):
    """n requests, conc at a time, each worker keeping one connection open the way a browser does."""
    import http.client
    from urllib.parse import urlsplit
    host = urlsplit(outs["SiteUrl"]).netloc
    paths = ["/", "/api/exposures", "/api/status", "/board/", "/assets/site.css"]
    per_worker = [list(range(w, n, conc)) for w in range(conc)]

    def worker(idx):
        conn, out = None, []
        for i in idx:
            p = paths[i % len(paths)]
            for attempt in (1, 2):   # a kept-alive connection the server closed is reopened once
                if conn is None:
                    conn = http.client.HTTPSConnection(host, timeout=20)
                t = time.perf_counter()
                try:
                    conn.request("GET", p, headers={"user-agent": "seventytwo-scale-test", "accept-encoding": "gzip"})
                    r = conn.getresponse()
                    r.read()
                    out.append((p, r.status, (time.perf_counter() - t) * 1000, r.getheader("x-cache") or ""))
                    break
                except Exception:
                    conn.close()
                    conn = None
                    if attempt == 2:
                        out.append((p, 0, (time.perf_counter() - t) * 1000, ""))
        if conn:
            conn.close()
        return out
    t0 = time.time()
    with ThreadPoolExecutor(conc) as ex:
        results = [r for rs in ex.map(worker, per_worker) for r in rs]
    secs = time.time() - t0
    lat = sorted(r[2] for r in results)
    q = lambda f: round(lat[min(len(lat) - 1, int(f * len(lat)))])  # noqa: E731
    per = {}
    for p in paths:
        rs = sorted(r[2] for r in results if r[0] == p)
        per[p] = {"requests": len(rs), "p50_ms": round(statistics.median(rs)), "hits": sum(1 for r in results if r[0] == p and r[3].startswith("Hit"))}
    record({"test": "edge", "at": iso(now()), "requests": len(results), "concurrency": conc, "seconds": round(secs, 1),
            "per_second": round(len(results) / secs), "p50_ms": q(0.5), "p95_ms": q(0.95), "p99_ms": q(0.99),
            "errors": sum(1 for r in results if not 200 <= r[1] < 400),
            "cache_hits": sum(1 for r in results if r[3].startswith("Hit")),
            "api_requests": sum(1 for r in results if r[0].startswith("/api/")),
            "api_cache_hits": sum(1 for r in results if r[0].startswith("/api/") and r[3].startswith("Hit")),
            "paths": per, "from": "one laptop in the US, through CloudFront, one kept-alive connection per worker"})


def clocks_cmd(n=50, minutes=4):
    """n real-speed clocks started together; each immune globulin window closes `minutes` after the start and
    nobody answers, so every one must close on its own, at its deadline, not before."""
    sfn = s.client("stepfunctions")
    first = now() - timedelta(days=6) + timedelta(minutes=minutes)
    wid = f"scaleclock{int(time.time())}"
    w = {"pk": f"WINDOW#{wid}", "sk": "META", "gsi1pk": "DRILL", "gsi1sk": iso(first), "drill": True, "id": wid,
         "place": "Drill Hall", "city": "Testville", "state": "IA", "timezone": "America/Chicago",
         "start_utc": iso(first), "end_utc": iso(first + timedelta(hours=1)), "time_clear": True, "flags": [],
         "local_label": "Scale test", "quote": "Scale test.", "url": outs["SiteUrl"], "issuer": "Seventy-Two scale test",
         "ttl": int((now() + timedelta(hours=3)).timestamp())}
    store.put(w)
    d = trips.deadlines_json(trips.window_deadlines(w))
    ids = [f"scale{int(time.time())}{i:03d}" for i in range(n)]

    def start(cid):
        as_of = iso(now())
        store.put({"pk": f"CLOCK#{cid}", "sk": "META", "id": cid, "window_id": wid, "place": "Drill Hall",
                   "deadlines": d, "as_of": as_of, "scale": 1, "created": as_of, "events": [], "tokens": {},
                   "site_url": outs["SiteUrl"], "ttl": int((now() + timedelta(hours=3)).timestamp())})
        ex = sfn.start_execution(stateMachineArn=outs["ClockStateMachineArn"], name=f"clock-{cid}",
                                 input=json.dumps({"clock_id": cid, "deadlines": d, "as_of": as_of, "scale": 1}))
        store.table.update_item(Key={"pk": f"CLOCK#{cid}", "sk": "META"}, UpdateExpression="SET execution_arn = :a",
                                ExpressionAttributeValues={":a": ex["executionArn"]})
        return ex["executionArn"]
    t0 = time.time()
    with ThreadPoolExecutor(10) as ex:
        arns = list(ex.map(start, ids))
    started_s = round(time.time() - t0, 1)
    group = clock_group()
    since = int(t0 * 1000) - 2000
    closed = {}
    time.sleep(minutes * 60)
    while time.time() - t0 < minutes * 60 + 240 and len(closed) < n:
        kw = {"logGroupName": group, "startTime": since, "filterPattern": '{ $.clock_closed = "scale*" }'}
        token = None
        while True:
            page = s.client("logs").filter_log_events(**kw, **({"nextToken": token} if token else {}))
            for e in page["events"]:
                try:
                    x = json.loads(e["message"])
                except ValueError:
                    continue
                if x.get("clock_closed") in ids and x.get("kind") == "ig_closed":
                    closed[x["clock_closed"]] = x["deadline_slip_ms"]
            token = page.get("nextToken")
            if not token:
                break
        if len(closed) < n:
            time.sleep(5)
    for arn in arns:
        try:
            sfn.stop_execution(executionArn=arn, cause="scale test finished")   # each watch would run 15 more days
        except Exception:
            pass
    delete_all([{"pk": f"CLOCK#{c}", "sk": "META"} for c in ids] + [{"pk": w["pk"], "sk": "META"}])
    slips = sorted(closed.values())
    record({"test": "clocks", "at": iso(now()), "clocks": n, "minutes": minutes, "started_in_s": started_s,
            "closed": len(closed), "early": sum(1 for x in slips if x < 0),
            "slip_ms": {"min": round(slips[0]), "p50": round(statistics.median(slips)), "max": round(slips[-1])} if slips else None,
            "how": "each clock's own log line: wall-clock time when its window closed, minus its deadline, in milliseconds"})


def clock_group():
    res = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in s.client("cloudformation").describe_stack_resources(StackName=STACK)["StackResources"]}
    cfg = s.client("lambda").get_function_configuration(FunctionName=res["ClockFunction"])
    return cfg.get("LoggingConfig", {}).get("LogGroup", f"/aws/lambda/{res['ClockFunction']}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    steps = {"trips": trips_cmd, "matcher": matcher_cmd, "burst": burst_cmd, "cleanup": cleanup_cmd, "baseline": baseline_cmd,
             "edge": edge_cmd, "clocks": clocks_cmd}
    for c in (["trips", "matcher", "baseline", "burst", "cleanup", "edge", "clocks"] if cmd == "all" else [cmd]):
        print(f"== {c}", flush=True)
        steps[c]()
