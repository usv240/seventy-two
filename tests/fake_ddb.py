"""A small in-memory stand-in for the DynamoDB calls the matcher makes when it records a match: a strongly
consistent get, and an update with a condition on the trip's revision. Thread-safe.

It takes and returns plain Python values, exactly like the real thing: the client of a boto3 DynamoDB resource
(store.table.meta.client) has boto3's serializer attached, so low-level {"S": ...} values are an error there.
The fake rejects them too, so a test cannot pass on a call that would fail on AWS."""
import threading


class ConditionalCheckFailedException(Exception):
    pass


def _plain(v, where):
    if isinstance(v, dict) and len(v) == 1 and next(iter(v)) in ("S", "N", "M", "L", "BOOL", "NULL", "SS", "NS"):
        raise ValueError(f"low-level DynamoDB type in {where}: the resource client takes plain values")
    return v


class FakeClient:
    exceptions = type("exceptions", (), {"ConditionalCheckFailedException": ConditionalCheckFailedException})

    def __init__(self, items=None, between=None):
        self.items = {k: dict(v) for k, v in (items or {}).items()}   # pk -> {"matches": {...}, "rev": n}
        self.lock = threading.Lock()
        self.between = between      # called after a read and before the write, to force a race in a test
        self.updates = 0

    def get_item(self, TableName, Key, **kw):
        _plain(Key["pk"], "Key")
        with self.lock:
            it = self.items.get(Key["pk"])
            got = {"Item": {k: (dict(v) if isinstance(v, dict) else v) for k, v in it.items()}} if it is not None else {}
        if self.between:
            self.between()
        return got

    def update_item(self, TableName, Key, ExpressionAttributeNames, ExpressionAttributeValues, **kw):
        _plain(Key["pk"], "Key")
        for k, v in ExpressionAttributeValues.items():
            _plain(v, k)
        with self.lock:
            it = self.items.get(Key["pk"])
            want = int(ExpressionAttributeValues[":rev"])
            if it is None or int(it.get("rev", 0)) != want:
                raise ConditionalCheckFailedException()
            it.setdefault("matches", {})[ExpressionAttributeNames["#w"]] = ExpressionAttributeValues[":m"]
            it["rev"] = want + 1
            self.updates += 1


class FakeTable:
    name = "t"

    def __init__(self, client):
        self.puts = []
        self.meta = type("meta", (), {"client": client})()

    def put_item(self, **kw):
        self.puts.append(kw)
