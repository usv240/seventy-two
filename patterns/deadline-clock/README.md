# Deadline clock

The pattern under Seventy-Two, on its own, for any app where a person has deadlines: a vaccine window after an
exposure, a refund window, a visa form, a recall, a boil-water notice. One file, `template.yaml`, about 190 lines.
Nothing to build: the function's code is in the template.

**What it does.** You start one AWS Step Functions execution per person, with their deadlines. It runs them side by
side. Before each deadline it publishes a reminder to an SNS topic. Then it waits on a task token whose timeout is
the deadline. When the person acts, your app calls the function and the workflow resumes. If they never act, the
window closes on its own at the deadline. Waiting costs nothing: a paused Standard execution is not billed by the
hour.

**Why a task token and not scheduled jobs.** Each deadline needs three things: a reminder, a way for "I acted" to
cancel what comes next, and a close when nobody answers. A task token gives all three in one state. Its timeout is
measured after the reminder runs, so a slow or retried reminder cannot move the deadline. The whole timeline is one
execution history you can show the person, and one `StopExecution` ends it.

**The race it settles.** When "I acted" and the deadline arrive together, the token succeeds or times out, once,
and the function's answer (`"in_time": true` or `false`, or "Too late" on the link's page) always says which. On AWS,
100 clocks were each told "I acted" within a second either side of their deadline (`bench/clock_race.py` in the
Seventy-Two repo): 100 of 100 ended exactly once, 100 of 100 answers agreed with how the workflow ended, and each
clock sent exactly one final message.

**Measured against the obvious alternative.** One-time EventBridge Scheduler schedules, one per deadline, deleted
when the person acts (`bench/clock_vs_scheduler.py` in the Seventy-Two repo): 50 schedules due at whole seconds all
fired, but a median of 24 s and at most 50 s late, and 10 of 10 deleted before they were due stayed silent. 50 of
these clocks closed their windows a median of 1.0 s and at most 1.5 s late. Scheduler costs less per deadline, so it
is a fine choice when a minute does not matter and you are happy to keep track of which schedule belongs to which
deadline, and to handle a delete that races a firing, yourself.

## Deploy

```
sam deploy --guided --template-file template.yaml --stack-name deadline-clock --capabilities CAPABILITY_IAM
```

Or, without the SAM CLI:

```
aws cloudformation deploy --template-file template.yaml --stack-name deadline-clock --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND
```

It creates one DynamoDB table, one SNS topic, one Lambda function (with a function URL for the one-click links)
and one state machine.

## Use it

Start a clock for a person (times in ISO 8601 UTC):

```
aws stepfunctions start-execution --state-machine-arn <ClockStateMachineArn> --input '{
  "clock_id": "person-123",
  "deadlines": [
    {"name": "vaccine",       "remind_at": "2026-11-15T23:25:00Z", "deadline_at": "2026-11-16T23:25:00Z"},
    {"name": "antibody shot", "remind_at": "2026-11-18T23:25:00Z", "deadline_at": "2026-11-19T23:25:00Z"}]}'
```

When the person acts, from your app:

```
aws lambda invoke --function-name <ClockStepFunction> --payload '{"action": "done", "clock_id": "person-123", "name": "vaccine"}' out.json
```

It answers `{"ok": true, "in_time": true}`, or `"in_time": false` if the deadline won the race: tell the person the
same thing the workflow will do.

That works before the reminder (the reminder is skipped), while the workflow waits (it resumes at once), or in the
moment between the two (the wait step sees it and resumes). Subscribe your app, an email address or an SQS queue to
`EventsTopicArn`: it receives `reminder`, `acted` and `missed` for each deadline.

**Or let the person press a link.** Each `reminder` message carries an `act_url`. Put it in the email or text your
app sends. Opening it shows a "Yes, I did it" button; pressing it does the same as the call above. Every link has
its own random key, and opening it records nothing by itself, so a mail scanner that follows links cannot act for
the person.

## Check it

- `python try_it.py` deploys a stack named `deadline-clock-try`, runs one clock with two deadlines 50 and 80 seconds
  away, acts on the first, leaves the second, checks that the first ends in Acted and the second in Missed at its
  deadline, then deletes the stack. Costs well under a cent. Every clock is added to `runs.json`. On November 15,
  2026, 4 of 4 passed: the first acted through the function; the next 3, run together, acted through the link in
  their reminder, every reminder, "acted" and "missed" arrived on a queue subscribed to the topic, and each ignored
  deadline closed itself 0.21 to 0.58 s after it passed.
- `tests/test_pattern_deadline_clock.py` (in the Seventy-Two repo) runs the function's code with AWS faked: the
  reminder, the wait, "I acted", and the race between them.
- `cfn-lint template.yaml` reports no findings.

## How Seventy-Two grows it

Seventy-Two runs this same shape, with more around it: the deadlines come from a disease's rule file
(`src/domains/`), each reminder also goes to the person's browser by Web Push, milestones (start and end of a symptom
watch) are Wait states in the same Map, and the clock page reads the execution history live. In its scale test,
50 clocks started together all closed their windows on their own, none early, the latest 1.5 seconds after its
deadline.
