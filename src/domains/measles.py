"""Measles: what a notice is, what the deadlines are, and what to say. Everything disease-specific is here.

The rest of Seventy-Two (the schedule, the reader's checks, the stream matcher, the clocks, the forgetting)
reads these values and does not know which disease it is watching. hepatitis_a.py is a second instance.
"""
DISEASE = "measles"
DISCOVER_WORDS = ("measles",)   # a link on a news page is read as a new notice only if it says one of these

READER_ROLE = "You read public health notices about measles exposures and record the exposure windows they list."
NOT_A_NOTICE = "A page about case counts, vaccine clinics or general advice, with no specific places and times"

# Each action has a deadline counted from the first exposure. The clock runs one Map iteration per action:
# wait until a day before, remind, then wait for "I got it" (a task token) or the deadline (its timeout).
ACTIONS = [
    {"kind": "mmr", "within_hours": 72, "remind_hours_before": 24, "covered_by": [],
     "label": "the MMR vaccine", "box": "MMR vaccine", "info": "mmr-window", "phase_word": "MMR window open",
     "calendar_note": "Get the MMR vaccine before this time if you are not protected.",
     "window_title": "MMR vaccine window closes",
     "reminder": "About 24 hours are left to get the MMR vaccine for this exposure. Call your doctor, pharmacy or health department today.",
     "done": "You told us you got the MMR vaccine. Keep watching for symptoms until the watch ends.",
     "closed": "The 72-hour MMR window has closed.",
     "step": "Call your doctor, pharmacy or health department today about the MMR vaccine."},
    {"kind": "ig", "within_hours": 6 * 24, "remind_hours_before": 24, "covered_by": ["mmr"],
     "label": "immune globulin", "box": "Immune globulin", "info": "ig-window", "phase_word": "Immune globulin open",
     "calendar_note": "For babies, pregnant people and people with weak immune systems who missed the MMR window.",
     "window_title": "Immune globulin window closes",
     "reminder": "About 24 hours are left for immune globulin. It matters most for babies, pregnant people and people with weak immune systems.",
     "done": "You told us you got immune globulin. Keep watching for symptoms for 28 days after the exposure.",
     "closed": "The 6-day immune globulin window has closed.",
     "step": ("The 72-hour window for the MMR vaccine has closed. Babies, pregnant people and people with weak immune "
              "systems may still get immune globulin, an antibody shot. Call your doctor today.")},
]
WATCH = {"start_days_after_first": 5, "end_days_after_last": 21,
         "end_days_after_last_if": {"ig": 28},   # after immune globulin, symptoms can start later
         "step": "Watch for fever and rash. If symptoms start, call before you go in.",
         "clear": "The watch period has ended. If you have no symptoms, no action is needed."}
MILESTONES = [
    {"kind": "watch_start", "at": "watch_start",
     "text": "From today, watch for fever, cough, runny nose, red eyes and a rash. If symptoms start, call before you go in."},
    {"kind": "all_clear", "at": "watch_end",
     "text": "The watch period has ended. If you have had no symptoms, you are in the clear for this exposure."},
]
SOURCES = {
    "mmr": {"label": "CDC: MMR vaccine within 72 hours of first exposure",
            "url": "https://www.cdc.gov/measles/hcp/vaccine-considerations/index.html"},
    "ig": {"label": "CDC: immune globulin within 6 days of first exposure",
           "url": "https://www.cdc.gov/measles/downloads/measles-immune-globulin-post-exposure-prophylaxis.pdf"},
    "watch": {"label": "CDC: watch for symptoms from day 5 after the first exposure through day 21 after the last",
              "url": "https://www.cdc.gov/infection-control/media/pdfs/Measles-Exposure-Guide-HC-Settings-508.pdf"},
}
ALERT_TITLE = "Measles exposure on your trip"
WINDOW_RULE = ("Record one window per place per continuous span of time: visits on separate days are separate windows, "
               "but one stay that runs past midnight or over several days is one window, with end_date set. A line that "
               "names several separate days for one place (for example 'Mon. & Wed.: the library') is one window for each day.")
