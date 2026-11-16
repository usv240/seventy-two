"""Hepatitis A, the second instance of the pattern: a restaurant or event posts that a food worker had hepatitis A.

Deploy it with DOMAIN=hepatitis_a and a sources.json of hepatitis A notices. Nothing outside this file changes
on the server: the reader's checks, the matcher, the clocks and the forgetting are the same code. (The website's
words are written for measles and would be rewritten.) The rules follow CDC guidance: hepatitis A vaccine or
immune globulin within 2 weeks of exposure; symptoms usually start 15 to 50 days after it.
"""
DISEASE = "hepatitis A"
DISCOVER_WORDS = ("hepatitis a", "hepatitis-a", "hep a")

READER_ROLE = ("You read public health notices about hepatitis A exposures, usually at a restaurant or event where an "
               "infected food worker served food, and record the exposure windows they list.")
NOT_A_NOTICE = "A page about case counts, vaccine clinics, outbreak summaries or general advice, with no specific places and dates"

ACTIONS = [
    {"kind": "hepa_vaccine", "within_hours": 14 * 24, "remind_hours_before": 48, "covered_by": [],
     "label": "the hepatitis A vaccine", "box": "Hepatitis A vaccine", "info": "hepa-window", "phase_word": "Vaccine window open",
     "window_title": "Hepatitis A vaccine window closes",
     "reminder": "Two days are left to get the hepatitis A vaccine for this exposure. Call your doctor, pharmacy or health department.",
     "done": "You told us you got the hepatitis A vaccine. Watch for symptoms until the watch ends.",
     "closed": "The 2-week window for post-exposure protection has closed.",
     "step": "Call your doctor, pharmacy or health department about the hepatitis A vaccine. It works best as soon as possible."},
    {"kind": "ig", "within_hours": 14 * 24, "remind_hours_before": 48, "covered_by": ["hepa_vaccine"],
     "label": "immune globulin", "box": "Immune globulin", "info": "ig-window", "phase_word": "Immune globulin open",
     "window_title": "Immune globulin window closes",
     "reminder": "Two days are left for immune globulin, for babies under 12 months and people who cannot have the vaccine.",
     "done": "You told us you got immune globulin. Watch for symptoms until the watch ends.",
     "closed": "The 2-week immune globulin window has closed.",
     "step": "Babies under 12 months and people who cannot have the vaccine may get immune globulin instead. Call your doctor."},
]
WATCH = {"start_days_after_first": 15, "end_days_after_last": 50, "end_days_after_last_if": {},
         "step": "Watch for fever, tiredness, nausea, dark urine and yellow skin or eyes. If they start, call your doctor.",
         "clear": "The watch period has ended. If you have no symptoms, no action is needed."}
MILESTONES = [
    {"kind": "watch_start", "at": "watch_start",
     "text": "From today, watch for fever, tiredness, nausea, dark urine and yellow skin or eyes. If they start, call your doctor."},
    {"kind": "all_clear", "at": "watch_end",
     "text": "The watch period has ended. If you have had no symptoms, you are in the clear for this exposure."},
]
SOURCES = {
    "hepa_vaccine": {"label": "CDC: hepatitis A vaccine or immune globulin within 2 weeks of exposure",
                     "url": "https://www.cdc.gov/hepatitis-a/hcp/clinical-overview/index.html"},
    "ig": {"label": "CDC: immune globulin for those who cannot be vaccinated, within 2 weeks",
           "url": "https://www.cdc.gov/hepatitis-a/hcp/clinical-overview/index.html"},
    "watch": {"label": "CDC: symptoms begin 15 to 50 days after exposure",
              "url": "https://www.cdc.gov/hepatitis-a/hcp/clinical-overview/index.html"},
}
ALERT_TITLE = "Hepatitis A exposure on your trip"
WINDOW_RULE = ("Record one window per place. When the notice gives a range of days (for example 'from Oct. 24 to Nov. 7'), "
               "set date to the first day and end_date to the last, and leave the times null unless hours are written. "
               "If it gives two ranges, a wider one for watching for symptoms and a narrower one for getting vaccinated, "
               "the exposure is the wider range: record that one, and quote it. Vaccine deadlines are worked out per person.")
