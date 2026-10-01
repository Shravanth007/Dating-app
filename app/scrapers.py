"""Scrapers for the only two sources: public LinkedIn + public Instagram.
Apify actors first (when APIFY_TOKEN is set), built-in scrapers as the free fallback."""
import html, json, os, re, time, urllib.error

from .core import BOT, UA, http


def li_slug(url):
    m = re.match(r"^https?://([a-z]{2,3}\.)?(www\.)?linkedin\.com/in/([A-Za-z0-9\-_%]+)/?", url.strip())
    return m and m.group(3)


def ig_handle(url):
    m = re.match(r"^https?://(www\.)?instagram\.com/([A-Za-z0-9._]{1,30})/?", url.strip())
    return m and m.group(2) not in ("p", "reel", "reels", "explore", "accounts", "stories") and m.group(2)


def metas(page):
    return {m.group(1): html.unescape(m.group(2)) for m in
            re.finditer(r'<meta[^>]+(?:property|name)="([^"]+)"[^>]+content="([^"]*)"', page)}


def trim(x, depth=0):
    """Drop media/id noise and cap sizes so scraper JSON is readable and fits the prompt."""
    empty = (None, "", [], {})
    if isinstance(x, dict):
        out = {k: trim(v, depth + 1) for k, v in x.items()
               if not re.search(r"(url|urn|id|image|pic|picture|logo|thumbnail|cursor|token|hash)s?$", k, re.I)}
        return {k: v for k, v in out.items() if v not in empty}
    if isinstance(x, list):
        return [v for v in (trim(v, depth + 1) for v in x) if v not in empty][:15]
    return x[:1500] if isinstance(x, str) else x


def apify(actor, payload):
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        return None
    items = json.loads(http(f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}",
                            payload, {"Content-Type": "application/json"}, timeout=240))
    return items[0] if items else None


def linkedin(slug):
    url = f"https://www.linkedin.com/in/{slug}/"
    try:
        item = apify("harvestapi~linkedin-profile-scraper",
                     {"profileScraperMode": "Profile details no email ($4 per 1k)", "queries": [url]})
        if item:
            name = item.get("fullName") or " ".join(filter(None, (item.get("firstName"), item.get("lastName"))))
            photo = item.get("photo") or item.get("profilePicture") or ""
            return {"via": "apify", "name": name, "photo": photo if isinstance(photo, str) else "", "data": trim(item)}
    except Exception:
        pass  # fall through to the built-in scraper
    # Built-in: LinkedIn's public guest page embeds a schema.org Person graph + recent posts. Browsers get rate-limited
    # (HTTP 999) quickly; link-preview crawlers are still served the full public page, so rotate through them.
    page, err = "", None
    for attempt, ua in enumerate([UA, "facebookexternalhit/1.1", "Twitterbot/1.0", "facebookexternalhit/1.1"]):
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
        raise ValueError(f"LinkedIn blocked the request (HTTP {err.code}) — paste the public profile text instead")
    m = metas(page)
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


def instagram(handle):
    try:
        item = apify("apify~instagram-profile-scraper", {"usernames": [handle]})
        if item and item.get("private"):
            raise ValueError("Instagram profile is private — only public profiles are allowed")
        if item and item.get("username"):
            posts = [{k: p.get(k) for k in ("type", "caption", "hashtags", "timestamp", "likesCount",
                                             "commentsCount", "locationName", "alt")} for p in item.get("latestPosts", [])]
            data = {k: item.get(k) for k in ("fullName", "username", "biography", "businessCategoryName", "verified",
                                             "followersCount", "followsCount", "postsCount", "externalUrl")}
            data["externalUrl"] = data["externalUrl"] or None
            return {"via": "apify", "name": item.get("fullName", ""), "photo": item.get("profilePicUrlHD", ""),
                    "data": trim({**data, "external_link": item.get("externalUrl"), "recent_posts": posts})}
    except ValueError:
        raise
    except Exception:
        pass
    # Built-in: IG's private API 429s from servers (so do instaloader & co.); the public profile page served to
    # crawlers embeds the profile JSON and the recent posts.
    page = http(f"https://www.instagram.com/{handle}/", headers={"User-Agent": BOT})
    m = metas(page)
    i = page.find('"xig_user_by_igid_v2":')
    if i < 0:
        raise ValueError("Instagram profile not found or not public")
    u = json.JSONDecoder().raw_decode(page, i + len('"xig_user_by_igid_v2":'))[0]
    if u.get("is_private"):
        raise ValueError("Instagram profile is private — only public profiles are allowed")
    posts, seen = [], set()
    for c in re.finditer(r'"caption":\{"pk"', page):
        cap = json.JSONDecoder().raw_decode(page, c.start() + len('"caption":'))[0]
        if cap["pk"] in seen:
            continue
        seen.add(cap["pk"])
        kind = re.search(r'"product_type":"(\w+)"', page[c.end():c.end() + 3000])
        posts.append({"type": {"clips": "reel", "carousel_container": "carousel", "feed": "photo"}.get(
            kind and kind.group(1), kind and kind.group(1)), "caption": cap.get("text", "")})
    count = re.search(r"([\d.,]+[KMB]?) Posts", m.get("og:description", ""))
    data = {"fullName": u.get("full_name"), "username": u.get("username"), "biography": u.get("biography"),
            "verified": u.get("is_verified"), "followersCount": u.get("follower_count"),
            "followsCount": u.get("following_count"), "postsCount": count and count.group(1),
            "pronouns": u.get("pronouns"), "recent_posts": posts}
    return {"via": "builtin", "name": u.get("full_name", ""), "photo": m.get("og:image", ""), "data": trim(data)}
