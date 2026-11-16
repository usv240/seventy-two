"""Run the matcher again over windows stored in a time range, for example after a matcher failure.

    python scripts/replay.py --hours 24          windows first seen in the last 24 hours
    python scripts/replay.py --since 2026-11-14T00:00:00Z

DynamoDB Streams keeps records for 24 hours, and the SQS queue on the matcher only records which batch failed
(its position in the stream), not the windows. The windows themselves are in the table, so this reads them
from there and hands them to the same matcher function, shaped as stream INSERT records, then empties the
failure queue so its alarm clears. The matcher ignores a window a trip already has, so running this twice is
harmless. Pick a range that covers the failed batch (the queue message says when it failed).
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer

STACK = os.environ.get("STACK", "seventytwo")
REGION = os.environ.get("REGION", "us-east-1")

ap = argparse.ArgumentParser()
ap.add_argument("--hours", type=float, default=24)
ap.add_argument("--since")
args = ap.parse_args()
since = args.since or (datetime.now(timezone.utc) - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

s = boto3.Session(region_name=REGION)
outs = {o["OutputKey"]: o["OutputValue"] for o in s.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}
res = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in s.client("cloudformation").describe_stack_resources(StackName=STACK)["StackResources"]}
table = s.resource("dynamodb").Table(outs["TableName"])

items, kw = [], {"IndexName": "gsi1", "KeyConditionExpression": Key("gsi1pk").eq("WINDOW")}
while True:
    page = table.query(**kw)
    items += [i for i in page["Items"] if i.get("first_seen", "") >= since]
    if "LastEvaluatedKey" not in page:
        break
    kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]

ser = TypeSerializer()
lam = s.client("lambda")
total = {"windows": 0, "matches": 0}
for i in range(0, len(items), 25):
    batch = items[i:i + 25]
    event = {"Records": [{"eventName": "INSERT", "dynamodb": {"Keys": {"pk": {"S": w["pk"]}}, "NewImage": ser.serialize(w)["M"]}}
                         for w in batch]}
    r = lam.invoke(FunctionName=res["MatcherFunction"], Payload=json.dumps(event, default=str).encode())
    out = json.loads(r["Payload"].read() or b"{}")
    total["windows"] += out.get("windows", 0)
    total["matches"] += out.get("matches", 0)
# the failure queue only says which batch failed; once the windows are matched again, its messages are done with
sqs = s.client("sqs")
queue = sqs.get_queue_url(QueueName=res["MatcherFailures"].rsplit("/", 1)[-1])["QueueUrl"]
cleared = 0
while True:
    msgs = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=10, WaitTimeSeconds=1).get("Messages", [])
    if not msgs:
        break
    for m in msgs:
        sqs.delete_message(QueueUrl=queue, ReceiptHandle=m["ReceiptHandle"])
        cleared += 1
print(json.dumps({"since": since, **total, "failure_messages_cleared": cleared}))
