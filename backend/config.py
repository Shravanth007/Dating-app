"""All settings in one place. Everything can be overridden with environment variables."""
import os
from pathlib import Path

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")  # required: powers every agent
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")                # optional: sturdier LinkedIn/Instagram scraping
MODEL = os.environ.get("MODEL", "anthropic/claude-opus-5.5")   # any OpenRouter model id
PORT = int(os.environ.get("PORT", 8000))

ROOT = Path(__file__).resolve().parent.parent                  # the project folder
DATA_FILE = Path(os.environ.get("DATA_FILE", ROOT / "data" / "demo.json"))
FRONTEND = ROOT / "frontend" / "index.html"

DATE_TURNS = 8          # lines of dialogue per date
DATES_PER_PERSON = 3    # in "run everyone", each agent dates its top-3 matches (not all n² pairs)
DATES_PER_USER = 5      # after a hunt, the user's agent dates its top-5 matches
HUNT_MAX_STEPS = 40     # max tool calls the hunter agent may make in one hunt
