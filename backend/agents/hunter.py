"""Agent 4 — the HUNTER: the user's agent goes out on the web and finds real people for them.

It is a tool-using agent. It decides what to search, reads candidates' LinkedIn + Instagram itself to judge whether they
are a proper match, and recruits the ones it believes in (each recruit gets their own agent via the reader). Then the
user's agent scores the pool, goes on dates with its top matches, and ranks them."""
import json
from concurrent.futures import wait

from ..config import DATES_PER_USER, HUNT_MAX_STEPS
from ..llm import run_agent
from ..store import log, lock, new_person, save, state
from ..tools.instagram import ig_handle, read_instagram
from ..tools.linkedin import li_slug, read_linkedin
from ..tools.web_search import web_search
from .dater import persona, start_date
from .matcher import card, ranking, search
from .reader import analyze

HUNT_SYS = """{persona}

Right now you are HUNTING: you go out on the open web and find real people who could be a great match for your person.
How to work:
1. Plan from your partner brief (who, what kind of life, interests, values, age range, location as a soft preference).
2. Use web_search to find candidates. People must publicly present themselves online (creators, founders, athletes,
   artists, authors, public professionals) and be adults. Each must have BOTH an official LinkedIn (linkedin.com/in/...)
   and their own PUBLIC Instagram. Search for the exact profile URLs (e.g. "<name> instagram", "<name> linkedin").
3. Before recruiting anyone, READ both their LinkedIn and Instagram with your tools and judge honestly whether they
   are a proper match for your person (gender wanted, approximate age from their career timeline, interests, values,
   lifestyle). Skip people who don't fit; it's fine to say why.
4. recruit() the ones you believe in. Never guess a URL - only use URLs you saw in search results or that you read.
5. Stop with finish() when you have recruited {count} people (or when you truly cannot find more)."""


def hunt(uid, count=10):
    u = state["people"][uid]
    h = u["hunt"] = {"running": True, "step": "planning", "found": 0, "target": count}
    recruited = []
    say = lambda text: log(uid, text)
    b = u["onboarding"] or {}

    def tool_search(query):
        h["step"] = "searching the web"
        say(f"🔎 {query}")
        return web_search(query)

    def tool_read_linkedin(url):
        h["step"] = "reading profiles"
        say(f"📖 LinkedIn: {url}")
        return read_linkedin(url)["data"]

    def tool_read_instagram(url):
        h["step"] = "reading profiles"
        say(f"📸 Instagram: {url}")
        return read_instagram(url)["data"]

    def tool_recruit(name, linkedin_url, instagram_url, gender, why):
        if len(recruited) >= count:
            return {"error": "You already recruited enough people. Call finish()."}
        if not (li_slug(linkedin_url) and ig_handle(instagram_url)):
            return {"error": "Need a linkedin.com/in/ URL and an instagram.com/ profile URL."}
        for q in state["people"].values():
            if li_slug(q["linkedin"]) == li_slug(linkedin_url) or \
                    (ig_handle(q["instagram"]) or "").lower() == ig_handle(instagram_url).lower():
                if q.get("profile") and q["id"] not in recruited:
                    recruited.append(q["id"])
                    h["found"] = len(recruited)
                return {"ok": True, "note": f"{q['name']} is already in the pool"}
        h["step"] = "building their agent"
        say(f"🤝 Recruiting {name}: {why}")
        p = new_person("candidate", linkedin_url.strip(), instagram_url.strip(), found_by=uid, found_reason=why,
                       gender=gender if gender in ("man", "woman", "nonbinary") else "")
        analyze(p["id"], also_log=uid)  # their own agent reads them and builds their profile
        if not p.get("profile"):
            with lock:
                state["people"].pop(p["id"], None)
            save()
            say(f"✗ {name} dropped: {p['status'].removeprefix('error: ')}")
            return {"error": p["status"]}
        recruited.append(p["id"])
        h["found"] = len(recruited)
        return {"ok": True, "recruited_so_far": len(recruited), "headline": p["profile"]["headline"]}

    def tool_finish(summary):
        say(f"🏁 {summary}")
        return {"ok": True}

    url = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    tools = {
        "web_search": ("Search the web. Returns result text and the URLs found.",
                       {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                       tool_search),
        "read_linkedin": ("Read a public LinkedIn profile (linkedin.com/in/...).", url, tool_read_linkedin),
        "read_instagram": ("Read a public Instagram profile and its recent posts.", url, tool_read_instagram),
        "recruit": ("Add a person you judged a proper match to the dating pool. Their own agent will be built from "
                    "their LinkedIn + Instagram.",
                    {"type": "object", "properties": {
                        "name": {"type": "string"}, "linkedin_url": {"type": "string"},
                        "instagram_url": {"type": "string"},
                        "gender": {"type": "string", "enum": ["man", "woman", "nonbinary", ""]},
                        "why": {"type": "string", "description": "one sentence: why they fit your person"}},
                     "required": ["name", "linkedin_url", "instagram_url", "gender", "why"]}, tool_recruit),
        "finish": ("Stop hunting.", {"type": "object", "properties": {"summary": {"type": "string"}},
                                     "required": ["summary"]}, tool_finish),
    }
    try:
        say(f"🎯 Brief: {u['profile']['partner_brief']['ideal_partner']}")
        want = {"men": "men", "women": "women"}.get(b.get("interested_in"), "people of any gender")
        task = (f"Find {count} real people for {u['name']}. They want {want}, aged {b.get('age_min', 18)}-"
                f"{b.get('age_max', 99)}, goal: {b.get('relationship_goal') or 'open'}, near {b.get('city') or 'anywhere'}."
                f"\nYour person: {json.dumps(card(u), ensure_ascii=False)}\nPeople already in the pool (recruit them "
                f"only if they truly fit): {', '.join(q['name'] for q in state['people'].values() if q['kind'] != 'user') or 'none'}")
        run_agent(HUNT_SYS.format(persona=persona(u), count=count), task, tools, on_event=say,
                  max_steps=HUNT_MAX_STEPS, effort="medium")

        h["step"] = "scoring the pool"
        say(f"Recruited {len(recruited)} people. Scoring everyone in the pool against the brief…")
        search(uid)
        top = (u.get("search") or {}).get("matches", [])[:DATES_PER_USER]
        if not top:
            raise ValueError("nobody compatible in the pool yet")
        h["step"] = "on dates"
        futures = []
        for m in top:
            say(f"💘 Going on a date with {state['people'][m['id']]['name']} (pre-date fit {m['fit']})")
            futures.append(start_date(uid, m["id"])[1])
        wait([f for f in futures if f])
        rows = ranking(uid, state["people"], state["dates"])
        if rows:
            say(f"🏆 Best match: {state['people'][rows[0]['id']]['name']} ({rows[0]['score']}/100). {rows[0]['why']}")
        h["step"] = "done"
    except Exception as e:
        h["step"] = f"error: {e}"[:300]
        say(f"⚠️ {e}")
    h["running"] = False
    save()
