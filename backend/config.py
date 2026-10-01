"""All settings in one place. Everything can be overridden with environment variables."""
import os
from pathlib import Path

env = os.environ.get

OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", "")         # required: powers every agent
APIFY_TOKEN = env("APIFY_TOKEN", "")                       # optional: sturdier LinkedIn/Instagram scraping
MODEL = env("MODEL", "anthropic/claude-opus-5.5")          # any OpenRouter model id
HUNTER_MODEL = env("HUNTER_MODEL", MODEL)                  # the hunt is the longest loop; a cheaper model saves most here
PORT = int(env("PORT", 8000))

ROOT = Path(__file__).resolve().parent.parent              # the project folder
DATA_FILE = Path(env("DATA_FILE", ROOT / "data" / "demo.json"))
FRONTEND = ROOT / "frontend" / "index.html"

# ---- limits: so a run can never burn more than you allow ----
BUDGET_USD = float(env("BUDGET_USD", 10))     # hard stop: no model call is made once total spend reaches this
HUNT_TARGET = 25                              # default number of real people a hunt recruits (the brief asks ≥25)
HUNT_MAX = 40                                 # most people one hunt may recruit
HUNT_MAX_STEPS = 90                           # most agent turns in one hunt
HUNT_MAX_SEARCHES = 45                        # most web searches in one hunt
DATES_PER_USER = 3                            # after a hunt: live dates with the top matches, one at a time
DATES_PER_PERSON = 2                          # "Run everyone": each agent dates its top-2 matches
DATE_TURNS = 8                                # lines of dialogue per date
TOOL_RESULT_CHARS = 5000                      # a tool result is cut to this many characters
KEEP_TOOL_RESULTS = 6                         # older tool results are shrunk so long loops stay cheap
WORKERS = 6                                   # agents running at the same time
