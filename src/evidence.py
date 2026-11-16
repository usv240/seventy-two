"""Is a date or time the model proposed actually written in the notice? Pure functions, standard library.

The reader asks Claude for dates as YYYY-MM-DD and times as HH:MM, then these functions look for the
same date or time written the way people write it: "Sept. 13", "September 13th", "9/13/26", "Nov. 3-5",
"7:25 p.m.", "7 to 9 pm", "noon". The text passed in is already normalized (lowercase, plain dashes).
"""
import re

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
SEP = r"(?:-|to|through|thru|until|and|&)"
AMPM = r"(?:a\.?\s?m\.?|p\.?\s?m\.?)"


def _names(month):
    full = MONTHS[month - 1]
    names = {full, full[:3]} | ({"sept"} if month == 9 else set())
    return "|".join(sorted(names, key=len, reverse=True))


def date_spans(text, d):
    """Where `text` states the date `d` (a datetime.date): (start, end) character spans."""
    names = _names(d.month)
    day = rf"0?{d.day}(?:st|nd|rd|th)?"
    pats = [
        rf"\b(?:{names})\.?,?\s+{day}\b",                    # Sept. 13, September 13th
        rf"\b{day}\s+(?:of\s+)?(?:{names})\b",               # 13 September
        rf"(?<![\d/]){d.month}/0?{d.day}(?:/(?:20)?\d\d)?\b",  # 9/13, 9/13/26
        rf"(?<![\d/])0{d.month}/0?{d.day}(?:/(?:20)?\d\d)?\b" if d.month < 10 else None,
        rf"\b{d.year}-{d.month:02d}-{d.day:02d}\b",          # 2026-09-13
    ]
    spans = [m.span() for p in pats if p for m in re.finditer(p, text)]
    # a range that covers the day: "Nov. 3-5", "November 3 through 5", "Nov. 3 and Nov. 4"
    rng = rf"\b(?:{names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*{SEP}\s*(?:(?:{names})\.?\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\b"
    for m in re.finditer(rng, text):
        if int(m.group(1)) <= d.day <= int(m.group(2)):
            spans.append(m.span())
    # a range across months: "Oct. 24 - Nov. 7", "January 31 through February 9"
    any_month = "|".join(sorted({n for i in range(1, 13) for n in _names(i).split("|")}, key=len, reverse=True))
    cross = rf"\b({any_month})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*{SEP}\s*({any_month})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b"
    month_of = lambda name: next(i for i in range(1, 13) if name in _names(i).split("|"))
    for m in re.finditer(cross, text):
        a = (month_of(m.group(1)), int(m.group(2)))
        b = (month_of(m.group(3)), int(m.group(4)))
        x = (d.month, d.day)
        if (a <= b and a <= x <= b) or (a > b and (x >= a or x <= b)):   # the second case wraps past New Year
            spans.append(m.span())
    return spans


def time_spans(text, hh, mm):
    """Where `text` states the clock time hh:mm (24-hour), in 12-hour or 24-hour form."""
    h12 = hh % 12 or 12
    ap = "a" if hh < 12 else "p"
    pats = [rf"(?<![\d:]){h12}[:.]{mm:02d}(?!\d)" if mm else rf"(?<![\d:]){h12}(?:[:.]00)?\s*{ap}\.?\s?m\b",
            rf"(?<![\d:]){hh:02d}:{mm:02d}(?!\d)" if hh != h12 else None]
    if mm == 0:  # "7 to 9 pm", "7-9 p.m.": the first time borrows the second one's am/pm
        pats.append(rf"(?<![\d:]){h12}(?:[:.]00)?\s*{SEP}\s*\d{{1,2}}(?:[:.]\d\d)?\s*{AMPM}")
    if (hh, mm) == (12, 0):
        pats.append(r"\bnoon\b")
    if (hh, mm) in ((0, 0), (23, 59)):
        pats.append(r"\bmidnight\b")
    return [m.span() for p in pats if p for m in re.finditer(p, text)]


DATE_BEFORE = 700  # characters above the quote: a date heading over a list of places. The farthest seen in 125 real windows was 582.
DATE_AFTER = 200
_ANY_MONTH = "|".join(sorted({n for i in range(1, 13) for n in _names(i).split("|")}, key=len, reverse=True))
ANY_DATE = re.compile(rf"\b(?:{_ANY_MONTH})\.?,?\s+\d{{1,2}}(?:st|nd|rd|th)?\b|(?<![\d/])\d{{1,2}}/\d{{1,2}}(?:/(?:20)?\d\d)?\b|\b20\d\d-\d\d-\d\d\b")


def heading_date_ok(text, d, start, end, before=DATE_BEFORE):
    """For a date that is not in the quote itself: the nearest date written above the quote must be this one.
    In a list ("Thursday, November 5 / Walmart ... / Friday, November 6 / Hy-Vee ..."), a place belongs to the
    heading just above it, not to any date somewhere above it."""
    mine = date_spans(text, d)
    if any(s < end and e > start for s, e in mine):
        return True
    if ANY_DATE.search(text, start, end):  # the quote states its own date, and it is not this one
        return False
    above = [m.span() for m in ANY_DATE.finditer(text, max(0, start - before), start)]
    if not above:
        return False
    nearest = max(above, key=lambda sp: sp[1])
    return any(s <= nearest[0] < e or nearest[0] <= s < nearest[1] for s, e in mine)


def near(spans, start, end, before=DATE_BEFORE, after=DATE_AFTER):
    """True if any span falls inside the quote or within a few lines of it.
    Notices often put the date in a heading above a list of places, so the look-back is longer."""
    return any(s < end + after and e > start - before for s, e in spans)


PLACE_WITHIN = 150  # characters around the quote. The farthest seen in 125 real windows was 102.
TIME_WITHIN = 0     # times must be inside the quote. In 125 real windows, all 242 start and end times were.
_GENERIC = {"the", "of", "and", "at", "in", "on", "for", "inc", "llc", "street", "avenue", "road", "suite", "building",
            "airport", "international", "hospital", "terminal", "terminals", "station", "store", "market", "health",
            "medical", "center", "centre", "clinic", "school", "church", "emergency", "department", "room", "gate",
            "county", "city", "area", "areas", "floor", "community", "public", "location", "locations", "north", "south", "east", "west", "other", "used", "connect"}


def place_words(place):
    """The words that name a place (Walmart, Marshfield, Terminal letters aside), not generic ones (hospital, airport)."""
    return {w for w in re.findall(r"[a-z0-9']+", place.lower()) if w not in _GENERIC and len(w) > 2}


def place_near(text, place, start, end, within=PLACE_WITHIN):
    """True if a naming word of the place is written in the quote or within a few lines of it.
    A place with no naming word at all ('Emergency Department') passes: there is nothing to look for."""
    words = place_words(place)
    if not words:
        return True
    lo, hi = max(0, start - within), end + within
    return any(re.search(rf"\b{re.escape(w)}\b", text[lo:hi]) for w in words)


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def weekday_in(quote, d):
    """True if the quote names the weekday of `d` ("between 1:30 and 3:30 p.m. Thursday")."""
    name = WEEKDAYS[d.weekday()]
    return bool(re.search(rf"\b(?:{name}|{name[:3]})\b", quote))


def date_supported(text, d, start, end):
    """The date is written in the quote or is the nearest date heading above it, or the quote names its weekday
    and the page states the date somewhere (news stories say 'Thursday' near the place and the date further down)."""
    return heading_date_ok(text, d, start, end) or (weekday_in(text[start:end], d) and stated(text, d))


# The time zones each state uses (several states span two). A window's zone must be one of its state's.
STATE_ZONES = {
    "AL": {"America/Chicago"}, "AK": {"America/Anchorage", "America/Juneau", "America/Nome", "America/Sitka", "America/Yakutat", "America/Metlakatla", "America/Adak"},
    "AZ": {"America/Phoenix", "America/Denver"}, "AR": {"America/Chicago"}, "CA": {"America/Los_Angeles"},
    "CO": {"America/Denver"}, "CT": {"America/New_York"}, "DE": {"America/New_York"}, "DC": {"America/New_York"},
    "FL": {"America/New_York", "America/Chicago"}, "GA": {"America/New_York"}, "HI": {"Pacific/Honolulu"},
    "ID": {"America/Boise", "America/Los_Angeles", "America/Denver"}, "IL": {"America/Chicago"},
    "IN": {"America/Indiana/Indianapolis", "America/Chicago", "America/New_York", "America/Indiana/Knox", "America/Indiana/Tell_City"},
    "IA": {"America/Chicago"}, "KS": {"America/Chicago", "America/Denver"}, "KY": {"America/New_York", "America/Chicago", "America/Kentucky/Louisville"},
    "LA": {"America/Chicago"}, "ME": {"America/New_York"}, "MD": {"America/New_York"}, "MA": {"America/New_York"},
    "MI": {"America/Detroit", "America/New_York", "America/Chicago", "America/Menominee"}, "MN": {"America/Chicago"},
    "MS": {"America/Chicago"}, "MO": {"America/Chicago"}, "MT": {"America/Denver"}, "NE": {"America/Chicago", "America/Denver"},
    "NV": {"America/Los_Angeles", "America/Denver"}, "NH": {"America/New_York"}, "NJ": {"America/New_York"},
    "NM": {"America/Denver"}, "NY": {"America/New_York"}, "NC": {"America/New_York"}, "ND": {"America/Chicago", "America/Denver"},
    "OH": {"America/New_York"}, "OK": {"America/Chicago"}, "OR": {"America/Los_Angeles", "America/Boise", "America/Denver"},
    "PA": {"America/New_York"}, "RI": {"America/New_York"}, "SC": {"America/New_York"}, "SD": {"America/Chicago", "America/Denver"},
    "TN": {"America/Chicago", "America/New_York"}, "TX": {"America/Chicago", "America/Denver"}, "UT": {"America/Denver"},
    "VT": {"America/New_York"}, "VA": {"America/New_York"}, "WA": {"America/Los_Angeles"}, "WV": {"America/New_York"},
    "WI": {"America/Chicago"}, "WY": {"America/Denver"}, "PR": {"America/Puerto_Rico"},
}


def zone_fits_state(tz, state):
    """True when the time zone is one the state uses, or when the state is unknown or outside the list."""
    zones = STATE_ZONES.get((state or "").upper()[:2])
    return zones is None or tz in zones


def stated(text, d):
    """True if the date is written anywhere in the text (used for published and watch-until dates)."""
    return bool(date_spans(text, d))
