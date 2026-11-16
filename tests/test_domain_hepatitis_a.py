"""The pattern, reused: the same server code run with DOMAIN=hepatitis_a. Runs in its own Python process,
because the domain is chosen once, at start-up, exactly as it is on Lambda."""
import json
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

CHECK = r'''
import json
from datetime import datetime, timedelta, timezone
import rules, reader, clock, trips

NOTICE = """Springfield County Health Department: possible hepatitis A exposure at Rosa's Diner
Published November 10, 2026
A food service worker at Rosa's Diner, 12 Main Street, Springfield, has been diagnosed with hepatitis A.
Anyone who ate or drank at Rosa's Diner on Tuesday, November 3, 2026, between 11 a.m. and 9 p.m. may have been exposed.
People who were exposed and have not been vaccinated should get the hepatitis A vaccine or immune globulin within two weeks."""
proposal = {"is_exposure_notice": True, "windows": [{
    "place": "Rosa's Diner", "city": "Springfield", "date": "2026-11-03", "start_time": "11:00", "end_time": "21:00",
    "timezone": "America/Chicago", "time_clear": True,
    "quote": "Anyone who ate or drank at Rosa's Diner on Tuesday, November 3, 2026, between 11 a.m. and 9 p.m. may have been exposed."}]}
wins, rejected = reader.validate(proposal, NOTICE, "test", "https://example.test/notice")
w = wins[0]
first = rules.parse_iso(w["start_utc"])
d = rules.deadlines(first, rules.parse_iso(w["end_utc"]))
st = rules.status(d, datetime(2026, 11, 10, 18, tzinfo=timezone.utc))
plan = clock.plan({"deadlines": trips.deadlines_json(d), "as_of": "2026-11-10T18:00:00Z", "scale": 3600}, None)["items"]
print(json.dumps({
    "disease": rules.DISEASE, "kept": len(wins), "rejected": rejected,
    "deadline_days": round((d["hepa_vaccine_deadline"] - first).total_seconds() / 86400, 2),
    "watch": [(d["watch_start"] - first).days, (d["watch_end"] - rules.parse_iso(w["end_utc"])).days],
    "phase": st["phase"], "step": st["next_step"],
    "plan": [(i["kind"], i.get("open")) for i in plan],
    "messages": sorted(k for k in clock.MESSAGES if k.startswith("hepa_vaccine")),
    "reader_mentions": "hepatitis A" in reader.SYSTEM and "hepatitis A" in reader.TOOL["description"],
    "left_at_publication": sorted(rules.window_left_at_publication(first, first + timedelta(days=7)).items()),
}))
'''


def run(domain):
    env = {**os.environ, "DOMAIN": domain, "AWS_DEFAULT_REGION": "us-east-1", "PYTHONPATH": str(SRC)}
    out = subprocess.run([sys.executable, "-c", CHECK], cwd=SRC, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_hepatitis_a_runs_on_the_same_code():
    r = run("hepatitis_a")
    assert r["disease"] == "hepatitis A" and r["kept"] == 1 and not r["rejected"]
    assert r["deadline_days"] == 14.0          # vaccine or immune globulin within 2 weeks
    assert r["watch"] == [15, 50]              # symptoms 15 to 50 days after exposure
    assert r["phase"] == "hepa_vaccine_open" and "hepatitis A vaccine" in r["step"]
    assert r["plan"] == [["hepa_vaccine", True], ["ig", True], ["watch_start", None], ["all_clear", None]]
    assert r["messages"] == ["hepa_vaccine_closed", "hepa_vaccine_done", "hepa_vaccine_reminder"]
    assert r["reader_mentions"] is True
    assert r["left_at_publication"] == [["hepa_vaccine_hours_left", 168.0], ["ig_hours_left", 168.0]]


def test_measles_is_unchanged_by_the_refactor():
    import importlib  # noqa: F401
    sys.path.insert(0, str(SRC))
    import rules
    assert rules.DISEASE == "measles" and [a["kind"] for a in rules.ACTIONS] == ["mmr", "ig"]


def test_one_stack_runs_a_second_disease_next_to_the_first():
    """The Lab and the clock take a disease per request: same reader, checks and workflow, other domain file."""
    import clock
    import reader
    import rules
    hep = rules.for_domain("hepatitis_a")
    assert rules.for_domain(None) is rules and rules.for_domain(rules.NAME) is rules
    assert hep.DISEASE == "hepatitis A" and rules.DISEASE == "measles"
    assert [a["kind"] for a in hep.ACTIONS] == ["hepa_vaccine", "ig"]
    assert "hepatitis A" in reader.system_for(hep) and "measles" not in reader.system_for(hep)
    assert "hepatitis A" in reader.tool_for(hep)["description"]
    t0 = rules.parse_iso("2026-11-01T18:00:00Z")
    d = hep.deadlines(t0, t0)
    assert (d["hepa_vaccine_deadline"] - t0).days == 14 and (d["watch_end"] - t0).days == 50
    items = clock.plan({"deadlines": {k: rules.iso(v) for k, v in d.items()}, "as_of": rules.iso(t0), "scale": 3600, "domain": "hepatitis_a"}, None)["items"]
    assert items[0]["kind"] == "hepa_vaccine"   # the clock workflow's Map gets the second disease's deadlines
    assert "hepa_vaccine_reminder" in clock.messages(hep) and "mmr_reminder" not in clock.messages(hep)
    import pytest
    with pytest.raises(ValueError):
        rules.for_domain("../etc")
