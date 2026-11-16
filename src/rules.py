"""Post-exposure timing rules, read from the domain (domains/measles.py by default). Pure functions, no AWS, no AI.

Every deadline Seventy-Two shows comes from here, and every result carries the rule's source so the page can
show where it came from. Nothing in this file names a disease: the actions, their windows, the watch period
and the words come from the domain.
"""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone

import domains

D = domains.load(globals().get("_DOMAIN_OVERRIDE"))   # set only on a copy made by for_domain()
NAME = D.__name__.rsplit(".", 1)[-1]
DISEASE = D.DISEASE
_copies = {}


def for_domain(name):
    """These same rules for another disease's domain file, so one stack can run a second disease next to the
    first: the Lab reads its notices and the same clock workflow runs its deadlines. None means this one."""
    if not name or name == NAME:
        return sys.modules[__name__]
    if name not in domains.AVAILABLE:
        raise ValueError(f"unknown domain: {name}")
    if name not in _copies:
        spec = importlib.util.spec_from_file_location(f"rules_{name}", __file__)
        m = importlib.util.module_from_spec(spec)
        m._DOMAIN_OVERRIDE = name
        spec.loader.exec_module(m)
        _copies[name] = m
    return _copies[name]

# The clock, as data. Each action is one iteration of the clock workflow's Map state: wait until a day
# before its deadline, remind, then wait for "I got it" (a task token) or for the deadline (the token's
# timeout). Each milestone is a Wait state and a message.
ACTIONS = [{**a, "deadline": f"{a['kind']}_deadline"} for a in D.ACTIONS]
MILESTONES = D.MILESTONES
SOURCES = D.SOURCES
WATCH_START_DAY = D.WATCH["start_days_after_first"]
WATCH_END_DAY = D.WATCH["end_days_after_last"]
LONGER_WATCH = D.WATCH.get("end_days_after_last_if", {})   # an action that makes symptoms start later


def public_actions():
    """What a page needs to show each action: its kind, names, and whether it makes the watch run longer."""
    return [{"kind": a["kind"], "label": a["label"], "box": a.get("box", a["label"]), "info": a.get("info", f"{a['kind']}-window"),
             "within_hours": a["within_hours"], "phase_word": a.get("phase_word", f"{a['label']} open").upper(),
             "window_title": a.get("window_title", f"{a['label']} window closes"), "calendar_note": a.get("calendar_note", a["step"]),
             "longer_watch_days": max(0, LONGER_WATCH.get(a["kind"], 0) - WATCH_END_DAY)} for a in D.ACTIONS]


def parse_iso(value):
    """ISO 8601 string to an aware UTC datetime."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value}")
    return dt.astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def overlaps(a_start, a_end, b_start, b_end):
    """True when two closed intervals share at least one instant."""
    return a_start <= b_end and a_end >= b_start


def watch_days(given=()):
    """Days after the last exposure that the watch runs, given the actions a person took."""
    return max([WATCH_END_DAY] + [d for k, d in LONGER_WATCH.items() if k in set(given)])


def deadlines(first_exposure, last_exposure, ig_given=False, notice_watch_until=None, given=()):
    """Each action's deadline, and the watch period, as UTC datetimes.

    notice_watch_until: the date a health department printed ("watch for symptoms through Oct 5"), as a
    datetime at the end of that local day. When given, it replaces our computed watch end, because the
    notice is the authority for its own exposure.
    """
    given = set(given) | ({"ig"} if ig_given else set())
    out = {f"{a['kind']}_deadline": first_exposure + timedelta(hours=a["within_hours"]) for a in D.ACTIONS}
    out["watch_start"] = first_exposure + timedelta(days=WATCH_START_DAY)
    out["watch_end"] = notice_watch_until if notice_watch_until is not None else last_exposure + timedelta(days=watch_days(given))
    return out


def status(d, now):
    """Where a person stands at `now`, in words a person can act on: hours left for each action (0 when
    closed) and the one step that matters most right now."""
    def hours_left(t):
        return max(0.0, round((t - now).total_seconds() / 3600, 1))

    left = {a["kind"]: hours_left(d[f"{a['kind']}_deadline"]) for a in D.ACTIONS}
    first_open = next((a for a in D.ACTIONS if left[a["kind"]] > 0), None)
    if first_open:
        phase, step = f"{first_open['kind']}_open", first_open["step"]
    elif now < d["watch_end"]:
        phase, step = "watch", D.WATCH["step"]
    else:
        phase, step = "clear", D.WATCH["clear"]
    return {
        "phase": phase,
        "next_step": step,
        **{f"{k}_hours_left": v for k, v in left.items()},
        "watch_days_left": max(0, (d["watch_end"] - now).days),
        "in_watch": d["watch_start"] <= now < d["watch_end"],
    }


def lag_days(exposure_start, published_at):
    """Days between the exposure and the notice being published."""
    return round((published_at - exposure_start).total_seconds() / 86400, 1)


def window_left_at_publication(exposure_start, published_at):
    """How much of each action's window was left when the public could first read the notice."""
    d = deadlines(exposure_start, exposure_start)
    return {f"{a['kind']}_hours_left": max(0.0, round((d[f"{a['kind']}_deadline"] - published_at).total_seconds() / 3600, 1))
            for a in D.ACTIONS}
