"""Agent 3 — the DATER: two agents go on a date, each speaking as its person, then each privately debriefs."""
import json, uuid

from pydantic import BaseModel

from ..config import DATE_TURNS
from ..llm import ask
from ..store import lock, pool, save, state


class Verdict(BaseModel):
    score: int  # 0-100: how good a fit the other person is for MY person
    chemistry: str
    shared_ground: list[str]
    friction: list[str]
    second_date: bool
    summary: str


def persona(p):
    """System prompt that turns the model into this person's agent."""
    known = ("what they told you when they onboarded" if p["kind"] == "user"
             else "what you learned from their public LinkedIn and Instagram")
    return (f"You are the AI dating agent for {p['name']}. You represent them and act on their behalf, using only "
            f"{known}:\n{json.dumps(p['profile'], ensure_ascii=False)}\n"
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
            line = ask(persona(me), f"You're on a first date with {other['name']} ({other['profile']['headline']}). "
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
            v = ask(persona(me), f"The date with {other['name']} is over. Transcript:\n{transcript_text(d)}\n\n"
                    f"Their profile summary: {other['profile']['summary']}\n\nPrivately debrief {me['name']}: how good "
                    f"a match is {other['name']} for them, 0-100, against your partner brief? Be honest, not polite.",
                    Verdict, effort="medium")
            with lock:
                d["verdicts"][me["id"]] = v
        d["status"] = "done"
    except Exception as e:
        d["status"] = f"error: {e}"[:300]
    save()


def chat(to_id, from_id, text):
    """You chat live with a person's agent; it answers as them (persona built only from their LinkedIn + Instagram)."""
    other, me = state["people"].get(to_id), state["people"].get(from_id)
    if not (other and other.get("profile")):
        raise ValueError("That agent is not ready yet")
    text = (text or "").strip()[:800]
    if not text:
        raise ValueError("Type a message first")
    key = f"{from_id}:{to_id}"
    with lock:
        thread = state.setdefault("chats", {}).setdefault(key, [])
        thread.append({"who": from_id, "text": text})
    who = f"{me['name']} ({(me.get('profile') or {}).get('headline', '')})" if me else "someone"
    history = "\n".join(f"{'Them' if m['who'] == from_id else other['name']}: {m['text']}" for m in thread[-20:])
    reply = ask(persona(other), f"You are chatting live, one-on-one, with {who} on a dating app. Conversation so "
                f"far:\n{history}\n\nWrite ONLY {other['name']}'s next message (1-3 sentences, natural, in their voice). "
                "Stick to what the profile actually says; if asked about something it doesn't cover, stay light and "
                "vague or turn the question back - never make up specific facts, people or stories.",
                max_tokens=1000).strip().strip('"')
    with lock:
        thread.append({"who": to_id, "text": reply})
    save()
    return reply


def start_date(a, b):
    """Create (or reuse) a date between a and b and start it in the background. Returns (date_id, future|None)."""
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
