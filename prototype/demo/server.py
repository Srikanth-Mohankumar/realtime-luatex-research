#!/usr/bin/env python3
"""Local demo bridge: browser <-> a persistent LuaTeX engine, for editing
ONE real .tex document live.

Run:  python3 server.py [path/to/article.tex] [port]
      (default: ../templates/ieeetran/article.tex, port 8123)
Open: http://localhost:8123/

The document needs NO preparation: paragraphs are auto-detected (blank-line
separated prose at environment/brace depth zero) and tagged with \\RTpara
markers in a generated working copy (<name>-marked.tex). The original file
is never modified.

Both halves of the real-time architecture run:
  * FAST PATH — a persistent lualatex with the document's own preamble;
    every keystroke recompiles the edited paragraph with its captured
    context (~1-3 ms) and the page overlays it at the cached position.
  * BACKGROUND CONVERGENCE — after 1.5 s idle, the document with all edits
    applied is recompiled for real, producing a fresh page-position cache
    (glyph display lists captured at shipout); the client re-renders when
    the revision counter bumps.
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
from rtclient import Server, match_para, build_request, lua_val  # noqa: E402

args = [a for a in sys.argv[1:]]
DOC_FILE = Path(args[0]).resolve() if args and not args[0].isdigit() \
    else ROOT / "templates" / "ieeetran" / "article.tex"
PORT = int(args[-1]) if args and args[-1].isdigit() else 8123
DIR = DOC_FILE.parent
STEM = DOC_FILE.stem
ENV = {**os.environ, "TEXINPUTS": str(ROOT / "engine") + ":"}

PARA_RE = re.compile(r"(\\RTpara\{(\d+)\}\n)(.*?)(\n[ \t]*\n|\n?$)", re.S)
PROSE_CMDS = ("\\emph", "\\textbf", "\\textit", "\\IEEEPARstart",
              "\\LaTeX", "\\TeX", "\\noindent")


def auto_mark(body):
    """Insert \\RTpara{n} before each prose paragraph: a blank-line-separated
    block starting with text, at environment AND brace depth zero (so text
    inside \\author{...}, abstract, tables, bibliographies is left alone)."""
    out, pid, env, brace, in_para = [], 0, 0, 0, False
    for line in body.splitlines():
        code = re.sub(r"(?<!\\)%.*", "", line)     # ignore comments
        s = code.strip()
        prose = s and (s[0].isalpha() or s[0] in "`'$" or
                       s.startswith(PROSE_CMDS))
        if env == 0 and brace == 0 and not in_para and prose:
            pid += 1
            out.append("\\RTpara{%d}" % pid)
            in_para = True
        if not s:
            in_para = False
        env += s.count("\\begin{") - s.count("\\end{")
        brace += (s.replace("\\{", "").replace("\\}", "").count("{")
                  - s.replace("\\{", "").replace("\\}", "").count("}"))
        out.append(line)
    return "\n".join(out)


def sanitize(text):
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


def parse_marked(marked_body):
    return {int(m.group(2)): " ".join(
        l.strip() for l in m.group(3).strip().splitlines())
        for m in PARA_RE.finditer(marked_body)}


class Doc:
    def __init__(self):
        self.lock = threading.Lock()
        self.conv_lock = threading.Lock()
        self.edits = {}
        self.rev = 1
        self.converging = False
        self.conv_timer = None

        src = DOC_FILE.read_text()
        m = re.search(r"(.*?)\\begin\{document\}(.*)\\end\{document\}",
                      src, re.S)
        if not m:
            sys.exit(f"{DOC_FILE}: no document environment found")
        self.preamble, body = m.group(1), m.group(2)
        self.marked_body = auto_mark(body)
        self.write_variant(f"{STEM}-marked.tex", self.marked_body)
        self.write_serve()

        print(f"[{STEM}] compiling reference ({DOC_FILE.name})...")
        for _ in range(2):
            r = self.compile_tex(f"{STEM}-marked.tex")
        if "RTCAPTURE: wrote" not in r.stdout:
            print(r.stdout[-2500:])
            sys.exit("reference compile failed")
        self.load_capture(DIR / f"{STEM}-marked-capture.json")
        print(f"[{STEM}] {len(self.body)} editable paragraphs, "
              f"{len(self.pages)} pages")
        self.spawn()

    def write_variant(self, name, body):
        (DIR / name).write_text(
            self.preamble + "\\usepackage{rtcapture}\n"
            + "\\begin{document}" + body + "\\end{document}\n")

    def write_serve(self):
        (DIR / f"{STEM}-serve.tex").write_text(
            self.preamble + "\\begin{document}\n"
            "\\directlua{dofile(\"" + str(ROOT / "engine" / "serve2.lua")
            + "\")}%\n"
            "\\directlua{RTLOADAUX(\"" + f"{STEM}-marked.aux" + "\")}%\n"
            "\\newif\\ifserving \\servingtrue\n"
            "\\loop\\directlua{SERVE_ONE()}\\ifserving\\repeat\n"
            "\\end{document}\n")

    def compile_tex(self, name):
        return subprocess.run(
            ["lualatex", "-interaction=nonstopmode", name],
            cwd=DIR, capture_output=True, text=True, env=ENV, timeout=300)

    def load_capture(self, path):
        data = json.loads(path.read_text())
        self.captured = data["paras"]
        self.pages = data.get("pages", [])
        self.fonts = data.get("fonts", {})
        body = parse_marked(self.marked_body)
        body.update(self.edits)
        self.body = body
        self.caps = {pid: match_para(pid, srctext, self.captured)
                     for pid, srctext in body.items()}

    def spawn(self):
        t0 = time.perf_counter()
        self.srv = Server(DIR, f"{STEM}-serve.tex")
        fonts = sorted({(p["font_name"], p["font_size"])
                        for p in self.captured if p["font_name"]})
        self.srv.request(lua_val({"preload": [list(f) for f in fonts]}))
        print(f"[{STEM}] engine ready in "
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
                def repl(m):
                    pid = int(m.group(2))
                    if pid in self.edits:
                        return m.group(1) + self.edits[pid] + m.group(4)
                    return m.group(0)
                self.write_variant(f"{STEM}-live.tex",
                                   PARA_RE.sub(repl, self.marked_body))
                marked_aux = DIR / f"{STEM}-marked.aux"
                live_aux = DIR / f"{STEM}-live.aux"
                if marked_aux.exists() and not live_aux.exists():
                    shutil.copy(marked_aux, live_aux)
                r = self.compile_tex(f"{STEM}-live.tex")
                if "RTCAPTURE: wrote" not in r.stdout:
                    print(f"[{STEM}] convergence compile failed")
                    return
                self.load_capture(DIR / f"{STEM}-live-capture.json")
                self.rev += 1
                print(f"[{STEM}] converged rev {self.rev} in "
                      f"{time.perf_counter() - t0:.2f} s")
            finally:
                self.converging = False

    def doc(self):
        paras = []
        for pid, srctext in sorted(self.body.items()):
            cap = self.caps.get(pid)
            paras.append({
                "id": pid, "text": srctext,
                "sig": cap["sig"] if cap else [],
                "font_size": cap["font_size"] if cap else 655360,
            })
        return {"name": DOC_FILE.name, "rev": self.rev, "paras": paras,
                "pages": self.pages, "fonts": self.fonts}


THE_DOC = None


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
        elif self.path.startswith("/api/doc"):
            self.send_json(THE_DOC.doc())
        elif self.path.startswith("/api/rev"):
            self.send_json({"rev": THE_DOC.rev,
                            "converging": THE_DOC.converging})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or "{}")
        if self.path == "/api/compile":
            self.send_json(THE_DOC.compile(int(body["id"]), body["text"]))
        elif self.path == "/api/restart":
            with THE_DOC.lock:
                THE_DOC.respawn()
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    THE_DOC = Doc()
    print(f"editing {DOC_FILE}")
    print(f"demo at http://localhost:{PORT}/")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
