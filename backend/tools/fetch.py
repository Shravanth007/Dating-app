"""Small helpers shared by the scraping tools."""
import html, json, re, urllib.request

from ..config import APIFY_TOKEN

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"


def http(url, data=None, headers=None, timeout=30):
    """GET (or POST JSON when `data` is given) and return the body as text."""
    req = urllib.request.Request(url, data=data and json.dumps(data).encode(),
                                 headers={"User-Agent": CHROME, "Accept-Language": "en-US,en", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf8", "replace")


def meta_tags(page):
    """<meta property="og:title" content="..."> → {"og:title": "..."}"""
    return {m.group(1): html.unescape(m.group(2)) for m in
            re.finditer(r'<meta[^>]+(?:property|name)="([^"]+)"[^>]+content="([^"]*)"', page)}


def trim(x):
    """Drop media/id noise and empty values, cap sizes, so scraped JSON is readable and fits in a prompt."""
    empty = (None, "", [], {})
    if isinstance(x, dict):
        out = {k: trim(v) for k, v in x.items()
               if not re.search(r"(url|urn|id|image|pic|picture|logo|thumbnail|cursor|token|hash)s?$", k, re.I)}
        return {k: v for k, v in out.items() if v not in empty}
    if isinstance(x, list):
        return [v for v in (trim(v) for v in x) if v not in empty][:15]
    return x[:1500] if isinstance(x, str) else x


def apify(actor, payload):
    """Run an Apify actor synchronously and return its first result (None when no APIFY_TOKEN is set)."""
    if not APIFY_TOKEN:
        return None
    items = json.loads(http(f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={APIFY_TOKEN}",
                            payload, {"Content-Type": "application/json"}, timeout=240))
    return items[0] if items else None
