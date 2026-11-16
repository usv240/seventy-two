"""The standalone deadline-clock pattern (patterns/deadline-clock/): its inline function, run with AWS faked."""
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "patterns" / "deadline-clock" / "template.yaml"


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda l, s, n: l.construct_scalar(n) if isinstance(n, yaml.ScalarNode)
                              else (l.construct_sequence(n) if isinstance(n, yaml.SequenceNode) else l.construct_mapping(n)))


def load(monkeypatch):
    t = yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_Loader)
    code = t["Resources"]["ClockStep"]["Properties"]["InlineCode"]
    rows, sent, published = {}, [], []

    class TimedOut(Exception):
        pass

    class Table:
        def get_item(self, Key, **kw):
            it = rows.get((Key["clock_id"], Key["name"]))
            return {"Item": dict(it)} if it else {}

        def update_item(self, Key, ExpressionAttributeNames, ExpressionAttributeValues, **kw):
            it = rows.setdefault((Key["clock_id"], Key["name"]), dict(Key))
            for name, val in zip(ExpressionAttributeNames.values(), ExpressionAttributeValues.values()):
                it[name] = val
            return {"Attributes": dict(it)}

    fake = types.ModuleType("boto3")
    fake.resource = lambda _n: types.SimpleNamespace(Table=lambda _t: Table())
    def send_task_success(**kw):
        if kw["taskToken"] == "LATE":   # a token whose deadline already passed
            raise TimedOut()
        sent.append(kw["taskToken"])

    class Gone(Exception):
        pass

    sfn = types.SimpleNamespace(send_task_success=send_task_success,
                                exceptions=types.SimpleNamespace(TaskTimedOut=TimedOut, TaskDoesNotExist=Gone))
    sns = types.SimpleNamespace(publish=lambda **kw: published.append(kw["Message"]))
    fake.client = lambda n: {"sns": sns, "stepfunctions": sfn}[n]
    monkeypatch.setitem(sys.modules, "boto3", fake)
    monkeypatch.setenv("TABLE", "t")
    monkeypatch.setenv("TOPIC", "arn:topic")
    ns = {}
    exec(compile(code, "index.py", "exec"), ns)
    return ns["handler"], rows, sent, published, t


def test_the_template_is_one_small_stack_with_nothing_to_build(monkeypatch):
    _, _, _, _, t = load(monkeypatch)
    kinds = sorted(r["Type"] for r in t["Resources"].values())
    assert kinds == ["AWS::DynamoDB::Table", "AWS::Lambda::Permission", "AWS::SNS::Topic", "AWS::Serverless::Function",
                     "AWS::Serverless::StateMachine"]
    states = t["Resources"]["Clock"]["Properties"]["Definition"]["States"]["EveryDeadline"]["ItemProcessor"]["States"]
    assert states["WaitForThePerson"]["TimeoutSecondsPath"] == "$.r.wait_s"   # the deadline is the timeout
    assert states["WaitForThePerson"]["Catch"][0]["Next"] == "Missed"
    assert all(states[n].get("Retry") for n in ("Remind", "WaitForThePerson", "Acted", "Missed"))   # every step retries


def test_remind_wait_done_and_the_race_between_them(monkeypatch):
    handler, rows, sent, published, _ = load(monkeypatch)
    soon = (datetime.now(timezone.utc) + timedelta(seconds=30.4)).isoformat().replace("+00:00", "Z")
    r = handler({"action": "remind", "clock_id": "c", "name": "vaccine", "deadline_at": soon}, None)
    assert r == {"done": False, "wait_s": 31} and '"reminder"' in published[-1]
    handler({"action": "wait", "clock_id": "c", "name": "vaccine", "token": "T1"}, None)
    assert handler({"action": "done", "clock_id": "c", "name": "vaccine"}, None) == {"ok": True, "in_time": True} and sent == ["T1"]
    # "I acted" before the reminder: no reminder, and the workflow goes straight to Acted
    handler({"action": "done", "clock_id": "c", "name": "antibody shot"}, None)
    assert handler({"action": "remind", "clock_id": "c", "name": "antibody shot", "deadline_at": soon}, None)["done"] is True
    # "I acted" after the reminder but before the token was saved: the wait step resumes at once
    handler({"action": "done", "clock_id": "d", "name": "vaccine"}, None)
    handler({"action": "wait", "clock_id": "d", "name": "vaccine", "token": "T2"}, None)
    assert sent == ["T1", "T2"]


def test_the_one_click_link_needs_its_key_and_a_press(monkeypatch):
    import json
    from urllib.parse import urlparse
    handler, rows, sent, published, _ = load(monkeypatch)
    soon = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    handler({"action": "remind", "clock_id": "order-1", "name": "return the item", "deadline_at": soon,
             "link": "https://abc.lambda-url.us-east-1.on.aws/"}, None)
    msg = json.loads(published[-1])
    assert msg["event"] == "reminder" and msg["act_url"].startswith("https://abc.lambda-url.us-east-1.on.aws/?c=order-1")
    query = urlparse(msg["act_url"]).query
    handler({"action": "wait", "clock_id": "order-1", "name": "return the item", "token": "T9"}, None)

    def visit(method, q, body=None):
        return handler({"requestContext": {"http": {"method": method}}, "rawQueryString": q, "body": body}, None)

    assert visit("GET", query.replace("k=", "k=x"))["statusCode"] == 404           # a wrong key finds nothing
    opened = visit("GET", query)                                                   # opening the link, as a mail scanner would
    assert opened["statusCode"] == 200 and "Yes, I did it" in opened["body"] and sent == []
    assert "Recorded" in visit("POST", query)["body"] and sent == ["T9"]           # pressing the button resumes the workflow
    assert "Already recorded" in visit("GET", query)["body"]


def test_a_press_after_the_deadline_is_told_it_was_too_late(monkeypatch):
    handler, rows, sent, published, _ = load(monkeypatch)
    soon = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    handler({"action": "remind", "clock_id": "r", "name": "claim the refund", "deadline_at": soon, "link": "https://x/"}, None)
    handler({"action": "wait", "clock_id": "r", "name": "claim the refund", "token": "LATE"}, None)
    assert handler({"action": "done", "clock_id": "r", "name": "claim the refund"}, None) == {"ok": True, "in_time": False}
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    rows[("q", "x")] = {"clock_id": "q", "name": "x", "deadline_at": past}   # no token yet, deadline gone
    assert handler({"action": "done", "clock_id": "q", "name": "x"}, None)["in_time"] is False
