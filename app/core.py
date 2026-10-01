"""Shared config, state and HTTP helper.

Env: OPENROUTER_API_KEY (required), MODEL (default anthropic/claude-opus-5.5),
     APIFY_TOKEN (optional: Apify scrapers first, built-in scrapers as fallback)."""
import json, os, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODEL = os.environ.get("MODEL", "anthropic/claude-opus-5.5")
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_FILE", ROOT / "data" / "demo.json"))
DATES_PER_PERSON = 3  # ponytail: each agent dates its top-3 search hits, not all n² pairs; raise for a full round robin
DATE_TURNS = 8
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
BOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

lock = threading.RLock()
pool = ThreadPoolExecutor(12)
state = json.loads(DATA.read_text("utf8")) if DATA.exists() else {}
state.setdefault("people", {}), state.setdefault("dates", {})
state["pipeline"] = {"running": False, "step": ""}


def save():
    with lock:
        DATA.write_text(json.dumps(state, indent=1), "utf8")


def http(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data and json.dumps(data).encode(),
                                 headers={"User-Agent": UA, "Accept-Language": "en-US,en", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf8", "replace")
