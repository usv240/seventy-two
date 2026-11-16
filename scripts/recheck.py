"""Re-check every stored exposure window with the reader's current checks, without calling the model.

    python scripts/recheck.py          report only
    python scripts/recheck.py --apply  also correct the stored windows, and drop the state of sources
                                       that are no longer in sources/sources.json

For each window it reads the archived notice text from S3 and asks the same questions reader.validate
asks today: is the quote in the notice, is the date written in or just above it, are the start and end
times written near it, and are the published and watch-until dates printed on the page. A date or time
that fails is removed (a window can only get wider, never narrower). Nothing is guessed.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import evidence  # noqa: E402
import reader  # noqa: E402
from textutil import normalize  # noqa: E402

STACK, REGION = os.environ.get("STACK", "seventytwo"), os.environ.get("REGION", "us-east-1")
APPLY = "--apply" in sys.argv

s = boto3.Session(region_name=REGION)
outs = {o["OutputKey"]: o["OutputValue"] for o in s.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}
table = s.resource("dynamodb").Table(outs["TableName"])
s3 = s.client("s3")


def query(pk):
    items, kw = [], {"IndexName": "gsi1", "KeyConditionExpression": Key("gsi1pk").eq(pk)}
    while True:
        page = table.query(**kw)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


texts, notices = {}, {}
counts = {"windows": 0, "quote_ok": 0, "date_ok": 0, "start_time_ok": 0, "end_time_ok": 0, "timed": 0,
          "watch_until_printed": 0, "watch_until_removed": 0, "published_ok": 0, "published_removed": 0}
changes = []
for w in query("WINDOW"):
    counts["windows"] += 1
    sha = w["notice_sha"]
    if sha not in texts:
        n = table.get_item(Key={"pk": f"NOTICE#{sha}", "sk": "META"}).get("Item") or {}
        notices[sha] = n
        texts[sha] = normalize(s3.get_object(Bucket=outs["ArchiveBucketName"], Key=n["s3_key"] + ".txt")["Body"].read().decode("utf-8", "replace")) if n.get("s3_key") else ""
    t, n = texts[sha], notices[sha]
    q = normalize(w["quote"])
    at = t.find(q)
    if at < 0:
        changes.append((w["id"], "quote not found: needs a fresh read"))
        continue
    counts["quote_ok"] += 1
    tz = ZoneInfo(w["timezone"])
    start = datetime.fromisoformat(w["start_utc"].replace("Z", "+00:00")).astimezone(tz)
    end = datetime.fromisoformat(w["end_utc"].replace("Z", "+00:00")).astimezone(tz)
    if evidence.date_supported(t, start.date(), at, at + len(q)):
        counts["date_ok"] += 1
    else:
        changes.append((w["id"], f"date {start.date()} not near the quote: needs a fresh read"))
    if w.get("time_clear"):
        counts["timed"] += 1
        near = lambda dt: evidence.near(evidence.time_spans(t, dt.hour, dt.minute), at, at + len(q), before=evidence.TIME_WITHIN, after=evidence.TIME_WITHIN)
        counts["start_time_ok"] += near(start)
        counts["end_time_ok"] += ("end_time_missing" in (w.get("flags") or [])) or near(end)
    upd = {}
    if w.get("watch_until"):
        if reader.checked_date(w["watch_until"], t):
            counts["watch_until_printed"] += 1
        else:
            counts["watch_until_removed"] += 1
            upd["watch_until"] = None
            changes.append((w["id"], f"watch_until {w['watch_until']} is not printed on the page (the model computed it): removed"))
    if w.get("published_date"):
        if reader.checked_date(w["published_date"], t, n.get("url") or w.get("url") or ""):
            counts["published_ok"] += 1
        else:
            counts["published_removed"] += 1
            upd["published_date"] = None
            changes.append((w["id"], f"published_date {w['published_date']} is not on the page: removed"))
    if upd and APPLY:
        names = {f"#{k}": k for k in upd}
        table.update_item(Key={"pk": w["pk"], "sk": w["sk"]}, UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in upd),
                          ExpressionAttributeNames=names, ExpressionAttributeValues={f":{k}": v for k, v in upd.items()})

listed = {x["id"] for x in json.loads((ROOT / "sources" / "sources.json").read_text(encoding="utf-8"))}
stale = [i for i in query("SOURCE") if i.get("gsi1sk") not in listed and not i.get("found_on")]
for i in stale:
    changes.append((i["gsi1sk"], "source no longer in sources.json: its status row is dropped"))
    if APPLY:
        table.delete_item(Key={"pk": i["pk"], "sk": i["sk"]})

print(json.dumps(counts, indent=1))
for c in changes:
    print(*c)
print("applied" if APPLY else "report only (pass --apply to correct)")
