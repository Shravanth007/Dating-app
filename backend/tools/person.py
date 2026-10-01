"""Tool: read ONE person — their public LinkedIn and their public Instagram together.

Every successful scrape is saved to data/scrape_cache.json, so each profile is fetched from LinkedIn/Instagram only
once, ever: the hunter's read and that person's own agent share it, and it survives restarts (fewer requests → fewer
blocks, and no repeated Apify cost)."""
import json, threading

from ..config import ROOT
from .instagram import ig_handle, read_instagram
from .linkedin import li_slug, read_linkedin

CACHE_FILE = ROOT / "data" / "scrape_cache.json"
_lock = threading.Lock()
_cache = json.loads(CACHE_FILE.read_text("utf8")) if CACHE_FILE.exists() else {}


def _cached(key, fn, url):
    with _lock:
        if key in _cache:
            return _cache[key]
    result = fn(url)  # raises on failure; failures are not cached so a later retry can succeed
    with _lock:
        _cache[key] = result
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(_cache, indent=1, ensure_ascii=False), "utf8")
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
