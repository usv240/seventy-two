"""HTML to readable text, and the links on a page or feed. Standard library only."""
import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

SKIP = {"script", "style", "noscript", "svg", "head", "nav", "form", "button", "iframe"}
BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table",
         "section", "article", "ul", "ol", "header", "footer", "blockquote", "dd", "dt"}
CELL = {"td", "th"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        elif tag in BLOCK:
            self.parts.append("\n")
        elif tag in CELL:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html):
    p = _Text()
    p.feed(html)
    text = "".join(p.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found, self.href, self.text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href, self.text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.found.append((self.href, " ".join("".join(self.text).split())))
            self.href = None


def links(page, base):
    """(absolute url, link text) for every link on an HTML page, or every item in an RSS or Atom feed."""
    head = page[:1000].lower()
    if "<rss" in head or "<feed" in head:
        items = re.findall(r"<(?:item|entry)\b.*?</(?:item|entry)>", page, re.S | re.I)
        out = []
        for it in items:
            title = re.search(r"<title[^>]*>(.*?)</title>", it, re.S | re.I)
            link = re.search(r"<link[^>]*>(.*?)</link>", it, re.S | re.I) or re.search(r"<link[^>]*href=\"([^\"]+)\"", it, re.I)
            if link:
                clean = lambda s: html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", s)).strip()
                out.append((urljoin(base, clean(link.group(1))), clean(title.group(1)) if title else ""))
        return out
    p = _Links()
    p.feed(page)
    return [(urljoin(base, h.strip()), t) for h, t in p.found if h and not h.startswith(("#", "mailto:", "javascript:"))]


def normalize(s):
    """Lowercase, unify quotes and dashes, collapse whitespace. Used to check quotes."""
    s = s.lower()
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                                   "–": "-", "—": "-", "‒": "-", " ": " "}))
    return re.sub(r"\s+", " ", s).strip()
