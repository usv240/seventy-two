"""Deploy the deadline clock, run real clocks, and show that it keeps time and says so. Then delete it all.

    python try_it.py              deploy a stack named deadline-clock-try, run the test, delete the stack
    python try_it.py --keep       leave the stack in place afterwards

The test is a shop's refund clock, to show the pattern is not about health. Three customers each get two deadlines,
50 and 80 seconds away, each reminded 20 seconds before. A queue subscribed to the clock's SNS topic stands in for
your app. When the first reminder arrives, the script opens its one-click link and presses "Yes, I did it", the way
the customer would ("return the item"). Nobody answers the second ("claim the refund"). Each clock passes only if
the first ends in Acted, the second ends in Missed at its deadline and not before, and all four messages (two
reminders, acted, missed) arrived on the topic. Every clock is added to runs.json. Needs Python 3.12 with boto3 and
AWS credentials. Costs well under a cent. The template is deployed through CloudFormation's AWS SAM transform, so
nothing is built.
"""
import json
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
STACK = "deadline-clock-try"
CLOCKS = 3
cfn, sfn, sns, sqs = (boto3.client(n) for n in ("cloudformation", "stepfunctions", "sns", "sqs"))


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def deploy():
    name = f"c{int(time.time())}"
    kind = "UPDATE" if any(s["StackStatus"] != "DELETE_COMPLETE" for s in cfn.list_stacks()["StackSummaries"] if s["StackName"] == STACK) else "CREATE"
    cfn.create_change_set(StackName=STACK, ChangeSetName=name, ChangeSetType=kind, TemplateBody=(HERE / "template.yaml").read_text(),
                          Capabilities=["CAPABILITY_IAM", "CAPABILITY_AUTO_EXPAND"])
    cfn.get_waiter("change_set_create_complete").wait(StackName=STACK, ChangeSetName=name, WaiterConfig={"Delay": 3})
    cfn.execute_change_set(StackName=STACK, ChangeSetName=name)
    cfn.get_waiter(f"stack_{kind.lower()}_complete").wait(StackName=STACK, WaiterConfig={"Delay": 5, "MaxAttempts": 120})
    return {o["OutputKey"]: o["OutputValue"] for o in cfn.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}


def listen(topic_arn):
    """A queue on the clock's topic, standing in for your app."""
    url = sqs.create_queue(QueueName=f"{STACK}-events-{int(time.time())}")["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    sqs.set_queue_attributes(QueueUrl=url, Attributes={"Policy": json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"}, "Action": "sqs:SendMessage", "Resource": arn,
        "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}}}]})})
    sub = sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=arn, Attributes={"RawMessageDelivery": "true"},
                        ReturnSubscriptionArn=True)["SubscriptionArn"]
    return url, sub


def receive(url, wait=5):
    got = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=wait).get("Messages", [])
    for m in got:
        sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])
    return [(json.loads(m["Body"]), datetime.now(timezone.utc)) for m in got]


def press(act_url):
    """Open the link (it must only show a button), then press the button."""
    with urllib.request.urlopen(act_url, timeout=10) as r:
        opened = r.read().decode()
    req = urllib.request.Request(act_url, data=b"", method="POST", headers={"content-type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as r:
        pressed = r.read().decode()
    return "Yes, I did it" in opened, "Recorded" in pressed


def run(out, queue):
    now = datetime.now(timezone.utc)
    first, second = now + timedelta(seconds=50), now + timedelta(seconds=80)
    clocks = {}
    for _ in range(CLOCKS):
        cid = "order-" + uuid.uuid4().hex[:8]
        ex = sfn.start_execution(stateMachineArn=out["ClockStateMachineArn"], name=f"try-{cid}", input=json.dumps({
            "clock_id": cid, "deadlines": [
                {"name": "return the item", "remind_at": iso(first - timedelta(seconds=20)), "deadline_at": iso(first)},
                {"name": "claim the refund", "remind_at": iso(second - timedelta(seconds=20)), "deadline_at": iso(second)}]}))
        clocks[cid] = {"arn": ex["executionArn"], "events": [], "link": None}
    running = set(clocks)
    quiet_until = None
    while running or (quiet_until and time.time() < quiet_until):
        for msg, at in receive(queue):
            c = clocks.get(msg.get("clock_id"))
            if not c:
                continue
            c["events"].append(f'{msg["deadline"]}: {msg["event"]}')
            if msg["event"] == "reminder" and msg["deadline"] == "return the item":
                shown, recorded = press(msg["act_url"])   # the customer acts from the reminder
                c["link"] = {"opened_shows_button": shown, "pressed_records": recorded,
                             "pressed_s_after_reminder": round((datetime.now(timezone.utc) - at).total_seconds(), 2)}
        for cid in list(running):
            if sfn.describe_execution(executionArn=clocks[cid]["arn"])["status"] != "RUNNING":
                running.discard(cid)
        if not running and quiet_until is None:
            quiet_until = time.time() + 15   # the last messages can trail the execution's end
    results = []
    for cid, c in clocks.items():
        ended = {}
        for e in sfn.get_execution_history(executionArn=c["arn"], maxResults=500)["events"]:
            d = e.get("stateEnteredEventDetails")
            if d and d["name"] in ("Acted", "Missed"):
                ended[json.loads(d["input"])["d"]["name"]] = (d["name"], e["timestamp"])
        late = (ended["claim the refund"][1] - second).total_seconds()
        want = sorted(["return the item: reminder", "return the item: acted", "claim the refund: reminder", "claim the refund: missed"])
        results.append({
            "at": iso(datetime.now(timezone.utc)), "clock": cid, "return the item": ended["return the item"][0],
            "claim the refund": ended["claim the refund"][0], "missed_window_closed_s_after_deadline": round(late, 2),
            "acted_through": "the one-click link in the reminder", "link": c["link"],
            "messages_on_topic": sorted(c["events"]),
            "passed": (ended["return the item"][0] == "Acted" and ended["claim the refund"][0] == "Missed" and 0 <= late < 3
                       and sorted(c["events"]) == want and bool(c["link"]) and all(c["link"][k] for k in ("opened_shows_button", "pressed_records")))})
    return results


def main():
    t0 = time.time()
    out = deploy()
    deployed_s = round(time.time() - t0)
    print(f"deployed in {deployed_s} s")
    queue, sub = listen(out["EventsTopicArn"])
    try:
        results = run(out, queue)
    finally:
        sns.unsubscribe(SubscriptionArn=sub)
        sqs.delete_queue(QueueUrl=queue)
        if "--keep" not in sys.argv:
            cfn.delete_stack(StackName=STACK)
            cfn.get_waiter("stack_delete_complete").wait(StackName=STACK)
            print("stack and queue deleted")
    for r in results:
        r["deploy_s"] = deployed_s
    print(json.dumps(results, indent=1))
    log = HERE / "runs.json"   # every run is kept, so the README quotes a run, not a hope
    runs = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
    log.write_text(json.dumps(runs + results, indent=1), encoding="utf-8")
    print(f"{sum(r['passed'] for r in results)} of {len(results)} clocks passed")
    return results


if __name__ == "__main__":
    main()
