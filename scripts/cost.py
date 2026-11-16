"""What Seventy-Two costs to run, from its own measurements, at AWS list prices (us-east-1).

    python scripts/cost.py [hours]     default: the last 12 hours, scaled to 30 days
    python scripts/cost.py 12 --until 2026-11-15T04:30:00Z
                                       12 hours ending then, so a scale test (scripts/scale.py) is not counted as traffic

It reads the stack's CloudWatch metrics (Lambda, Step Functions, DynamoDB) for the window, the model
cost stored with every notice read, and the state transitions of one real clock. The account may run
other things, so the AWS bill is not used: only this stack's own resources are counted.
"""
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

STACK = os.environ.get("STACK", "seventytwo")
REGION = os.environ.get("REGION", "us-east-1")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
HOURS = float(args[0]) if args and args[0][0].isdigit() else 12.0
UNTIL = sys.argv[sys.argv.index("--until") + 1] if "--until" in sys.argv else None
SCALE = 30 * 24 / HOURS

# list prices, us-east-1, as published on the AWS pricing pages
P = {"lambda_gb_s_arm": 0.0000133334, "lambda_req": 0.20 / 1e6,
     "sfn_transition": 0.025 / 1000, "express_req": 1.00 / 1e6, "express_gb_s": 0.00001667,
     "ddb_wru": 0.625 / 1e6, "ddb_rru": 0.125 / 1e6, "alarm": 0.10, "custom_metric": 0.30}

s = boto3.Session(region_name=REGION)
cfn, cw, sfn = s.client("cloudformation"), s.client("cloudwatch"), s.client("stepfunctions")
res = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in cfn.describe_stack_resources(StackName=STACK)["StackResources"]}
outs = {o["OutputKey"]: o["OutputValue"] for o in cfn.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}
end = datetime.fromisoformat(UNTIL.replace("Z", "+00:00")) if UNTIL else datetime.now(timezone.utc)
start = end - timedelta(hours=HOURS)


def total(ns, name, dims, stat="Sum"):
    r = cw.get_metric_statistics(Namespace=ns, MetricName=name, Dimensions=dims, StartTime=start, EndTime=end,
                                 Period=3600, Statistics=[stat])
    return sum(p[stat] for p in r["Datapoints"])


lam = s.client("lambda")
lambda_cost, rows = 0.0, {}
for logical in ("IngestFunction", "SnapshotFunction", "ClockFunction", "ApiFunction", "MatcherFunction"):
    fn = res[logical]
    mem = lam.get_function_configuration(FunctionName=fn)["MemorySize"] / 1024
    inv = total("AWS/Lambda", "Invocations", [{"Name": "FunctionName", "Value": fn}])
    ms = total("AWS/Lambda", "Duration", [{"Name": "FunctionName", "Value": fn}])
    c = (ms / 1000 * mem * P["lambda_gb_s_arm"] + inv * P["lambda_req"]) * SCALE
    rows[logical] = {"invocations_per_month": round(inv * SCALE), "usd_per_month": round(c, 3)}
    lambda_cost += c

ingest_arn = outs["IngestStateMachineArn"]
runs = total("AWS/States", "ExecutionsStarted", [{"Name": "StateMachineArn", "Value": ingest_arn}]) * SCALE
sources = 27
# parent: start, Map, PublishBoard, end, plus one transition per item; each child is one Express run
sfn_cost = runs * (6 + sources) * P["sfn_transition"] + runs * sources * (P["express_req"] + 2.0 * 0.0625 * P["express_gb_s"])

wcu = total("AWS/DynamoDB", "ConsumedWriteCapacityUnits", [{"Name": "TableName", "Value": res["Table"]}]) * SCALE
rcu = total("AWS/DynamoDB", "ConsumedReadCapacityUnits", [{"Name": "TableName", "Value": res["Table"]}]) * SCALE
ddb_cost = wcu * P["ddb_wru"] + rcu * P["ddb_rru"]

table = s.resource("dynamodb").Table(res["Table"])
costs, recent, kw = [], [], {}
window_start = start.strftime("%Y-%m-%dT%H:%M:%SZ")
while True:
    page = table.scan(ProjectionExpression="pk, #u, fetched_at, source_id", ExpressionAttributeNames={"#u": "usage"}, **kw)
    for i in page["Items"]:
        if i["pk"].startswith("NOTICE#") and i.get("usage") and not str(i.get("source_id", "")).startswith("drill"):
            costs.append(float(i["usage"]["cost_usd"]))
            if i.get("fetched_at", "") >= window_start:
                recent.append(float(i["usage"]["cost_usd"]))
    if "LastEvaluatedKey" not in page:
        break
    kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]

clock_runs = sfn.list_executions(stateMachineArn=outs["ClockStateMachineArn"], maxResults=20)["executions"]
transitions = None
for e in clock_runs:
    if e["status"] == "SUCCEEDED":
        hist = sfn.get_execution_history(executionArn=e["executionArn"], maxResults=1000)["events"]
        transitions = sum(1 for h in hist if h["type"].endswith("StateEntered"))
        break

alarms = len(s.client("cloudwatch").describe_alarms(AlarmNamePrefix=f"{STACK}-")["MetricAlarms"])
# A custom metric is billed for each hour it is written ($0.30 a month if written every hour). Count, for each
# metric name, the hours in the window that had data; also give the ceiling, every metric written every hour.
names = sorted({m["MetricName"] for p in s.client("cloudwatch").get_paginator("list_metrics").paginate(Namespace="SeventyTwo") for m in p["Metrics"]})
active_hours = {}
for n in names:
    pts = cw.get_metric_statistics(Namespace="SeventyTwo", MetricName=n, StartTime=start, EndTime=end, Period=3600, Statistics=["SampleCount"])["Datapoints"]
    active_hours[n] = len(pts)
metric_months = sum(h / HOURS for h in active_hours.values())   # metric-months, scaled from the window
metrics = sum(1 for h in active_hours.values() if h)
# the Ops page asks CloudWatch for about 85 series at most once every 10 minutes, and only when someone opens it
ops_max = round(85 * 0.01 / 1000 * 6 * 24 * 30, 2)
model_month = sum(recent) * SCALE
print(json.dumps({
    "window_hours": HOURS, "window_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "scheduled_reads_per_month": round(runs),
    "lambda_usd_per_month": round(lambda_cost, 2), "lambda_by_function": rows,
    "step_functions_ingest_usd_per_month": round(sfn_cost, 2),
    "dynamodb_usd_per_month": round(ddb_cost, 2),
    "alarms": alarms, "alarms_usd_per_month": round(alarms * P["alarm"], 2),
    "custom_metrics_written": metrics, "custom_metric_hours_in_window": active_hours,
    "custom_metrics_usd_per_month": round(metric_months * P["custom_metric"], 2),
    "custom_metrics_usd_per_month_ceiling": round(len([n for n in names if active_hours[n]]) * P["custom_metric"], 2),
    "free_tier_covers": "10 custom metrics, 10 alarms, 1M Lambda requests and 400,000 GB-s, 4,000 Standard state transitions",
    "inside_free_tier_usd_per_month": round(max(0, metric_months - 10) * P["custom_metric"] + max(0, alarms - 10) * P["alarm"]
                                            + sfn_cost + ddb_cost + model_month, 2),
    "model_reads_in_window": len(recent), "model_usd_per_month_at_this_rate": round(model_month, 2),
    "total_usd_per_month": round(lambda_cost + sfn_cost + ddb_cost + alarms * P["alarm"] + metric_months * P["custom_metric"] + model_month, 2),
    "ops_page_usd_per_month_at_most": ops_max,
    "model_reads_so_far": len(costs), "model_usd_so_far": round(sum(costs), 3),
    "model_usd_per_notice_median": round(statistics.median(costs), 4) if costs else None,
    "model_usd_per_notice_max": round(max(costs), 4) if costs else None,
    "clock_state_transitions": transitions,
    "clock_usd_each": round(transitions * P["sfn_transition"], 5) if transitions else None,
    "note": "List prices, before the AWS Free Tier (which covers the alarms and metrics). CloudFront, S3, API Gateway, SNS, SSM and logs are cents at this traffic.",
}, indent=1))
