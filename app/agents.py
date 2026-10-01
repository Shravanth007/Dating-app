"""The agents: read the person -> search the pool -> date -> debrief, plus ranking + full pipeline."""
import json, time, uuid
from concurrent.futures import wait

from pydantic import BaseModel

from .core import DATE_TURNS, DATES_PER_PERSON, lock, pool, save, state
from .llm import llm
from .scrapers import ig_handle, instagram, li_slug, linkedin


# ---------------- agent step 1: learn who it represents ----------------

class Evidence(BaseModel):
    source: str  # "LinkedIn" | "Instagram"
    signal: str  # what was observed
    inference: str  # what the agent concluded


class PartnerBrief(BaseModel):
    ideal_partner: str
    must_haves: list[str]
    nice_to_haves: list[str]
    avoid: list[str]


class Profile(BaseModel):
    name: str
    headline: str
    summary: str
    needs: list[str]
    hobbies: list[str]
    interests: list[str]
    values: list[str]
    personality: list[str]
    communication_style: str
    lifestyle: str
    voice: str
    looking_for: str
    partner_brief: PartnerBrief
    green_flags: list[str]
    possible_dealbreakers: list[str]
    ideal_first_date: str
    evidence: list[Evidence]
    data_confidence: str


ANALYZE_SYS = """You are a personal AI dating agent. Before you can date on someone's behalf you must deeply understand
who you represent. Your ONLY two sources are their public LinkedIn and public Instagram (scraped JSON below). Use nothing
else - no outside or prior knowledge about them, even if they are famous.

Build their profile:
- needs: what they need from a partner and a relationship (infer from work rhythm, values, what they post about)
- hobbies, interests, values, personality traits, communication_style, lifestyle (pace, travel, routines)
- voice: how they actually talk/write (tone, phrases, emoji use) taken from their captions and posts - you will speak in it
- looking_for + partner_brief: the kind of person you will search for on their behalf (ideal partner, must-haves,
  nice-to-haves, what to avoid)
- green_flags, possible_dealbreakers, ideal_first_date (specific, grounded in their interests)
- evidence: 6-12 items of source -> concrete signal (quote or fact) -> inference
- data_confidence: how much the sources revealed and what is guessed
Be specific, not generic: cite real details. Do not infer sexual orientation, religion, health or ethnicity."""


def analyze(pid):
    p = state["people"][pid]
    try:
        for key, slug_fn, scrape in (("linkedin", li_slug, linkedin), ("instagram", ig_handle, instagram)):
            src = p["sources"][key]
            set_status(pid, f"reading {key}…")
            try:
                got = scrape(slug_fn(p[key]))
                with lock:
                    src.update(via=got["via"], data=got["data"], error="")
                    p["name"] = p["name"] or got["name"]
                    p["photo"] = p["photo"] or got["photo"]
            except Exception as e:
                src.update(via="pasted" if src.get("pasted") else "", data={}, error=str(e)[:300])
                if not src.get("pasted"):
                    raise ValueError(f"{key}: {e}")
        set_status(pid, "analyzing…")
        srcs = "\n\n".join(f"=== {k.title()} ({p[k]}) ===\n{json.dumps(s['data'], ensure_ascii=False)}"
                           + (f"\nText copied from the public profile:\n{s['pasted']}" if s.get("pasted") else "")
                           for k, s in p["sources"].items())
        prof = llm(ANALYZE_SYS, srcs, Profile, effort="medium", max_tokens=16000)
        with lock:
            p["profile"], p["search"] = prof, None
            p["name"] = p["name"] or prof["name"]
        set_status(pid, "ready")
    except Exception as e:
        set_status(pid, f"error: {e}"[:300])


def set_status(pid, s):
    with lock:
        if pid in state["people"]:
            state["people"][pid]["status"] = s
    save()


# ---------------- agent step 2: search the pool for its person ----------------

class Match(BaseModel):
    id: str
    fit: int  # 0-100
    reason: str


class Matches(BaseModel):
    matches: list[Match]


def card(q):
    f = q["profile"]
    return {"id": q["id"], "name": q["name"], "headline": f["headline"], "summary": f["summary"],
            "hobbies": f["hobbies"], "interests": f["interests"], "values": f["values"],
            "looking_for": f["looking_for"], "lifestyle": f["lifestyle"]}


def search(pid):
    p = state["people"][pid]
    pool_ = [card(q) for q in state["people"].values() if q.get("profile") and compatible(p, q)]
    if not pool_:
        return
    set_status(pid, "searching…")
    try:
        res = llm(persona(p), "You are now searching the dating pool for your person. Using your partner brief, score "
                  "EVERY candidate below 0-100 for how well they fit your person (be discerning: spread the scores, "
                  "most should not be above 80) with a one-sentence specific reason.\n\nCandidates:\n"
                  + json.dumps(pool_, ensure_ascii=False), Matches, effort="medium", max_tokens=12000)
        ids = {c["id"] for c in pool_}
        with lock:
            p["search"] = {"matches": sorted((m for m in res["matches"] if m["id"] in ids),
                                             key=lambda m: m["fit"], reverse=True)}
        set_status(pid, "ready")
    except Exception as e:
        set_status(pid, f"error: search: {e}"[:300])


# ---------------- agent step 3: the date ----------------

class Verdict(BaseModel):
    score: int  # 0-100, how good a fit for MY person
    chemistry: str
    shared_ground: list[str]
    friction: list[str]
    second_date: bool
    summary: str


def persona(p):
    return (f"You are the AI dating agent for {p['name']}. You represent them and act on their behalf, using only what "
            f"you learned from their LinkedIn and Instagram:\n{json.dumps(p['profile'], ensure_ascii=False)}\n"
            "On dates you speak AS them, in first person, in their voice. Be natural, warm and specific - ask real "
            "questions, share real details, notice honestly where you click and where you don't. Never invent facts "
            "that contradict the profile.")


def transcript_text(d):
    return "\n".join(f"{state['people'][t['who']]['name']}: {t['text']}" for t in d["transcript"]) or "(nothing yet)"


def run_date(did):
    d = state["dates"][did]
    a, b = state["people"][d["a"]], state["people"][d["b"]]
    try:
        d["venue"] = d["venue"] or a["profile"]["ideal_first_date"]
        for turn in range(DATE_TURNS):
            me, other = (a, b) if turn % 2 == 0 else (b, a)
            line = llm(persona(me), f"You're on a first date with {other['name']} ({other['profile']['headline']}). "
                       f"Setting: {d['venue']}.\nConversation so far:\n{transcript_text(d)}\n\n"
                       + ("Open the date." if turn == 0 else "Reply to them, and keep it moving: react, share, ask.")
                       + (" This is your last line - wrap up the date in character." if turn >= DATE_TURNS - 2 else "")
                       + " Write ONLY your next line of dialogue (1-3 sentences; a short action in *asterisks* is ok).",
                       max_tokens=1500)
            with lock:
                d["transcript"].append({"who": me["id"], "text": line.strip().strip('"')})
            save()
        d["status"] = "debriefing"
        for me, other in ((a, b), (b, a)):
            v = llm(persona(me), f"The date with {other['name']} is over. Transcript:\n{transcript_text(d)}\n\n"
                    f"Their public profile summary: {other['profile']['summary']}\n\nPrivately debrief {me['name']}: "
                    f"how good a match is {other['name']} for them, 0-100, against your partner brief? Be honest, not "
                    "polite.", Verdict, effort="medium")
            with lock:
                d["verdicts"][me["id"]] = v
        d["status"] = "done"
    except Exception as e:
        d["status"] = f"error: {e}"[:300]
    save()


def start_date(a, b):
    with lock:
        if a == b or a not in state["people"] or b not in state["people"]:
            raise ValueError("Pick two different people")
        if not (state["people"][a].get("profile") and state["people"][b].get("profile")):
            raise ValueError("Both agents must finish reading their person first")
        for d in state["dates"].values():
            if {d["a"], d["b"]} == {a, b} and not d["status"].startswith("error"):
                return d["id"], None
        did = uuid.uuid4().hex[:8]
        state["dates"][did] = {"id": did, "a": a, "b": b, "venue": "", "transcript": [], "verdicts": {},
                               "status": "on the date"}
    save()
    return did, pool.submit(run_date, did)


# ---------------- matching + ranking ----------------

def open_to(p, q):
    s, g = p.get("seeking") or "everyone", q.get("gender", "")
    return s == "everyone" or not g or s == g


def compatible(p, q):
    return p["id"] != q["id"] and open_to(p, q) and open_to(q, p)


def ranking(pid, people, dates):
    p, rows = people[pid], []
    fits = {m["id"]: m for m in (p.get("search") or {}).get("matches", [])}
    for q in people.values():
        if not q.get("profile") or not compatible(p, q):
            continue
        d = next((d for d in dates.values() if {d["a"], d["b"]} == {pid, q["id"]} and d["status"] == "done"), None)
        if d:  # my agent's verdict weighs more, but a fit has to be mutual
            mine, theirs = d["verdicts"][pid], d["verdicts"][q["id"]]
            rows.append({"id": q["id"], "score": round(0.6 * mine["score"] + 0.4 * theirs["score"]), "stage": "dated",
                         "date": d["id"], "why": mine["summary"], "second_date": mine["second_date"] and theirs["second_date"]})
        elif q["id"] in fits:
            rows.append({"id": q["id"], "score": fits[q["id"]]["fit"], "stage": "pre-date", "date": None,
                         "why": fits[q["id"]]["reason"], "second_date": False})
    return sorted(rows, key=lambda r: r["score"], reverse=True)


def pipeline():
    """Everyone: finish reading → every agent searches the pool → each dates its top matches."""
    step = lambda s: state["pipeline"].update(running=bool(s != "done"), step=s)
    try:
        step("reading people")
        while any(not (p["status"] == "ready" or p["status"].startswith("error")) for p in state["people"].values()):
            time.sleep(2)
        step("agents searching")
        ready = [p["id"] for p in state["people"].values() if p.get("profile")]
        wait([pool.submit(search, pid) for pid in ready])
        step("agents dating")
        pairs = set()
        for pid in ready:
            for m in (state["people"][pid].get("search") or {}).get("matches", [])[:DATES_PER_PERSON]:
                pairs.add(tuple(sorted((pid, m["id"]))))
        wait([f for f in (start_date(a, b)[1] for a, b in pairs) if f])
        step("done")
    except Exception as e:
        step(f"error: {e}")
    save()
