"""All settings in one place. Everything can be overridden with environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent              # the project folder

# Keys can live in a .env file in the project folder (KEY=value per line; git-ignored, never pushed)
if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text("utf8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

env = os.environ.get

OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", "")         # required: powers every agent
APIFY_TOKEN = env("APIFY_TOKEN", "")                       # optional: sturdier LinkedIn/Instagram scraping
# Free OpenRouter models by default (both support tool calling + JSON output). Any OpenRouter model id works.
MODEL = env("MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
FALLBACK_MODELS = [m for m in env("FALLBACK_MODELS", "qwen/qwen3.8-27b:free").split(",") if m]  # used if MODEL is busy
HUNTER_MODEL = env("HUNTER_MODEL", MODEL)                  # the hunt is the longest loop
PORT = int(env("PORT", 8000))

TMP = Path("/tmp") if env("VERCEL") == "1" else ROOT / "data"   # Vercel: only /tmp is writable
DATA_FILE = Path(env("DATA_FILE", TMP / "state.json"))
FRONTEND = ROOT / "frontend" / "index.html"

# ---- limits: so a run can never burn more than you allow ----
BUDGET_USD = float(env("BUDGET_USD", 2))      # hard stop: no model call once total spend reaches this ($0 on free models)
MAX_CALLS = int(env("MAX_CALLS", 400))        # hard stop on the number of model calls (free models have daily quotas)
MIN_SECONDS_BETWEEN_CALLS = 3.2 if MODEL.endswith(":free") else 0  # free models allow ~20 requests/minute
HUNT_TARGET = 25                              # default number of real people a hunt recruits (the brief asks ≥25)
HUNT_MAX = 40                                 # most people one hunt may recruit
HUNT_MAX_STEPS = 90                           # most agent turns in one hunt
HUNT_MAX_SEARCHES = 45                        # most web searches in one hunt
DATES_PER_USER = 3                            # after a hunt: live dates with the top matches, one at a time
DATES_PER_PERSON = 2                          # "Run everyone": each agent dates its top-2 matches
DATE_TURNS = 8                                # lines of dialogue per date
TOOL_RESULT_CHARS = 5000                      # a tool result is cut to this many characters
KEEP_TOOL_RESULTS = 6                         # older tool results are shrunk so long loops stay cheap
# Per visitor (IP) per day, so a public link can't burn the free AI quota or Apify credit
LIMITS = {"people": 10, "onboard": 5, "date": 6, "chat": 40, "plan": 6, "hunt": 2, "run": 1, "analyze": 6, "search": 6}
WORKERS = 4                                   # agents running at the same time
