import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import match  # noqa: E402
import rules  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 9, 13, 23, 25, tzinfo=UTC)  # PHL, Sep 13 7:25 PM EDT


def test_deadlines_follow_cdc():
    d = rules.deadlines(T0, T0 + timedelta(hours=4, minutes=40))
    assert d["mmr_deadline"] == T0 + timedelta(hours=72)
    assert d["ig_deadline"] == T0 + timedelta(days=6)
    assert d["watch_start"] == T0 + timedelta(days=5)
    assert d["watch_end"] == T0 + timedelta(hours=4, minutes=40) + timedelta(days=21)


def test_notice_watch_date_wins():
    printed = datetime(2026, 10, 5, 23, 59, tzinfo=UTC)
    assert rules.deadlines(T0, T0, notice_watch_until=printed)["watch_end"] == printed


def test_status_phases():
    d = rules.deadlines(T0, T0)
    assert rules.status(d, T0 + timedelta(hours=10))["phase"] == "mmr_open"
    assert rules.status(d, T0 + timedelta(hours=10))["mmr_hours_left"] == 62.0
    assert rules.status(d, T0 + timedelta(days=4))["phase"] == "ig_open"
    assert rules.status(d, T0 + timedelta(days=10))["phase"] == "watch"
    assert rules.status(d, T0 + timedelta(days=30))["phase"] == "clear"


def test_lag_and_window_left():
    published = datetime(2026, 9, 20, tzinfo=UTC)
    assert rules.lag_days(T0, published) == 6.0
    left = rules.window_left_at_publication(T0, published)
    assert left == {"mmr_hours_left": 0.0, "ig_hours_left": 0.0}


def test_overlap_edges():
    a, b = T0, T0 + timedelta(hours=1)
    assert rules.overlaps(a, b, b, b + timedelta(hours=1))
    assert not rules.overlaps(a, b, b + timedelta(seconds=1), b + timedelta(hours=1))


def test_place_match_airport_code():
    w = {"place": "Philadelphia International Airport, Terminals B and F", "city": "Philadelphia"}
    assert match.score("PHL", w) >= 0.5
    assert match.score("Philadelphia airport terminal B", w) >= 0.5
    assert not match.terminal_conflict("PHL terminal B", w)
    assert match.terminal_conflict("PHL terminal A", w)


def test_place_no_match_on_generic_words():
    w = {"place": "Sharp Memorial Hospital Emergency Department", "city": "San Diego"}
    assert match.score("Children's Hospital emergency room", w) == 0.0
