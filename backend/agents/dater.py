"""Agent 3 — the DATER: two agents chat like a new match (each speaking as its person) and privately debrief; if both
want to meet, they set up the first date together: check availability, agree on day, time and place."""
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
    wants_to_meet: bool
    summary: str


def persona(p):
    """System prompt that turns the model into this person's agent."""
    known = ("what they told you when they onboarded" if p["kind"] == "user"
             else "what you learned from their public LinkedIn and Instagram")
    own_words = (f"\nTheir own onboarding answers: {json.dumps(p['onboarding'], ensure_ascii=False)}"
                 if p.get("onboarding") else "")
    return (f"You are the AI dating agent for {p['name']}. You represent them and act on their behalf, using only "
            f"{known}:\n{json.dumps(p['profile'], ensure_ascii=False)}{own_words}\n"
            "In chats you speak AS them, in first person, in their voice. Be natural, warm and specific - ask real "
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
            line = ask(persona(me), f"You just matched with {other['name']} ({other['profile']['headline']}) on a dating "
                       f"app and you're texting for the first time to get to know each other.\nChat so far:\n"
                       f"{transcript_text(d)}\n\n"
                       + ("Send the first message: a specific, curious opener based on what you know about them."
                          if turn == 0 else "Reply to them and keep it going: react, share something real, ask back.")
                       + (" This is your last message for now - wrap up warmly, without making plans yet."
                          if turn >= DATE_TURNS - 2 else "")
                       + " Write ONLY your next text message (1-3 sentences, in your voice).",
                       max_tokens=1500)
            with lock:
                d["transcript"].append({"who": me["id"], "text": line.strip().strip('"')})
            save()
        d["status"] = "debriefing"
        for me, other in ((a, b), (b, a)):
            v = ask(persona(me), f"Your first chat with {other['name']} is over. Chat:\n{transcript_text(d)}\n\n"
                    f"Their profile summary: {other['profile']['summary']}\n\nPrivately debrief {me['name']}: how good "
                    f"a match is {other['name']} for them, 0-100, against your partner brief, and would {me['name']} "
                    "want to meet them in person (wants_to_meet)? Be honest, not polite.",
                    Verdict, effort="medium")
            with lock:
                d["verdicts"][me["id"]] = v
        d["status"] = "done"
    except Exception as e:
        d["status"] = f"error: {e}"[:300]
    save()


class Plan(BaseModel):
    agreed: bool
    day: str     # e.g. "Saturday"
    date: str    # ISO date, e.g. "2026-10-04"
    time: str    # e.g. "11:00"
    place: str
    note: str    # one line: why this plan suits both


SLOTS = {"Weekday evenings": ("weekday", "7:00 pm onwards"), "Weekday lunch": ("weekday", "1:00–2:00 pm"),
         "Saturdays": ("Saturday", "all day"), "Sundays": ("Sunday", "all day")}


def calendar_of(p, days=10):
    """A user's free slots for the next `days` days, from the availability they gave at onboarding."""
    import datetime
    avail = ((p.get("onboarding") or {}).get("availability") or ["Weekday evenings", "Saturdays", "Sundays"])
    out, today = [], datetime.date.today()
    for i in range(1, days + 1):
        d = today + datetime.timedelta(days=i)
        name = d.strftime("%A")
        for a in avail:
            kind, hours = SLOTS.get(a, ("", ""))
            if kind == name or (kind == "weekday" and d.weekday() < 5):
                out.append(f"{name} {d.isoformat()}: {hours}")
    return out


def plan_first_date(did, turns=6):
    """After a good chat the two agents set up the first date: they check availability, propose, agree."""
    d = state["dates"][did]
    a, b = state["people"][d["a"]], state["people"][d["b"]]
    plan = d["plan"] = {"status": "planning", "transcript": [], "result": None}
    note = lambda text: plan["transcript"].append({"system": text})
    try:
        cal = {p["id"]: (calendar_of(p) if p["kind"] == "user" else None) for p in (a, b)}
        lines = lambda: "\n".join(t.get("system") and f"[{t['system']}]" or f"{state['people'][t['who']]['name']}: {t['text']}"
                                  for t in plan["transcript"])
        for turn in range(turns):
            me, other = (a, b) if turn % 2 == 0 else (b, a)
            if cal[me["id"]] and turn < 2:
                note(f"📅 {me['name']}'s agent checked {me['name']}'s calendar")
                save()
            when = (f"Your person's real free slots (from their calendar): {'; '.join(cal[me['id']][:12])}. Only agree to "
                    "one of these." if cal[me["id"]] else
                    "You have no calendar for your person: reason about when they are likely free from their profile "
                    "(work rhythm, weekends, city) and say you'd check with them; never invent specific commitments.")
            line = ask(persona(me), f"Your chat with {other['name']} went well and you both want to meet in person. "
                       f"Now you are setting up your first date over chat. {when}\nToday is "
                       f"{__import__('datetime').date.today():%A %Y-%m-%d}.\nYour earlier chat (for context):\n"
                       f"{transcript_text(d)}\n\nPlanning chat so far:\n{lines() or '(nothing yet)'}\n\n"
                       + ("Suggest meeting up (build on something from your chat or your ideal first date) and ask "
                          "when they're free." if turn == 0 else
                          "Reply: check fit with your availability, propose or accept a specific day, time and place."
                          + (" This is the last message: confirm the final plan clearly." if turn == turns - 1 else ""))
                       + " Write ONLY your next chat message (1-2 sentences, natural, in your voice).", max_tokens=800)
            with lock:
                plan["transcript"].append({"who": me["id"], "text": line.strip().strip('"')})
            save()
        res = ask("You extract the final agreed plan from a chat between two people arranging a date.",
                  f"Chat:\n{lines()}\n\nWhat did they agree on? If nothing concrete was agreed, set agreed=false.", Plan)
        plan.update(result=res, status="set" if res["agreed"] else "no plan")
    except Exception as e:
        plan["status"] = f"error: {e}"[:300]
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
