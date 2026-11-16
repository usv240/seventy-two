"""Steps of the per-person clock (one Step Functions Standard execution per clock).

plan:   turns the CDC deadlines into one Map item per action and milestone (rules.ACTIONS, rules.MILESTONES),
        with wall-clock timestamps for the Wait states (scaled in demo mode)
wait:   stores the task token so the "I got it" button can resume the workflow
notify: records what happened and, when an address is verified, sends an email with a calendar file
"""
import json
import math
import os
from datetime import datetime, timedelta, timezone

import boto3

import obs
import rules
import store

ses = boto3.client("sesv2")
sfn = boto3.client("stepfunctions")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "")
SITE_URL = os.environ.get("SITE_URL", "")

def messages(r):
    return {
        **{f"{a['kind']}_{k}": a[k] for a in r.ACTIONS for k in ("reminder", "done", "closed")},
        **{m["kind"]: m["text"] for m in r.MILESTONES},
        "closed": "This clock has finished. Its record will delete itself within a day.",
    }


def push_kinds(r):
    return {f"{a['kind']}_{k}" for a in r.ACTIONS for k in ("reminder", "closed")} | {m["kind"] for m in r.MILESTONES}


MESSAGES, PUSH_KINDS = messages(rules), push_kinds(rules)


def rules_of(clock_id=None, c=None):
    """The rules for this clock's disease: the same workflow runs every domain."""
    c = c if c is not None else (store.get(f"CLOCK#{clock_id}") or {})
    return rules.for_domain(c.get("domain"))


def plan(event, _ctx):
    """One item per deadline (rules.ACTIONS) and per milestone (rules.MILESTONES), for the Map state."""
    if event.get("domain"):
        r = rules.for_domain(event["domain"])
    else:
        r = rules_of(event["clock_id"]) if event.get("clock_id") else rules
    d = {k: rules.parse_iso(v) for k, v in event["deadlines"].items()}
    as_of = rules.parse_iso(event["as_of"])
    scale = int(event.get("scale", 1))
    real_now = datetime.now(timezone.utc)

    def at(t):  # map a moment in the person's timeline onto wall-clock time for the Wait state
        if scale == 1:
            return max(t, real_now)
        return real_now + timedelta(seconds=max(0.0, (t - as_of).total_seconds() / scale))

    def secs(a, b):  # rounded up, so a window never closes before its deadline
        return max(1, math.ceil((at(b) - at(a)).total_seconds()))

    items = []
    for a in r.ACTIONS:
        deadline = d[a["deadline"]]
        remind = max(as_of, deadline - timedelta(hours=a["remind_hours_before"]))
        items.append({"type": "action", "kind": a["kind"], "open": deadline > as_of,
                      "remind_at": rules.iso(at(remind)), "wait_s": secs(remind, deadline),
                      "deadline_at": at(deadline).strftime("%Y-%m-%dT%H:%M:%S.%fZ")})
    for m in r.MILESTONES:
        items.append({"type": "milestone", "kind": m["kind"], "at": rules.iso(at(max(as_of, d[m["at"]])))})
    return {"items": items}


def _record(clock_id, kind, extra=None, r=None):
    text = messages(r).get(kind, kind) if r is not None else MESSAGES.get(kind, kind)
    event = {"kind": kind, "at": rules.iso(datetime.now(timezone.utc)), "text": text, **(extra or {})}
    store.table.update_item(
        Key={"pk": f"CLOCK#{clock_id}", "sk": "META"},
        UpdateExpression="SET events = list_append(if_not_exists(events, :e), :new)",
        ExpressionAttributeValues={":e": [], ":new": [event]})
    return event


def wait(event, _ctx):
    """Called with .waitForTaskToken. Saves the token; the workflow pauses until the person answers or time runs out."""
    store.table.update_item(
        Key={"pk": f"CLOCK#{event['clock_id']}", "sk": "META"},
        UpdateExpression="SET tokens.#k = :t",
        ExpressionAttributeNames={"#k": event["kind"]},
        ExpressionAttributeValues={":t": event["token"]})
    c = store.get(f"CLOCK#{event['clock_id']}") or {}
    if any(e.get("kind") == f"{event['kind']}_done" for e in c.get("events") or []):
        # "I got it" arrived after the reminder but before this token was saved: resume the workflow at once
        sfn.send_task_success(taskToken=event["token"], output=json.dumps({"confirmed": event["kind"], "early": True}))
        return {"ok": True, "resumed": True}
    _record(event["clock_id"], f"{event['kind']}_waiting", {"text": "Waiting for you to tell us you got it."})
    return {"ok": True}


def covered(clock_id, kind):
    """True when no reminder is needed: the person already said they got it (before the reminder was due),
    or an earlier action already protects them (immune globulin is not needed after MMR)."""
    base = kind[: -len("_reminder")] if kind.endswith("_reminder") else kind
    c = store.get(f"CLOCK#{clock_id}") or {}
    action = next((a for a in rules_of(c=c).ACTIONS if a["kind"] == base), None)
    if not action:
        return False
    ev = c.get("events", [])
    return any(e.get("kind") == f"{k}_done" for k in [base] + list(action["covered_by"]) for e in ev)


def seconds_left(deadline_at, now=None):
    """Whole seconds from now until the planned wall-clock deadline, rounded up so a window never closes early.
    The reminder step returns this, and the task token's timeout is set from it: a retried or slow reminder
    cannot move the moment the window closes."""
    now = now or datetime.now(timezone.utc)
    deadline = datetime.strptime(deadline_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return max(1, math.ceil((deadline - now).total_seconds()))


def notify(event, _ctx):
    clock_id, kind = event["clock_id"], event["kind"]
    r = rules_of(clock_id)
    left = {"wait_s": seconds_left(event["deadline_at"])} if event.get("deadline_at") else {}
    if kind.endswith("_reminder") and covered(clock_id, kind):
        return {"skip": True, **left}
    if kind.endswith("_done"):  # already recorded when the person pressed it
        c0 = store.get(f"CLOCK#{clock_id}") or {}
        if any(e.get("kind") == kind for e in c0.get("events") or []):
            store.table.update_item(Key={"pk": f"CLOCK#{clock_id}", "sk": "META"}, UpdateExpression="REMOVE tokens.#k",
                                    ExpressionAttributeNames={"#k": kind[:-5]})
            return {"skip": False, "kind": kind, "already": True}
    extra = None
    if kind == "all_clear":  # an action that makes symptoms start later (immune globulin, for measles) extends the watch
        c0 = store.get(f"CLOCK#{clock_id}") or {}
        given = {e["kind"][:-5] for e in c0.get("events", []) if e.get("kind", "").endswith("_done")}
        extra_days = r.watch_days(given) - r.WATCH_END_DAY
        if extra_days > 0:
            watch_end = rules.parse_iso(c0["deadlines"]["watch_end"]) + timedelta(days=extra_days)
            label = next(a["label"] for a in r.ACTIONS if a["kind"] in given and a["kind"] in r.LONGER_WATCH)
            extra = {"text": f"Day {r.WATCH_END_DAY} has passed. Because you received {label}, keep watching for symptoms "
                             f"until {watch_end.strftime('%B %d')}. If symptoms start, call before you go in."}
    ev = _record(clock_id, kind, extra, r)
    if kind.endswith("_closed"):
        if event.get("deadline_at"):   # the wall-clock deadline the workflow planned (exact for demo-speed clocks too)
            planned = datetime.strptime(event["deadline_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            slip = round((datetime.now(timezone.utc) - planned).total_seconds() * 1000)
        else:
            slip = deadline_slip_ms(store.get(f"CLOCK#{clock_id}") or {}, kind[:-len("_closed")])
        obs.metric(DeadlineSlipMs=slip)
        obs.log(clock_closed=clock_id, kind=kind, deadline_slip_ms=slip)
    if kind.endswith("_done") or kind.endswith("_closed"):  # the token is spent
        store.table.update_item(Key={"pk": f"CLOCK#{clock_id}", "sk": "META"},
                                UpdateExpression="REMOVE tokens.#k",
                                ExpressionAttributeNames={"#k": kind.rsplit("_", 1)[0]})
    c = store.get(f"CLOCK#{clock_id}") or {}
    if c.get("push") and kind in push_kinds(r):  # a notification on the person's own devices
        import push
        url = f"{c.get('site_url') or SITE_URL}/clock/?id={clock_id}"
        alive = push.send_all(c["push"], f"Seventy-Two: {c.get('place', 'your exposure')[:60]}", ev["text"], url)
        if len(alive) != len(c["push"]):
            store.table.update_item(Key={"pk": f"CLOCK#{clock_id}", "sk": "META"}, UpdateExpression="SET push = :p",
                                    ExpressionAttributeValues={":p": alive})
    if c.get("email") and FROM_EMAIL and kind in push_kinds(r):
        try:
            ses.send_email(FromEmailAddress=FROM_EMAIL, Destination={"ToAddresses": [c["email"]]},
                           Content={"Simple": {"Subject": {"Data": f"Seventy-Two: {c.get('place', 'your exposure')}"},
                                               "Body": {"Text": {"Data": f"{ev['text']}\n\nYour clock: {c.get('site_url') or SITE_URL}/clock/?id={clock_id}\n\nNot medical advice. Your health department: {c.get('phone') or 'see the notice'}."}}}})
        except Exception as e:  # email is a convenience; the clock page and calendar file are the main channel
            print(json.dumps({"level": "warn", "email_failed": str(e)[:200]}))
    return {**ev, "skip": False, **left}


def deadline_slip_ms(c, kind, now=None):
    """How long after its deadline a window actually closed, in wall-clock milliseconds. For a demo clock the
    deadline is where the sped-up timeline puts it. Negative would mean early, which the rounding up prevents."""
    try:
        deadline = rules.parse_iso(c["deadlines"][f"{kind}_deadline"])
        scale = int(c.get("scale", 1))
        now = now or datetime.now(timezone.utc)
        if scale > 1:
            deadline = rules.parse_iso(c["created"]) + (deadline - rules.parse_iso(c["as_of"])) / scale
        return round((now - deadline).total_seconds() * 1000)
    except (KeyError, TypeError, ValueError):
        return None


def handler(event, ctx):
    """One function serves every clock step; the state machine says which."""
    return {"plan": plan, "wait": wait, "notify": notify}[event["step"]](event, ctx)
