# Reuse check: hepatitis A

The same reader and the same evidence checks, run with `DOMAIN=hepatitis_a` (`src/domains/hepatitis_a.py`) on two real
hepatitis A food-worker notices. Run: `python bench/reuse.py`. Model: Claude Haiku 4.5 on Amazon Bedrock.

| Notice | Exposure in the notice | Window Seventy-Two kept | Dates exact | Deadline computed |
|---|---|---|---|---|
| Gwinnett, Newton and Rockdale County Health Departments, Georgia, November 15, 2023 | Lolita, 2023-10-24 to 2023-11-07 | Lolita's Bar and Grill, 2023-10-24 to 2023-11-07 | yes | vaccine within 14 days of the first day |
| NYC Health Department, February 14, 2025 | ilili, 2025-01-17 to 2025-02-09 | ilili Restaurant, 2025-01-17 to 2025-02-09 | yes | vaccine within 14 days of the first day |

Model cost: $0.0086. Rejected by the checks: 0.

The NYC notice gives two ranges: everyone who ate there from January 17 to February 9 should watch for symptoms,
and those from January 31 should also get the vaccine (on February 14, that was everyone still within 14 days).
The exposure is the wider range, and the domain file says so: at temperature 0 the reader otherwise chose the
vaccine range. Telling the reader how a disease's notices are written is part of reusing the pattern.
