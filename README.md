# Proxy Hearts 💘 — AI agents that date for you

**In one breath (≤200 chars):** Sign up like any dating app; your AI agent hunts the web for real people, reads their LinkedIn + Instagram, goes on dates with their agents, and ranks who fits you best.

---

## What happens, step by step

```
 YOU                          YOUR AGENT                                  THE PEOPLE IT FINDS
 ───                          ──────────                                  ───────────────────
 1. Onboarding        ──►     reads your answers, writes your
    (who you are,             profile + a "partner brief"
    who you want)                     │
                                      ▼
                       2. HUNT: searches the web (web_search tool),
                          reads candidates' LinkedIn + Instagram   ──►    each recruit gets their OWN agent,
                          (read_linkedin / read_instagram tools),         built only from their LinkedIn +
                          judges the fit, recruits the good ones          Instagram (needs, hobbies, voice…)
                                      │
                                      ▼
                       3. scores everyone in the pool
                                      │
                                      ▼
                       4. DATES: your agent and their agent       ◄──►    they talk, live, in each person's voice
                          go on a date (8 lines of dialogue)
                                      │
                                      ▼
                       5. both agents write a private debrief
                          (score, chemistry, 2nd date?)
                                      │
                                      ▼
 6. Your ranking      ◄──     who fits you best, and why
```

The agents do the work themselves. Each is an LLM (via **OpenRouter**) given **tools** and left to decide which ones to call, in what order, and what to make of the results. The code only runs the tools. Every tool call shows up live in your agent's activity feed.

---

## Run it (3 commands)

```bash
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-...        # Windows PowerShell:  $env:OPENROUTER_API_KEY="sk-or-..."
python -m backend                          # then open http://localhost:8000
```

Optional settings (environment variables):

| Variable | What it does | Default |
|---|---|---|
| `OPENROUTER_API_KEY` | **Required.** Powers every agent | none |
| `MODEL` | Any OpenRouter model id | `anthropic/claude-opus-5.5` |
| `APIFY_TOKEN` | Uses Apify's scrapers first (sturdier on cloud servers) | not set: built-in scrapers |
| `PORT` | Web server port | `8000` |

Run the tests with `python tests/test_core.py`. To see exactly what the scrapers return for someone, run `python -m backend --scrape <linkedin_url> <instagram_url>`.

---

## Project structure

```
Proxy Hearts/
├── frontend/
│   └── index.html            ← the whole website (HTML + CSS + JavaScript in one file, no build step)
│
├── backend/                  ← the Python server and the agents
│   ├── server.py             ← web server: serves the frontend + the JSON API (start here)
│   ├── config.py             ← all settings (API keys, model, how many dates…)
│   ├── store.py              ← the app's memory (people, dates, activity logs → data/demo.json)
│   ├── llm.py                ← talks to OpenRouter: ask() and run_agent() (the tool-calling loop)
│   ├── pipeline.py           ← "Run everyone": every agent searches, dates and gets ranked
│   │
│   ├── agents/               ← the four agent jobs
│   │   ├── reader.py         ← gets to know a person (reads LinkedIn + Instagram → profile)
│   │   ├── hunter.py         ← your agent hunts the web for real people who match you
│   │   ├── matcher.py        ← scores the pool for a person + the ranking formula
│   │   └── dater.py          ← two agents go on a date, then each debriefs
│   │
│   └── tools/                ← what agents can use
│       ├── web_search.py     ← search the web (OpenRouter web plugin)
│       ├── linkedin.py       ← read a public LinkedIn profile
│       ├── instagram.py      ← read a public Instagram profile
│       └── fetch.py          ← shared HTTP helpers
│
├── data/
│   ├── demo.json             ← your run (people, dates, rankings): stays on your machine, git-ignored
│   ├── scrape_cache.json     ← every profile scraped, saved once: git-ignored
│   └── seed_people.txt       ← real people (LinkedIn + Instagram) you can bulk-add to the pool
│
├── tests/test_core.py
├── requirements.txt          ← just `pydantic`; everything else is Python's standard library
└── render.yaml               ← one-click deploy to Render
```

---

## The agents and their tools

| Agent | File | Tools it can call | What it produces |
|---|---|---|---|
| **Reader** | `agents/reader.py` | `read_linkedin`, `read_instagram` (only its own person's links) | Profile: needs, hobbies, interests, values, personality, communication style, lifestyle, voice, partner brief, green flags, dealbreakers, ideal first date, and an **evidence table** (source → signal → inference) |
| **Hunter** | `agents/hunter.py` | `web_search`, `read_linkedin`, `read_instagram`, `recruit`, `finish` | Finds real people with a public LinkedIn and Instagram, reads both to check the fit, and recruits the good matches |
| **Matcher** | `agents/matcher.py` | none (pure judgment) | A 0–100 fit score and a reason for everyone in the pool |
| **Dater** | `agents/dater.py` | none (conversation) | A live date transcript and a private debrief from each side |

**Ranking:** for pairs that dated, 60% your agent's debrief plus 40% theirs, because fit has to be mutual. Pairs that haven't met yet use the pre-date fit score.

**Sources rule:** every person the agents find is built from exactly two sources, their public LinkedIn and their public Instagram. Your own agent is built from your onboarding answers, plus your own links if you add them.

---

## Technical: how LinkedIn and Instagram are scraped

| | Tier 1: Apify (if `APIFY_TOKEN`) | Tier 2: built-in, free, Python stdlib only |
|---|---|---|
| **Instagram** | [`apify/instagram-profile-scraper`](https://apify.com/apify/instagram-profile-scraper) | Instagram's private web API (used by instaloader and most GitHub scrapers) returns 429 to servers. We request `instagram.com/<handle>/` as a search-engine crawler instead. That page embeds the profile JSON (name, bio, verified, followers, following, private flag) and the recent posts' captions, which we pull out with `json.raw_decode`. Private profiles are rejected. |
| **LinkedIn** | [`harvestapi/linkedin-profile-scraper`](https://apify.com/harvestapi/linkedin-profile-scraper) (no cookies) | The public profile page embeds a schema.org `Person` (headline, location, about, followers, experience, education, languages) plus recent posts and articles, parsed from `ld+json`. Browsers soon get HTTP 999, so we rotate to link-preview crawler user agents. |
| **Web search** | none | OpenRouter's web search plugin (`plugins: [{"id": "web"}]`) |

Safety: the server only fetches LinkedIn and Instagram URLs it rebuilds from a validated username (no arbitrary URL fetching), and all onboarding input is validated (18+, image-only photo, size limits).

**Stack:** Python 3.11+ standard library (`http.server`, `urllib`, `json`, `re`, `threading`), pydantic, OpenRouter (tool calling, strict JSON-schema output, web search). The frontend is vanilla HTML/CSS/JS and data lives in a JSON file. There is no framework and no build step.
