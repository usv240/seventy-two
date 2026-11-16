"""The obvious alternative to the clock, measured: one-time EventBridge Scheduler schedules, one per deadline.

    python bench/clock_vs_scheduler.py

Makes a temporary SQS queue, an IAM role Scheduler can use to send to it, and a schedule group. In the group it
creates 50 one-time schedules (at(...), flexible window off), due over two minutes at whole seconds, each sending
its due time to the queue, and 10 more that it deletes before they are due, to check that a cancelled schedule
stays silent. It records when SQS received each message, then deletes the group, the queue and the role.
Writes docs/scheduler.json. Costs well under a cent.

Compare with the clock's own lateness, measured the same way by its scale test and drills: the time Step Functions
entered the "missed" state, minus the deadline.
"""
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
N, CANCEL = 50, 10
tag = f"seventytwo-sched-test-{int(time.time())}"
session = boto3.Session(region_name="us-east-1")
sqs, iam, sch, sts = (session.client(n) for n in ("sqs", "iam", "scheduler", "sts"))


def setup():
    account = sts.get_caller_identity()["Account"]
    url = sqs.create_queue(QueueName=tag)["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    role = iam.create_role(RoleName=tag, AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": account}}}]}))["Role"]["Arn"]
    iam.put_role_policy(RoleName=tag, PolicyName="send", PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": "sqs:SendMessage", "Resource": arn}]}))
    sch.create_schedule_group(Name=tag)
    return url, arn, role


def schedule(name, due, arn, role):
    for attempt in range(10):   # a new role takes a few seconds to be usable
        try:
            return sch.create_schedule(
                Name=name, GroupName=tag, ScheduleExpression=f"at({due.strftime('%Y-%m-%dT%H:%M:%S')})",
                ScheduleExpressionTimezone="UTC", FlexibleTimeWindow={"Mode": "OFF"}, ActionAfterCompletion="DELETE",
                Target={"Arn": arn, "RoleArn": role, "Input": json.dumps({"name": name, "due": due.isoformat()})})
        except sch.exceptions.ValidationException as e:
            if "role" not in str(e).lower() or attempt == 9:
                raise
            time.sleep(3)


def main():
    url, arn, role = setup()
    got = {}
    try:
        start = (datetime.now(timezone.utc) + timedelta(seconds=150)).replace(microsecond=0)
        dues = {f"d{i:02d}": start + timedelta(seconds=round(i * 120 / N)) for i in range(N)}
        for name, due in dues.items():
            schedule(name, due, arn, role)
        for i in range(CANCEL):   # cancelled before they are due: "I acted" in a scheduler design
            name = f"cancel{i}"
            schedule(name, start + timedelta(seconds=30 + i), arn, role)
            sch.delete_schedule(Name=name, GroupName=tag)
        print(f"{N} schedules due from {start:%H:%M:%S} UTC over 2 minutes; {CANCEL} created and deleted")
        stop = max(dues.values()) + timedelta(seconds=150)
        while datetime.now(timezone.utc) < stop and len([k for k in got if k.startswith("d")]) < N:
            for m in sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=10,
                                         AttributeNames=["SentTimestamp"]).get("Messages", []):
                body = json.loads(m["Body"])
                sent = datetime.fromtimestamp(int(m["Attributes"]["SentTimestamp"]) / 1000, timezone.utc)
                got[body["name"]] = (sent - datetime.fromisoformat(body["due"])).total_seconds()
                sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])
        time.sleep(20)   # anything a cancelled schedule might still send
        for m in sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=5).get("Messages", []):
            got[json.loads(m["Body"])["name"]] = None
    finally:
        for name in [s["Name"] for s in sch.list_schedules(GroupName=tag).get("Schedules", [])]:
            sch.delete_schedule(Name=name, GroupName=tag)
        sch.delete_schedule_group(Name=tag)
        sqs.delete_queue(QueueUrl=url)
        iam.delete_role_policy(RoleName=tag, PolicyName="send")
        iam.delete_role(RoleName=tag)
        print("queue, role and schedule group deleted")
    late = sorted(v for k, v in got.items() if k.startswith("d"))
    clocks = json.loads((ROOT / "docs" / "scale.json").read_text(encoding="utf-8"))
    result = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "what": "one-time EventBridge Scheduler schedules, at(...) with the flexible window off, each sending to SQS; lateness is SQS's SentTimestamp minus the due time",
        "schedules": N, "delivered": len(late),
        "late_s": {"min": round(late[0], 2), "median": round(statistics.median(late), 2),
                   "p95": round(late[max(0, int(len(late) * 0.95) - 1)], 2), "max": round(late[-1], 2)} if late else None,
        "early": sum(1 for v in late if v < 0),
        "cancelled": CANCEL, "cancelled_that_still_fired": sum(1 for k in got if k.startswith("cancel")),
        "all_late_s": [round(v, 2) for v in late],
        "clock_scale_test": next((c for c in clocks if c.get("test") == "clocks"), None) if isinstance(clocks, list) else None,
    }
    (ROOT / "docs" / "scheduler.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("all_late_s", "clock_scale_test")}, indent=1))


if __name__ == "__main__":
    main()
