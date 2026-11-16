"""The clock planner, merging, trip matching and the stream matcher. No AWS calls: the table is faked."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import clock  # noqa: E402
import merge  # noqa: E402
import rules  # noqa: E402
import store  # noqa: E402
import trips  # noqa: E402
import watch  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 9, 13, 23, 25, tzinfo=UTC)
W = {"id": "w1", "place": "Philadelphia International Airport, Terminals B and F", "city": "Philadelphia", "state": "PA",
     "timezone": "America/New_York", "start_utc": "2026-09-13T23:25:00Z", "end_utc": "2026-09-14T04:15:00Z",
     "watch_until": None, "time_clear": True, "quote": "q", "url": "u", "issuer": "PDPH", "first_seen": "2026-09-19T15:00:00Z"}
STOP = {"place": "PHL airport, Terminal B", "city": "Philadelphia", "state": "PA", "date": "2026-09-13",
        "start_time": "20:00", "end_time": "23:30", "timezone": "America/New_York"}


def test_plan_has_one_item_per_deadline_and_milestone():
    d = trips.deadlines_json(rules.deadlines(T0, T0))
    p = clock.plan({"deadlines": d, "as_of": rules.iso(T0 + timedelta(hours=4)), "scale": 3600}, None)["items"]
    assert [i["kind"] for i in p] == [a["kind"] for a in rules.ACTIONS] + [m["kind"] for m in rules.MILESTONES]
    mmr = p[0]
    assert mmr["open"] is True and mmr["wait_s"] == 24   # 24 hours run as 24 seconds at demo speed
    late = clock.plan({"deadlines": d, "as_of": rules.iso(T0 + timedelta(days=4)), "scale": 1}, None)["items"]
    assert late[0]["open"] is False and late[1]["open"] is True


def test_the_token_timeout_is_measured_to_the_deadline_after_the_reminder():
    """However long the reminder took (retries included), the window closes at the planned deadline, never before."""
    d = trips.deadlines_json(rules.deadlines(T0, T0))
    item = clock.plan({"deadlines": d, "as_of": rules.iso(T0 + timedelta(hours=100)), "scale": 3600}, None)["items"][1]
    at = datetime.strptime(item["deadline_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    assert clock.seconds_left(item["deadline_at"], now=at - timedelta(seconds=30.2)) == 31   # rounded up
    assert clock.seconds_left(item["deadline_at"], now=at - timedelta(seconds=5)) == 5
    assert clock.seconds_left(item["deadline_at"], now=at + timedelta(seconds=3)) == 1         # late: close at once


def test_messages_cover_every_step_the_workflow_sends():
    kinds = [f"{a['kind']}_{k}" for a in rules.ACTIONS for k in ("reminder", "done", "closed")]
    kinds += [m["kind"] for m in rules.MILESTONES] + ["closed"]
    assert all(k in clock.MESSAGES for k in kinds)


def test_same_exposure_from_two_departments_merges():
    other = {**W, "id": "w2", "place": "Philadelphia International Airport Terminal B", "issuer": "PA DOH", "url": "u2"}
    rows = merge.merge([W, other])
    assert len(rows) == 1 and len(rows[0]["sources"]) == 2


def test_trip_matches_and_terminal_conflict():
    stop = trips.parse_stops([STOP])[0]
    kind, m = trips.compare(stop, W, T0 + timedelta(days=5, hours=16))
    assert kind == "match" and m["status"]["phase"] == "ig_open"
    other = trips.parse_stops([{**STOP, "place": "PHL airport, Terminal D"}])[0]
    assert trips.compare(other, W, T0)[0] != "match"
    assert trips.watch_expires([stop]) == rules.parse_iso(stop["end_utc"]) + timedelta(days=21)


def test_same_place_at_another_time_is_flagged_not_matched():
    """People misremember times: the same place a few hours off the published window is shown, never matched."""
    morning = trips.parse_stops([{**STOP, "start_time": "09:00", "end_time": "10:00"}])[0]   # the window is 7:25 to 11:15 pm
    kind, r = trips.compare(morning, W, T0)
    assert kind == "nearby" and r["same_place"] is True and r["hours_apart"] == 9.4
    next_week = trips.parse_stops([{**STOP, "date": "2026-09-20"}])[0]
    assert trips.compare(next_week, W, T0)[0] != "nearby" or not trips.compare(next_week, W, T0)[1].get("same_place")


from fake_ddb import FakeClient, FakeTable  # noqa: E402


def stream_event(w):
    from boto3.dynamodb.types import TypeSerializer
    img = TypeSerializer().serialize(store.to_ddb(w))["M"]
    return {"Records": [{"eventName": "INSERT", "dynamodb": {"Keys": {"pk": {"S": f"WINDOW#{w['id']}"}}, "NewImage": img}}]}


def test_a_window_looks_up_only_its_own_state_and_days():
    stop = trips.parse_stops([STOP])[0]
    keys = watch.stop_keys(stop)
    assert "TRIPSTOP#PA#2026-09-13" in keys and all(k.startswith("TRIPSTOP#PA#") for k in keys)
    assert set(watch.window_keys(W)) & set(keys)                       # the PHL window meets the PHL trip
    assert not set(watch.window_keys({**W, "state": "CA"})) & set(keys)  # a California window never reads it
    far = {**W, "start_utc": "2026-10-13T23:25:00Z", "end_utc": "2026-10-14T04:15:00Z"}
    assert not set(watch.window_keys(far)) & set(keys)                   # nor one a month later


def test_new_window_reaches_a_saved_trip_once(monkeypatch):
    client = FakeClient({"WATCH#t1": {"matches": {}}})
    monkeypatch.setattr(store, "table", FakeTable(client))
    trip = {"id": "t1", "stops": trips.parse_stops([STOP]), "matches": {}}
    monkeypatch.setattr(watch, "candidate_watches", lambda w, now, stats=None: [trip])
    assert watch.on_new_windows(stream_event(W), None)["matches"] == 1
    assert list(client.items["WATCH#t1"]["matches"]) == ["w1"]
    trip["matches"] = {}
    assert watch.on_new_windows(stream_event(W), None)["matches"] == 0   # a retried batch never records it twice


def test_two_departments_racing_on_the_same_exposure_alert_once(monkeypatch):
    """Two reports of one exposure, matched at the same moment in parallel batches: one alert, not two."""
    import threading
    gate, lock, reads = threading.Barrier(2), threading.Lock(), []

    def both_read_first():   # the first two reads meet here, so both see the trip before either writes
        with lock:
            reads.append(1)
            n = len(reads)
        if n <= 2:
            gate.wait(timeout=5)
    client = FakeClient({"WATCH#t1": {"matches": {}}}, between=both_read_first)
    monkeypatch.setattr(store, "table", FakeTable(client))
    sent = []
    monkeypatch.setattr(watch, "_push", lambda t, m: sent.append(m["window"]["id"]))
    other = {**W, "id": "w2", "place": "Philadelphia International Airport Terminal B", "issuer": "PA DOH", "url": "u2"}
    trip = lambda: {"id": "t1", "stops": trips.parse_stops([STOP]), "matches": {}}  # noqa: E731
    now = T0 + timedelta(days=5)
    out = []
    threads = [threading.Thread(target=lambda w=w: out.append(watch._match_one(w, [trip()], now))) for w in (W, other)]
    [x.start() for x in threads]
    [x.join() for x in threads]
    assert sorted(out) == [0, 1] and len(sent) == 1 and client.updates == 1


def test_drill_windows_only_meet_drill_trips(monkeypatch):
    monkeypatch.setattr(store, "table", FakeTable(FakeClient({"WATCH#t1": {"matches": {}}})))
    monkeypatch.setattr(watch, "candidate_watches", lambda w, now, stats=None: [{"id": "t1", "stops": trips.parse_stops([STOP]), "matches": {}}])
    assert watch.on_new_windows(stream_event({**W, "drill": True}), None)["matches"] == 0
