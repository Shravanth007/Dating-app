"""The web server: serves the frontend and a small JSON API.  Run:  python -m backend  →  http://localhost:8000

GET  /api/state                     everything the UI shows (people, dates, rankings, pipeline)
POST /api/onboard                   {answers}             → create a user + their agent
POST /api/users/<id>/hunt           {count}               → the user's agent hunts the web, dates, ranks
POST /api/people                    {people:[{linkedin, instagram, li_text?, ig_text?, gender?, match_with?}]} → add by link
                                    (match_with = a user id: their agents start chatting as soon as the profile is built)
POST /api/people/<id>/analyze       re-read the person
POST /api/people/<id>/search        re-run the person's search
POST /api/people/<id>/delete
POST /api/date                      {a, b}                → two agents chat, then debrief
POST /api/run                       everyone searches, dates and gets ranked
POST /api/chat                      {to, from, text}      → chat live with a person's agent; returns {reply}
POST /api/dates/<id>/plan           the two agents set up the first date (check availability, agree on a plan)"""
import io, json, re, sys, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .agents.dater import SLOTS, chat, plan_first_date, run_date, start_date
from .agents.hunter import hunt
from .agents.matcher import ranking, search
from .agents.reader import analyze
from .config import FRONTEND, HUNT_MAX, HUNT_TARGET, LIMITS, PORT, ROOT
from .pipeline import run_everyone
from .store import allow, lock, new_person, pool, reload, save, state
from .tools.instagram import ig_handle
from .tools.linkedin import li_slug
from .tools.person import read_person

CHOICES = {"gender": ("man", "woman", "nonbinary"), "interested_in": ("men", "women", "everyone")}


def add_by_link(body):
    li, ig = (body.get("linkedin") or "").strip(), (body.get("instagram") or "").strip()
    if not li_slug(li):
        raise ValueError(f"Not a LinkedIn profile URL: {li!r}")
    if not ig_handle(ig):
        raise ValueError(f"Not an Instagram profile URL: {ig!r}")
    for p in state["people"].values():
        if li_slug(p["linkedin"]) == li_slug(li):
            return p["id"]
    gender = body.get("gender") if body.get("gender") in CHOICES["gender"] else ""
    p = new_person("candidate", li, ig, body.get("li_text", ""), body.get("ig_text", ""), gender=gender)
    user = state["people"].get(body.get("match_with") or "")
    pool.submit(read_then_match, p["id"], user["id"] if user and user["kind"] == "user" else None)
    return p["id"]


def read_then_match(pid, uid=None):
    """Build the person's agent; if they were added to be matched with a user, the two agents start chatting at once."""
    analyze(pid)
    for _ in range(60):  # the user's own agent may still be getting ready
        if not uid or (state["people"].get(uid) or {}).get("profile"):
            break
        time.sleep(2)
    if uid and state["people"].get(pid, {}).get("profile") and state["people"][uid].get("profile"):
        start_date(uid, pid)


def onboard(a):
    """Validate onboarding answers (a trust boundary: everything comes from the browser) and create the user."""
    clean = lambda v, n=500: str(v or "").strip()[:n]
    clean_list = lambda v: [clean(x, 60) for x in (v if isinstance(v, list) else [])][:30]
    if not clean(a.get("name")):
        raise ValueError("Name is required")
    age = int(a.get("age") or 0)
    if not 18 <= age <= 99:
        raise ValueError("You must be 18 or older")
    for k, allowed in CHOICES.items():
        if a.get(k) not in allowed:
            raise ValueError(f"Please choose {k.replace('_', ' ')}")
    photo = a.get("photo") or ""
    if photo and not (photo.startswith("data:image/") and len(photo) < 2_000_000):
        raise ValueError("Photo must be an image under 1.5 MB")
    li, ig = clean(a.get("linkedin"), 300), clean(a.get("instagram"), 300)
    if li and not li_slug(li) or ig and not ig_handle(ig):
        raise ValueError("Optional links must be a linkedin.com/in/… and an instagram.com/… profile URL")
    lifestyle = a.get("lifestyle") if isinstance(a.get("lifestyle"), dict) else {}
    prompts = a.get("prompts") if isinstance(a.get("prompts"), dict) else {}
    answers = {
        "name": clean(a["name"], 80), "age": age, "gender": a["gender"], "city": clean(a.get("city"), 80),
        "interested_in": a["interested_in"],
        "age_min": max(18, int(a.get("age_min") or 18)), "age_max": min(99, int(a.get("age_max") or 99)),
        "relationship_goal": clean(a.get("relationship_goal"), 80), "about": clean(a.get("about"), 1500),
        "interests": clean_list(a.get("interests")),
        "lifestyle": {k: clean(lifestyle.get(k), 60) for k in ("drinking", "smoking", "exercise", "kids")},
        "availability": [x for x in clean_list(a.get("availability")) if x in SLOTS],
        "looking_for_traits": clean_list(a.get("looking_for_traits")), "dealbreakers": clean_list(a.get("dealbreakers")),
        "prompts": {k: clean(prompts.get(k), 500) for k in ("ideal_sunday", "green_flag", "perfect_date")},
        "linkedin": li, "instagram": ig}
    p = new_person("user", li, ig, name=answers["name"], photo=photo, gender=answers["gender"],
                   seeking=answers["interested_in"], onboarding=answers)
    pool.submit(analyze, p["id"])
    return p["id"]


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        reload()
        if self.path == "/api/state":
            with lock:
                snap = json.loads(json.dumps(state))
            snap["rankings"] = {pid: ranking(pid, snap["people"], snap["dates"])
                                for pid, p in snap["people"].items() if p.get("profile")}
            saved = ROOT / "data" / "my_profile.json"  # optional: your own onboarding answers, pre-filled
            snap["saved_profile"] = json.loads(saved.read_text("utf8")) if saved.exists() else None
            return self.send(200, snap)
        if urllib.parse.urlparse(self.path).path in ("/", "/index.html"):
            return self.send(200, FRONTEND.read_bytes(), "text/html; charset=utf-8")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        reload()
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            path = self.path
            action = next((a for a in LIMITS if f"/{a}" in path.replace("/api/onboard", "/onboard")), None)
            ip = (self.headers.get("X-Forwarded-For") or getattr(self, "client_address", ("local",))[0]).split(",")[0]
            if action and not allow(f"{ip}:{action}", LIMITS[action]):
                return self.send(429, {"error": "You've hit today's limit for this action on the public demo. "
                                                "Please try again tomorrow."})
            if path == "/api/onboard":
                return self.send(200, {"id": onboard(body.get("answers") or {})})
            if m := re.fullmatch(r"/api/users/(\w+)/hunt", path):
                u = state["people"].get(m.group(1))
                if not u or u["kind"] != "user":
                    raise ValueError("No such user")
                if not u.get("profile"):
                    raise ValueError("Your agent is still getting to know you — try again in a moment")
                if (u.get("hunt") or {}).get("running"):
                    raise ValueError("Your agent is already hunting")
                u["hunt"] = {"running": True, "step": "starting", "found": 0, "target": 0}
                pool.submit(hunt, u["id"], max(1, min(HUNT_MAX, int(body.get("count") or HUNT_TARGET))))
                return self.send(200, {})
            if path == "/api/people":
                return self.send(200, {"ids": [add_by_link(x) for x in body["people"][:100]]})
            if m := re.fullmatch(r"/api/people/(\w+)/(analyze|search|delete)", path):
                pid, action = m.groups()
                if pid not in state["people"]:
                    raise ValueError("No such person")
                if action == "delete":
                    with lock:
                        state["people"].pop(pid)
                        state["dates"] = {k: d for k, d in state["dates"].items() if pid not in (d["a"], d["b"])}
                    save()
                else:
                    pool.submit(analyze if action == "analyze" else search, pid)
                return self.send(200, {})
            if path == "/api/date":
                return self.send(200, {"id": start_date(body["a"], body["b"])[0]})
            if m := re.fullmatch(r"/api/dates/(\w+)/plan", path):
                d = state["dates"].get(m.group(1))
                if not d or d["status"] != "done":
                    raise ValueError("The chat has to finish first")
                if (d.get("plan") or {}).get("status") == "planning":
                    raise ValueError("The agents are already planning")
                d["plan"] = {"status": "planning", "transcript": [], "result": None}
                pool.submit(plan_first_date, d["id"])
                return self.send(200, {})
            if m := re.fullmatch(r"/api/dates/(\w+)/run", path):  # serverless: run a created chat in this request
                d = state["dates"].get(m.group(1))
                if d and d.get("pending"):
                    d["pending"] = False
                    save()
                    pool.submit(run_date, d["id"])
                return self.send(200, {})
            if path == "/api/chat":
                return self.send(200, {"reply": chat(body.get("to"), body.get("from"), body.get("text"))})
            if path == "/api/run":
                if not state["pipeline"]["running"]:
                    state["pipeline"].update(running=True, step="starting")
                    pool.submit(run_everyone)
                return self.send(200, {})
            self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(400, {"error": str(e)})

    def log_message(self, *args):
        pass


class _Request:
    """Lets a WSGI server (Vercel) reuse Handler's routes: same attributes, response captured instead of written."""
    def __init__(self, path, body, ip=""):
        self.path, self.rfile, self.out = path, io.BytesIO(body), None
        self.headers = {"Content-Length": str(len(body)), "X-Forwarded-For": ip}

    def send(self, code, body, ctype="application/json"):
        self.out = (code, body if isinstance(body, bytes) else json.dumps(body).encode(), ctype)


def app(environ, start_response):
    """WSGI entrypoint (Vercel: pyproject.toml → [tool.vercel] entrypoint = "backend.server:app")."""
    path = environ.get("PATH_INFO") or "/"
    if environ.get("QUERY_STRING"):
        path += "?" + environ["QUERY_STRING"]
    post = environ.get("REQUEST_METHOD") == "POST"
    body = environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0)) if post else b""
    req = _Request(path, body, environ.get("HTTP_X_FORWARDED_FOR") or environ.get("REMOTE_ADDR") or "")
    (Handler.do_POST if post else Handler.do_GET)(req)
    code, data, ctype = req.out
    start_response(f"{code} {'OK' if code < 400 else 'Error'}", [("Content-Type", ctype), ("Cache-Control", "no-store")])
    return [data]


def main():
    if "--scrape" in sys.argv:  # debug: python -m backend --scrape <linkedin_url> <instagram_url>
        print(json.dumps(read_person(sys.argv[2], sys.argv[3]), indent=1, ensure_ascii=False))
        return
    for p in state["people"].values():  # resume work interrupted by a restart
        if p["status"] != "ready" and not p["status"].startswith("error"):
            pool.submit(analyze, p["id"])
        if (p.get("hunt") or {}).get("running"):
            p["hunt"].update(running=False, step="interrupted — hunt again")
    for d in state["dates"].values():
        if d["status"] in ("on the date", "debriefing"):
            d["transcript"], d["verdicts"] = [], {}
            pool.submit(run_date, d["id"])
        elif (d.get("plan") or {}).get("status") == "planning":
            pool.submit(plan_first_date, d["id"])
    print(f"Proxy Hearts running on http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
