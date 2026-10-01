"""Tool: read ONE person — their public LinkedIn and their public Instagram together.

Results are cached in memory for the session, so when the hunter has already read someone, that person's own agent
reads the same data without scraping LinkedIn/Instagram a second time (fewer requests → fewer blocks)."""
import threading

from .instagram import ig_handle, read_instagram
from .linkedin import li_slug, read_linkedin

_cache, _lock = {}, threading.Lock()


def _cached(key, fn, url):
    with _lock:
        if key in _cache:
            return _cache[key]
    result = fn(url)  # raises on failure; failures are not cached so a retry can succeed
    with _lock:
        _cache[key] = result
    return result


def read_person(linkedin_url, instagram_url):
    """Returns {"linkedin": {...}, "instagram": {...}, "both_public": bool}. Each side has name/photo/via/data,
    or an "error" explaining why it could not be read (private, not found, blocked…)."""
    out = {}
    for side, url, key_fn, fn in (("linkedin", linkedin_url, li_slug, read_linkedin),
                                  ("instagram", instagram_url, ig_handle, read_instagram)):
        key = key_fn(url or "")
        if not key:
            out[side] = {"error": f"not a {side} profile URL: {url!r}"}
            continue
        try:
            out[side] = _cached(f"{side}:{key.lower()}", fn, url)
        except Exception as e:
            out[side] = {"error": str(e)[:300]}
    out["both_public"] = all("data" in out[s] for s in ("linkedin", "instagram"))
    return out


def already_read(linkedin_url, instagram_url):
    """Did someone read this exact pair successfully already? (the hunter must read before it recruits)"""
    li, ig = li_slug(linkedin_url or ""), ig_handle(instagram_url or "")
    with _lock:
        return bool(li and ig and f"linkedin:{li.lower()}" in _cache and f"instagram:{ig.lower()}" in _cache)
