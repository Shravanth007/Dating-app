"""Agent 1 — the READER: gets to know the person it represents.

A candidate's agent uses its tools to read the person's public LinkedIn + Instagram (its only two sources) and turns
what it read into a dating profile. A user's agent starts from their onboarding answers (plus their own links, if given)."""
import json

from pydantic import BaseModel

from ..llm import ask, run_agent
from ..store import lock, log, save, set_status, state
from ..tools.person import read_person


class Evidence(BaseModel):
    source: str     # "LinkedIn" | "Instagram" | "Onboarding"
    signal: str     # what was observed
    inference: str  # what the agent concluded from it


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


READ_SYS = """You are a personal AI dating agent. You are about to represent a real person and date on their behalf, so
first you must get to know them. Use your read_person tool to read their public LinkedIn and public Instagram.
Then reply with a short note on what stood out."""

CANDIDATE_PROFILE_SYS = """You are a personal AI dating agent. Your ONLY two sources about the person you represent are
their public LinkedIn and public Instagram (what your tools returned, below). Use nothing else - no outside or prior
knowledge about them, even if they are famous.

Build their profile:
- needs: what they need from a partner and a relationship (infer from work rhythm, values, what they post about)
- hobbies, interests, values, personality traits, communication_style, lifestyle (pace, travel, routines)
- voice: how they actually talk/write (tone, phrases, emoji use) from their captions and posts - you will speak in it
- looking_for + partner_brief: the kind of person you will look for on their behalf
- green_flags, possible_dealbreakers, ideal_first_date (specific, grounded in their interests)
- evidence: 6-12 items of source -> concrete signal (quote or fact) -> inference
- data_confidence: how much the sources revealed and what is guessed
Be specific, not generic: cite real details. Do not infer sexual orientation, religion, health or ethnicity."""

USER_PROFILE_SYS = """You are a personal AI dating agent and you have just onboarded your person, like a dating app
would. Their onboarding answers are authoritative about who they are and what they want (gender, age range, goal,
traits, dealbreakers). If they also linked a public LinkedIn/Instagram, use what your tools read to add real detail.

Build their profile: needs, hobbies, interests, values, personality, communication_style, lifestyle, voice (how they
come across in their own words), looking_for, partner_brief (turn their stated type, goal and dealbreakers into the
brief you will hunt with), green_flags, possible_dealbreakers, ideal_first_date, evidence (source = "Onboarding",
"LinkedIn" or "Instagram" -> signal -> inference), data_confidence."""


def analyze(pid, also_log=None):
    """Run the reader agent for one person. `also_log`: another person's feed to mirror progress into (the hunter's)."""
    p = state["people"][pid]
    is_user = p["kind"] == "user"

    def event(text):
        log(pid, text)
        if also_log:
            log(also_log, f"[{p['name'] or 'new person'}] {text}")

    def tool_read_person():
        """Scrape both of this person's profiles (cached if the hunter already read them) and keep what came back."""
        set_status(pid, "reading linkedin + instagram…")
        event(f"📖 reading LinkedIn {p['linkedin'] or '-'} + Instagram {p['instagram'] or '-'}")
        got = read_person(p["linkedin"], p["instagram"])
        with lock:
            for key in ("linkedin", "instagram"):
                src, side = p["sources"][key], got[key]
                if not p[key]:
                    continue
                if "data" in side:
                    src.update(via=side["via"], data=side["data"], error="")
                    p["name"] = p["name"] or side["name"]
                    p["photo"] = p["photo"] or side["photo"]
                else:
                    src.update(via="pasted" if src["pasted"] else "", data={}, error=side["error"])
                    event(f"⚠️ {key}: {side['error']}")
        save()
        return {k: got[k].get("data", got[k]) for k in ("linkedin", "instagram") if p[k]}

    try:
        if p["linkedin"] or p["instagram"]:
            set_status(pid, "getting to know them…")
            note = run_agent(READ_SYS, f"Your person: LinkedIn {p['linkedin'] or '-'} · Instagram "
                             f"{p['instagram'] or '-'}", {"read_person": (
                                 "Read your person's public LinkedIn profile and public Instagram (bio + recent posts).",
                                 {"type": "object", "properties": {}}, tool_read_person)},
                             on_event=event, max_steps=4, effort="low")
            if note:
                event(f"💭 {note[:300]}")
            if not any(src["data"] or src["error"] for src in p["sources"].values()):
                tool_read_person()  # safety net: the agent must not skip reading
        if not is_user:
            for key in ("linkedin", "instagram"):
                src = p["sources"][key]
                if not src["data"] and not src["pasted"]:
                    raise ValueError(f"{key}: {src['error'] or 'could not be read'}")
        set_status(pid, "analyzing…")
        event("🧠 building the profile")
        read = "\n\n".join(f"=== {k.title()} ({p[k]}) ===\n{json.dumps(s['data'], ensure_ascii=False)}"
                           + (f"\nText copied from the public profile:\n{s['pasted']}" if s["pasted"] else "")
                           for k, s in p["sources"].items() if s["data"] or s["pasted"])
        if is_user:
            read = "=== Onboarding answers ===\n" + json.dumps(p["onboarding"], ensure_ascii=False) + "\n\n" + read
        prof = ask(USER_PROFILE_SYS if is_user else CANDIDATE_PROFILE_SYS, read, Profile, effort="medium",
                   max_tokens=16000)
        with lock:
            p["profile"], p["search"] = prof, None
            p["name"] = p["name"] or prof["name"]
        event(f"✅ profile ready: {prof['headline']}")
        set_status(pid, "ready")
    except Exception as e:
        set_status(pid, f"error: {e}")
