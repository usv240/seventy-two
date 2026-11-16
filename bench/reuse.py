"""The pattern, reused on real notices: the same reader and checks, with DOMAIN=hepatitis_a.

    python bench/reuse.py      writes bench/reuse.md

Two real hepatitis A food-worker notices, read by Claude Haiku 4.5 with src/domains/hepatitis_a.py and checked
by the same code that checks measles notices. The expected window is what each notice says, labelled by hand.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ["DOMAIN"] = "hepatitis_a"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import reader  # noqa: E402
import rules  # noqa: E402
from run import page  # noqa: E402  (the same cached fetch as the measles benchmark)

NOTICES = [
    {"url": "https://www.gnrhealth.com/news/potential-hepatitis-a-exposure-from-restaurant-worker/",
     "issuer": "Gwinnett, Newton and Rockdale County Health Departments, Georgia, November 15, 2023",
     "expect": {"place": "Lolita", "first": "2023-10-24", "last": "2023-11-07"}},
    {"url": "https://www.nyc.gov/site/doh/about/press/pr2025/health-department-warns-patrons-of-possible-exposure-to-hepatitis-a.page",
     "issuer": "NYC Health Department, February 14, 2025",
     "expect": {"place": "ilili", "first": "2025-01-17", "last": "2025-02-09"}},
]


def local_day(iso, tz):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


rows, cost = [], 0.0
for n in NOTICES:
    text = page(n["url"])
    result, usage = reader.read(text, url=n["url"], source_hint=n["issuer"])
    wins, rejected = reader.validate(result, text, "reuse", n["url"]) if result.get("is_exposure_notice") else ([], [])
    cost += usage["cost_usd"]
    e = n["expect"]
    got = [{"place": w["place"], "first": local_day(w["start_utc"], w["timezone"]), "last": local_day(w["end_utc"], w["timezone"]),
            "quote": w["quote"]} for w in wins]
    hit = any(e["place"].lower() in g["place"].lower() and g["first"] <= e["last"] and g["last"] >= e["first"] for g in got)
    exact = any(e["place"].lower() in g["place"].lower() and g["first"] == e["first"] and g["last"] == e["last"] for g in got)
    first = rules.parse_iso(wins[0]["start_utc"]) if wins else None
    d = rules.deadlines(first, rules.parse_iso(wins[0]["end_utc"])) if wins else {}
    rows.append({"issuer": n["issuer"], "expected": e, "windows": got, "rejected": rejected, "covers_expected": hit,
                 "exact_dates": exact, "vaccine_deadline_days": round((d["hepa_vaccine_deadline"] - first).days) if wins else None,
                 "cost_usd": usage["cost_usd"]})
    print(json.dumps(rows[-1], indent=1))

lines = ["# Reuse check: hepatitis A", "",
         "The same reader and the same evidence checks, run with `DOMAIN=hepatitis_a` (`src/domains/hepatitis_a.py`) on two real",
         "hepatitis A food-worker notices. Run: `python bench/reuse.py`. Model: Claude Haiku 4.5 on Amazon Bedrock.", "",
         "| Notice | Exposure in the notice | Window Seventy-Two kept | Dates exact | Deadline computed |", "|---|---|---|---|---|"]
for r in rows:
    e = r["expected"]
    w = "; ".join(f"{g['place']}, {g['first']} to {g['last']}" for g in r["windows"]) or "none"
    lines.append(f"| {r['issuer']} | {e['place']}, {e['first']} to {e['last']} | {w} | {'yes' if r['exact_dates'] else 'no'} | "
                 f"vaccine within {r['vaccine_deadline_days']} days of the first day |")
lines += ["", f"Model cost: ${cost:.4f}. Rejected by the checks: {sum(len(r['rejected']) for r in rows)}.", "",
          "The NYC notice gives two ranges: everyone who ate there from January 17 to February 9 should watch for symptoms,",
          "and those from January 31 should also get the vaccine (on February 14, that was everyone still within 14 days).",
          "The exposure is the wider range, and the domain file says so: at temperature 0 the reader otherwise chose the",
          "vaccine range. Telling the reader how a disease's notices are written is part of reusing the pattern."]
(HERE / "reuse.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
