"""The data behind the public /ops/ page: what CloudWatch and X-Ray already measure about this stack.

One answer, cached: CloudFront keeps it for 5 minutes, and a copy in DynamoDB means CloudWatch is asked at
most once every 10 minutes however many people open the page. Nothing here is about a visitor: these are
counts, timings and costs of the stack itself.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import boto3

import store

CACHE = "public, max-age=300"
FRESH_S = 600   # CloudWatch is asked at most every 10 minutes: about 85 series, $0.00085 a time
HOURS = 24
cw = boto3.client("cloudwatch")
xray = boto3.client("xray")
ssm = boto3.client("ssm")
FUNCTIONS = json.loads(os.environ.get("OPS_FUNCTIONS") or "{}")   # role -> function name (the API adds its own)
INGEST_SM = os.environ.get("OPS_INGEST_SM", "")
CLOCK_SM = os.environ.get("CLOCK_STATE_MACHINE_ARN", "")
QUEUE = os.environ.get("OPS_QUEUE", "")
TABLE = os.environ.get("TABLE", "")
DIST_PARAM = os.environ.get("OPS_DISTRIBUTION_PARAM", "")
_dist = {}


def functions():
    f = dict(FUNCTIONS)
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        f["api"] = os.environ["AWS_LAMBDA_FUNCTION_NAME"]
    return f


def distribution_id():
    if "id" not in _dist:
        try:
            _dist["id"] = ssm.get_parameter(Name=DIST_PARAM)["Parameter"]["Value"] if DIST_PARAM else ""
        except Exception:
            _dist["id"] = ""
    return _dist["id"]


def series_specs(api_id):
    """Every series the page shows: (id, label, namespace, metric, dimensions, statistic, how to total it)."""
    def d(**kw):
        return [{"Name": k, "Value": v} for k, v in kw.items() if v]

    S = []
    add = lambda *a: S.append(a)  # noqa: E731
    # the edge and the API
    dist = distribution_id()
    if dist:
        add("cf_requests", "CloudFront requests", "AWS/CloudFront", "Requests", d(DistributionId=dist, Region="Global"), "Sum", "sum")
        add("cf_errors", "CloudFront error rate", "AWS/CloudFront", "TotalErrorRate", d(DistributionId=dist, Region="Global"), "Average", "summary")
    if api_id:
        add("api_requests", "API requests", "AWS/ApiGateway", "Count", d(ApiId=api_id), "Sum", "sum")
        add("api_4xx", "API 4xx", "AWS/ApiGateway", "4xx", d(ApiId=api_id), "Sum", "sum")
        add("api_5xx", "API 5xx", "AWS/ApiGateway", "5xx", d(ApiId=api_id), "Sum", "sum")
        add("api_p50", "API latency p50", "AWS/ApiGateway", "Latency", d(ApiId=api_id), "p50", "summary")
        add("api_p99", "API latency p99", "AWS/ApiGateway", "Latency", d(ApiId=api_id), "p99", "summary")
    # each function
    for role, name in functions().items():
        add(f"fn_{role}_inv", f"{role} invocations", "AWS/Lambda", "Invocations", d(FunctionName=name), "Sum", "sum")
        add(f"fn_{role}_err", f"{role} errors", "AWS/Lambda", "Errors", d(FunctionName=name), "Sum", "sum")
        add(f"fn_{role}_thr", f"{role} throttles", "AWS/Lambda", "Throttles", d(FunctionName=name), "Sum", "sum")
        add(f"fn_{role}_p95", f"{role} duration p95", "AWS/Lambda", "Duration", d(FunctionName=name), "p95", "summary")
        add(f"fn_{role}_conc", f"{role} concurrency", "AWS/Lambda", "ConcurrentExecutions", d(FunctionName=name), "Maximum", "max")
    if functions().get("matcher"):
        add("stream_age", "Stream iterator age", "AWS/Lambda", "IteratorAge", d(FunctionName=functions()["matcher"]), "Maximum", "max")
    # workflows
    for key, arn in (("ingest", INGEST_SM), ("clock", CLOCK_SM)):
        if arn:
            add(f"sfn_{key}_started", f"{key} runs started", "AWS/States", "ExecutionsStarted", d(StateMachineArn=arn), "Sum", "sum")
            add(f"sfn_{key}_failed", f"{key} runs failed", "AWS/States", "ExecutionsFailed", d(StateMachineArn=arn), "Sum", "sum")
            add(f"sfn_{key}_ok", f"{key} runs succeeded", "AWS/States", "ExecutionsSucceeded", d(StateMachineArn=arn), "Sum", "sum")
    if INGEST_SM:
        add("sfn_ingest_ms", "ingest run time", "AWS/States", "ExecutionTime", d(StateMachineArn=INGEST_SM), "Average", "summary")
    # data
    if TABLE:
        add("ddb_rcu", "DynamoDB read units", "AWS/DynamoDB", "ConsumedReadCapacityUnits", d(TableName=TABLE), "Sum", "sum")
        add("ddb_wcu", "DynamoDB write units", "AWS/DynamoDB", "ConsumedWriteCapacityUnits", d(TableName=TABLE), "Sum", "sum")
        add("ddb_rthr", "DynamoDB read throttles", "AWS/DynamoDB", "ReadThrottleEvents", d(TableName=TABLE), "Sum", "sum")
        add("ddb_wthr", "DynamoDB write throttles", "AWS/DynamoDB", "WriteThrottleEvents", d(TableName=TABLE), "Sum", "sum")
    if QUEUE:
        add("dlq", "Unmatched stream batches", "AWS/SQS", "ApproximateNumberOfMessagesVisible", d(QueueName=QUEUE), "Maximum", "max")
    # what Seventy-Two itself reports (src/obs.py)
    for mid, label, metric, stat, how in (
            ("src_read", "Pages read by Claude", "SourceRead", "Sum", "sum"),
            ("src_failed", "Pages that failed", "SourceFailed", "Sum", "sum"),
            ("win_found", "New exposure windows", "WindowsFound", "Sum", "sum"),
            ("win_rejected", "Proposals the checks dropped", "WindowsRejected", "Sum", "sum"),
            ("model_usd", "Claude spend", "ModelCostUSD", "Sum", "sum"),
            ("model_calls", "Claude calls", "ModelCostUSD", "SampleCount", "sum"),
            ("model_p50", "Claude latency p50", "ModelLatencyMs", "p50", "summary"),
            ("model_p95", "Claude latency p95", "ModelLatencyMs", "p95", "summary"),
            ("checks", "Checks", "CheckLatencyMs", "SampleCount", "sum"),
            ("check_p50", "Check latency p50", "CheckLatencyMs", "p50", "summary"),
            ("check_p95", "Check latency p95", "CheckLatencyMs", "p95", "summary"),
            ("match_windows", "Windows matched", "MatchWindowMs", "SampleCount", "sum"),
            ("match_p95", "Match time per window p95", "MatchWindowMs", "p95", "summary"),
            ("match_stops", "Saved stops read per window (max)", "MatcherIndexItems", "Maximum", "max"),
            ("match_trips", "Trips read per window (max)", "MatcherTripsRead", "Maximum", "max"),
            ("stored_p50", "Stored to matched p50", "StoredToMatchedMs", "p50", "summary"),
            ("stored_max", "Stored to matched, slowest", "StoredToMatchedMs", "Maximum", "max"),
            ("trip_matches", "Trip matches", "TripMatches", "Sum", "sum"),
            ("push_sent", "Notifications accepted", "PushSent", "Sum", "sum"),
            ("push_failed", "Notifications refused", "PushFailed", "Sum", "sum"),
            ("slip_n", "Windows closed by the clock", "DeadlineSlipMs", "SampleCount", "sum"),
            ("slip_max", "Latest close after a deadline", "DeadlineSlipMs", "Maximum", "max")):
        add(mid, label, "SeventyTwo", metric, [], stat, how)
    return S


def metrics(now, api_id):
    """24 hourly points per series, plus a 24-hour value for percentiles and averages (not an average of hours)."""
    end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    start = end - timedelta(hours=HOURS)
    specs = series_specs(api_id)
    queries = []
    for mid, _, ns, name, dims, stat, how in specs:
        m = {"Namespace": ns, "MetricName": name, "Dimensions": dims}
        queries.append({"Id": mid, "MetricStat": {"Metric": m, "Period": 3600, "Stat": stat}, "ReturnData": True})
        if how == "summary":
            queries.append({"Id": f"{mid}_day", "MetricStat": {"Metric": m, "Period": HOURS * 3600, "Stat": stat}, "ReturnData": True})
    got, token = {}, None
    while True:
        kw = {"MetricDataQueries": queries, "StartTime": start, "EndTime": end, "ScanBy": "TimestampAscending"}
        if token:
            kw["NextToken"] = token
        r = cw.get_metric_data(**kw)
        for res in r["MetricDataResults"]:
            got.setdefault(res["Id"], {}).update(zip((t.isoformat() for t in res["Timestamps"]), res["Values"]))
        token = r.get("NextToken")
        if not token:
            break
    hours = [(start + timedelta(hours=i)) for i in range(HOURS)]
    out = {}
    for mid, label, ns, name, _, stat, how in specs:
        pts = got.get(mid, {})
        values = [_round(pts.get(h.isoformat())) for h in hours]
        present = [v for v in values if v is not None]
        if how == "sum":
            total = _round(sum(present)) if present else 0
        elif how == "max":
            total = max(present) if present else None
        else:
            day = list(got.get(f"{mid}_day", {}).values())
            total = _round(day[0]) if day else None
        out[mid] = {"label": label, "metric": f"{ns} {name}", "stat": stat, "values": values, "total": total}
    return {"start": start.isoformat(), "end": end.isoformat(), "hours": HOURS, "series": out}


def _round(v):
    if v is None:
        return None
    return round(v, 6) if abs(v) < 1 else round(v, 1)


# ---------- the X-Ray service map, reduced to this stack and named for people ----------

AWS_NAMES = {"monitoring": "CloudWatch", "states": "Step Functions", "stepfunctions": "Step Functions", "ssm": "SSM",
             "sesv2": "SES", "email": "SES", "sqs": "SQS", "xray": "X-Ray", "s3": "S3", "dynamodb": "DynamoDB",
             "sns": "SNS", "sts": "STS"}


def _label(node, roles):
    name, typ = node.get("Name") or "", node.get("Type") or ""
    if typ in ("AWS::Lambda::Function", "AWS::Lambda") and name in roles:
        return roles[name], "function"
    if typ == "AWS::StepFunctions::StateMachine":
        return ("Reading workflow" if name.endswith("-ingest") else "Clock workflow" if name.endswith("-clock") else name), "workflow"
    if typ == "client":
        return "Callers", "client"
    if typ.startswith("AWS::DynamoDB"):
        return "DynamoDB", "aws"
    if typ.startswith("AWS::S3"):
        return "S3", "aws"
    if typ.startswith("AWS::"):
        short = typ.split("::")[1]
        return AWS_NAMES.get(short.lower(), short), "aws"
    host = name.lower()
    if host == "a link pasted in the lab":
        return "Links pasted in the Lab", "outside"
    if host.startswith("bedrock"):
        return "Bedrock (Claude)", "outside"
    if any(p in host for p in ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")):
        return "Push services", "outside"
    if host.endswith(".amazonaws.com"):
        if ".s3." in host or host.startswith("s3.") or host.startswith("s3-"):
            return "S3", "aws"   # a presigned link to a bucket (the drill notice)
        return AWS_NAMES.get(host.split(".")[0], host.split(".")[0].upper()), "aws"
    return "Health department sites", "outside"


MAP_CALLS = 4   # X-Ray's service map covers at most 6 hours a call; four calls cover the day


def service_graph(now):
    """The last 24 hours of X-Ray's service map, as four 6-hour calls merged, kept to this stack's own nodes and
    their neighbours, with remote hosts grouped (every health department site is one node). A quiet hour or two still
    shows the whole stack, because the 30-minute reads ran in the same day."""
    def window(i):
        end = now - timedelta(hours=6 * i)
        try:
            return xray.get_service_graph(StartTime=end - timedelta(hours=6), EndTime=end)
        except Exception as e:
            return {"error": str(e)[:200]}
    with ThreadPoolExecutor(MAP_CALLS) as pool:
        graphs = list(pool.map(window, range(MAP_CALLS)))
    if all(g.get("error") for g in graphs):
        return {"error": graphs[0]["error"], "nodes": [], "edges": []}
    roles = {v: k for k, v in functions().items()}
    sm_names = {a.rsplit(":", 1)[-1] for a in (INGEST_SM, CLOCK_SM) if a}
    nodes, edges, hosts = {}, {}, {}
    for g in graphs:
        services = g.get("Services", [])
        ours = {s["ReferenceId"] for s in services if s.get("Name") in roles or s.get("Name") in sm_names}
        by_ref = {s["ReferenceId"]: s for s in services}
        # keep our nodes, what they call, and callers that call them
        keep = set(ours)
        for s in services:
            if s["ReferenceId"] in ours:
                keep.update(e["ReferenceId"] for e in s.get("Edges", []))
            elif any(e["ReferenceId"] in ours for e in s.get("Edges", [])) and s.get("Type") == "client":
                keep.add(s["ReferenceId"])
        for ref in keep:
            s = by_ref.get(ref)
            if not s:
                continue
            label, group = _label(s, roles)
            st = s.get("SummaryStatistics") or {}
            n = nodes.setdefault(label, {"id": label, "group": group, "requests": 0, "errors": 0, "faults": 0, "time": 0.0})
            n["requests"] += st.get("TotalCount", 0)
            n["errors"] += (st.get("ErrorStatistics") or {}).get("TotalCount", 0)
            n["faults"] += (st.get("FaultStatistics") or {}).get("TotalCount", 0)
            n["time"] += st.get("TotalResponseTime", 0.0)
            hosts.setdefault(label, set()).add(s.get("Name"))
            for e in s.get("Edges", []):
                t = by_ref.get(e["ReferenceId"])
                if e["ReferenceId"] not in keep or not t:
                    continue
                b = _label(t, roles)[0]
                if label == b:
                    continue  # the Lambda service and its function are one box here
                est = e.get("SummaryStatistics") or {}
                x = edges.setdefault((label, b), {"from": label, "to": b, "requests": 0, "time": 0.0, "faults": 0})
                x["requests"] += est.get("TotalCount", 0)
                x["time"] += est.get("TotalResponseTime", 0.0)
                x["faults"] += (est.get("FaultStatistics") or {}).get("TotalCount", 0)
    for label, n in nodes.items():
        n["hosts"] = len(hosts.get(label, ()))
    for x in list(nodes.values()) + list(edges.values()):
        spent = x.pop("time")
        x["avg_ms"] = round(spent / x["requests"] * 1000) if x["requests"] else None
    return {"since": (now - timedelta(hours=6 * MAP_CALLS)).isoformat(), "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "edges": sorted(edges.values(), key=lambda e: (e["from"], e["to"]))}


def snapshot(event, alarms=None):
    """The ops page's answer. Reuses a copy made in the last 5 minutes by any function instance."""
    saved = store.get("OPS", "SNAPSHOT")
    if saved and time.time() - saved.get("made_epoch", 0) < FRESH_S:
        return saved["data"]
    now = datetime.now(timezone.utc)
    api_id = ((event or {}).get("requestContext") or {}).get("apiId", "")
    t0 = time.time()
    data = {"as_of": now.isoformat(), **metrics(now, api_id), "graph": service_graph(now), "alarms": alarms or [],
            "functions": {k: v.split("-")[1] if "-" in v else v for k, v in functions().items()},
            "made_in_ms": None}
    data["made_in_ms"] = round((time.time() - t0) * 1000)
    try:
        store.put({"pk": "OPS", "sk": "SNAPSHOT", "made_epoch": int(time.time()), "data": data,
                   "ttl": int(time.time()) + 86400})
    except Exception:
        pass  # too large or throttled: the answer is still good, only not shared
    return data
