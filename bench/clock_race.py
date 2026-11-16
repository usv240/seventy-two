"""The race the deadline clock is built for, run on AWS: "I acted" arriving at the same moment as the deadline.

    python bench/clock_race.py

Deploys patterns/deadline-clock as a temporary stack (deadline-clock-try), subscribes a queue to its topic, and
starts 100 clocks, each with one deadline, spread over 30 seconds. For each, "I acted" is sent through the clock's
function at a random moment within a second either side of its deadline, the way an app would call it. Then it
checks, for every clock:
  - the workflow ended exactly once: in Acted or in Missed, never both, never neither;
  - the answer the function gave the person ("in_time") agrees with how the workflow ended;
  - the topic carried exactly one final message ("acted" or "missed").
Deletes the queue and the stack, and writes docs/race.json (and appends it to docs/scale.json). Costs well under a cent.
"""
import importlib.util
import json
import random
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("try_it", ROOT / "patterns" / "deadline-clock" / "try_it.py")
try_it = importlib.util.module_from_spec(spec)
spec.loader.exec_module(try_it)   # its deploy() and listen(): the same stack and queue as the pattern's own test

N, SPREAD_S, JITTER_S = 100, 30, 1.0
sfn, lam, cfn, sns, sqs = (boto3.client(n) for n in ("stepfunctions", "lambda", "cloudformation", "sns", "sqs"))


def main():
    random.seed(72)
    t0 = time.time()
    out = try_it.deploy()
    print(f"deployed in {time.time() - t0:.0f} s")
    queue, sub = try_it.listen(out["EventsTopicArn"])
    fn, sm = out["ClockStepFunction"], out["ClockStateMachineArn"]
    try:
        # warm the function so the presses are not slowed by cold starts
        with ThreadPoolExecutor(32) as pool:
            list(pool.map(lambda _: lam.invoke(FunctionName=fn, Payload=json.dumps({"action": "done", "clock_id": "warm", "name": "warm"})), range(32)))
        base = datetime.now(timezone.utc) + timedelta(seconds=45)
        clocks = []
        for i in range(N):
            due = base + timedelta(seconds=SPREAD_S * i / N)
            cid = "race-" + uuid.uuid4().hex[:8]
            ex = sfn.start_execution(stateMachineArn=sm, name=cid, input=json.dumps({"clock_id": cid, "deadlines": [
                {"name": "deadline", "remind_at": try_it.iso(due - timedelta(seconds=20)), "deadline_at": try_it.iso(due)}]}))
            clocks.append({"id": cid, "arn": ex["executionArn"], "due": due, "offset_s": round(random.uniform(-JITTER_S, JITTER_S), 3)})
        print(f"{N} clocks started; deadlines from {base:%H:%M:%S} over {SPREAD_S} s")

        def press(c):
            at = c["due"] + timedelta(seconds=c["offset_s"])
            time.sleep(max(0.0, (at - datetime.now(timezone.utc)).total_seconds()))
            sent = datetime.now(timezone.utc)
            r = json.loads(lam.invoke(FunctionName=fn, Payload=json.dumps({"action": "done", "clock_id": c["id"], "name": "deadline"}))["Payload"].read())
            c.update(sent_s_after_deadline=round((sent - c["due"]).total_seconds(), 3), in_time=r.get("in_time"))

        events, stop = {}, threading.Event()

        def drain():
            while not stop.is_set():
                for msg, _ in try_it.receive(queue, wait=2):
                    events.setdefault(msg["clock_id"], []).append(msg["event"])
        reader = threading.Thread(target=drain)
        reader.start()
        with ThreadPoolExecutor(N) as pool:
            list(pool.map(press, clocks))
        while any(sfn.describe_execution(executionArn=c["arn"])["status"] == "RUNNING" for c in clocks):
            time.sleep(3)
        time.sleep(15)   # the last messages can trail the executions
        stop.set()
        reader.join()
        for c in clocks:   # read every outcome before the stack (and its executions) is deleted
            hist = sfn.get_execution_history(executionArn=c["arn"], maxResults=500)["events"]
            c["ended"] = [e["stateEnteredEventDetails"]["name"] for e in hist
                          if e.get("stateEnteredEventDetails", {}).get("name") in ("Acted", "Missed")]
    finally:
        sns.unsubscribe(SubscriptionArn=sub)
        sqs.delete_queue(QueueUrl=queue)
        cfn.delete_stack(StackName=try_it.STACK)
        cfn.get_waiter("stack_delete_complete").wait(StackName=try_it.STACK)
        print("stack and queue deleted")

    for c in clocks:
        c["final_messages"] = [e for e in events.get(c["id"], []) if e in ("acted", "missed")]
    once = [c for c in clocks if len(c["ended"]) == 1]
    agree = [c for c in once if c["in_time"] == (c["ended"][0] == "Acted")]
    one_msg = [c for c in once if c["final_messages"] == [c["ended"][0].lower()]]
    acted = [c for c in once if c["ended"][0] == "Acted"]
    missed = [c for c in once if c["ended"][0] == "Missed"]
    late_acted = [c["sent_s_after_deadline"] for c in acted if c["sent_s_after_deadline"] > 0]
    early_missed = [c["sent_s_after_deadline"] for c in missed if c["sent_s_after_deadline"] < 0]
    result = {
        "test": "race", "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "what": "100 clocks; 'I acted' sent to each within a second either side of its deadline",
        "clocks": N, "ended_exactly_once": len(once), "ended_twice_or_never": N - len(once),
        "acted": len(acted), "missed": len(missed),
        "answer_agrees_with_outcome": len(agree), "exactly_one_final_message": len(one_msg),
        "latest_press_still_in_time_s": max(late_acted) if late_acted else None,
        "earliest_press_too_late_s": min(early_missed) if early_missed else None,
        "press_sent_s_after_deadline": {"min": min(c["sent_s_after_deadline"] for c in clocks),
                                        "median": statistics.median(c["sent_s_after_deadline"] for c in clocks),
                                        "max": max(c["sent_s_after_deadline"] for c in clocks)},
        "how": "bench/clock_race.py against patterns/deadline-clock, deployed then deleted; outcomes from each execution's history, messages from a queue on the topic",
        "clocks_detail": [{k: c[k] for k in ("id", "offset_s", "sent_s_after_deadline", "in_time", "ended", "final_messages")} for c in clocks],
    }
    (ROOT / "docs" / "race.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    scale = json.loads((ROOT / "docs" / "scale.json").read_text(encoding="utf-8"))
    scale.append({k: v for k, v in result.items() if k != "clocks_detail"})
    (ROOT / "docs" / "scale.json").write_text(json.dumps(scale, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "clocks_detail"}, indent=1))


if __name__ == "__main__":
    main()
