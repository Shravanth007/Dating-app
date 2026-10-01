"""'Run everyone': every agent in the pool searches, then each dates its top matches, so every person gets a ranking."""
import time
from concurrent.futures import wait

from .agents.dater import start_date
from .agents.matcher import search
from .config import DATES_PER_PERSON
from .store import pool, save, state


def run_everyone():
    step = lambda s: state["pipeline"].update(running=s != "done" and not s.startswith("error"), step=s)
    try:
        step("reading people")
        while any(p["status"] != "ready" and not p["status"].startswith("error") for p in state["people"].values()):
            time.sleep(2)
        step("agents searching")
        ready = [p["id"] for p in state["people"].values() if p.get("profile")]
        wait([pool.submit(search, pid) for pid in ready])
        step("agents dating")
        pairs = {tuple(sorted((pid, m["id"]))) for pid in ready
                 for m in (state["people"][pid].get("search") or {}).get("matches", [])[:DATES_PER_PERSON]}
        wait([f for f in (start_date(a, b)[1] for a, b in pairs) if f])
        step("done")
    except Exception as e:
        step(f"error: {e}")
    save()
