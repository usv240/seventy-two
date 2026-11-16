"""The code that checks every window Claude proposes. No model calls: these are the checks themselves."""
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import evidence  # noqa: E402
import reader  # noqa: E402
from textutil import normalize  # noqa: E402

NOTICE = """Health Department Warns of Possible Measles Exposure at Philadelphia International Airport
Published September 19, 2026
A traveler with measles was at the following locations:
Sunday, Sept. 13: Terminal B and Terminal F, from 7:25 p.m. to 12:15 a.m.
Monday, Sept. 14: Gate C22, 6 to 8 am
Anyone exposed should watch for symptoms through October 5, 2026."""


def win(**kw):
    w = {"place": "Philadelphia International Airport, Terminals B and F", "date": "2026-09-13", "start_time": "19:25",
         "end_time": "00:15", "timezone": "America/New_York", "time_clear": True,
         "quote": "Sunday, Sept. 13: Terminal B and Terminal F, from 7:25 p.m. to 12:15 a.m."}
    w.update(kw)
    return w


def run(*windows, **page):
    return reader.validate({"is_exposure_notice": True, "windows": list(windows), **page}, NOTICE, "test")


def test_date_and_time_forms():
    t = normalize(NOTICE)
    assert evidence.date_spans(t, date(2026, 9, 13))
    assert evidence.date_spans(t, date(2026, 10, 5))
    assert not evidence.date_spans(t, date(2026, 9, 15))
    assert evidence.time_spans(t, 19, 25) and evidence.time_spans(t, 0, 15)
    assert evidence.time_spans(t, 6, 0)   # "6 to 8 am" borrows the am
    assert not evidence.time_spans(t, 21, 0)
    assert evidence.date_spans("nov. 3-5", date(2026, 11, 4))
    assert evidence.date_spans("on 9/13/26 at", date(2026, 9, 13))


def test_good_window_passes_and_crosses_midnight():
    wins, rejected = run(win())
    assert not rejected and len(wins) == 1
    assert wins[0]["start_utc"] == "2026-09-13T23:25:00Z" and wins[0]["end_utc"] == "2026-09-14T04:15:00Z"
    assert "crosses_midnight" in wins[0]["flags"]


def test_quote_must_be_in_the_notice():
    _, rejected = run(win(quote="Terminal D, all day Sunday"))
    assert rejected[0]["reason"] == "quote not found in notice"


LIST = """Exposure locations
Thursday, November 5, 2026
Walmart, Ankeny | 5-8 p.m.
Friday, November 6, 2026
Hy-Vee, North Ankeny Blvd | 12-3 p.m."""


def test_date_must_be_the_heading_just_above():
    hy = {"place": "Hy-Vee", "state": "IA", "date": "2026-11-06", "start_time": "12:00", "end_time": "15:00",
          "timezone": "America/Chicago", "time_clear": True, "quote": "Hy-Vee, North Ankeny Blvd | 12-3 p.m."}
    wins, rejected = reader.validate({"windows": [hy]}, LIST, "t")
    assert wins and not rejected
    _, rejected = reader.validate({"windows": [{**hy, "date": "2026-11-05"}]}, LIST, "t")   # the heading above Walmart
    assert rejected[0]["reason"] == "date not written in or near the quote"


def test_time_zone_must_fit_the_state():
    _, rejected = run(win(state="PA", timezone="America/Los_Angeles"))
    assert rejected[0]["reason"] == "time zone does not match the state"


def test_end_date_must_be_written():
    _, rejected = run(win(end_date="2026-09-20"))
    assert rejected[0]["reason"] == "end date not written in or near the quote"
    wins, rejected = run(win(end_date="2026-09-14"))   # 7:25 p.m. to 12:15 a.m.: the next day is implied by the times
    assert wins and not rejected and wins[0]["end_utc"] == "2026-09-14T04:15:00Z"


def test_place_must_be_written_near_the_quote():
    _, rejected = run(win(place="Newark Liberty International Airport, Terminal C"))
    assert rejected[0]["reason"] == "place not written in or near the quote"
    wins, rejected = run(win(place="Philadelphia International Airport"))   # named in the heading above
    assert wins and not rejected


def test_date_must_be_written_near_the_quote():
    _, rejected = run(win(date="2026-09-15"))
    assert rejected[0]["reason"] == "date not written in or near the quote"


def test_weekday_in_quote_counts_when_the_page_states_the_date():
    page = "Anyone in the emergency room between 1:30 and 3:30 p.m. Thursday may have been exposed.\n" + "x " * 600 + "\nThe visit was on Nov. 5, 2026."
    w = {"place": "Emergency room", "date": "2026-11-05", "start_time": "13:30", "end_time": "15:30", "timezone": "America/Chicago",
         "time_clear": True, "quote": "between 1:30 and 3:30 p.m. Thursday"}
    wins, rejected = reader.validate({"windows": [w]}, page, "t")
    assert len(wins) == 1 and not rejected
    wins, rejected = reader.validate({"windows": [{**w, "date": "2026-11-12"}]}, page, "t")   # also a Thursday, not on the page
    assert rejected and rejected[0]["reason"] == "date not written in or near the quote"


def test_time_not_in_the_notice_widens_to_the_whole_day():
    wins, rejected = run(win(start_time="21:00", end_time="23:00"))
    assert not rejected
    assert wins[0]["time_clear"] is False and "time_not_found" in wins[0]["flags"]
    assert wins[0]["start_utc"] == "2026-09-13T04:00:00Z"   # midnight Eastern: wider, never narrower


def test_page_dates_are_kept_only_if_printed():
    wins, _ = run(win(), watch_until="2026-10-05")
    assert wins[0]["watch_until"] == "2026-10-05"
    wins, _ = run(win(), watch_until="2026-10-04")   # computed by the model, not printed: dropped
    assert wins[0]["watch_until"] is None
    assert reader.published({"published_date": "2026-09-19"}, NOTICE) == "2026-09-19"
    assert reader.published({"published_date": "2026-09-18"}, NOTICE) is None
    assert reader.published({"published_date": None}, "", "https://www.phila.gov/2026-09-19-health-department") == "2026-09-19"


def test_a_several_day_window_is_labelled_with_both_days():
    page = "The aquarium on Tuesday, March 10, and Wednesday, March 11, 2026, may have been an exposure site."
    w = {"place": "Aquarium", "state": "UT", "date": "2026-03-10", "end_date": "2026-03-11", "timezone": "America/Denver",
         "time_clear": False, "quote": "The aquarium on Tuesday, March 10, and Wednesday, March 11"}
    wins, rejected = reader.validate({"windows": [w]}, page, "t")
    assert not rejected and wins[0]["local_label"] == "Tue Mar 10 to Wed Mar 11, 2026, all day"
    assert wins[0]["end_utc"] == "2026-03-12T05:59:00Z"   # the whole of the second day counts


def test_windows_sent_as_a_json_string_still_check():
    import json
    wins, rejected = reader.validate({"is_exposure_notice": True, "windows": json.dumps([win(), "junk"])}, NOTICE, "test")
    assert len(wins) == 1 and rejected == [{"place": None, "reason": "not a window"}]


def test_window_ids_are_deterministic():
    a, _ = run(win())
    b, _ = run(win())
    assert a[0]["id"] == b[0]["id"]
