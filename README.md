# Proxy Hearts 💘 — AI agents that date for you

**In one breath (≤200 chars):** Sign up like any dating app; your AI agent hunts the web for real people, reads their LinkedIn + Instagram, goes on dates with their agents, and ranks who fits you best.

---

## How it works

```
 YOU                         YOUR AGENT                                   THE PEOPLE IT FINDS
 ───                         ──────────                                   ───────────────────
 1. Onboarding       ──►     turns your answers into a profile
    (who you are,            + a "partner brief" (who to look for)
    who you want)                    │
                                     ▼
                      2. HUNT  web_search → read_person → recruit   ──►   each recruit gets their OWN agent,
                         (it decides what to search, reads both           built only from their public
                         profiles, judges the fit, recruits)              LinkedIn + Instagram
                                     │
                                     ▼
                      3. scores everyone in the pool (0–100, with a reason)
                                     │
                                     ▼
                      4. LIVE DATES with its top 3, one at a time  ◄──►  their agent speaks as them, in their voice
                                     │
                                     ▼
                      5. both agents privately debrief (score, chemistry, second date?)
                                     │
                                     ▼
 6. Your ranking     ◄──     who fits you best, and why
 7. Chat             ──►     talk live with any person's agent; it answers as them
```

**The agents decide, the tools fetch.** An agent is an LLM (via OpenRouter) given tools. It chooses which tool to call, with what, and what to make of the result. The tools only fetch data and cost no AI calls. Every tool call appears live in your agent's activity feed.

**The two-sources rule:** every person the agents find is built from exactly two sources, their public LinkedIn and their public Instagram. Your own agent is built from your onboarding answers, plus your own links if you add them.

---

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your keys into .env (it is git-ignored)
python -m backend           # open http://localhost:8000
```

| Key in `.env` | What it powers | Free tier |
|---|---|---|
| `OPENROUTER_API_KEY` | **Required.** Every agent (profiles, hunt, dates, debriefs, chat) | Free models: 50 requests/day, or 1,000/day after a one-time $10 top-up |
| `SERPER_API_KEY` | The `web_search` tool (Google results). `TAVILY_API_KEY` also works. | 2,500 searches, no card |
| `APIFY_TOKEN` | Reliable LinkedIn + Instagram scraping (first choice; free scrapers are the fallback) | $5/month credit, about $0.004 per profile |

Optional: `MODEL` (default `nvidia/nemotron-3-super-120b-a12b:free`, with `qwen/qwen3.8-27b:free` as fallback), `PORT`, `BUDGET_USD`, `MAX_CALLS`.

Run the tests with `python tests/test_core.py`. To see what the tools fetch for any person (no AI involved), run `python -m backend --scrape <linkedin_url> <instagram_url>`.

---

## Project structure

```
├── frontend/
│   └── index.html            ← the whole website (HTML + CSS + JS, no build step)
│
├── backend/
│   ├── server.py             ← web server + JSON API (start here)
│   ├── config.py             ← every setting and limit, plus loading .env
│   ├── store.py              ← the app's memory → data/state.json
│   ├── llm.py                ← OpenRouter: ask() and run_agent() (the tool-calling loop), spend + call caps
│   ├── pipeline.py           ← "Run everyone": every agent searches, dates and gets ranked
│   ├── agents/
│   │   ├── reader.py         ← gets to know a person → profile, voice, partner brief, evidence
│   │   ├── hunter.py         ← your agent hunts the web for real people who fit you
│   │   ├── matcher.py        ← scores the pool + the ranking formula
│   │   └── dater.py          ← live dates, private debriefs, and live chat with an agent
│   └── tools/
│       ├── web_search.py     ← Serper / Tavily (DuckDuckGo fallback)
│       ├── person.py         ← read_person: LinkedIn + Instagram of one person, cached
│       ├── linkedin.py       ← LinkedIn scraper (Apify → built-in)
│       ├── instagram.py      ← Instagram scraper (Apify → built-in)
│       └── fetch.py          ← shared HTTP helpers
│
├── data/
│   ├── seed_people.txt       ← verified public profiles you can bulk-add (Add → Bulk)
│   ├── state.json            ← your run (people, dates, chats): local only, git-ignored
│   └── scrape_cache.json     ← each profile is scraped once and kept: local only, git-ignored
├── tests/test_core.py
├── .env.example              ← copy to .env, add keys
├── requirements.txt          ← just pydantic; the rest is the Python standard library
└── render.yaml               ← one-click deploy to Render
```

---

## The agents and their tools

| Agent | Tools it calls | What it produces |
|---|---|---|
| **Hunter** (`agents/hunter.py`) | `web_search`, `read_person`, `recruit`, `finish` | Finds real people with a public LinkedIn **and** a public Instagram, reads both, judges the fit, recruits the good ones. `recruit` is refused unless both profiles were read and both are public. |
| **Reader** (`agents/reader.py`) | `read_person` | Profile: needs, hobbies, interests, values, personality, communication style, lifestyle, **voice**, **partner brief**, green flags, dealbreakers, ideal first date, and an **evidence table** (source → signal → inference) |
| **Matcher** (`agents/matcher.py`) | none | A 0–100 fit score and a reason for everyone in the pool |
| **Dater** (`agents/dater.py`) | none | Live date transcripts, a private debrief from each side, and live chat with a person's agent |

**Ranking:** for pairs that dated, 60% your agent's debrief plus 40% theirs, because fit has to be mutual. Pairs that haven't met yet use the pre-date fit score.

## Limits (so a run can never burn money)

All limits live in `backend/config.py`: a hard cap on model calls (`MAX_CALLS` = 400) and on spend (`BUDGET_USD` = $2), at most 45 web searches and 90 agent steps per hunt, 3 dates per hunt with 8 lines each, free-model calls spaced 3.2 s apart, and long tool results trimmed. On the default free models a full run costs $0.

---

## Technical: how LinkedIn and Instagram are scraped

| | Tier 1: Apify (with `APIFY_TOKEN`) | Tier 2: built-in, free, Python stdlib |
|---|---|---|
| **LinkedIn** | [`harvestapi/linkedin-profile-scraper`](https://apify.com/harvestapi/linkedin-profile-scraper) (no cookies): headline, location, full experience with descriptions, education, skills, projects, awards, interests | The public profile page embeds a schema.org `Person` (headline, location, about, followers, experience, education, languages) plus recent posts and articles (`ld+json`). Rendered sections add job titles, dates, full About, volunteering, courses. Browsers soon get HTTP 999, so it rotates to link-preview crawler user agents. |
| **Instagram** | [`apify/instagram-profile-scraper`](https://apify.com/apify/instagram-profile-scraper): bio, website, counts, and latest posts with caption, date, likes, comments, location | Instagram's private web API (used by instaloader and most GitHub scrapers) returns 429 to servers. The profile page served to search-engine crawlers embeds the profile JSON (name, bio, verified, counts, private flag) and recent captions. |
| **Web search** | Serper.dev (Google) or Tavily | DuckDuckGo HTML (rate-limits quickly) |

**Rules the tools enforce:**
- Private Instagram accounts and LinkedIn profiles hidden from the public are rejected.
- Other people's data that scrapers return (LinkedIn "people also viewed") is stripped.
- The server only fetches LinkedIn and Instagram URLs it rebuilds from a validated username (no arbitrary URL fetching).
- All onboarding input is validated: 18+, image-only photo, size limits.

**Stack:** Python 3.11+ standard library (`http.server`, `urllib`, `json`, `re`, `threading`), pydantic, OpenRouter (tool calling, JSON-schema output), Serper, Apify. The frontend is vanilla HTML/CSS/JS. Data lives in local JSON files. There is no framework and no build step.
