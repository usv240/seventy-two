"""The public board: what GET /api/exposures returns, and what the S3 snapshot holds.

One function builds both, so if the API fails and CloudFront serves the snapshot instead,
visitors see the same rows, merged and filtered the same way."""
from datetime import timedelta

import merge
import rules
import store

PUBLIC = ("id", "place", "address", "city", "state", "timezone", "start_utc", "end_utc", "local_label",
          "watch_until", "time_clear", "flags", "quote", "url", "issuer", "phone", "published_date",
          "lag_days", "first_seen",
          "sources", "ids", "lag_measured", "source_kind")
RECENT_DAYS = rules.WATCH_END_DAY  # the longest a person needs to watch after an exposure


def public_window(w):
    return {**{k: w.get(k) for k in PUBLIC}, **{k: v for k, v in w.items() if k.startswith("left_at_publication_")}}


def board(now, include_old=False):
    since = None if include_old else rules.iso(now - timedelta(days=RECENT_DAYS))
    rows = [public_window(w) for w in merge.merge(store.windows(since))]
    rows.sort(key=lambda w: w["start_utc"], reverse=True)
    return {"as_of": rules.iso(now), "windows": rows}
