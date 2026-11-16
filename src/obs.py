"""What every function reports about itself, so anyone can watch the stack from outside (the /ops/ page).

Metrics: one JSON log line in CloudWatch Embedded Metric Format becomes CloudWatch metrics, with no API call,
no agent and no extra latency. A name ending in Ms is milliseconds, one ending in USD is dollars.

Traces: Lambda's active tracing starts an X-Ray trace for each invocation. Here the X-Ray SDK adds a part
(a subsegment) for every AWS call (DynamoDB, S3, SSM, Step Functions, CloudWatch) and the Bedrock call to Claude,
and remote() adds one for every page fetched and every push sent, named by its host only: a trace never holds a
full address, so a link a visitor pastes into the Lab is never recorded.
Outside Lambda (tests, scripts, the benchmark) both are switched off and cost nothing.
"""
import json
import os
import time
from contextlib import contextmanager

NAMESPACE = "SeventyTwo"
IN_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
recorder = None

if IN_LAMBDA:
    try:
        from aws_xray_sdk.core import patch, xray_recorder

        for module in ("botocore", "httpx"):   # not httplib: it would record full URLs
            try:
                patch([module])
            except Exception:  # tracing helps; it is never a reason for a function to fail
                pass
        recorder = xray_recorder
    except Exception:
        recorder = None


def _unit(name):
    if name.endswith("Ms"):
        return "Milliseconds"
    if name.endswith("USD"):
        return "None"
    return "Count"


def emf(values, timestamp_ms=None):
    """The Embedded Metric Format record for these values (a dict that prints as one log line)."""
    return {"_aws": {"Timestamp": int(timestamp_ms if timestamp_ms is not None else time.time() * 1000),
                     "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": [[]],
                                            "Metrics": [{"Name": k, "Unit": _unit(k)} for k in values]}]},
            **values}


def metric(**values):
    """Record metrics. Values that are None are left out, so a caller never has to guard."""
    values = {k: v for k, v in values.items() if v is not None}
    if values and (IN_LAMBDA or os.environ.get("OBS_PRINT")):
        print(json.dumps(emf(values)))


def log(**fields):
    """One structured log line, the kind CloudWatch Logs Insights queries in the saved queries."""
    if IN_LAMBDA or os.environ.get("OBS_PRINT"):
        print(json.dumps(fields, default=str))


@contextmanager
def span(name, **annotations):
    """A named part of the current trace, with annotations that X-Ray can search by. A no-op outside Lambda."""
    sub = None
    if recorder:
        try:
            sub = recorder.begin_subsegment(name)
            for k, v in annotations.items():
                if v is not None:
                    sub.put_annotation(k, v if isinstance(v, (int, float, bool)) else str(v)[:250])
        except Exception:
            sub = None
    try:
        yield sub
    except Exception as e:
        if sub is not None:
            try:
                sub.add_exception(e, [])
            except Exception:
                pass
        raise
    finally:
        if sub is not None:
            try:
                recorder.end_subsegment()
            except Exception:
                pass


@contextmanager
def remote(host):
    """A call to a site outside AWS, as a node on X-Ray's service map, named by its host and nothing more."""
    sub = None
    if recorder:
        try:
            sub = recorder.begin_subsegment(host[:200], namespace="remote")
            sub.put_http_meta("url", f"https://{host[:200]}/")
        except Exception:
            sub = None
    try:
        yield sub
    except Exception as e:
        if sub is not None:
            try:
                sub.add_exception(e, [])
            except Exception:
                pass
        raise
    finally:
        if sub is not None:
            try:
                recorder.end_subsegment()
            except Exception:
                pass


class Timer:
    """with Timer() as t: ...; t.ms is the elapsed time in milliseconds."""

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self.t0) * 1000, 1)
        return False
