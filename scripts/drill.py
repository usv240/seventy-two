"""Prove the two paths that only run when something goes wrong or something new happens, on the live stack.

    python scripts/drill.py failover   make the API return 503 for one minute; CloudFront must serve the S3 snapshot
    python scripts/drill.py trip       save a drill trip, then have the ingest function read a drill notice;
                                       Claude's window must reach the trip through DynamoDB Streams
    python scripts/drill.py trip --keep   leave the drill trip in place (its page shows the match) for a demo
    python scripts/drill.py clock      a real-speed clock whose window closes in 3 minutes; the task token must time out
    python scripts/drill.py trip-open     save a drill trip and print its id (open /watch/?id=... first)
    python scripts/drill.py trip-fire ID  then have the ingest function read the drill notice that matches it

Every result is appended to docs/drills.json with its time, so the write-up quotes a run, not a hope.
Drill data never reaches the public board: the drill notice is shared by a private ten-minute link, and its
window is filed under its own index key and deletes itself within two hours.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
STACK = os.environ.get("STACK", "seventytwo")
REGION = os.environ.get("REGION", "us-east-1")
s = boto3.Session(region_name=REGION)
outs = {o["OutputKey"]: o["OutputValue"] for o in s.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}
LOG = ROOT / "docs" / "drills.json"


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def record(entry):
    runs = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    runs.append(entry)
    LOG.write_text(json.dumps(runs, indent=1), encoding="utf-8")
    print(json.dumps(entry, indent=1))


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"cache-control": "no-cache", "user-agent": "seventytwo-drill", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def failover():
    lam = s.client("lambda")
    fn = outs["ApiFunctionName"]
    env = lam.get_function_configuration(FunctionName=fn)["Environment"]["Variables"]

    def set_env(v):
        lam.update_function_configuration(FunctionName=fn, Environment={"Variables": v})
        lam.get_waiter("function_updated_v2").wait(FunctionName=fn)

    site, api = outs["SiteUrl"], outs["ApiUrl"]
    verify = {"x-origin-verify": env.get("ORIGIN_SECRET", "").split(",")[0]}   # the API refuses calls that skip CloudFront
    bust = lambda: f"?drill={time.time_ns()}"   # a fresh cache key, so CloudFront asks the API, not its cache
    before = get(f"{site}/api/exposures{bust()}")
    set_env({**env, "FAILOVER_DRILL": "1"})
    try:
        time.sleep(5)
        direct = get(f"{api}/api/exposures", verify)
        t0 = time.time()
        through = get(f"{site}/api/exposures{bust()}")
        ms = round((time.time() - t0) * 1000)
    finally:
        set_env({k: v for k, v in env.items() if k != "FAILOVER_DRILL"})
    after = get(f"{site}/api/exposures{bust()}")
    refused = get(f"{api}/api/exposures")[0]
    ok = direct[0] == 503 and through[0] == 200 and through[1].get("snapshot") is True
    record({"drill": "failover", "at": iso(now()), "passed": ok,
            "api_direct_status": direct[0], "through_cloudfront_status": through[0],
            "served_from": "S3 snapshot" if through[1].get("snapshot") else "API",
            "rows_from_api_before": len(before[1].get("windows", [])), "rows_from_snapshot": len(through[1].get("windows", [])),
            "rows_from_api_after": len(after[1].get("windows", [])), "response_ms": ms,
            "direct_call_without_cloudfront": refused})


def _modules():
    os.environ.update(TABLE=outs["TableName"], AWS_DEFAULT_REGION=REGION)
    sys.path.insert(0, str(ROOT / "src"))
    import store  # noqa: E402
    import watch  # noqa: E402
    return store, watch


def drill_stop():
    day = now() - timedelta(days=1)
    return {"place": "Drill Hall", "city": "Testville", "state": "IA", "date": day.strftime("%Y-%m-%d"),
            "start_time": "10:00", "end_time": "12:00", "timezone": "America/Chicago"}


def drill_notice():
    """A notice written like a real one, about a place that does not exist, shared for ten minutes by a private link."""
    day = now() - timedelta(days=1)
    when = f"{day:%A}, {day:%B} {day.day}, {day.year}"
    html = f"""<html><body><h1>Testville Health Department warns of possible measles exposure at Drill Hall</h1>
<p>Published {now():%B} {now().day}, {now().year}</p>
<p>The Testville Health Department is notifying the public that a person with confirmed measles was at the following location
while contagious. All times are Central Time. This is a drill: Testville, Iowa, does not exist.</p>
<p>{when}: Drill Hall, 1 Test Street, Testville, IA, from 10 a.m. to 12 p.m.</p>
<p>Anyone who was there and is not immune should call their doctor. Questions: 555-0100.</p></body></html>"""
    s3 = s.client("s3")
    key = f"drill/notice-{int(time.time())}.html"
    s3.put_object(Bucket=outs["ArchiveBucketName"], Key=key, Body=html.encode(), ContentType="text/html")
    return s3.generate_presigned_url("get_object", Params={"Bucket": outs["ArchiveBucketName"], "Key": key}, ExpiresIn=600), key


def trip_open():
    """Save a drill trip and print its id, so a page can be opened on it before the window arrives."""
    _, watch = _modules()
    print(watch.create([drill_stop()], [], drill=True, site_url=outs["SiteUrl"])["id"])


def tick(**kw):
    """One progress line for the video recorder (trip-serve), as it happens."""
    if TICKS:
        print(json.dumps({"tick": kw}), flush=True)


TICKS = False


def trip(keep=False, watch_id=None):
    """The whole path: a notice is read by the ingest function (fetch, hash, Claude, the evidence checks, DynamoDB),
    the new window goes through DynamoDB Streams to the matcher, and the saved trip shows the match."""
    store, watch = _modules()
    w = {"id": watch_id} if watch_id else watch.create([drill_stop()], [], drill=True, site_url=outs["SiteUrl"])
    url, key = drill_notice()
    src = {"id": f"drill-{int(time.time())}", "url": url, "issuer": "Testville Health Department", "state": "IA",
           "kind": "notice", "drill": True}
    t0 = time.time()
    lam = s.client("lambda")
    r = lam.invoke(FunctionName=outs["IngestFunctionName"], Payload=json.dumps({"source": src}).encode())
    read = json.loads(r["Payload"].read() or b"{}")
    read_s = round(time.time() - t0, 1)
    tick(step="read", s=read_s, windows=read.get("windows"), cost=read.get("cost_usd"))
    found = None
    while time.time() - t0 < 90:
        got = watch.get(w["id"])
        if got and got["matches"]:
            found = got["matches"][0]
            tick(step="matched", s=round(time.time() - t0, 1))
            break
        time.sleep(0.5)
    seconds = round(time.time() - t0, 1)
    pushes = push_log(t0)
    if pushes:
        tick(step="push", s=round(time.time() - t0, 1), status=pushes[0].get("status"))
    s.client("s3").delete_object(Bucket=outs["ArchiveBucketName"], Key=key)
    store.table.delete_item(Key={"pk": f"SOURCE#{src['id']}", "sk": "STATE"})
    if not keep:
        for wid in (found or {}).get("window", {}).get("ids") or [(found or {}).get("window", {}).get("id")]:
            if wid:
                store.table.delete_item(Key={"pk": f"WINDOW#{wid}", "sk": "META"})
        watch.delete(w["id"])
    record({"drill": "trip", "at": iso(now()), "passed": bool(found), "path": "notice > ingest Lambda > Claude > checks > DynamoDB > Streams > matcher > trip",
            "windows_read": read.get("windows"), "model_cost_usd": read.get("cost_usd"), "seconds_to_read": read_s,
            "seconds_from_read_to_match": round(seconds - read_s, 1), "seconds_total": seconds,
            "quote": (found or {}).get("window", {}).get("quote"),
            "status_shown": ((found or {}).get("status") or {}).get("phase"),
            "push_service_replies": pushes or "no device had turned alerts on for this drill trip",
            "trip_page": f"{outs['SiteUrl']}/watch/?id={w['id']}" if keep else "deleted after the drill"})


def push_log(since_epoch):
    """What the browsers' push services answered the matcher, from its own log lines."""
    res = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in s.client("cloudformation").describe_stack_resources(StackName=STACK)["StackResources"]}
    group = s.client("lambda").get_function_configuration(FunctionName=res["MatcherFunction"]).get("LoggingConfig", {}).get(
        "LogGroup", f"/aws/lambda/{res['MatcherFunction']}")
    for _ in range(20):   # CloudWatch Logs takes a few seconds to deliver
        events = s.client("logs").filter_log_events(logGroupName=group, startTime=int(since_epoch * 1000), filterPattern='"push"')["events"]
        found = [json.loads(e["message"]) for e in events if e["message"].strip().startswith("{")]
        if found:
            return found
        time.sleep(2)
    return []


def clock():
    """A real-speed clock (scale 1) on a drill exposure whose immune globulin window closes in 3 minutes.
    Nobody answers, so the task token must time out at the deadline and close the window on its own."""
    store, _ = _modules()
    first = now() - timedelta(days=6) + timedelta(minutes=3)
    wid = f"drillclock{int(time.time())}"
    store.put({"pk": f"WINDOW#{wid}", "sk": "META", "gsi1pk": "DRILL", "gsi1sk": iso(first), "drill": True, "id": wid,
               "place": "Drill Hall", "city": "Testville", "state": "IA", "timezone": "America/Chicago",
               "start_utc": iso(first), "end_utc": iso(first + timedelta(hours=1)), "time_clear": True, "flags": [],
               "local_label": "Drill", "quote": "Drill.", "url": outs["SiteUrl"], "issuer": "Seventy-Two drill",
               "ttl": int((now() + timedelta(hours=2)).timestamp())})
    req = urllib.request.Request(outs["SiteUrl"] + "/api/clocks", data=json.dumps({"window_id": wid, "scale": 1}).encode(),
                                 headers={"content-type": "application/json", "origin": outs["SiteUrl"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        c = json.load(r)
    closed_at, t0 = None, time.time()
    while time.time() - t0 < 600:
        got = get(f"{outs['SiteUrl']}/api/clocks/{c['id']}")[1]
        ev = {e["kind"]: e["at"] for e in got.get("events", [])}
        deadline = datetime.fromisoformat(got["deadlines"]["ig_deadline"].replace("Z", "+00:00"))
        if "ig_closed" in ev:
            closed_at = ev["ig_closed"]
            break
        time.sleep(5)
    sfn = s.client("stepfunctions")
    sfn.stop_execution(executionArn=c["execution_arn"], cause="drill finished")   # its watch would run 15 more days
    store.table.delete_item(Key={"pk": f"CLOCK#{c['id']}", "sk": "META"})
    store.table.delete_item(Key={"pk": f"WINDOW#{wid}", "sk": "META"})
    late = round((datetime.fromisoformat(closed_at.replace("Z", "+00:00")) - deadline).total_seconds(), 1) if closed_at else None
    record({"drill": "clock", "at": iso(now()), "passed": closed_at is not None and late is not None and 0 <= late < 60,
            "speed": "real (1 second is 1 second)", "immune_globulin_deadline": iso(deadline), "window_closed_at": closed_at,
            "seconds_after_deadline": late, "how": "the task token's timeout, caught as States.Timeout; nobody pressed anything",
            "events": list(ev)})


def trip_serve(watch_id):
    """For the video: load everything, print 'ready', then write the drill window when a line arrives on stdin."""
    global TICKS
    TICKS = True
    _modules()
    print("ready", flush=True)
    sys.stdin.readline()
    trip(keep=True, watch_id=watch_id)


if __name__ == "__main__":
    {"failover": failover, "trip": lambda: trip(keep="--keep" in sys.argv), "trip-open": trip_open,
     "trip-fire": lambda: trip(keep=True, watch_id=sys.argv[2]),
     "trip-serve": lambda: trip_serve(sys.argv[2]), "clock": clock}[sys.argv[1]]()
