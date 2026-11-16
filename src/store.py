"""DynamoDB and S3 helpers shared by every function."""
import json
import os
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

TABLE = os.environ.get("TABLE", "seventytwo")
ARCHIVE_BUCKET = os.environ.get("ARCHIVE_BUCKET", "")
SITE_BUCKET = os.environ.get("SITE_BUCKET", "")

ddb = boto3.resource("dynamodb", config=Config(max_pool_connections=32))   # the matcher writes 16 at a time
table = ddb.Table(TABLE)
s3 = boto3.client("s3")


def to_ddb(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


def from_ddb(obj):
    if isinstance(obj, list):
        return [from_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


def put(item, condition=None):
    kwargs = {"Item": to_ddb(item)}
    if condition:
        kwargs["ConditionExpression"] = condition
    return table.put_item(**kwargs)


def get(pk, sk="META"):
    """One item, or None. An item past its TTL counts as deleted now: DynamoDB removes it within a day or two,
    but nothing Seventy-Two shows waits for that."""
    item = table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
    if item and item.get("ttl") and int(item["ttl"]) < int(time.time()):
        return None
    return from_ddb(item) if item else None


def get_many(pks, sk="META"):
    """Many items by key, 100 per BatchGetItem request. Anything past its TTL is left out, as in get()."""
    keys, out = [{"pk": pk, "sk": sk} for pk in pks], []
    for i in range(0, len(keys), 100):
        req, tries = {table.name: {"Keys": keys[i:i + 100]}}, 0
        while req:
            r = ddb.batch_get_item(RequestItems=req)
            out += r["Responses"].get(table.name, [])
            req, tries = r.get("UnprocessedKeys") or None, tries + 1
            if req:
                time.sleep(min(1.0, 0.05 * 2 ** tries))   # DynamoDB asked us to slow down
    now = int(time.time())
    return [from_ddb(i) for i in out if not (i.get("ttl") and int(i["ttl"]) < now)]


def windows(since_iso=None):
    """Exposure windows ordered by end time. since_iso limits to windows ending after it."""
    cond = Key("gsi1pk").eq("WINDOW")
    if since_iso:
        cond = cond & Key("gsi1sk").gte(since_iso)
    items, kwargs = [], {"IndexName": "gsi1", "KeyConditionExpression": cond}
    while True:
        page = table.query(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            return from_ddb(items)
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


_recent = {}


def windows_cached(max_age_s=30):
    """The windows, read at most every max_age_s seconds by a warm function. The board changes at most every
    30 minutes, so a check under load reads the table once, not once per request."""
    hit = _recent.get("windows")
    if hit and time.time() - hit[0] < max_age_s:
        return hit[1]
    rows = windows()
    _recent["windows"] = (time.time(), rows)
    return rows


def ttl_at(epoch_seconds):
    """DynamoDB deletes the item on its own after this time. This is the privacy policy."""
    return int(epoch_seconds)


def now_epoch():
    return int(time.time())
