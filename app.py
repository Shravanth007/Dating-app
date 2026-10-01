"""Agentic dating site. One server, one page. Run: python app.py  ->  http://localhost:8000"""
import html, json, os, re, threading, urllib.request, uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5-5"
DATA = Path(__file__).with_name("data.json")
DATES_PER_PERSON = 3  # ponytail: top-K prescreen, not all n² pairs; raise for a full round robin
DATE_TURNS = 8
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"

lock = threading.RLock()
pool = ThreadPoolExecutor(8)
state = json.loads(DATA.read_text("utf8")) if DATA.exists() else {"people": {}, "dates": {}}
_client = None


def llm():
    global _client
    _client = _client or anthropic.Anthropic()
    return _client


def save():
    with lock:
        DATA.write_text(json.dumps(state, indent=1), "utf8")


# ---------------- sources: LinkedIn + Instagram, nothing else ----------------

def li_slug(url):
    m = re.match(r"^https?://([a-z]{2,3}\.)?(www\.)?linkedin\.com/in/([A-Za-z0-9\-_%]+)/?", url.strip())
    return m and m.group(3)


def ig_handle(url):
    m = re.match(r"^https?://(www\.)?instagram\.com/([A-Za-z0-9._]{1,30})/?", url.strip())
    return m and m.group(2) not in ("p", "reel", "explore", "accounts") and m.group(2)


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf8", "replace")


def metas(page):
    out = {}
    for m in re.finditer(r'<meta[^>]+(?:property|name)="([^"]+)"[^>]+content="([^"]*)"', page):
        out[m.group(1)] = html.unescape(m.group(2))
    return out


def fetch_linkedin(slug):
    # Only the guest view of the public profile is fetched (URL built from the slug, no SSRF).
    page = get(f"https://www.linkedin.com/in/{slug}/")
    m = metas(page)
    parts = [m.get("og:title", ""), m.get("description", "")]
    for ld in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        parts.append(re.sub(r"\s+", " ", ld)[:6000])
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        raise ValueError("LinkedIn returned no public data (login wall)")
    return {"text": text, "name": m.get("og:title", "").split(" - ")[0].split(" | ")[0], "photo": m.get("og:image", "")}


def fetch_instagram(handle):
    # The private API 429s from servers; the public profile page served to crawlers embeds bio + recent captions.
    page = get(f"https://www.instagram.com/{handle}/", {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
    m = metas(page)
    if not m.get("og:description"):
        raise ValueError("Instagram profile not found or not public")
    if re.search(rf'"username":"{re.escape(handle)}"[^{{}}]*"is_private":true|"is_private":true[^{{}}]*"username":"{re.escape(handle)}"', page, re.I):
        raise ValueError("Instagram profile is private — only public profiles are allowed")
    js = lambda s: json.loads(f'"{s}"')
    bio = re.search(r'"biography":"((?:[^"\\]|\\.)*)"', page)
    caps = list(dict.fromkeys(js(c) for c in re.findall(r'"caption":\{"pk":"\d+","text":"((?:[^"\\]|\\.)*)"', page)))
    text = (f"{m.get('og:title', '')}\n{m.get('og:description', '')}\nBio: {js(bio.group(1)) if bio else ''}\n"
            "Recent post captions:\n- " + "\n- ".join(c[:400] for c in caps[:15]))
    return {"text": text, "name": m.get("og:title", "").split(" (")[0], "photo": m.get("og:image", "")}


# ---------------- agent 1: read the person ----------------

class Evidence(BaseModel):
    source: str  # "LinkedIn" | "Instagram"
    signal: str  # what was observed
    inference: str  # what the agent concluded


class Profile(BaseModel):
    name: str
    headline: str
    summary: str
    needs: list[str]
    hobbies: list[str]
    interests: list[str]
    values: list[str]
    personality: list[str]
    communication_style: str
    lifestyle: str
    looking_for: str
    green_flags: list[str]
    possible_dealbreakers: list[str]
    ideal_first_date: str
    evidence: list[Evidence]
    data_confidence: str


ANALYZE = """You are a personal dating agent. You are about to represent this person and go on dates on their behalf.
Your ONLY two sources are their public LinkedIn and public Instagram, below. Use nothing else; no outside knowledge.
Read both carefully and build their dating profile: needs (what they need from a partner), hobbies, interests, values,
personality, communication style, lifestyle, what they are likely looking for, green flags, possible dealbreakers, and an
ideal first date. Ground every claim in the sources; list the key evidence (source, signal, inference). Where you infer,
infer sensibly; do not invent facts. Do not infer sexual orientation, religion, health or ethnicity. In data_confidence,
say how much the sources revealed.

=== LinkedIn ({li_url}) ===
{li}

=== Instagram ({ig_url}) ===
{ig}"""


def analyze(pid):
    p = state["people"][pid]
    try:
        for key, slug_fn, fetch in (("linkedin", li_slug, fetch_linkedin), ("instagram", ig_handle, fetch_instagram)):
            src = p["sources"][key]
            pasted = src.get("pasted", "").strip()
            set_status(pid, f"reading {key}…")
            try:
                got = fetch(slug_fn(p[key]))
                src.update(fetched=got["text"], error="")
                p["name"] = p["name"] or got["name"]
                p["photo"] = p["photo"] or got["photo"]
            except Exception as e:
                src.update(fetched="", error=str(e))
                if not pasted:
                    raise ValueError(f"{key}: {e}")
        set_status(pid, "analyzing…")
        text = lambda k: "\n\n".join(x for x in (p["sources"][k].get("fetched"), p["sources"][k].get("pasted")) if x)
        r = llm().messages.parse(
            model=MODEL, max_tokens=16000, output_config={"effort": "medium"}, output_format=Profile,
            messages=[{"role": "user", "content": ANALYZE.format(li_url=p["linkedin"], ig_url=p["instagram"],
                                                                 li=text("linkedin"), ig=text("instagram"))}])
        with lock:
            p["profile"] = r.parsed_output.model_dump()
            p["name"] = p["name"] or p["profile"]["name"]
        set_status(pid, "ready")
    except Exception as e:
        set_status(pid, f"error: {e}")


def set_status(pid, s):
    with lock:
        state["people"][pid]["status"] = s
    save()


# ---------------- agents 2: the date ----------------

class Verdict(BaseModel):
    score: int  # 0-100, how good a fit for MY person
    chemistry: str
    shared_ground: list[str]
    friction: list[str]
    second_date: bool
    summary: str


def persona(p):
    return (f"You are the AI dating agent for {p['name']}. You go on dates on their behalf and speak as them, in first "
            f"person, using only what you know from their profile:\n{json.dumps(p['profile'], indent=1)}\n"
            "Be natural, warm and specific — ask real questions, share real details from the profile, notice "
            "where you click and where you don't. Never invent facts that contradict the profile.")


def transcript_text(d):
    return "\n".join(f"{state['people'][t['who']]['name']}: {t['text']}" for t in d["transcript"]) or "(nothing yet)"


def run_date(did):
    d = state["dates"][did]
    a, b = state["people"][d["a"]], state["people"][d["b"]]
    try:
        d["venue"] = d["venue"] or a["profile"]["ideal_first_date"]
        for turn in range(DATE_TURNS):
            me, other = (a, b) if turn % 2 == 0 else (b, a)
            ask = (f"You're on a first date with {other['name']} ({other['profile']['headline']}). Setting: {d['venue']}.\n"
                   f"Conversation so far:\n{transcript_text(d)}\n\n"
                   + ("Open the date." if turn == 0 else "Reply to them.") +
                   " Write only your next line of dialogue (1-3 sentences, an action in *asterisks* is ok).")
            r = llm().messages.create(model=MODEL, max_tokens=2000, output_config={"effort": "low"},
                                      system=persona(me), messages=[{"role": "user", "content": ask}])
            with lock:
                d["transcript"].append({"who": me["id"], "text": next(c.text for c in r.content if c.type == "text").strip()})
            save()
        for me, other in ((a, b), (b, a)):
            r = llm().messages.parse(
                model=MODEL, max_tokens=4000, output_config={"effort": "low"}, output_format=Verdict, system=persona(me),
                messages=[{"role": "user", "content": f"The date with {other['name']} is over. Transcript:\n"
                           f"{transcript_text(d)}\n\nPrivately debrief your person: how good a match is "
                           f"{other['name']} for {me['name']}, 0-100? Be honest, not polite."}])
            with lock:
                d["verdicts"][me["id"]] = r.parsed_output.model_dump()
        d["status"] = "done"
    except Exception as e:
        d["status"] = f"error: {e}"
    save()


def start_date(a, b):
    with lock:
        for d in state["dates"].values():
            if {d["a"], d["b"]} == {a, b} and not d["status"].startswith("error"):
                return d["id"]
        did = uuid.uuid4().hex[:8]
        state["dates"][did] = {"id": did, "a": a, "b": b, "venue": "", "transcript": [], "verdicts": {}, "status": "on the date"}
    save()
    pool.submit(run_date, did)
    return did


# ---------------- matching + ranking ----------------

def words(prof):
    blob = " ".join(prof[k] if isinstance(prof[k], str) else " ".join(prof[k])
                    for k in ("hobbies", "interests", "values", "needs", "lifestyle"))
    return {w for w in re.findall(r"[a-z]{4,}", blob.lower())}


def open_to(p, q):
    s, g = p.get("seeking", "everyone"), q.get("gender", "")
    return s == "everyone" or not g or s == g


def compatible(p, q):
    return p["id"] != q["id"] and open_to(p, q) and open_to(q, p)


def prescreen(p, q):
    a, b = words(p["profile"]), words(q["profile"])
    return len(a & b) / (len(a | b) or 1)  # ponytail: Jaccard on profile words, embeddings if this ranks badly


def ranking(pid, people, dates):
    p = people[pid]
    rows = []
    for q in people.values():
        if not q.get("profile") or not compatible(p, q):
            continue
        d = next((d for d in dates.values() if {d["a"], d["b"]} == {pid, q["id"]} and d["status"] == "done"), None)
        if d:  # my agent's verdict weighs more than theirs, but it has to be mutual
            mine, theirs = d["verdicts"][pid], d["verdicts"][q["id"]]
            rows.append({"id": q["id"], "score": round(0.6 * mine["score"] + 0.4 * theirs["score"]), "dated": d["id"],
                         "why": mine["summary"], "second_date": mine["second_date"] and theirs["second_date"]})
        else:
            rows.append({"id": q["id"], "score": round(60 * prescreen(p, q)), "dated": None,
                         "why": "Not dated yet — estimate from profile overlap.", "second_date": False})
    return sorted(rows, key=lambda r: (r["dated"] is not None, r["score"]), reverse=True)


def speed_round():
    ready = [p for p in state["people"].values() if p.get("profile")]
    pairs = set()
    for p in ready:
        cands = sorted((q for q in ready if compatible(p, q)), key=lambda q: prescreen(p, q), reverse=True)
        pairs |= {frozenset((p["id"], q["id"])) for q in cands[:DATES_PER_PERSON]}
    return [start_date(*sorted(pr)) for pr in pairs]


# ---------------- HTTP ----------------

def add_person(body):
    li, ig = body.get("linkedin", "").strip(), body.get("instagram", "").strip()
    if not li_slug(li):
        raise ValueError(f"Not a LinkedIn profile URL: {li!r}")
    if not ig_handle(ig):
        raise ValueError(f"Not an Instagram profile URL: {ig!r}")
    pid = uuid.uuid4().hex[:8]
    with lock:
        state["people"][pid] = {
            "id": pid, "name": body.get("name", "").strip(), "linkedin": li, "instagram": ig, "photo": "",
            "gender": body.get("gender", ""), "seeking": body.get("seeking") or "everyone",
            "sources": {"linkedin": {"pasted": body.get("li_text", "")}, "instagram": {"pasted": body.get("ig_text", "")}},
            "profile": None, "status": "queued"}
    save()
    pool.submit(analyze, pid)
    return pid


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/state":
            with lock:
                snap = json.loads(json.dumps(state))
            snap["rankings"] = {pid: ranking(pid, snap["people"], snap["dates"])
                                for pid, p in snap["people"].items() if p.get("profile")}
            return self.send(200, snap)
        self.send(200, Path(__file__).with_name("index.html").read_bytes(), "text/html; charset=utf-8")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        try:
            if self.path == "/api/people":
                return self.send(200, {"ids": [add_person(x) for x in body["people"]]})
            if m := re.fullmatch(r"/api/people/(\w+)/analyze", self.path):
                pool.submit(analyze, m.group(1))
                return self.send(200, {})
            if m := re.fullmatch(r"/api/people/(\w+)/delete", self.path):
                with lock:
                    state["people"].pop(m.group(1), None)
                    state["dates"] = {k: d for k, d in state["dates"].items() if m.group(1) not in (d["a"], d["b"])}
                save()
                return self.send(200, {})
            if self.path == "/api/date":
                return self.send(200, {"id": start_date(body["a"], body["b"])})
            if self.path == "/api/round":
                return self.send(200, {"ids": speed_round()})
            self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(400, {"error": str(e)})

    def log_message(self, *a):
        pass


def selftest():
    prof = lambda *w: {"hobbies": list(w), "interests": [], "values": [], "needs": [], "lifestyle": ""}
    ppl = {"a": {"id": "a", "profile": prof("climbing", "jazz"), "seeking": "everyone", "gender": "men"},
           "b": {"id": "b", "profile": prof("climbing", "jazz"), "seeking": "everyone", "gender": ""},
           "c": {"id": "c", "profile": prof("knitting"), "seeking": "everyone", "gender": ""},
           "d": {"id": "d", "profile": prof("climbing"), "seeking": "women", "gender": "men"}}
    v = lambda s: {"score": s, "summary": "", "second_date": True}
    dates = {"x": {"id": "x", "a": "a", "b": "c", "status": "done", "verdicts": {"a": v(40), "c": v(90)}}}
    r = ranking("a", ppl, dates)
    assert [x["id"] for x in r] == ["c", "b"], r  # dated first; d excluded (seeks women, a is a man)
    assert r[0]["score"] == 60 and r[1]["score"] == 60
    assert li_slug("https://www.linkedin.com/in/jane-doe/") == "jane-doe" and not li_slug("https://evil.com/in/x")
    assert ig_handle("https://instagram.com/jane.doe") == "jane.doe" and not ig_handle("https://instagram.com/p/abc")
    print("selftest ok")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        selftest()
    else:
        for p in state["people"].values():  # resume anything interrupted
            if p["status"] not in ("ready",) and not p["status"].startswith("error"):
                pool.submit(analyze, p["id"])
        for d in state["dates"].values():
            if d["status"] == "on the date":
                d["transcript"] = []
                pool.submit(run_date, d["id"])
        port = int(os.environ.get("PORT", 8000))
        print(f"http://localhost:{port}")
        ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
