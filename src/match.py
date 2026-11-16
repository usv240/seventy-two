"""Decides whether a place someone visited is the place in a notice. Deterministic."""
import re

from textutil import normalize

# Airport codes people type, mapped to words that appear in notices.
AIRPORTS = {
    "phl": "philadelphia international airport", "jfk": "john f kennedy international airport",
    "lga": "laguardia airport", "ewr": "newark liberty international airport",
    "bos": "logan international airport", "iad": "dulles international airport",
    "dca": "reagan national airport", "bwi": "baltimore washington international airport",
    "atl": "hartsfield jackson atlanta international airport", "ord": "o'hare international airport",
    "mdw": "midway international airport", "dfw": "dallas fort worth international airport",
    "iah": "george bush intercontinental airport", "hou": "hobby airport", "aus": "austin bergstrom international airport",
    "den": "denver international airport", "slc": "salt lake city international airport",
    "phx": "phoenix sky harbor international airport", "las": "harry reid international airport",
    "lax": "los angeles international airport", "sfo": "san francisco international airport",
    "san": "san diego international airport", "sea": "seattle tacoma international airport",
    "pdx": "portland international airport", "msp": "minneapolis saint paul international airport",
    "dtw": "detroit metropolitan airport", "clt": "charlotte douglas international airport",
    "mco": "orlando international airport", "mia": "miami international airport",
    "fll": "fort lauderdale hollywood international airport", "tpa": "tampa international airport",
    "bna": "nashville international airport", "msy": "louis armstrong new orleans international airport",
    "pit": "pittsburgh international airport", "cle": "cleveland hopkins international airport",
    "cmh": "john glenn columbus international airport", "ind": "indianapolis international airport",
    "mke": "milwaukee mitchell international airport", "dsm": "des moines international airport",
    "oma": "eppley airfield", "mci": "kansas city international airport", "stl": "st louis lambert international airport",
    "rdu": "raleigh durham international airport", "gsp": "greenville spartanburg international airport",
    "buf": "buffalo niagara international airport", "abq": "albuquerque international sunport",
    "elp": "el paso international airport", "sat": "san antonio international airport",
    "smf": "sacramento international airport", "oak": "oakland international airport", "sjc": "san jose international airport",
}

STOP = {"the", "a", "an", "of", "at", "in", "on", "and", "to", "for", "inc", "st", "street", "ave", "avenue",
        "rd", "road", "blvd", "suite", "near", "area", "building", "center", "centre", "county", "city"}
WEAK = {"international", "airport", "hospital", "terminal", "station", "store", "market", "health",
        "medical", "clinic", "school", "church", "emergency", "department", "room", "gate", "concourse"}


def tokens(s):
    words = re.findall(r"[a-z0-9']+", normalize(s or ""))
    out = []
    for w in words:
        out.extend(AIRPORTS.get(w, w).split())
    return {w for w in out if w not in STOP and len(w) > 1}


def score(visited, window):
    """0 to 1. Strong words (names) count, generic words only help."""
    a = tokens(visited)
    b = tokens(" ".join(filter(None, [window.get("place"), window.get("address"), window.get("city")])))
    if not a or not b:
        return 0.0
    strong_a = a - WEAK
    shared = a & b
    strong_shared = shared - WEAK
    if not strong_shared:
        return 0.0
    return round(min(1.0, (len(strong_shared) + 0.25 * len(shared & WEAK)) / max(1, len(strong_a))), 2)


def terminal_conflict(visited, window):
    """True when both name a terminal and they differ (Terminal A vs Terminal B)."""
    t1 = set(re.findall(r"terminal ([a-z0-9])\b", normalize(visited)))
    t2 = set(re.findall(r"terminals? ([a-z0-9](?:\s*(?:,|and|&)\s*[a-z0-9])*)", normalize(window.get("place") or "")))
    t2 = {c for grp in t2 for c in re.findall(r"\b[a-z0-9]\b", grp)}  # whole one-character tokens, not letters of "and"
    return bool(t1 and t2 and not (t1 & t2))
