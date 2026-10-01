"""Agent 2 — the MATCHER: an agent scores everyone in the pool for its person; plus the final ranking formula."""
import json

from pydantic import BaseModel

from ..llm import ask
from ..store import lock, set_status, state
from .dater import persona

PLURAL = {"man": "men", "men": "men", "woman": "women", "women": "women"}


class Match(BaseModel):
    id: str
    fit: int  # 0-100
    reason: str


class Matches(BaseModel):
    matches: list[Match]


def open_to(p, q):
    """Is p open to dating q? (gender filter from onboarding; unknown gender never excludes anyone)"""
    s, g = p.get("seeking") or "everyone", q.get("gender", "")
    return s == "everyone" or g not in PLURAL or PLURAL[g] == s


def compatible(p, q):
    return p["id"] != q["id"] and open_to(p, q) and open_to(q, p)


def card(q):
    """The short version of a profile other agents get to see."""
    f = q["profile"]
    return {"id": q["id"], "name": q["name"], "headline": f["headline"], "summary": f["summary"],
            "hobbies": f["hobbies"], "interests": f["interests"], "values": f["values"],
            "looking_for": f["looking_for"], "lifestyle": f["lifestyle"]}


def search(pid):
    """The agent scores every compatible person in the pool against its partner brief."""
    p = state["people"][pid]
    candidates = [card(q) for q in state["people"].values() if q.get("profile") and compatible(p, q)]
    if not candidates:
        return
    set_status(pid, "searching…")
    try:
        res = ask(persona(p), "You are now searching the dating pool for your person. Using your partner brief, score "
                  "EVERY candidate below 0-100 for how well they fit your person (be discerning: spread the scores, "
                  "most should not be above 80) with a one-sentence specific reason.\n\nCandidates:\n"
                  + json.dumps(candidates, ensure_ascii=False), Matches, effort="medium", max_tokens=12000)
        ids = {c["id"] for c in candidates}
        with lock:
            p["search"] = {"matches": sorted((m for m in res["matches"] if m["id"] in ids),
                                             key=lambda m: m["fit"], reverse=True)}
        set_status(pid, "ready")
    except Exception as e:
        set_status(pid, f"error: search: {e}")


def ranking(pid, people, dates):
    """Who fits `pid` best. Dated pairs: 60% my agent's debrief + 40% theirs (it has to be mutual).
    Not dated yet: my agent's pre-date search score."""
    p, rows = people[pid], []
    fits = {m["id"]: m for m in (p.get("search") or {}).get("matches", [])}
    for q in people.values():
        if not q.get("profile") or not compatible(p, q):
            continue
        d = next((d for d in dates.values() if {d["a"], d["b"]} == {pid, q["id"]} and d["status"] == "done"), None)
        if d:
            mine, theirs = d["verdicts"][pid], d["verdicts"][q["id"]]
            rows.append({"id": q["id"], "score": round(0.6 * mine["score"] + 0.4 * theirs["score"]), "stage": "dated",
                         "date": d["id"], "why": mine["summary"], "wants_to_meet": mine["wants_to_meet"] and theirs["wants_to_meet"]})
        elif q["id"] in fits:
            rows.append({"id": q["id"], "score": fits[q["id"]]["fit"], "stage": "pre-date", "date": None,
                         "why": fits[q["id"]]["reason"], "wants_to_meet": False})
    return sorted(rows, key=lambda r: r["score"], reverse=True)
