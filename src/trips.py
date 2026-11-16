"""A person's trip against exposure windows. Deterministic: no AI, no AWS.

Used by the check (a trip against every window on the board) and by the matcher
(every saved trip against one new window, the moment it is stored)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import board
import match
import reader
import rules

MAX_STOPS = 20
STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
          "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
          "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
          "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
          "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
          "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
          "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
          "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
          "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR"}


def state_code(s):
    s = (s or "").strip()
    return STATES.get(s.lower(), s.upper()[:2] if len(s) == 2 else None) or None


def stop_interval(s):
    """A stop as a UTC interval. No time given means the whole day; one time means a short visit."""
    tz = ZoneInfo(s.get("timezone") or "America/New_York")
    d = datetime.strptime(s["date"], "%Y-%m-%d")
    hm = reader._hhmm(s.get("start_time"))
    em = reader._hhmm(s.get("end_time"))
    end_d = datetime.strptime(s.get("end_date") or s["date"], "%Y-%m-%d")
    time_given = hm is not None
    start = d.replace(hour=hm[0], minute=hm[1], tzinfo=tz) if hm else d.replace(tzinfo=tz)
    if em:
        end = end_d.replace(hour=em[0], minute=em[1], tzinfo=tz)
    elif hm:
        end = start + timedelta(hours=1)
    else:
        end = end_d.replace(hour=23, minute=59, tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), time_given


def parse_stops(stops):
    """Checked stops with their UTC intervals. A stop whose date or timezone does not parse is dropped."""
    out = []
    for s in (stops or [])[:MAX_STOPS]:
        try:
            a, b, time_given = stop_interval(s)
        except Exception:
            continue
        keep = {k: s.get(k) for k in ("place", "city", "state", "date", "start_time", "end_date", "end_time", "timezone")}
        keep["state"] = state_code(keep["state"])
        out.append({**keep, "start_utc": rules.iso(a), "end_utc": rules.iso(b), "time_given": time_given})
    return out


def window_deadlines(w, first=None, last=None, r=None):
    first = first or rules.parse_iso(w["start_utc"])
    last = last or rules.parse_iso(w["end_utc"])
    notice_watch = None
    if w.get("watch_until"):
        try:
            notice_watch = datetime.strptime(w["watch_until"], "%Y-%m-%d").replace(
                hour=23, minute=59, tzinfo=ZoneInfo(w.get("timezone") or "UTC")).astimezone(timezone.utc)
        except ValueError:
            pass
    return (r or rules).deadlines(first, last, notice_watch_until=notice_watch)


def deadlines_json(d):
    return {k: rules.iso(v) for k, v in d.items()}


NEAR_IN_TIME = timedelta(hours=12)   # same place, outside the published hours by up to this much: worth a second look


def compare(stop, w, as_of):
    """('match', result) | ('nearby', result) | (None, None) for one parsed stop and one window."""
    s_start, s_end = rules.parse_iso(stop["start_utc"]), rules.parse_iso(stop["end_utc"])
    w_start, w_end = rules.parse_iso(w["start_utc"]), rules.parse_iso(w["end_utc"])
    visited = " ".join(filter(None, [stop.get("place"), stop.get("city")]))
    if not rules.overlaps(s_start, s_end, w_start, w_end):
        gap = (w_start - s_end) if s_end < w_start else (s_start - w_end)
        if gap <= NEAR_IN_TIME and match.score(visited, w) >= 0.5 and not match.terminal_conflict(visited, w):
            return "nearby", {"window": board.public_window(w), "stop": stop.get("place"), "score": match.score(visited, w),
                              "same_place": True, "hours_apart": round(gap.total_seconds() / 3600, 1)}
        return None, None
    sc = match.score(visited, w)
    if sc >= 0.5 and not match.terminal_conflict(visited, w):
        first, last = max(s_start, w_start), min(s_end, w_end)
        d = window_deadlines(w, first, last)
        return "match", {"window": board.public_window(w), "stop": stop.get("place"), "score": sc,
                         "first_exposure": rules.iso(first), "last_exposure": rules.iso(last),
                         "time_given": stop.get("time_given", True), "deadlines": deadlines_json(d),
                         "status": rules.status(d, as_of), "rules": rules.SOURCES}
    if sc > 0 or (stop.get("state") and stop.get("state") == w.get("state")):
        return "nearby", {"window": board.public_window(w), "stop": stop.get("place"), "score": sc}
    return None, None


def check(stops, windows, as_of):
    """Every parsed stop against every window. Returns (matches, nearby)."""
    matches, nearby = [], []
    for s in stops:
        for w in windows:
            kind, r = compare(s, w, as_of)
            if kind == "match":
                matches.append(r)
            elif kind == "nearby":
                nearby.append(r)
    return matches, nearby


def watch_expires(stops):
    """A saved trip is watched until 21 days after its last stop: after that, no notice could still
    leave a person anything to do or watch for."""
    last = max(rules.parse_iso(s["end_utc"]) for s in stops)
    return last + timedelta(days=rules.WATCH_END_DAY)
