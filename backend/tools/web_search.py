"""Tool: search the web. No model cost: it returns raw results, the agent does the thinking.

Providers, first one configured wins (all have free tiers, no credit card):
  SERPER_API_KEY  → Google results via serper.dev (2,500 free searches; best at finding exact profile URLs)
  TAVILY_API_KEY  → tavily.com (1,000 free searches / month)
  (none)          → DuckDuckGo's HTML page (free, no key, but it rate-limits scripts quickly)
Besides the results, it lists every LinkedIn / Instagram profile URL it spotted."""
import html, json, os, re, urllib.parse

from .fetch import http
from .instagram import ig_handle
from .linkedin import li_slug


def _text(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def _serper(query, n):
    r = http("https://google.serper.dev/search", {"q": query, "num": n},
             {"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"})
    return [{"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
            for x in json.loads(r).get("organic", [])]


def _tavily(query, n):
    r = http("https://api.tavily.com/search", {"query": query, "max_results": n},
             {"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}", "Content-Type": "application/json"})
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": (x.get("content") or "")[:300]}
            for x in json.loads(r).get("results", [])]


def _duckduckgo(query, n):
    page = http(f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}")
    if "anomaly" in page.lower():
        raise RuntimeError("DuckDuckGo is rate-limiting this machine; set SERPER_API_KEY or TAVILY_API_KEY (free)")
    out = []
    for href, title, snippet in re.findall(
            r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', page, re.S):
        href = html.unescape(href)
        url = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        out.append({"title": _text(title), "url": url, "snippet": _text(snippet)})
    return out[:n]


def web_search(query, max_results=10):
    if os.environ.get("SERPER_API_KEY"):
        results = _serper(query, max_results)
    elif os.environ.get("TAVILY_API_KEY"):
        results = _tavily(query, max_results)
    else:
        results = _duckduckgo(query, max_results)
    urls = [r["url"] for r in results]
    return {"results": results,
            "linkedin_profiles": sorted({f"https://www.linkedin.com/in/{li_slug(u)}/" for u in urls if li_slug(u)}),
            "instagram_profiles": sorted({f"https://www.instagram.com/{ig_handle(u)}/" for u in urls if ig_handle(u)})}
