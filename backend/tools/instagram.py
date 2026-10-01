"""Tool: read a public Instagram profile.

Tier 1: Apify actor apify/instagram-profile-scraper when APIFY_TOKEN is set.
Tier 2 (free): Instagram's private web API (used by instaloader and most GitHub scrapers) answers 429 to servers, but
the public profile page served to search-engine crawlers embeds the profile JSON and the recent posts' captions."""
import json, re

from .fetch import apify, http, meta_tags, trim

GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def ig_handle(url):
    """https://www.instagram.com/jane.doe/ → "jane.doe" (None if it isn't an Instagram profile URL)."""
    m = re.match(r"^https?://(www\.)?instagram\.com/([A-Za-z0-9._]{1,30})/?", (url or "").strip())
    return m and m.group(2) not in ("p", "reel", "reels", "explore", "accounts", "stories") and m.group(2)


def read_instagram(url):
    handle = ig_handle(url)
    if not handle:
        raise ValueError(f"Not an Instagram profile URL: {url}")
    try:
        item = apify("apify~instagram-profile-scraper", {"usernames": [handle]})
        if item and item.get("private"):
            raise ValueError("Instagram profile is private — only public profiles are allowed")
        if item and item.get("username"):
            posts = [{k: p.get(k) for k in ("type", "caption", "hashtags", "timestamp", "likesCount",
                                             "commentsCount", "locationName", "alt")} for p in item.get("latestPosts", [])]
            data = {k: item.get(k) for k in ("fullName", "username", "biography", "businessCategoryName", "verified",
                                             "followersCount", "followsCount", "postsCount")}
            return {"via": "apify", "name": item.get("fullName", ""), "photo": item.get("profilePicUrlHD", ""),
                    "data": trim({**data, "external_link": item.get("externalUrl"), "recent_posts": posts})}
    except ValueError:
        raise
    except Exception:
        pass  # fall through to the built-in scraper

    page = http(f"https://www.instagram.com/{handle}/", headers={"User-Agent": GOOGLEBOT})
    m = meta_tags(page)
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
        kind = kind and kind.group(1)
        posts.append({"type": {"clips": "reel", "carousel_container": "carousel", "feed": "photo"}.get(kind, kind),
                      "caption": cap.get("text", "")})
    count = re.search(r"([\d.,]+[KMB]?) Posts", m.get("og:description", ""))
    data = {"fullName": u.get("full_name"), "username": u.get("username"), "biography": u.get("biography"),
            "verified": u.get("is_verified"), "followersCount": u.get("follower_count"),
            "followsCount": u.get("following_count"), "postsCount": count and count.group(1),
            "pronouns": u.get("pronouns"), "recent_posts": posts}
    return {"via": "builtin", "name": u.get("full_name", ""), "photo": m.get("og:image", ""), "data": trim(data)}
