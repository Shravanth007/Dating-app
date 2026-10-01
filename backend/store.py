"""The app's memory: every person, agent log and date lives in one JSON file (data/demo.json)."""
import json, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor

from .config import DATA_FILE

lock = threading.RLock()          # one lock guards `state` (many agents write at once)
pool = ThreadPoolExecutor(12)     # background workers that run agents
state = json.loads(DATA_FILE.read_text("utf8")) if DATA_FILE.exists() else {}
state.setdefault("people", {}), state.setdefault("dates", {})
state["pipeline"] = {"running": False, "step": ""}


def save():
    with lock:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(state, indent=1), "utf8")


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
