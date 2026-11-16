# Benchmark results

Run: `python bench/run.py` (reads `bench/labels.json`, caches pages in `bench/pages/`, writes `bench/results.json`).
Model: Claude Haiku 4.5 on Amazon Bedrock (`us.anthropic.claude-haiku-4-5-20251001-v1:0`, temperature 0). Run on 2026-11-15, with the reader's current checks (`src/reader.py`, `src/evidence.py`).

All pages are real public pages that Seventy-Two did not write. Ground truth for notices is what each notice itself says, copied by hand into `labels.json`. **Every window in every notice is labelled, with its place, date, start time and end time,** and a window counts only if all four match.

## Exposure notices (positives)

| Notice | Windows in the notice | Kept | Matched exactly (place, date, start, end) | Regex baseline |
|---|---|---|---|---|
| Philadelphia, PHL airport, Sep 13 (runs past midnight) | 1 | 1 | 1 of 1 | 2 |
| Philadelphia, PHL airport, Jul 4 | 1 | 1 | 1 of 1 | 1 |
| Philadelphia, PHL airport, Feb 12 | 1 | 1 | 1 of 1 | 1 |
| Philadelphia, PHL, 30th Street Station, Amtrak 175, Jan 7 | 3 | 3 | 3 of 3 | 3 |
| Wisconsin DHS, Marshfield Medical Center, Nov 5 | 1 | 1 | 1 of 1 | 0 |
| Polk County, Iowa, 7 sites, Nov 5 to 8 | 7 | 7 | 7 of 7 | 0 |
| San Diego County, 3 sites | 3 | 3 | 3 of 3 | 0 |
| Chautauqua County, NY, hospital. **Hard:** also lists 8 vaccine clinics with places, dates and times | 2 | 2 | 2 of 2 | 12 |
| Wausau Pilot & Review, Weston emergency department. **Hard:** a news story that also announces a vaccine clinic | 1 | 1 | 1 of 1 | 0 |
| New Jersey DOH, Nov 13 release (not in the source list), 4 places. Stays past midnight and over four days | 6 | 6 | 6 of 6 | 9 |
| WSMV, a Nashville TV story. "Oct. 30 & Nov. 2" with no times; a party past midnight | 8 | 8 | 8 of 8 | 7 |
| **Held out:** South Carolina DPH, Feb 27. **Hard:** also names schools in quarantine and a vaccine clinic with a place, date and time | 1 | 1 | 1 of 1 | 2 |
| **Held out:** South Carolina DPH, Mar 3 | 1 | 1 | 1 of 1 | 1 |
| **Held out:** KUTV, a Salt Lake City TV story. Dates, no times | 3 | 2 | 1 of 3 | 2 |
| **Total** | **39** | **38** | **37 of 39** | |

**The held-out test.** The last three notices come from departments Seventy-Two does not read. I labelled them before they were ever run and scored them once, on Nov 15. **3 of their 5 windows matched exactly; 0 false windows.** On the 11 notices above them, the ones the reader and checks were developed against, **34 of 34**. The other 2 are "the Loveland Living Planet Aquarium on Tuesday, March 10, and Wednesday, March 11": the reader made one window from the start of the 10th to the end of the 11th instead of two. That still covers both days, so nobody who was there would be missed, but the label is strict and it counts as a miss. Its label showed only the first day; that was fixed afterward (`reader._label`), which changed no reading or check, and the scoring was made stricter at the same time, so one window can stand for only one labelled window. Rerun on the shipped code, the result is the same: 3 of 5.

**Why the New Jersey and Nashville notices are not called held out.** They were added after the checks were written, but the Nashville story first exposed a gap ("Oct. 30 & Nov. 2" read as one day), and a rule for lines that name two days was then added to the reader's instructions. A notice that changed the code is not held out, so both are reported with the rest. No clinic was read as an exposure.

## Pages that are not exposure notices (negative control)

Thirteen real measles pages. Nine are news about case counts (Al Jazeera, CNBC, Stateline, CIDRAP), reference pages (Wikipedia twice, WHO, NFID) and vaccine guidance (CHOP). Four are **hard negatives** that name places, dates and times that are not exposures: two vaccine clinics (Utah County, Fillmore County), a free vaccine offer with an office address and hours (Kane County), and measles found in wastewater at a named treatment plant (Mecklenburg County).

| | Seventy-Two | Regex baseline |
|---|---|---|
| Exposure windows created from 13 non-notice pages | **0** | 9 (all false) |
| of which from the 4 hard negatives | **0** | 3 |

## Claude alone, without the checks (Nov 15 run)

The same answer from the same call, scored twice: once through the checks, once with every check off, which is what
trusting the model would give (`reader.validate(..., checks=False)`, no extra model calls).

| | With the checks | Claude alone |
|---|---|---|
| Labelled windows matched exactly (39) | 37 | 37 |
| Windows that are not exactly a labelled one | 1 | 1 |
| Windows created from the 13 non-notice pages | 0 | 0 |
| "Watch for symptoms until" dates that the notice never prints | **0** | **17** (on 9 of the 14 notices) |

On these notices Claude's places and times were right. Its mistake was quieter: on 17 windows it worked out a "watch
until" date (21 days after the exposure) and returned it as if the department had printed it. Shown on the board,
that is a deadline in the department's name that the department never gave. The checks keep a printed date only if
the page states it; Seventy-Two computes its own watch period and labels it as computed. The mutation test below
shows what the checks do when a place, date, time or quote is wrong.

## Do the checks catch a wrong answer? (mutation test)

On this run Claude made no mistake for the checks to catch, which proves little about the checks. So `run.py` also takes each real window Claude proposed and breaks it, then runs the same checks. The hardest breaks borrow a real date or time from another window of the same notice:

| The model's window, with... | Caught |
|---|---|
| a date 9 days later | **38 of 38** rejected |
| a place the notice never mentions | **38 of 38** rejected |
| a quote the notice never contains | **38 of 38** rejected |
| a start time 3 hours later | **28 of 34** (the wrong time is removed, so the window widens to the whole day) |
| the start time of another window in the same notice | **27 of 27** |
| the date of another window in the same notice | **25 of 28** rejected |

The 6 start times not caught moved onto another time written in the same quote, usually its own end time ("5 to 8 p.m." read as starting at 8). The 3 dates not caught are written in the window's own quote ("Oct. 30 & Nov. 2", or a stay from one date to the next). A text check cannot tell those apart.

## What the code checks (and the model cannot skip)

Each window Claude proposes is kept only if:

1. its quote appears in the notice word for word;
2. its date is in the quote; or, if the quote has no date, it is the nearest date written above it (a heading over a list of places, up to 700 characters up); or the quote names the weekday and the page states that date. Any end date must be written too, unless it is simply the next day of a stay past midnight;
3. a naming word of its place (not a generic word like "hospital") is written within 150 characters of the quote;
4. its start and end times are written in the quote (a time that is not is removed, which widens the window to the whole day: wider can only catch more people, never fewer);
5. its time zone exists and is one its state uses.

A page-wide date (published, or "watch for symptoms through") is kept only if the page prints it. Every limit was set from 125 real stored windows (`scripts/recheck.py` re-checks them all without calling the model): the farthest date heading was 582 characters above its quote, the farthest place name 102 characters away, and all 242 start and end times were inside their quotes.

## The pattern, reused

`bench/reuse.py` runs the same reader and checks with `DOMAIN=hepatitis_a` on two real hepatitis A food-worker notices (Georgia, 2023; New York City, 2025): **2 of 2** windows exact, from first to last day. See [reuse.md](reuse.md) for how the domain file tells the reader which of two ranges is the exposure.

## Measured limits

- Six candidate negative pages could not be fetched by a script (CDC and Mayo Clinic return HTTP 403; one news site timed out). They are listed in `results.json` and were not counted.
- The labelled set is small (14 notices, 10 publishers). It shows the reader is right about real notices, including ones it was not tuned on; it is not a statistical estimate of error rates.
- Total model cost for the run: $0.19 for 27 pages.
- The cached page text in `bench/pages/` is not committed, because most of it is other publishers' copyrighted work. `run.py` fetches each page on first run; a page that has since changed can give a different result, and the URLs and labels are what make the run repeatable.
