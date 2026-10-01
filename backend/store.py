"""The app's memory: every person, agent log, chat and date lives in one JSON document.

Locally it is the file data/state.json. On Vercel (serverless: no lasting disk, no background threads) it is kept in
Upstash Redis (connect it in Vercel → Storage; KV_REST_API_URL / KV_REST_API_TOKEN are injected automatically)."""
import json, os, threading, time, uuid
from concurrent.futures import Future, ThreadPoolExecutor

from .config import BUDGET_USD, DATA_FILE, WORKERS

ON_VERCEL = os.environ.get("VERCEL") == "1"
REDIS_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
KEY = "proxyhearts:state"
lock = threading.RLock()  # one lock guards `state` (many agents write at once)
_active = [0]             # jobs running in this process (don't reload state from Redis under their feet)


def _redis(*cmd):
    import urllib.request
    req = urllib.request.Request(REDIS_URL, data=json.dumps(list(cmd)).encode(),
                                 headers={"Authorization": f"Bearer {REDIS_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("result")


def _load():
    if REDIS_URL:
        raw = _redis("GET", KEY)
        return json.loads(raw) if raw else {}
    return json.loads(DATA_FILE.read_text("utf8")) if DATA_FILE.exists() else {}


def _defaults(s):
    s.setdefault("people", {}), s.setdefault("dates", {}), s.setdefault("spend", {"usd": 0.0, "calls": 0})
    s["spend"]["budget"] = BUDGET_USD
    s.setdefault("pipeline", {"running": False, "step": ""})
    return s


state = _defaults(_load())
if not ON_VERCEL:
    state["pipeline"] = {"running": False, "step": ""}


def reload():
    """Serverless: each request may land on a fresh instance, so read the latest shared state first."""
    if REDIS_URL and not _active[0]:
        with lock:
            fresh = _defaults(_load())
            state.clear()
            state.update(fresh)


def save():
    with lock:
        data = json.dumps(state, indent=None if REDIS_URL else 1)
    if REDIS_URL:
        _redis("SET", KEY, data)
    else:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(data, "utf8")


class _Inline:
    """On Vercel a function stops when its response is sent, so 'background' work runs inside the request."""
    def submit(self, fn, *args):
        f = Future()
        _active[0] += 1
        try:
            f.set_result(fn(*args))
        except Exception as e:
            f.set_exception(e)
        finally:
            _active[0] -= 1
        return f


class _Threads(ThreadPoolExecutor):
    def submit(self, fn, *args):
        def run():
            _active[0] += 1
            try:
                return fn(*args)
            finally:
                _active[0] -= 1
        return super().submit(run)


pool = _Inline() if ON_VERCEL else _Threads(WORKERS)  # workers that run agents


def new_person(kind, linkedin="", instagram="", li_text="", ig_text="", **extra):
    """kind: "user" (signed up via onboarding) or "candidate" (found by an agent or added by link)."""
    p = {"id": uuid.uuid4().hex[:8], "kind": kind, "name": "", "linkedin": linkedin, "instagram": instagram,
         "photo": "", "gender": "", "seeking": "everyone", "onboarding": None, "found_by": None, "found_reason": "",
         "sources": {"linkedin": {"via": "", "data": {}, "error": "", "pasted": (li_text or "")[:20000]},
                     "instagram": {"via": "", "data": {}, "error": "", "pasted": (ig_text or "")[:20000]}},
         "profile": None, "search": None, "status": "queued", "log": [], "hunt": None, **extra}
    with lock:
        state["people"][p["id"]] = p
    save()
    return p


def log(pid, text):
    """Append a line to a person's live agent activity feed (shown in the UI)."""
    with lock:
        if pid in state["people"]:
            lines = state["people"][pid].setdefault("log", [])
            lines.append({"t": int(time.time()), "text": str(text)[:400]})
            del lines[:-300]
    save()


def set_status(pid, status):
    with lock:
        if pid in state["people"]:
            state["people"][pid]["status"] = status[:300]
    save()
