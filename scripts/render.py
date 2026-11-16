"""Render web/ into build/site/: adds the shared header and footer to every page. Standard library only."""
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC, OUT = ROOT / "web", ROOT / "build" / "site"

NAV = [("Board", "/board/"), ("Lab", "/lab/"), ("Learn", "/learn/"), ("Stack", "/stack/"), ("Ops", "/ops/")]

HEAD = """<!doctype html>
<html lang="en" class="no-js">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="theme-color" content="#111111">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' fill='%23111'/%3E%3Ctext x='32' y='47' font-family='Arial Narrow,Arial' font-weight='700' font-size='38' text-anchor='middle' fill='%23f1c14b'%3E72%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/site.css">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/assets/icon-192.png">
<script>try{{var t=localStorage.getItem('seventytwo-theme');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-head">
  <div class="wrap">
    <a class="wordmark" href="/">Seventy-Two</a>
    <button class="menu-btn" type="button" aria-expanded="false" aria-controls="site-nav">Menu</button>
    <nav class="site-nav" id="site-nav" aria-label="Main">
      {nav}
      <button class="theme-toggle" type="button">Light</button>
      <a class="btn small" href="/check/">Check where you were</a>
    </nav>
  </div>
</header>
"""

FOOT = """<footer class="site-foot">
  <div class="wrap">
    <p><strong>Seventy-Two</strong> reads public measles exposure notices and applies the CDC's timing rules. It is not medical advice. Always follow your health department and your doctor.</p>
    <p>Questions about measles? Call the CDC's information line, or your local health department.</p>
    <a class="call num" href="tel:18002324636">CDC-INFO <span style="white-space:nowrap">800-232-4636</span></a>
    <div class="meta">
      <p><a href="/learn/#built">How it is built</a><br><a href="/stack/">The full stack</a><br><a href="/learn/#privacy">Privacy</a><br><a href="/board/#status">Live status</a><br><a href="/ops/">Ops: live metrics</a><br><a href="/learn/#references">References</a></p>
      <p>Pictures: {credits}</p>
      <p>United States, 2026. Built on AWS serverless for the Builder Center weekend challenge "No Pipeline, No Problem".</p>
    </div>
  </div>
</footer>
<script type="module" src="/assets/site.js"></script>
{scripts}
</body>
</html>
"""


def credits():
    c = json.loads((SRC / "assets" / "credits.json").read_text(encoding="utf-8"))
    used = {"board.jpg": "48 Aviation industry in Australia", "terminal-night.jpg": "Oslo Airport terminal night view",
            "poster-cdc.jpg": "Keep measles a memory", "poster-1939.jpg": "Measles awareness poster"}
    out = []
    for item in c:
        for fname, needle in used.items():
            if needle.lower() in (item.get("title") or "").lower():
                out.append(f'<a href="{item["page"]}">{item["title"]}</a>, {item.get("creator") or "unknown"}, {item["license"]}')
    return "; ".join(out)


def live_stats(site_url):
    """The status panel's numbers at deploy time, so a page read without JavaScript is never out of date."""
    if not site_url:
        return {}
    try:
        import urllib.request
        with urllib.request.urlopen(site_url.rstrip("/") + "/api/status", timeout=20) as r:
            st = json.load(r)
    except Exception:
        return {}
    src = st.get("sources") or []
    n = st.get("with_publication_date")
    return {"issuers": len({x.get("issuer") for x in src}), "states": len({x.get("state") for x in src if x.get("state")}),
            "sources": sum(1 for x in src if not x.get("found_on")), "lag": st.get("lag_days_median"), "samples": n,
            "mmrClosed": f"{st.get('mmr_closed_before_publication')} of {n}", "igClosed": f"{st.get('ig_closed_before_publication')} of {n}"}


def bake(html, stats):
    """Writes each live number into its data-stat element; the page's script still refreshes them."""
    for name, value in stats.items():
        if value is not None:
            html = re.sub(rf'(data-stat="{name}">)[^<]*(<)', rf"\g<1>{value}\g<2>", html)
    return html


def domain_config():
    """The disease's actions and watch period, from src/domains/<DOMAIN>.py, for the page scripts."""
    import os
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    import rules
    return {"disease": rules.DISEASE, "actions": rules.public_actions(),
            "watch": {"start_days": rules.WATCH_START_DAY, "end_days": rules.WATCH_END_DAY}, "domain": os.environ.get("DOMAIN", "measles")}


def render(site_url=""):
    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(SRC, OUT)
    (OUT / "data").mkdir(exist_ok=True)
    for name in ("scale.json", "drills.json"):   # the logged runs, so pages quote a run, not a hope
        if (ROOT / "docs" / name).exists():
            shutil.copy(ROOT / "docs" / name, OUT / "data" / name)
    cred = credits()
    stats = live_stats(site_url)
    config = json.dumps(domain_config()).replace("</", "<\\/")
    for page in OUT.rglob("*.html"):
        src = page.read_text(encoding="utf-8")
        m = re.match(r"<!--\s*page\s+(\{.*?\})\s*-->\s*", src, re.S)
        if not m:
            continue
        meta = json.loads(m.group(1))
        body = src[m.end():]
        nav = "\n      ".join(
            f'<a href="{href}"{" aria-current=\"page\"" if meta.get("nav") == label.lower() else ""}>{label}</a>'
            for label, href in NAV)
        scripts = "".join(f'<script type="module" src="{s}"></script>\n' for s in meta.get("scripts", []))
        head = HEAD.format(title=meta["title"], description=meta["description"], nav=nav)
        head = head.replace("</head>", f"<script>window.SEVENTY_TWO = {config};</script>\n</head>", 1)
        html = head + bake(body, stats) + FOOT.format(credits=cred, scripts=scripts)
        page.write_text(html, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print(render())
