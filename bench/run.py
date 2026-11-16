"""Benchmark the reader on real pages. Run: python bench/run.py

Two sets, both real pages Seventy-Two did not write:
  positives  exposure notices; the ground truth is what the notice itself says, labelled in labels.json
  negatives  measles pages that are NOT exposure notices (case counts, vaccine guidance, news about cases);
             the correct answer is zero exposure windows. "Hard" ones name places, dates and times that are
             not exposures (vaccine clinics, wastewater results), and two positives list clinics too.

Each page is fetched once and cached in bench/pages/ so reruns are cheap and repeatable.
Baselines: a regex reader that finds 'Month Day' dates near times, which is what a reasonable engineer
would write first without a model; and Claude alone, the same answer from the same call with every code check
off, which is what trusting the model would give. The second costs nothing extra: one call, scored twice.
"""
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import reader  # noqa: E402
from textutil import html_to_text  # noqa: E402

UA = "Mozilla/5.0 (compatible; SeventyTwo/1.0; benchmark)"


def page(url):
    p = HERE / "pages" / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".txt")
    if p.exists():
        return p.read_text(encoding="utf-8")
    html = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=25).read().decode("utf-8", "replace")
    text = html_to_text(html)
    p.parent.mkdir(exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return text


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
DATE_TIME = re.compile(rf"\b(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b[^.\n]{{0,80}}?\b\d{{1,2}}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?)", re.I)


def regex_reader(text):
    """Baseline: every 'Month Day ... time' in the text is one exposure window."""
    return len(DATE_TIME.findall(text))


def mutations(w, others=()):
    """Ways a model could be wrong about a real window. The checks should catch each one. The hardest are taken
    from another window of the same notice: a real date or time, written on the same page, just not for this place."""
    from datetime import datetime, timedelta
    day = (datetime.strptime(w["date"], "%Y-%m-%d") + timedelta(days=9)).strftime("%Y-%m-%d")
    out = {"wrong date (9 days later)": {**w, "date": day, "end_date": None},
           "invented place": {**w, "place": "Riverside Community Center"},
           "invented quote": {**w, "quote": "Anyone at the community center that evening may have been exposed to measles."}}
    hm = reader._hhmm(w.get("start_time"))
    if hm and w.get("time_clear"):
        out["wrong start time (3 hours later)"] = {**w, "start_time": f"{(hm[0] + 3) % 24:02d}:{hm[1]:02d}"}
    other_day = next((o for o in others if o.get("date") and o["date"] != w["date"]), None)
    if other_day:
        out["date of another window in the same notice"] = {**w, "date": other_day["date"], "end_date": None}
    other_time = next((o for o in others if o.get("time_clear") and reader._hhmm(o.get("start_time")) and o.get("start_time") not in
                       (w.get("start_time"), w.get("end_time")) and o.get("start_time") not in (w.get("quote") or "")), None)
    if other_time and hm and w.get("time_clear"):
        out["start time of another window in the same notice"] = {**w, "start_time": other_time["start_time"]}
    return out


def caught(original, mutated, text):
    """A mutation is caught if the window is dropped, or if its wrong time is removed (the window widens)."""
    kept, _ = reader.validate({"windows": [mutated]}, text, "bench")
    if not kept:
        return True
    if mutated.get("start_time") != original.get("start_time"):
        return "time_not_found" in kept[0]["flags"]
    return False


def exact(expect, windows):
    """How many labelled windows are found exactly: place keyword, local date, start and end time (or the whole
    day when no time is written). Each window can stand for one labelled window only."""
    hits, used = 0, set()
    for e in expect:
        for wi, w in enumerate(windows):
            if wi in used:
                continue
            local = w["local_label"].lower()
            if e["start"] is None:   # a day with no times: the label must be that one day, the whole day
                ok = "time not given" in local and e["date_label"].lower() in local
            else:
                head, tail = (local.split(" to ", 1) + [""])[:2]
                ok = (e["date_label"].lower() in head and e["start"].lower() in head
                      and (e["end"].lower() in tail if e.get("end") else not tail))
            if e["place"].lower() in w["place"].lower() and ok:
                hits += 1
                used.add(wi)
                break
    return hits


def main():
    labels = json.loads((HERE / "labels.json").read_text(encoding="utf-8"))
    rows, cost = [], 0.0
    mutation_tally = {}
    skipped = []
    for item in labels["positives"] + labels["negatives"]:
        try:
            text = page(item["url"])
        except Exception as e:  # some sites refuse scripted fetches; we record that rather than hide it
            skipped.append({"url": item["url"], "error": str(e)[:80]})
            print("SKIP", item["url"], str(e)[:80])
            continue
        t0 = time.time()
        result, usage = reader.read(text, url=item["url"], source_hint=item.get("issuer", ""))
        proposed = (result.get("windows") or []) if result.get("is_exposure_notice") and isinstance(result.get("windows"), list) else []
        for w in proposed:
            for name, m in mutations(w, [o for o in proposed if o is not w]).items():
                tally = mutation_tally.setdefault(name, [0, 0])
                tally[0] += caught(w, m, text)
                tally[1] += 1
        windows, rejected = reader.validate(result, text, "bench", item["url"]) if result.get("is_exposure_notice") else ([], [])
        alone, _ = reader.validate(result, text, "bench", item["url"], checks=False) if result.get("is_exposure_notice") else ([], [])
        cost += usage["cost_usd"]
        got = {(w["start_utc"][:10]) for w in windows}
        row = {"url": item["url"], "kind": "positive" if item in labels["positives"] else "negative",
               "expected_windows": item.get("windows", 0), "found_windows": len(windows), "rejected": len(rejected),
               "regex_windows": regex_reader(text), "seconds": round(time.time() - t0, 1), "cost_usd": usage["cost_usd"]}
        row["alone_windows"] = len(alone)
        row["alone_watch_until_not_written"] = sum(1 for w in alone if w.get("watch_until") and not reader.checked_date(w["watch_until"], text))
        if item.get("expect"):
            hits = exact(item["expect"], windows)
            row["expected_found"] = hits
            row["expected_total"] = len(item["expect"])
            row["extra_windows"] = max(0, len(windows) - item.get("windows", 0))  # e.g. a vaccine clinic read as an exposure
            row["alone_found"] = exact(item["expect"], alone)
            row["alone_wrong"] = len(alone) - row["alone_found"]   # windows that are not exactly a labelled one
            row["checked_wrong"] = len(windows) - hits
        if item.get("hard"):
            row["hard"] = True
        if item.get("held_out"):
            row["held_out"] = True
        if item.get("added_after_checks"):
            row["added_after_checks"] = True
        rows.append(row)
        print(json.dumps(row))
    pos = [r for r in rows if r["kind"] == "positive"]
    neg = [r for r in rows if r["kind"] == "negative"]
    summary = {
        "positives": len(pos),
        "labelled_windows": sum(r.get("expected_total", 0) for r in pos),
        "labelled_windows_found_exactly": sum(r.get("expected_found", 0) for r in pos),
        "held_out_notices": sum(1 for r in pos if r.get("held_out")),
        "held_out_windows": sum(r.get("expected_total", 0) for r in pos if r.get("held_out")),
        "held_out_found_exactly": sum(r.get("expected_found", 0) for r in pos if r.get("held_out")),
        "negatives": len(neg),
        "windows_created_from_negatives": sum(r["found_windows"] for r in neg),
        "hard_negatives": sum(1 for r in neg if r.get("hard")),
        "windows_created_from_hard_negatives": sum(r["found_windows"] for r in neg if r.get("hard")),
        "extra_windows_on_positives": sum(r.get("extra_windows", 0) for r in pos),
        "rejected_by_validator": sum(r["rejected"] for r in rows),
        "regex_windows_from_negatives": sum(r["regex_windows"] for r in neg),
        "claude_alone": {
            "labelled_windows_found_exactly": sum(r.get("alone_found", 0) for r in pos),
            "windows_not_exactly_a_labelled_one": sum(r.get("alone_wrong", 0) for r in pos),
            "held_out_found_exactly": sum(r.get("alone_found", 0) for r in pos if r.get("held_out")),
            "windows_from_negatives": sum(r["alone_windows"] for r in neg),
            "watch_until_dates_not_written_in_the_notice": sum(r["alone_watch_until_not_written"] for r in rows),
        },
        "with_checks_windows_not_exactly_a_labelled_one": sum(r.get("checked_wrong", 0) for r in pos),
        "regex_vs_truth_positives": [(r["regex_windows"], r["expected_windows"]) for r in pos],
        "skipped_unfetchable": skipped,
        "mutations_caught": {k: f"{v[0]} of {v[1]}" for k, v in mutation_tally.items()},
        "cost_usd_total": round(cost, 4),
        "model": reader.MODEL,
    }
    (HERE / "results.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
