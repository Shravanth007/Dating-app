# Proxy Hearts — an agentic dating site

**In one breath (≤200 chars):** Paste someone's LinkedIn + public Instagram; an AI agent reads both, builds their dating profile, goes on real conversational dates with other agents, and ranks who fits them best.

## Run it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python app.py            # http://localhost:8000  (PORT env var respected for hosting)
python app.py --test     # self-check for URL validation + ranking logic
```

## How it works

1. **Add people** (`#add`): paste a LinkedIn URL and a public Instagram URL, one at a time or in bulk (`linkedin instagram` per line).
2. **The agent reads its person**: the server fetches both public profiles and Claude (`claude-opus-5-5`, structured output) turns them into a profile:
   needs, hobbies, interests, values, personality, communication style, lifestyle, what they're looking for, green flags, possible dealbreakers, ideal first date.
   It also returns an **evidence table**: source → signal → inference, so you can see *why* it concluded each thing. The raw fetched text is shown on the profile page as well.
   Those two sources are the only input. The agent is told not to use outside knowledge and not to infer orientation, religion, health or ethnicity.
3. **The agents date** (`#dates`): "Start speed-dating round" sends each agent on dates with its top-3 candidates (pre-screened by profile overlap). You can also send any two agents on a date by hand.
   A date is an 8-line conversation at a venue taken from the profile's ideal first date. Each agent speaks for its person with that person's profile as its system prompt and sees only what the other agent says. The page streams the conversation live.
   Afterwards each agent writes a **private debrief** for its own person: a 0–100 score, chemistry, shared ground, friction, and whether it wants a second date.
4. **Rankings** (`#rank`): for every person, everyone else is ranked. Pairs that have dated are scored as 60% this person's agent's verdict plus 40% the other agent's (fit has to be mutual), and they rank above pairs that haven't met. Those undated pairs show an estimate from profile overlap, with a one-click "send on date".
   An optional gender/seeking filter keeps matches sensible.

## Technical: scraping LinkedIn and Instagram

Python stdlib only (`urllib` + `re` + `json`). No headless browser and no paid scraping API.

- **LinkedIn**: GET the public guest view `linkedin.com/in/<slug>/`. We parse `og:title`, the meta description (headline, about, experience, education, location) and the embedded `application/ld+json` Person graph (job titles, employers, schools, and the person's posts/articles when public).
- **Instagram**: the private `web_profile_info` API returns 429 from servers, so we GET `instagram.com/<handle>/` with a crawler user agent. That public page embeds the profile JSON, and we extract the `og:` tags (name, follower/post counts), `biography`, and the captions of recent posts. Private or missing profiles are rejected.
- **Fallback**: if either site blocks a request (login wall or rate limit), the form accepts text copied from that same public profile. It's still the same two sources.
- URLs are validated, and the server only fetches URLs it builds itself from the slug or handle. Any other link is refused before a request is made (no SSRF).

**Stack:** Python 3.11+, `http.server` (threaded), Anthropic Python SDK (`messages.parse` + Pydantic for the structured profile and verdicts). State lives in `data.json`. The frontend is one vanilla HTML/JS page that polls `/api/state` (no build step).

## Files

- `app.py`: scraping, the reading agent, the dating agents, ranking, HTTP API
- `index.html`: the whole UI (add, profiles, live dates, rankings)
- `data.json`: the finished demo run (created on first use)
