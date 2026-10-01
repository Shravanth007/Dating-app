"""HTTP API + static frontend.  python -m app  ->  http://localhost:8000"""
import json, os, re, threading, urllib.parse, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from .agents import analyze, pipeline, ranking, run_date, search, start_date
from .core import ROOT, lock, pool, save, state
from .scrapers import ig_handle, instagram, li_slug, linkedin


def add_person(body):
    li, ig = body.get("linkedin", "").strip(), body.get("instagram", "").strip()
    if not li_slug(li):
        raise ValueError(f"Not a LinkedIn profile URL: {li!r}")
    if not ig_handle(ig):
        raise ValueError(f"Not an Instagram profile URL: {ig!r}")
    with lock:
        for p in state["people"].values():
            if li_slug(p["linkedin"]) == li_slug(li):
                return p["id"]
        pid = uuid.uuid4().hex[:8]
        state["people"][pid] = {
            "id": pid, "name": "", "linkedin": li, "instagram": ig, "photo": "",
            "gender": body.get("gender", ""), "seeking": body.get("seeking") or "everyone",
            "sources": {"linkedin": {"via": "", "data": {}, "error": "", "pasted": body.get("li_text", "")[:20000]},
                        "instagram": {"via": "", "data": {}, "error": "", "pasted": body.get("ig_text", "")[:20000]}},
            "profile": None, "search": None, "status": "queued"}
    save()
    pool.submit(analyze, pid)
    return pid


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/state":
            with lock:
                snap = json.loads(json.dumps(state))
            snap["rankings"] = {pid: ranking(pid, snap["people"], snap["dates"])
                                for pid, p in snap["people"].items() if p.get("profile")}
            return self.send(200, snap)
        if urllib.parse.urlparse(self.path).path in ("/", "/index.html"):
            return self.send(200, (ROOT / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/people":
                return self.send(200, {"ids": [add_person(x) for x in body["people"][:100]]})
            if m := re.fullmatch(r"/api/people/(\w+)/(analyze|search|delete)", self.path):
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
            if self.path == "/api/date":
                return self.send(200, {"id": start_date(body["a"], body["b"])[0]})
            if self.path == "/api/run":
                if not state["pipeline"]["running"]:
                    state["pipeline"].update(running=True, step="starting")
                    threading.Thread(target=pipeline, daemon=True).start()
                return self.send(200, {})
            self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(400, {"error": str(e)})

    def log_message(self, *a):
        pass


def main():
    import sys
    if "--scrape" in sys.argv:  # python -m app --scrape <linkedin_url> <instagram_url>
        print(json.dumps({"linkedin": linkedin(li_slug(sys.argv[2])), "instagram": instagram(ig_handle(sys.argv[3]))},
                         indent=1, ensure_ascii=False))
    else:
        for p in state["people"].values():  # resume anything interrupted
            if p["status"] not in ("ready",) and not p["status"].startswith("error"):
                pool.submit(analyze, p["id"])
        for d in state["dates"].values():
            if d["status"] in ("on the date", "debriefing"):
                d["transcript"], d["verdicts"] = [], {}
                pool.submit(run_date, d["id"])
        port = int(os.environ.get("PORT", 8000))
        print(f"http://localhost:{port}")
        ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
