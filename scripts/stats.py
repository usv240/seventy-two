"""Print every number the write-up quotes, from the live stack.

    python scripts/stats.py

Reads the Step Functions run history and the site's own /api/status, so the article
never carries a number that the running system does not show.
"""
import collections
import json
import os
import urllib.request
from datetime import timezone

import boto3

STACK = os.environ.get("STACK", "seventytwo")
REGION = os.environ.get("REGION", "us-east-1")

session = boto3.Session(region_name=REGION)
cfn, sfn = session.client("cloudformation"), session.client("stepfunctions")
outs = {o["OutputKey"]: o["OutputValue"] for o in cfn.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}


def runs(arn):
    items = []
    for page in sfn.get_paginator("list_executions").paginate(stateMachineArn=arn):
        items += page["executions"]
    return items


every = runs(outs["IngestStateMachineArn"])
ingest = [e for e in every if not e["name"].startswith(("manual-", "catchup-"))]  # started by EventBridge Scheduler
clocks = runs(outs["ClockStateMachineArn"])
count = collections.Counter(e["status"] for e in ingest)
print(f"Reads started by hand (deploys and catch-up): {len(every) - len(ingest)}")
ordered = sorted(ingest, key=lambda e: e["startDate"])
failed_after_first = [e for e in ordered[1:] if e["status"] in ("FAILED", "TIMED_OUT", "ABORTED")]

with urllib.request.urlopen(outs["SiteUrl"] + "/api/status", timeout=20) as r:
    status = json.load(r)
status.pop("sources", None)

print("Scheduled reads")
print(f"  total {len(ingest)}  succeeded {count['SUCCEEDED']}  failed {count['FAILED']}  running {count['RUNNING']}")
if ordered:
    first, last = (e["startDate"].astimezone(timezone.utc) for e in (ordered[0], ordered[-1]))
    print(f"  first {first:%Y-%m-%d %H:%M} UTC ({ordered[0]['status']})  last {last:%Y-%m-%d %H:%M} UTC")
print(f"  failures after the first run: {len(failed_after_first)}")
print(f"Clocks started: {len(clocks)}  ({dict(collections.Counter(e['status'] for e in clocks))})")
print("Status panel")
for k, v in status.items():
    print(f"  {k}: {v}")
