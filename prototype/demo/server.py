#!/usr/bin/env python3
"""Local demo bridge: browser <-> persistent LuaTeX paragraph servers.

Run:  python3 server.py [port]         (from prototype/demo/)
Open: http://localhost:8123/

Implements both halves of the real-time architecture:
  * FAST PATH — one persistent lualatex per template; every keystroke
    recompiles the edited paragraph with its captured context (~1-3 ms).
  * BACKGROUND CONVERGENCE — after 1.5 s of idle, the document body with
    all edits applied is recompiled for real (sample-live.tex), producing a
    fresh page-position cache (full page display lists at shipout). The
    client polls a revision counter and re-renders the pages when it bumps.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "client"))
from rtclient import Server, parse_body, match_para, build_request, lua_val  # noqa: E402

TPL = ROOT / "templates"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
ENV = {**os.environ, "TEXINPUTS": "../../engine:"}

TEMPLATES = sorted(d.name for d in TPL.iterdir()
                   if (d / "server.tex").exists())

BODY_SRC = (TPL / "body-shared.tex").read_text()
PARA_RE = re.compile(r"(\\RTpara\{(\d+)\}\n)(.*?)(\n[ \t]*\n)", re.S)


def sanitize(text):
    """Make mid-edit input survivable: single line, comments stripped,
    $ and braces balanced. Not bulletproof — the watchdog handles the rest."""
    lines = [re.sub(r"(?<!\\)%.*", "", l) for l in text.splitlines()]
    text = " ".join(l.strip() for l in lines).strip()
    if text.count("$") % 2:
        text += "$"
    diff = text.count("{") - text.count("}")
    if diff > 0:
        text += "}" * diff
    elif diff < 0:
        text = "{" * -diff + text
    return text


def substituted_body(edits):
    def repl(m):
        pid = int(m.group(2))
        if pid in edits:
            return m.group(1) + edits[pid] + m.group(4)
        return m.group(0)
    return PARA_RE.sub(repl, BODY_SRC)


class Session:
    def __init__(self, name):
        self.name = name
        self.dir = TPL / name
        self.lock = threading.Lock()          # serializes engine requests
        self.conv_lock = threading.Lock()     # serializes convergence runs
        self.edits = {}
        self.rev = 1
        self.converging = False
        self.conv_timer = None
        if not (self.dir / "sample-capture.json").exists():
            print(f"[{name}] compiling sample (capture missing)...")
            for _ in range(2):
                subprocess.run(
                    ["lualatex", "-interaction=nonstopmode", "sample.tex"],
                    cwd=self.dir, capture_output=True, env=ENV)
        self.load_capture(self.dir / "sample-capture.json",
                          parse_body(TPL / "body-shared.tex"))
        self.spawn()

    def load_capture(self, path, body):
        data = json.loads(path.read_text())
        self.captured = data["paras"]
        self.pages = data.get("pages", [])
        self.fonts = data.get("fonts", {})
        self.body = body
        self.caps = {pid: match_para(pid, src, self.captured)
                     for pid, src in body.items()}

    def spawn(self):
        t0 = time.perf_counter()
        self.srv = Server(self.dir)
        fonts = sorted({(p["font_name"], p["font_size"])
                        for p in self.captured if p["font_name"]})
        self.srv.request(lua_val({"preload": [list(f) for f in fonts]}))
        print(f"[{self.name}] engine ready in "
              f"{(time.perf_counter() - t0) * 1000:.0f} ms")

    def respawn(self):
        try:
            self.srv.proc.kill()
        except Exception:
            pass
        self.spawn()

    # ---- fast path ----
    def compile(self, pid, text):
        cap = self.caps.get(pid)
        if cap is None:
            return {"error": f"no captured context for paragraph {pid}"}
        clean = sanitize(text)
        req = build_request(clean, cap)
        with self.lock:
            box = {}

            def work():
                try:
                    box["r"] = self.srv.request(req)
                except Exception as e:
                    box["e"] = str(e)

            t = threading.Thread(target=work, daemon=True)
            t.start()
            t.join(timeout=10)
            if t.is_alive() or "e" in box:
                self.respawn()
                return {"error": "engine wedged by this input; respawned — "
                                 + box.get("e", "timeout")}
        rt, resp = box["r"]
        resp["rt_ms"] = round(rt, 3)
        resp["rev"] = self.rev
        self.edits[pid] = clean
        self.schedule_convergence()
        return resp

    # ---- background convergence ----
    def schedule_convergence(self, delay=1.5):
        if self.conv_timer:
            self.conv_timer.cancel()
        self.conv_timer = threading.Timer(delay, self.converge)
        self.conv_timer.daemon = True
        self.conv_timer.start()

    def converge(self):
        with self.conv_lock:
            self.converging = True
            t0 = time.perf_counter()
            try:
                (self.dir / "body-live.tex").write_text(
                    substituted_body(self.edits))
                live = self.dir / "sample-live.tex"
                if not live.exists():
                    live.write_text(
                        "\\input{preamble.tex}\n\\usepackage{rtcapture}\n"
                        "\\begin{document}\n\\input{frontmatter.tex}\n"
                        "\\input{body-live.tex}\n\\end{document}\n")
                    # seed converged aux so \ref/\cite are right on run 1
                    if (self.dir / "sample.aux").exists():
                        shutil.copy(self.dir / "sample.aux",
                                    self.dir / "sample-live.aux")
                r = subprocess.run(
                    ["lualatex", "-interaction=nonstopmode", "sample-live.tex"],
                    cwd=self.dir, capture_output=True, text=True, env=ENV,
                    timeout=120)
                if "RTCAPTURE: wrote" not in r.stdout:
                    print(f"[{self.name}] convergence compile failed")
                    return
                body = dict(parse_body(TPL / "body-shared.tex"))
                body.update(self.edits)
                self.load_capture(self.dir / "sample-live-capture.json", body)
                self.rev += 1
                print(f"[{self.name}] converged rev {self.rev} in "
                      f"{time.perf_counter() - t0:.2f} s")
            finally:
                self.converging = False

    def doc(self):
        paras = []
        for pid, src in sorted(self.body.items()):
            cap = self.caps.get(pid)
            paras.append({
                "id": pid, "text": src,
                "sig": cap["sig"] if cap else [],
                "hsize": cap["hsize"] if cap else 0,
                "font_size": cap["font_size"] if cap else 655360,
            })
        return {"template": self.name, "rev": self.rev, "paras": paras,
                "pages": self.pages, "fonts": self.fonts}


SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


def session(name):
    if name not in TEMPLATES:
        raise ValueError("unknown template")
    with SESSIONS_LOCK:
        if name not in SESSIONS:
            SESSIONS[name] = Session(name)
        return SESSIONS[name]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            data = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/templates":
            self.send_json(TEMPLATES)
        elif self.path.startswith("/api/doc"):
            name = self.path.split("template=")[1].split("&")[0]
            self.send_json(session(name).doc())
        elif self.path.startswith("/api/rev"):
            name = self.path.split("template=")[1].split("&")[0]
            s = session(name)
            self.send_json({"rev": s.rev, "converging": s.converging})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or "{}")
        if self.path == "/api/compile":
            s = session(body["template"])
            self.send_json(s.compile(int(body["id"]), body["text"]))
        elif self.path == "/api/restart":
            s = session(body["template"])
            with s.lock:
                s.respawn()
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    print(f"templates: {', '.join(TEMPLATES)}")
    print(f"demo at http://localhost:{PORT}/  (engines spawn on first use)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
