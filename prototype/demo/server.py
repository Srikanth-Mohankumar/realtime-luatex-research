#!/usr/bin/env python3
"""Local demo bridge: browser <-> persistent LuaTeX paragraph servers.

Run:  python3 server.py [port]         (from prototype/demo/)
Open: http://localhost:8123/

One persistent lualatex process per journal template, spawned on first use
(same Server class as the test client). The page edits one paragraph at a
time; every keystroke round-trips through the real engine and re-renders
from the returned display list. A watchdog respawns a wedged engine.
"""
import json
import re
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

TEMPLATES = sorted(d.name for d in TPL.iterdir()
                   if (d / "server.tex").exists())


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


class Session:
    def __init__(self, name):
        self.name = name
        self.dir = TPL / name
        self.lock = threading.Lock()
        if not (self.dir / "sample-capture.json").exists():
            print(f"[{name}] compiling sample (capture missing)...")
            import os
            env = {**os.environ, "TEXINPUTS": "../../engine:"}
            for _ in range(2):
                subprocess.run(
                    ["lualatex", "-interaction=nonstopmode", "sample.tex"],
                    cwd=self.dir, capture_output=True, env=env)
        self.captured = json.loads(
            (self.dir / "sample-capture.json").read_text())["paras"]
        self.body = parse_body(TPL / "body-shared.tex")
        self.caps = {pid: match_para(pid, src, self.captured)
                     for pid, src in self.body.items()}
        self.spawn()

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

    def compile(self, pid, text):
        cap = self.caps.get(pid)
        if cap is None:
            return {"error": f"no captured context for paragraph {pid}"}
        req = build_request(sanitize(text), cap)
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
        return resp


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
            s = session(name)
            paras = []
            for pid, src in sorted(s.body.items()):
                cap = s.caps.get(pid)
                paras.append({
                    "id": pid, "text": src,
                    "sig": cap["sig"] if cap else [],
                    "hsize": cap["hsize"] if cap else 0,
                    "font_size": cap["font_size"] if cap else 655360,
                })
            self.send_json({"template": name, "paras": paras})
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
