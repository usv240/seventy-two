"""Observability and scale, with AWS faked: the metric lines CloudWatch reads, the ops page's numbers, the
service map, the clock's deadline accuracy, and a matcher that reads only the stops that could match."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import clock  # noqa: E402
import obs  # noqa: E402
import ops  # noqa: E402
import store  # noqa: E402
import trips  # noqa: E402
import watch  # noqa: E402

UTC = timezone.utc
W = {"id": "w1", "place": "Philadelphia International Airport, Terminals B and F", "city": "Philadelphia", "state": "PA",
     "timezone": "America/New_York", "start_utc": "2026-09-13T23:25:00Z", "end_utc": "2026-09-14T04:15:00Z",
     "time_clear": True, "quote": "q", "url": "u", "issuer": "PDPH", "first_seen": "2026-09-19T15:00:00Z"}
STOP = {"place": "PHL airport, Terminal B", "city": "Philadelphia", "state": "PA", "date": "2026-09-13",
        "start_time": "20:00", "end_time": "23:30", "timezone": "America/New_York"}


def test_metric_lines_are_embedded_metric_format_with_units():
    rec = obs.emf({"MatchWindowMs": 12.5, "ModelCostUSD": 0.006, "TripMatches": 1}, timestamp_ms=1)
    d = rec["_aws"]["CloudWatchMetrics"][0]
    assert d["Namespace"] == "SeventyTwo" and d["Dimensions"] == [[]]
    assert {m["Name"]: m["Unit"] for m in d["Metrics"]} == {"MatchWindowMs": "Milliseconds", "ModelCostUSD": "None", "TripMatches": "Count"}
    assert rec["MatchWindowMs"] == 12.5


def test_spans_and_metrics_are_free_outside_lambda(capsys):
    with obs.span("anything", source="x"):
        obs.metric(TripMatches=1)
    assert capsys.readouterr().out == ""


def test_deadline_slip_is_measured_on_the_clocks_own_timeline():
    c = {"deadlines": {"ig_deadline": "2026-01-07T00:00:00Z"}, "scale": 1, "created": "2026-01-01T00:00:00Z", "as_of": "2026-01-01T00:00:00Z"}
    assert clock.deadline_slip_ms(c, "ig", now=datetime(2026, 1, 7, 0, 0, 1, tzinfo=UTC)) == 1000
    fast = {**c, "scale": 3600}   # 6 days at one hour per second is 144 seconds
    assert clock.deadline_slip_ms(fast, "ig", now=datetime(2026, 1, 1, 0, 2, 24, 500000, tzinfo=UTC)) == 500
    assert clock.deadline_slip_ms({}, "ig") is None


class IndexTable:
    """Answers the index query with saved stops: two in Philadelphia on the right day, one elsewhere that day."""

    def __init__(self, items):
        self.items, self.queries = items, []

    def query(self, **kw):
        key = kw["KeyConditionExpression"].get_expression()["values"][0].get_expression()["values"][1]
        self.queries.append(key)
        return {"Items": [store.to_ddb(i) for i in self.items if i["gsi1pk"] == key]}


def test_the_matcher_reads_only_trips_whose_saved_stop_matches(monkeypatch):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    exp = now + timedelta(days=20)
    hit = trips.parse_stops([STOP])
    miss = trips.parse_stops([{**STOP, "place": "Reading Terminal Market", "start_time": "09:00", "end_time": "10:00"}])
    items = watch.index_items("hit", hit, exp, 0) + watch.index_items("miss", miss, exp, 0) + watch.index_items("drill", hit, exp, 0, drill=True)
    monkeypatch.setattr(store, "table", IndexTable(items))
    read = []
    monkeypatch.setattr(store, "get_many", lambda pks, sk="META": read.extend(pks) or [{"id": p.split("#")[1], "stops": hit} for p in pks])
    stats = {}
    found = watch.candidate_watches(W, now, stats)
    assert [t["id"] for t in found] == ["hit"] and read == ["WATCH#hit"]   # the other two trips are never read
    assert stats["index_items"] >= 3 and stats["trips_read"] == 1
    assert all(q.startswith("TRIPSTOP#PA#") or q.startswith("TRIPSTOP#XX#") for q in store.table.queries)


class FakeCW:
    def get_metric_data(self, MetricDataQueries, StartTime, EndTime, **kw):
        out = []
        for q in MetricDataQueries:
            if q["MetricStat"]["Period"] == 3600:
                ts = [StartTime + timedelta(hours=h) for h in (0, 5)]
                out.append({"Id": q["Id"], "Timestamps": ts, "Values": [2.0, 3.0]})
            else:
                out.append({"Id": q["Id"], "Timestamps": [StartTime], "Values": [42.0]})
        return {"MetricDataResults": out}


def test_ops_totals_add_sums_and_take_percentiles_over_the_whole_day(monkeypatch):
    monkeypatch.setattr(ops, "cw", FakeCW())
    monkeypatch.setattr(ops, "FUNCTIONS", {"matcher": "stack-MatcherFunction-x"})
    monkeypatch.setattr(ops, "distribution_id", lambda: "")
    m = ops.metrics(datetime(2026, 11, 15, 3, 30, tzinfo=UTC), "api1")
    s = m["series"]
    assert len(s["api_requests"]["values"]) == 24 and s["api_requests"]["values"][0] == 2.0 and s["api_requests"]["values"][1] is None
    assert s["api_requests"]["total"] == 5.0            # a sum adds the hours
    assert s["api_p99"]["total"] == 42.0                # a percentile is asked for the whole day, not averaged
    assert s["stream_age"]["total"] == 3.0              # a maximum is the largest hour


def test_service_map_keeps_this_stack_and_groups_remote_hosts(monkeypatch):
    calls = []

    class FakeXRay:
        def get_service_graph(self, **kw):
            calls.append((kw["StartTime"], kw["EndTime"]))
            if kw["EndTime"] != datetime(2026, 11, 15, tzinfo=UTC):
                return {"Services": []}   # only the latest 6 hours had traces
            st = lambda n, t: {"TotalCount": n, "TotalResponseTime": t, "ErrorStatistics": {"TotalCount": 0}, "FaultStatistics": {"TotalCount": 0}}  # noqa: E731
            return {"Services": [
                {"ReferenceId": 1, "Name": "stack-ingest", "Type": "AWS::StepFunctions::StateMachine", "SummaryStatistics": st(2, 20), "Edges": [{"ReferenceId": 2, "SummaryStatistics": st(54, 54)}]},
                {"ReferenceId": 2, "Name": "stack-IngestFunction-x", "Type": "AWS::Lambda::Function", "SummaryStatistics": st(54, 54),
                 "Edges": [{"ReferenceId": 3, "SummaryStatistics": st(10, 3)}, {"ReferenceId": 4, "SummaryStatistics": st(20, 4)}, {"ReferenceId": 5, "SummaryStatistics": st(7, 1)}]},
                {"ReferenceId": 3, "Name": "www.health.state.mn.us", "Type": "remote", "SummaryStatistics": st(10, 3), "Edges": []},
                {"ReferenceId": 4, "Name": "www.phila.gov", "Type": "remote", "SummaryStatistics": st(20, 4), "Edges": []},
                {"ReferenceId": 5, "Name": "bedrock-runtime.us-east-1.amazonaws.com", "Type": "remote", "SummaryStatistics": st(7, 21), "Edges": []},
                {"ReferenceId": 9, "Name": "someone-elses-function", "Type": "AWS::Lambda::Function", "SummaryStatistics": st(5, 1), "Edges": []}]}
    monkeypatch.setattr(ops, "xray", FakeXRay())
    monkeypatch.setattr(ops, "FUNCTIONS", {"ingest": "stack-IngestFunction-x"})
    monkeypatch.setattr(ops, "INGEST_SM", "arn:aws:states:us-east-1:1:stateMachine:stack-ingest")
    g = ops.service_graph(datetime(2026, 11, 15, tzinfo=UTC))
    assert len(calls) == 4 and min(c[0] for c in calls) == datetime(2026, 11, 14, tzinfo=UTC)   # four 6-hour calls cover the day
    names = {n["id"]: n for n in g["nodes"]}
    assert set(names) == {"Reading workflow", "ingest", "Health department sites", "Bedrock (Claude)"}
    assert names["Health department sites"]["requests"] == 30 and names["Health department sites"]["hosts"] == 2
    assert names["Bedrock (Claude)"]["avg_ms"] == 3000
    assert {(e["from"], e["to"]) for e in g["edges"]} == {("Reading workflow", "ingest"), ("ingest", "Health department sites"), ("ingest", "Bedrock (Claude)")}


def test_an_airport_exposure_reaches_every_matching_trip_once(monkeypatch):
    """Hundreds of trips can match one window; they are written in parallel, and a retried batch adds none."""
    from fake_ddb import FakeClient, FakeTable
    client = FakeClient({f"WATCH#t{i}": {"matches": {}} for i in range(120)})
    monkeypatch.setattr(store, "table", FakeTable(client))
    monkeypatch.setattr(watch, "_push", lambda t, m: None)
    now = datetime(2026, 9, 15, tzinfo=UTC)
    many = [{"id": f"t{i}", "stops": trips.parse_stops([STOP]), "matches": {}} for i in range(120)]
    assert watch._match_one(W, many, now) == 120 and client.updates == 120
    fresh = [{**t, "matches": {}} for t in many]
    assert watch._match_one(W, fresh, now) == 0 and client.updates == 120


def test_a_presigned_bucket_link_is_shown_as_s3():
    assert ops._label({"Name": "seventytwo-archivebucket-abc.s3.amazonaws.com", "Type": "remote"}, {}) == ("S3", "aws")
    assert ops._label({"Name": "a link pasted in the Lab", "Type": "remote"}, {}) == ("Links pasted in the Lab", "outside")
    assert ops._label({"Name": "www.phila.gov", "Type": "remote"}, {}) == ("Health department sites", "outside")
