# Proxy Hearts — an agentic dating site

**In one breath (≤200 chars):** Paste someone's LinkedIn + public Instagram; an AI agent reads both, learns who it represents, searches the pool, goes on real dates with other agents, and ranks who fits best.

## Run it

```bash
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-...     # required (LLM)
export APIFY_TOKEN=apify_api_...        # optional: Apify scrapers first, built-in scrapers as fallback
python -m app                           # http://localhost:8000   (PORT env respected)
python tests/test_core.py               # self-check
python -m app --scrape <linkedin_url> <instagram_url>   # see exactly what the scrapers return
```

Deploy: `render.yaml` is a ready-made blueprint for Render (set the two keys as secrets).

## Layout

```
app/
  core.py       config, shared state (data/demo.json), HTTP helper
  scrapers.py   LinkedIn + Instagram scrapers (Apify → built-in fallback), structured JSON out
  llm.py        OpenRouter chat completions with strict JSON-schema output, validated by pydantic
  agents.py     read the person → search the pool → date → debrief → rank; full pipeline
  server.py     HTTP API + serves the frontend
static/index.html   the whole UI (vanilla JS, no build)
data/people.txt     the 25+ real people used in the demo (LinkedIn + Instagram per line)
data/demo.json      the finished demo run (people, profiles, dates, rankings)
tests/test_core.py
```

## How it works

1. **Add people.** Paste a LinkedIn URL and a public Instagram URL, one at a time or in bulk (copy `data/people.txt` into Bulk add).
2. **The agent learns who it represents.** Both profiles are scraped into structured JSON. The agent (Claude via OpenRouter) reads that JSON and builds the person's profile: needs, hobbies, interests, values, personality, communication style, lifestyle, **voice** (how they write, used on dates) and a **partner brief** (ideal partner, must-haves, nice-to-haves, avoid). It also writes green flags, dealbreakers, an ideal first date, and an evidence table (source → signal → inference).
   Those two sources are its only input. It is told to ignore anything it knows about the person and not to infer orientation, religion, health or ethnicity.
3. **The agent searches.** Using its partner brief, each agent scores everyone else in the pool from 0 to 100, with a reason for each.
4. **The agents date.** Each agent dates its top 3 search hits. A date is an 8-line conversation at the venue from the person's ideal first date. Each agent speaks as its person, in their voice, and only knows what the other agent says. The UI streams it live.
   Then each agent writes a **private debrief** for its own person: a 0–100 score, chemistry, shared ground, friction, and whether it wants a second date.
5. **Rankings.** For every person, everyone else is ranked. Dated pairs use the debriefs (60% this person's agent, 40% the other's, because fit has to be mutual). Undated pairs use the agent's pre-date search score.
   **Run everything** does steps 3–5 for the whole pool in one click.

## Technical: how LinkedIn and Instagram are scraped

Each source has two tiers, and the profile page shows which tier produced the data (`via apify / builtin / pasted`).

| | Tier 1: Apify (if `APIFY_TOKEN`) | Tier 2: built-in, free, Python stdlib |
|---|---|---|
| **Instagram** | [`apify/instagram-profile-scraper`](https://apify.com/apify/instagram-profile-scraper): bio, counts, category, latest posts with captions, hashtags, likes, location, alt text | IG's `web_profile_info` API (used by instaloader and most GitHub scrapers) returns 429 to servers. Instead we request `instagram.com/<handle>/` as a search-engine crawler. That page embeds the profile JSON (`xig_user_by_igid_v2`: name, bio, verified, followers, following, private flag) and the recent posts' captions and types, which we extract with `json.raw_decode`. Private profiles are rejected. |
| **LinkedIn** | [`harvestapi/linkedin-profile-scraper`](https://apify.com/harvestapi/linkedin-profile-scraper) (no cookies): experience, education, skills, about, location | The public guest page `linkedin.com/in/<slug>/` embeds a schema.org `Person` graph (headline, location, about, followers, experience, education, languages, awards) plus the person's recent posts and articles, all parsed from `ld+json`. Browsers soon get HTTP 999, so we rotate to link-preview crawler user agents, which still get the public page. |

If both tiers are blocked, the form accepts text copied from that same public profile, so the sources stay the same two. The server only fetches URLs it builds from a validated slug or handle (no SSRF).

**Stack:** Python 3.11+ stdlib (`http.server`, `urllib`, `json`, `re`), pydantic, OpenRouter (`anthropic/claude-opus-5.5`, strict `json_schema` output), optional Apify. Frontend: one vanilla HTML/JS file that polls `/api/state`. Storage: one JSON file.
