"""Tool: read a public LinkedIn profile.

Tier 1: Apify actor harvestapi/linkedin-profile-scraper (no cookies) when APIFY_TOKEN is set.
Tier 2 (free): LinkedIn's public guest page embeds a schema.org Person graph (headline, location, about, experience,
education, languages) plus the person's recent posts and articles. Browsers get rate-limited (HTTP 999) quickly;
link-preview crawlers are still served the public page, so we rotate through them."""
import json, re, time, urllib.error

from .fetch import CHROME, apify, http, meta_tags, trim


def li_slug(url):
    """https://www.linkedin.com/in/jane-doe/ → "jane-doe" (None if it isn't a LinkedIn profile URL)."""
    m = re.match(r"^https?://([a-z]{2,3}\.)?(www\.)?linkedin\.com/in/([A-Za-z0-9\-_%]+)/?", (url or "").strip())
    return m and m.group(3)


def read_linkedin(url):
    slug = li_slug(url)
    if not slug:
        raise ValueError(f"Not a LinkedIn profile URL: {url}")
    url = f"https://www.linkedin.com/in/{slug}/"  # rebuilt from the slug: we never fetch arbitrary URLs
    try:
        item = apify("harvestapi~linkedin-profile-scraper",
                     {"profileScraperMode": "Profile details no email ($4 per 1k)", "queries": [url]})
        if item:
            name = item.get("fullName") or " ".join(filter(None, (item.get("firstName"), item.get("lastName"))))
            photo = item.get("photo") or item.get("profilePicture") or ""
            return {"via": "apify", "name": name, "photo": photo if isinstance(photo, str) else "", "data": trim(item)}
    except Exception:
        pass  # fall through to the built-in scraper

    page, err = "", None
    for attempt, ua in enumerate([CHROME, "facebookexternalhit/1.1", "Twitterbot/1.0", "facebookexternalhit/1.1"]):
        try:
            page = http(url, headers={"User-Agent": ua})
            if '"@type":"Person"' in page:
                break
        except urllib.error.HTTPError as e:
            err = e
            if e.code == 404:
                raise ValueError("LinkedIn profile not found")
        time.sleep(attempt)
    if not page and err:
        raise ValueError(f"LinkedIn blocked the request (HTTP {err.code})")

    m = meta_tags(page)
    nodes = []
    for ld in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            d = json.loads(ld)
            nodes += d.get("@graph", [d])
        except ValueError:
            pass
    person = next((n for n in nodes if n.get("@type") == "Person"), None)
    if not person:
        raise ValueError("LinkedIn returned no public profile (login wall or wrong URL)")
    org = lambda o: {"name": o.get("name", "").strip(), "start": o.get("member", {}).get("startDate"),
                     "end": o.get("member", {}).get("endDate")}
    title = m.get("og:title", "")
    data = {
        "name": person.get("name"),
        "headline": title.split(" - ", 1)[1].rsplit(" | ", 1)[0] if " - " in title else "",
        "location": person.get("address", {}).get("addressLocality"),
        "about": person.get("description"),
        "badges": person.get("disambiguatingDescription"),
        "followers": person.get("interactionStatistic", {}).get("userInteractionCount"),
        "job_titles": person.get("jobTitle"),
        "experience": [org(o) for o in person.get("worksFor", [])],
        "education": [org(o) for o in person.get("alumniOf", [])],
        "languages": [l.get("name", l) if isinstance(l, dict) else l for l in person.get("knowsLanguage", [])],
        "awards": person.get("awards"),
        "posts": [{"date": n.get("datePublished", "")[:10], "text": (n.get("text") or n.get("headline") or "")[:600]}
                  for n in nodes if n.get("@type") == "DiscussionForumPosting"],
        "articles": [n.get("headline") for n in nodes if n.get("@type") == "Article"],
        "summary_line": m.get("description", ""),
    }
    return {"via": "builtin", "name": person.get("name", ""), "photo": m.get("og:image", ""), "data": trim(data)}
