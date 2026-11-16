"""The API's own rules, with the table faked: only CloudFront may call it, limits count the visitor's own address,
and "I got it" works before the reminder as well as after."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import api  # noqa: E402
import store  # noqa: E402


def event(route, headers=None, body=None, path=None):
    return {"routeKey": route, "headers": headers or {}, "pathParameters": path or {},
            "body": json.dumps(body) if body is not None else None,
            "requestContext": {"http": {"sourceIp": "130.176.0.1"}}}   # a CloudFront edge address


def test_calls_that_skip_cloudfront_are_refused(monkeypatch):
    monkeypatch.setattr(api, "ORIGIN_SECRETS", {"s3cret"})
    assert api.handler(event("GET /api/status"), None)["statusCode"] == 403
    assert api.handler(event("GET /no-such", {"x-origin-verify": "s3cret"}), None)["statusCode"] == 404


def test_limits_count_the_visitor_not_cloudfront():
    e = event("POST /api/check", {"cloudfront-viewer-address": "203.0.113.7:51234"})
    assert api.client_ip(e) == "203.0.113.7"
    e6 = event("POST /api/check", {"cloudfront-viewer-address": "[2001:db8::1]:443"})
    assert api.client_ip(e6) == "2001:db8::1"
    assert api.client_ip(event("POST /api/check")) == "130.176.0.1"   # no header: the address API Gateway saw


class FakeTable:
    def __init__(self, item):
        self.item = item

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kw):
        if UpdateExpression.startswith("SET events = list_append"):
            self.item.setdefault("events", []).extend(ExpressionAttributeValues[":new"])


def test_i_got_it_before_the_reminder_is_recorded(monkeypatch):
    item = {"pk": "CLOCK#c1", "sk": "META", "scale": 1, "events": [], "tokens": {},
            "as_of": "2099-01-01T00:00:00Z", "created": "2099-01-01T00:00:00Z",
            "deadlines": {"mmr_deadline": "2099-01-04T00:00:00Z", "ig_deadline": "2099-01-07T00:00:00Z"}}
    monkeypatch.setattr(store, "get", lambda pk, sk="META": item)
    monkeypatch.setattr(store, "table", FakeTable(item))
    sent = []
    monkeypatch.setattr(api.sfn, "send_task_success", lambda **kw: sent.append(kw))
    r = api.clock_done("c1", {"kind": "mmr"})
    assert r["statusCode"] == 200 and not sent                       # no token yet: nothing to resume
    assert [e["kind"] for e in item["events"]] == ["mmr_done"]
    assert api.clock_done("c1", {"kind": "mmr"})["statusCode"] == 409  # pressed twice


def test_i_got_it_while_waiting_resumes_the_workflow(monkeypatch):
    item = {"pk": "CLOCK#c2", "sk": "META", "scale": 1, "events": [], "tokens": {"ig": "TOKEN"},
            "as_of": "2099-01-01T00:00:00Z", "created": "2099-01-01T00:00:00Z",
            "deadlines": {"mmr_deadline": "2099-01-04T00:00:00Z", "ig_deadline": "2099-01-07T00:00:00Z"}}
    monkeypatch.setattr(store, "get", lambda pk, sk="META": item)
    monkeypatch.setattr(store, "table", FakeTable(item))
    sent = []
    monkeypatch.setattr(api.sfn, "send_task_success", lambda **kw: sent.append(kw))
    assert api.clock_done("c2", {"kind": "ig"})["statusCode"] == 200
    assert sent and sent[0]["taskToken"] == "TOKEN"


def test_a_closed_window_cannot_be_marked_done(monkeypatch):
    item = {"pk": "CLOCK#c3", "sk": "META", "scale": 1, "events": [], "tokens": {},
            "as_of": "2020-01-01T00:00:00Z", "created": "2020-01-01T00:00:00Z",
            "deadlines": {"mmr_deadline": "2020-01-04T00:00:00Z", "ig_deadline": "2020-01-07T00:00:00Z"}}
    monkeypatch.setattr(store, "get", lambda pk, sk="META": item)
    assert api.clock_done("c3", {"kind": "mmr"})["statusCode"] == 409
