"""Several health departments often post the same exposure. Merge them into one row.

Two windows are the same exposure when their times overlap by most of their length and
their places share a strong word (a name, not a word like 'hospital')."""
import match
import rules


def same(a, b):
    a0, a1 = rules.parse_iso(a["start_utc"]), rules.parse_iso(a["end_utc"])
    b0, b1 = rules.parse_iso(b["start_utc"]), rules.parse_iso(b["end_utc"])
    inter = (min(a1, b1) - max(a0, b0)).total_seconds()
    if inter <= 0:
        return False
    shorter = min((a1 - a0).total_seconds(), (b1 - b0).total_seconds()) or 1
    return inter / shorter >= 0.8 and (match.score(a["place"], b) >= 0.5 or match.score(b["place"], a) >= 0.5)


def merge(windows):
    """Returns merged rows. Each keeps the clearest wording and lists every source that reported it.
    Lag is measured from the earliest dated press release, because a live list page only says when it
    was last updated."""
    groups = []
    for w in sorted(windows, key=lambda w: (w["start_utc"], w.get("published_date") or "9999")):
        for g in groups:
            if same(g[0], w):
                g.append(w)
                break
        else:
            groups.append([w])
    rows = []
    for g in groups:
        notices = [w for w in g if w.get("source_kind") == "notice" and w.get("published_date")]
        first = min(notices, key=lambda w: w["published_date"]) if notices else None
        best = max(g, key=lambda w: (w.get("time_clear", False), len(w.get("place", ""))))
        row = dict(best)
        row["sources"] = sorted({(w.get("issuer") or "", w.get("url") or "") for w in g})
        row["sources"] = [{"issuer": i, "url": u} for i, u in row["sources"]]
        row["ids"] = [w["id"] for w in g]
        if first:
            row["lag_days"] = first.get("lag_days")
            row["published_date"] = first["published_date"]
            row.update({k: v for k, v in first.items() if k.startswith("left_at_publication_")})
            row["lag_measured"] = True
        else:
            row["lag_measured"] = False
        rows.append(row)
    return rows
