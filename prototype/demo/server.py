#!/usr/bin/env python3
"""Local demo bridge: browser <-> a persistent LuaTeX engine, for editing
ONE production-marked .tex document live.

Run:  python3 server.py [path/to/article.tex] [port]
      (default: ../templates/ieeetran/article.tex, port 8123)
Open: http://localhost:8123/

Paragraphs are identified by the PRODUCTION wrapper convention:

  \\tagStructPara{}\\paraid{para10}\\NeoParStart{T}he text ...\\tagStructParaEnd{}%

\\paraid{<id>} plants the paragraph attribute during the capture pass
(rtcapture.sty redefines it), so captured contexts, page glyphs, and edit
requests are all keyed by the production paragraph id. The browser edits
the FULL source file; the paragraph under the cursor takes the fast path,
and the full edited source recompiles in the background (convergence).
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

PARA_RE = re.compile(
    r"\\paraid\{([\w.-]+)\}(.*?)(?:\\tagStructParaEnd\{\}|\n[ \t]*\n|$)", re.S)


def parse_paras(source):
    """{paraid: paragraph source text (lines joined)} from production marks."""
    return {m.group(1): " ".join(
        l.strip() for l in m.group(2).strip().splitlines())
        for m in PARA_RE.finditer(source)}


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


def with_rtcapture(source):
    return source.replace("\\begin{document}",
                          "\\usepackage{rtcapture}\n\\begin{document}", 1)


class ConvEngine:
    """Convergence engine: preamble loaded once, body typeset on request
    (converge-loop.lua). Runs in --draftmode: shipout callbacks fire (so
    the capture is produced) but no PDF is written. Used SINGLE-SHOT; a
    unique jobname keeps concurrent engines' log/aux files apart."""

    _seq = 0

    def __init__(self, preamble):
        ConvEngine._seq += 1
        self.jobname = f"{STEM}-conv{ConvEngine._seq}"
        (DIR / f"{STEM}-conv.tex").write_text(
            preamble + "\\usepackage{rtcapture}\n\\begin{document}\n"
            "\\directlua{dofile(\""
            + str(ROOT / "engine" / "converge-loop.lua") + "\")}%\n"
            "\\newif\\ifserving \\servingtrue\n"
            "\\loop\\directlua{CONV_ONE()}\\ifserving\\repeat\n"
            "\\end{document}\n")
        t0 = time.perf_counter()
        # no --draftmode: each run also produces the REAL PDF; the trailer
        # is finalized when the engine QUITs after its single run
        self.proc = subprocess.Popen(
            ["lualatex", "-interaction=nonstopmode",
             f"-jobname={self.jobname}", f"{STEM}-conv.tex"],
            cwd=DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1, env=ENV)
        for line in self.proc.stdout:
            if "CONVREADY" in line:
                break
        else:
            raise RuntimeError("convergence engine never became ready")
        print(f"[{STEM}] convergence engine ready in "
              f"{time.perf_counter() - t0:.1f} s (preamble resident)")

    def run(self, bodyfile, outjson, auxfile, timeout=90):
        box = {}

        def work():
            try:
                self.proc.stdin.write(f"RUN {bodyfile} {outjson} {auxfile}\n")
                self.proc.stdin.flush()
                for line in self.proc.stdout:
                    if "CONVDONE" in line:
                        box["ms"] = float(line.rsplit("CONVDONE", 1)[1])
                        return
                    if "CONVERR" in line:
                        box["err"] = line.strip()
                        return
                box["err"] = "engine stream closed"
            except Exception as e:
                box["err"] = str(e)

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            box["err"] = "timeout"
        return box

    def quit(self):
        """Graceful end: \\end{document} runs so the PDF trailer is
        written and the run's PDF becomes valid."""
        try:
            self.proc.stdin.write("QUIT\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass


class Doc:
    def __init__(self):
        self.lock = threading.Lock()
        self.conv_lock = threading.Lock()
        self.rev = 1
        self.converging = False
        self.conv_timer = None
        self.source = DOC_FILE.read_text()

        print(f"[{STEM}] compiling reference ({DOC_FILE.name})...")
        (DIR / f"{STEM}-live.tex").write_text(with_rtcapture(self.source))
        for _ in range(2):
            r = self.compile_tex(f"{STEM}-live.tex")
        if "RTCAPTURE: wrote" not in r.stdout:
            print(r.stdout[-2500:])
            sys.exit("reference compile failed")
        self.write_serve()
        self.load_capture()
        print(f"[{STEM}] {len(self.body)} paragraphs (\\paraid), "
              f"{len(self.pages)} pages")
        self.spawn()
        self.preamble = self.split_source(self.source)[0]
        # Convergence engines are SINGLE-SHOT: validation showed the
        # production template's float machinery leaks state across body
        # re-runs (run 2 lost the figure pages). Each engine is used for
        # exactly one repagination — guaranteed first-run semantics, which
        # we verified byte-identical to a fresh compile. A POOL of warm
        # engines absorbs bursts of structural edits; refills happen in
        # the background off the critical path.
        self.pool = []
        self.pool_lock = threading.Lock()
        self.pool_target = 2
        self.pool.append(ConvEngine(self.preamble))
        self.prewarm_async()
        self.start_watcher()
        # print view: page PNGs of the latest real PDF
        self.pdf_rev = 0
        self.png_dir = None
        self.render_pngs_async(DIR / f"{STEM}-live.pdf", self.rev)

    def render_pngs_async(self, pdfpath, rev, proc=None):
        """Rasterize a converged PDF's pages for the print view. If proc is
        given (a quitting engine), wait for it to exit first so the PDF
        trailer is written."""
        def work():
            try:
                if proc is not None:
                    # drain stdout until EOF: the quitting engine still
                    # writes \end{document} chatter and would BLOCK on a
                    # full pipe, never exiting and never finalizing the PDF
                    try:
                        for _ in proc.stdout:
                            pass
                    except Exception:
                        pass
                    proc.wait(timeout=120)
                if not Path(pdfpath).exists():
                    return
                outdir = DIR / f"{STEM}-pngs-r{rev}"
                outdir.mkdir(exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-png", "-r", "110", str(pdfpath),
                     str(outdir / "p")],
                    capture_output=True, timeout=300)
                old = self.png_dir
                self.png_dir, self.pdf_rev = outdir, rev
                print(f"[{STEM}] print view ready for rev {rev}")
                if old and old != outdir:
                    import shutil as sh
                    sh.rmtree(old, ignore_errors=True)
                if "-conv" in Path(pdfpath).name:
                    Path(pdfpath).unlink(missing_ok=True)
            except Exception as e:
                print(f"[{STEM}] png render failed: {e}")
        threading.Thread(target=work, daemon=True).start()

    # ---- supporting-file watcher ----
    # The template reads satellite.json (float placement/dimensions) at
    # BODY time (activated after \maketitle — verified), so single-shot
    # convergence engines pick up its current content on every run. This
    # watcher makes edits to supporting files trigger repagination on
    # their own. Extra globs via RT_WATCH=... (comma separated); set
    # RT_WATCH_PREAMBLE=1 if a template reads watched files at preamble
    # time (drains the warm pool on change).
    def start_watcher(self):
        import glob as globlib
        globs = ["satellite.json"] + [
            g for g in os.environ.get("RT_WATCH", "").split(",") if g]
        drain = os.environ.get("RT_WATCH_PREAMBLE") == "1"

        def snap():
            m = {}
            for g in globs:
                for f in globlib.glob(str(DIR / g)):
                    try:
                        m[f] = os.path.getmtime(f)
                    except OSError:
                        pass
            return m

        self._watch = snap()
        if self._watch:
            print(f"[{STEM}] watching supporting files: "
                  + ", ".join(Path(f).name for f in self._watch))

        def loop():
            while True:
                time.sleep(1.0)
                if self.converging:
                    self._watch = snap()   # ignore our own runs' writes
                    continue
                cur = snap()
                if cur != self._watch:
                    names = {Path(f).name
                             for f in set(cur) ^ set(self._watch)} | \
                            {Path(f).name for f in cur
                             if f in self._watch and cur[f] != self._watch[f]}
                    self._watch = cur
                    print(f"[{STEM}] supporting file changed "
                          f"({', '.join(sorted(names))}): repaginating")
                    if drain:
                        self.drain_pool()
                        self.prewarm_async()
                    self.last_stable = False
                    if self.conv_timer:
                        self.conv_timer.cancel()
                    self.conv_timer = threading.Timer(0.2, self.converge)
                    self.conv_timer.daemon = True
                    self.conv_timer.start()

        threading.Thread(target=loop, daemon=True).start()

    def take_conv_engine(self):
        with self.pool_lock:
            if self.pool:
                return self.pool.pop()
        print(f"[{STEM}] no warm engine (edit burst): paying preamble")
        return ConvEngine(self.preamble)

    def drain_pool(self):
        with self.pool_lock:
            engines, self.pool = self.pool, []
        for e in engines:
            e.kill()

    def prewarm_async(self):
        def work():
            while True:
                with self.pool_lock:
                    if len(self.pool) >= self.pool_target:
                        return
                    pre = self.preamble
                try:
                    eng = ConvEngine(pre)
                except Exception as e:
                    print(f"[{STEM}] prewarm failed: {e}")
                    return
                with self.pool_lock:
                    if pre == self.preamble:
                        self.pool.append(eng)
                    else:
                        eng.kill()   # preamble changed while warming
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def split_source(source):
        m = re.search(r"(.*?\\begin\{document\})(.*)(\\end\{document\})",
                      source, re.S)
        pre_bd = m.group(1)
        return pre_bd[:-len("\\begin{document}")], m.group(2)

    def write_serve(self):
        m = re.search(r"(.*?)\\begin\{document\}", self.source, re.S)
        (DIR / f"{STEM}-serve.tex").write_text(
            m.group(1) + "\\begin{document}\n"
            "\\directlua{dofile(\"" + str(ROOT / "engine" / "serve2.lua")
            + "\")}%\n"
            "\\directlua{RTLOADAUX(\"" + f"{STEM}-live.aux" + "\")}%\n"
            "\\newif\\ifserving \\servingtrue\n"
            "\\loop\\directlua{SERVE_ONE()}\\ifserving\\repeat\n"
            "\\end{document}\n")

    def compile_tex(self, name):
        return subprocess.run(
            ["lualatex", "-interaction=nonstopmode", name],
            cwd=DIR, capture_output=True, text=True, env=ENV, timeout=300)

    def load_capture(self, path=None):
        data = json.loads(
            (path or DIR / f"{STEM}-live-capture.json").read_text())
        self.captured = data["paras"]
        self.ids = data.get("ids", {})            # attr int (str) -> paraid
        id_of = {int(k): v for k, v in self.ids.items()}
        for p in self.captured:
            p["pid"] = id_of.get(p["attr"]) if p["attr"] is not None else None
        self.pages = data.get("pages", [])
        self.fonts = data.get("fonts", {})
        self.body = parse_paras(self.source)
        self.caps = {}
        for pid, text in self.body.items():
            cands = [p for p in self.captured if p["pid"] == pid]
            if cands:
                # reuse fingerprint ranking from rtclient.match_para
                best = match_para(None, text,
                                  [dict(p, attr=None) for p in cands])
                self.caps[pid] = best

    def spawn(self):
        t0 = time.perf_counter()
        self.srv = Server(DIR, f"{STEM}-serve.tex", env=ENV)
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

    # ---- differential pagination, tier 0 ----
    # A stable-height edit (same line count, same total height) moves
    # NOTHING else on any page: patch the page cache in place and defer
    # the expensive full recompile instead of scheduling it eagerly.
    @staticmethod
    def sig_vprofile(sig):
        """Vertical profile relative to the first baseline. The reference
        signature (post_linebreak) carries the leading interline glue from
        the document context, the fast-path vbox doesn't — absolute extents
        differ by a constant, relative profiles must match exactly."""
        if not sig:
            return None
        y1 = sig[0]["y"]
        return [(l["y"] - y1, l["h"], l["d"]) for l in sig]

    def patch_page(self, pid, sig, fonts):
        attr = next((int(k) for k, v in self.ids.items() if v == pid), None)
        if attr is None or not sig or not sig[0]["g"]:
            return False
        hits = [pg for pg in self.pages
                if any(g[4] == attr for g in pg["g"])]
        if len(hits) != 1:      # straddles pages/columns -> convergence path
            return False
        pg = hits[0]
        idx0 = next(i for i, g in enumerate(pg["g"]) if g[4] == attr)
        first = pg["g"][idx0]
        # anchor on the paragraph's first glyph, in the NEW sig's own space
        x0 = first[1] - sig[0]["g"][0][1]
        y0 = first[2] - sig[0]["y"]
        for fid, f in (fonts or {}).items():
            self.fonts["s" + str(fid)] = f
        newg = [[ln_g[0], x0 + ln_g[1], y0 + line["y"], "s" + str(ln_g[2]), attr]
                for line in sig for ln_g in line["g"]]
        rest = [g for g in pg["g"] if g[4] != attr]
        at = sum(1 for g in pg["g"][:idx0] if g[4] != attr)
        pg["g"] = rest[:at] + newg + rest[at:]
        self.caps[pid]["sig"] = sig   # future patches key off current state
        return True

    # ---- fast path: one paragraph, by production id ----
    def compile(self, pid, text):
        cap = self.caps.get(pid)
        if cap is None:
            return {"error": f"no captured context for '{pid}'"}
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
        resp["rev"] = self.rev
        if "sig" in resp:
            ref = cap.get("sig") or []
            stable = (self.sig_vprofile(ref) ==
                      self.sig_vprofile(resp["sig"]))
            if stable:
                stable = self.patch_page(pid, resp["sig"], resp.get("fonts"))
            resp["stable"] = stable
            self.last_stable = stable
        return resp

    # ---- background convergence: full edited source ----
    @staticmethod
    def skeleton(source):
        """Source with paragraph BODIES blanked: captures document
        structure (sections, floats, markers, preamble). If the skeleton
        changed, the edit is structural — sections/paragraphs were added
        or removed — and repagination must run immediately."""
        return PARA_RE.sub(lambda m: "\\paraid{%s}<>" % m.group(1), source)

    def update_source(self, source):
        structural = self.skeleton(source) != self.skeleton(self.source)
        self.source = source
        if self.conv_timer:
            self.conv_timer.cancel()
        if structural:
            delay = 0.2          # sections/paragraphs changed: go now
            self.last_stable = False
        elif getattr(self, "last_stable", False):
            delay = 25.0         # page cache already patched in place
        else:
            delay = 1.2          # height-changing paragraph edit
        self.conv_timer = threading.Timer(delay, self.converge)
        self.conv_timer.daemon = True
        self.conv_timer.start()
        return {"scheduled": True, "delay": delay,
                "structural": structural, "rev": self.rev}

    def converge(self):
        with self.conv_lock:
            self.converging = True
            self.conv_error = None
            t0 = time.perf_counter()
            try:
                pre, body = self.split_source(self.source)
                fast_ok = False
                if pre == self.preamble:
                    # fast repagination: body-only re-typeset in a warm
                    # preamble-resident engine (used once, then replaced)
                    (DIR / f"{STEM}-body.tex").write_text(body)
                    out = DIR / f"{STEM}-conv-capture.json"
                    eng = self.take_conv_engine()
                    r = eng.run(f"{STEM}-body.tex", out.name,
                                f"{STEM}-live.aux")
                    if "ms" in r:
                        # graceful end finalizes this run's real PDF; the
                        # print view rasterizes it in the background
                        eng.quit()
                        self.render_pngs_async(
                            DIR / f"{eng.jobname}.pdf", self.rev + 1,
                            proc=eng.proc)
                    else:
                        eng.kill()
                    self.prewarm_async()
                    if "ms" in r:
                        self.load_capture(out)
                        fast_ok = True
                        self.last_conv_s = round(
                            time.perf_counter() - t0, 2)
                        print(f"[{STEM}] converged rev {self.rev + 1} in "
                              f"{self.last_conv_s} s (warm engine, "
                              f"body {r['ms']:.0f} ms)")
                    else:
                        print(f"[{STEM}] warm engine failed "
                              f"({r.get('err')}); falling back")
                else:
                    # preamble edited: engines hold a stale preamble
                    print(f"[{STEM}] preamble changed: full compile + "
                          "engine respawn")
                if not fast_ok:
                    (DIR / f"{STEM}-live.tex").write_text(
                        with_rtcapture(self.source))
                    r2 = self.compile_tex(f"{STEM}-live.tex")
                    if "RTCAPTURE: wrote" not in r2.stdout:
                        errs = [l for l in r2.stdout.splitlines()
                                if l.startswith("!")]
                        self.conv_error = (errs[0] if errs
                                           else "compile produced no capture")
                        print(f"[{STEM}] convergence FAILED: "
                              f"{self.conv_error}")
                        return
                    self.load_capture()
                    if pre != self.preamble:
                        self.preamble = pre
                        self.drain_pool()
                        self.prewarm_async()
                        self.write_serve()
                        with self.lock:
                            self.respawn()
                    self.last_conv_s = round(time.perf_counter() - t0, 2)
                    print(f"[{STEM}] converged rev {self.rev + 1} in "
                          f"{self.last_conv_s} s (full compile)")
                    self.render_pngs_async(DIR / f"{STEM}-live.pdf",
                                           self.rev + 1)
                self.rev += 1
            finally:
                self.converging = False

    def doc(self):
        paras = []
        for pid, text in self.body.items():
            cap = self.caps.get(pid)
            paras.append({
                "id": pid,
                "sig": cap["sig"] if cap else [],
                "font_size": cap["font_size"] if cap else 655360,
            })
        return {"name": DOC_FILE.name, "rev": self.rev,
                "source": self.source, "paras": paras,
                "ids": self.ids, "pages": self.pages, "fonts": self.fonts}


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
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
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
                            "converging": THE_DOC.converging,
                            "conv_s": getattr(THE_DOC, "last_conv_s", None),
                            "pdf_rev": THE_DOC.pdf_rev,
                            "error": getattr(THE_DOC, "conv_error", None)})
        elif self.path.startswith("/api/page-image"):
            q = dict(p.split("=") for p in
                     self.path.split("?", 1)[1].split("&"))
            i = int(q["i"])
            d = THE_DOC.png_dir
            hit = None
            if d:
                for pat in (f"p-{i:02d}.png", f"p-{i:d}.png",
                            f"p-{i:03d}.png"):
                    if (d / pat).exists():
                        hit = d / pat
                        break
            if hit:
                data = hit.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                # the same URL must never serve a stale page raster
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_json({"error": "not ready"}, 404)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or "{}")
        if self.path == "/api/compile":
            self.send_json(THE_DOC.compile(body["id"], body["text"]))
        elif self.path == "/api/source":
            self.send_json(THE_DOC.update_source(body["source"]))
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
