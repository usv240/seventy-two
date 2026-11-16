import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from textutil import links  # noqa: E402

HTML = """<ul>
<li><a href="/news/releases/111326.htm">DHS Reports Second Death Connected to the Ongoing Measles Outbreak</a></li>
<li><a href="https://www.dhs.wisconsin.gov/news/releases/110626.htm">DHS Shares Possible Public Measles Exposure</a></li>
<li><a href="#top">Back to top</a> <a href="mailto:x@example.org">Email</a></li>
</ul>"""

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title><![CDATA[Measles exposure at Sky Harbor &amp; Terminal 4]]></title><link>https://www.maricopa.gov/CivicAlerts.aspx?aid=3900</link></item>
<item><title>Flu clinics open</title><link>https://www.maricopa.gov/CivicAlerts.aspx?aid=3901</link></item>
</channel></rss>"""


def test_html_links_are_absolute_and_skip_anchors():
    got = links(HTML, "https://www.dhs.wisconsin.gov/news/index.htm")
    assert got[0] == ("https://www.dhs.wisconsin.gov/news/releases/111326.htm",
                      "DHS Reports Second Death Connected to the Ongoing Measles Outbreak")
    assert len(got) == 2


def test_rss_items_unescape_cdata_titles():
    got = links(RSS, "https://www.maricopa.gov/RSSFeed.aspx")
    assert got[0] == ("https://www.maricopa.gov/CivicAlerts.aspx?aid=3900", "Measles exposure at Sky Harbor & Terminal 4")
    assert len(got) == 2


def test_norm_url_treats_trailing_slash_and_case_as_same_page():
    import ingest
    a = ingest.norm_url("https://WWW.polkcountyiowa.gov/health-department/news-and-press-releases/measles-case/")
    b = ingest.norm_url("https://www.polkcountyiowa.gov/health-department/news-and-press-releases/measles-case#top")
    assert a == b
    assert ingest.url_key(a) == ingest.url_key(b)
