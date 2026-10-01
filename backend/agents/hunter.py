"""Agent 4 — the HUNTER: the user's agent goes out on the web and finds real people for them.

It is a tool-using agent with three tools:
  web_search(query)                      find people + their profile URLs
  read_person(linkedin_url, instagram_url)  read BOTH public profiles of one person, to judge the fit
  recruit(...)                           add someone it judged a real match (only after reading both, both public)
Each recruit gets their own agent (the reader builds it from the same LinkedIn + Instagram data). Then the user's agent
scores the pool and goes on live dates, one at a time, with its top matches, and ranks them."""
import json

from ..config import DATES_PER_USER, HUNT_MAX_SEARCHES, HUNT_MAX_STEPS, HUNTER_MODEL
from ..llm import BudgetExceeded, run_agent
from ..store import lock, log, new_person, save, state
from ..tools.instagram import ig_handle
from ..tools.linkedin import li_slug
from ..tools.person import already_read, read_person
from ..tools.web_search import web_search
from .dater import persona, start_date
from .matcher import card, ranking, search
from .reader import analyze

HUNT_SYS = """{persona}

Right now you are HUNTING: you go out on the open web and find {count} real people who could be a great match for
your person. How to work:
1. Plan from your partner brief (gender wanted, age range, what kind of life, interests, values; location is a soft
   preference).
2. web_search to find candidates. They must be adults who publicly present themselves online (creators, founders,
   athletes, artists, authors, public professionals). Each must have BOTH an official LinkedIn (linkedin.com/in/...)
   and their own PUBLIC Instagram. Search for exact profile URLs (e.g. "<name> instagram", "<name> linkedin").
   Search smart: one search can surface several people.
3. read_person(linkedin_url, instagram_url) before recruiting anyone: read both profiles and judge honestly whether
   they fit your person (gender, approximate age from their career timeline, interests, values, lifestyle).
4. recruit() only people you read and believe in. Never guess a URL. Call tools in parallel when you can.
5. finish() once you have recruited {count} people, or when you truly cannot find more.
Limits: at most {searches} web searches."""


def hunt(uid, count):
    u = state["people"][uid]
    h = u["hunt"] = {"running": True, "step": "planning", "found": 0, "target": count}
    recruited, searches = [], [0]
    say = lambda text: log(uid, text)
    b = u["onboarding"] or {}

    def tool_search(query):
        if searches[0] >= HUNT_MAX_SEARCHES:
            return {"error": "search limit reached: recruit from what you have, then finish()"}
        searches[0] += 1
        h["step"] = f"searching the web ({searches[0]}/{HUNT_MAX_SEARCHES})"
        say(f"🔎 {query}")
        return web_search(query)

    def tool_read_person(linkedin_url, instagram_url):
        h["step"] = "reading profiles"
        say(f"📖 reading {linkedin_url} + {instagram_url}")
        got = read_person(linkedin_url, instagram_url)
        if not got["both_public"]:
            say("   ✗ " + "; ".join(f"{k}: {got[k]['error']}" for k in ("linkedin", "instagram") if "error" in got[k]))
        return {"both_public": got["both_public"],
                **{k: got[k].get("data", {"error": got[k].get("error")}) for k in ("linkedin", "instagram")}}

    def tool_recruit(name, linkedin_url, instagram_url, gender, why):
        if len(recruited) >= count:
            return {"error": "You already recruited enough people. Call finish()."}
        if not (li_slug(linkedin_url) and ig_handle(instagram_url)):
            return {"error": "Need a linkedin.com/in/ URL and an instagram.com/ profile URL."}
        if not already_read(linkedin_url, instagram_url):
            return {"error": "read_person() both profiles first (and both must be public)."}
        for q in state["people"].values():
            if li_slug(q["linkedin"]) == li_slug(linkedin_url) or \
                    (ig_handle(q["instagram"]) or "").lower() == ig_handle(instagram_url).lower():
                if q.get("profile") and q["id"] not in recruited and q["kind"] != "user":
                    recruited.append(q["id"])
                    h["found"] = len(recruited)
                return {"ok": True, "note": f"{q['name']} is already in the pool", "recruited_so_far": len(recruited)}
        h["step"] = f"building {name}'s agent"
        say(f"🤝 recruiting {name}: {why}")
        p = new_person("candidate", linkedin_url.strip(), instagram_url.strip(), found_by=uid, found_reason=why,
                       gender=gender if gender in ("man", "woman", "nonbinary") else "")
        analyze(p["id"], also_log=uid)  # their own agent reads them (cached data) and builds their profile
        if not p.get("profile"):
            with lock:
                state["people"].pop(p["id"], None)
            save()
            say(f"✗ {name} dropped: {p['status'].removeprefix('error: ')}")
            return {"error": p["status"]}
        recruited.append(p["id"])
        h["found"] = len(recruited)
        return {"ok": True, "recruited_so_far": len(recruited), "target": count}

    def tool_finish(summary):
        say(f"🏁 {summary}")
        return {"ok": True}

    obj = lambda **props: {"type": "object", "properties": props, "required": list(props)}
    s = {"type": "string"}
    tools = {
        "web_search": ("Search the web. Returns result text and the URLs found.", obj(query=s), tool_search),
        "read_person": ("Read one person's public LinkedIn AND public Instagram (bio + recent posts) together.",
                        obj(linkedin_url=s, instagram_url=s), tool_read_person),
        "recruit": ("Add a person you read and judged a real match to the dating pool; their own agent gets built.",
                    obj(name=s, linkedin_url=s, instagram_url=s,
                        gender={"type": "string", "enum": ["man", "woman", "nonbinary", ""]},
                        why={"type": "string", "description": "one sentence: why they fit your person"}),
                    tool_recruit),
        "finish": ("Stop hunting.", obj(summary=s), tool_finish),
    }
    try:
        say(f"🎯 Looking for: {u['profile']['partner_brief']['ideal_partner']}")
        want = {"men": "men", "women": "women"}.get(b.get("interested_in"), "people of any gender")
        task = (f"Find {count} real people for {u['name']}. They want {want}, aged {b.get('age_min', 18)}-"
                f"{b.get('age_max', 99)}, goal: {b.get('relationship_goal') or 'open'}, near {b.get('city') or 'anywhere'}."
                f"\nYour person: {json.dumps(card(u), ensure_ascii=False)}\nAlready in the pool (recruit them too if "
                f"they truly fit): {', '.join(q['name'] for q in state['people'].values() if q['kind'] != 'user') or 'nobody'}")
        run_agent(HUNT_SYS.format(persona=persona(u), count=count, searches=HUNT_MAX_SEARCHES), task, tools,
                  on_event=say, max_steps=HUNT_MAX_STEPS, effort="medium", model=HUNTER_MODEL)

        h["step"] = "scoring the pool"
        say(f"Recruited {len(recruited)} real people. Scoring everyone in the pool against what you want…")
        search(uid)
        top = (u.get("search") or {}).get("matches", [])[:DATES_PER_USER]
        if not top:
            raise ValueError("nobody compatible in the pool yet")
        for i, m in enumerate(top, 1):  # one live date at a time, so there is always exactly one to watch
            h["step"] = f"on a date ({i}/{len(top)})"
            say(f"💘 date {i}/{len(top)} with {state['people'][m['id']]['name']} (pre-date fit {m['fit']})")
            did, future = start_date(uid, m["id"])
            if future:
                future.result()
            d = state["dates"][did]
            if d["status"] == "done":
                say(f"   {d['verdicts'][uid]['score']}/100 · second date: {'yes' if d['verdicts'][uid]['second_date'] else 'no'}")
        rows = ranking(uid, state["people"], state["dates"])
        if rows:
            say(f"🏆 Best match: {state['people'][rows[0]['id']]['name']} ({rows[0]['score']}/100). {rows[0]['why']}")
        h["step"] = "done"
    except BudgetExceeded as e:
        h["step"] = "stopped: spending cap reached"
        say(f"💸 {e}")
    except Exception as e:
        h["step"] = f"error: {e}"[:300]
        say(f"⚠️ {e}")
    h["running"] = False
    save()
