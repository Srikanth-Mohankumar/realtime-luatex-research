#!/usr/bin/env python3
"""Local demo bridge: browser <-> persistent LuaTeX engines, serving a
QUEUE of production-marked .tex articles.

Run:  python3 server.py [article.tex | scan-root ...] [port]
      defaults: scan prototype/testdocs + /data/neopage/watcher/to-check,
      port 8123. Open http://localhost:8123/ and pick an article.

Discovery: any .tex containing \\documentclass AND \\paraid{...} markers
(generated variants like -dev/-live/-serve/-conv/-marked excluded).
Articles found outside the workdir are COPIED (whole folder) into
prototype/testdocs/ first — originals are never touched.

Per article (lazy, on first open; engines LRU-capped):
  * FAST PATH — persistent lualatex with the article's preamble; every
    keystroke recompiles the edited paragraph with its captured context.
  * DIFFERENTIAL PAGINATION — stable-profile edits patch the page cache
    in place; structural edits repaginate immediately.
  * CONVERGENCE — single-shot preamble-resident engines (pool, prewarmed)
    re-typeset the body in ~3 s and emit the REAL PDF, rasterized for the
    print view.
  * WATCHER — satellite.json changes auto-repaginate.
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

WORKDIR = ROOT / "testdocs"
WORKDIR.mkdir(exist_ok=True)
ENV = {**os.environ, "TEXINPUTS": str(ROOT / "engine") + ":"}
GENERATED = ("-live", "-serve", "-conv", "-marked", "-body", "-draft",
             "-dev", "-forautoqc", "-apg", "-conversion", "preamble-only")
# memory knobs: warm conv engines per article, concurrently open articles,
# and minutes of inactivity before an article's engines are put to sleep
POOL_TARGET = int(os.environ.get("RT_POOL", "2"))
MAX_SESSIONS = int(os.environ.get("RT_SESSIONS", "2"))
IDLE_MIN = float(os.environ.get("RT_IDLE_MIN", "15"))

args = [a for a in sys.argv[1:] if not a.isdigit()]
PORT = int(sys.argv[-1]) if sys.argv[1:] and sys.argv[-1].isdigit() else 8123
# STANDARD DEMO: serves only the self-contained IEEEtran sample article
# (production-style \paraid/\tagStructPara markup, prodshim stand-ins).
# The production track is opt-in: pass explicit files or scan roots, e.g.
#   python3 server.py /data/neopage/watcher/to-check
SCAN_ROOTS = ([Path(a).resolve() for a in args] if args else
              [ROOT / "templates" / "ieeetran" / "article.tex"])

PARA_RE = re.compile(
    r"\\paraid\{([\w.-]+)\}(.*?)(?:\\tagStructParaEnd\{\}|\n[ \t]*\n|$)", re.S)


def discover():
    """[(display name, path)] of production-marked main tex files."""
    out = []
    for root in SCAN_ROOTS:
        if root.is_file():
            out.append(root)
            continue
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.tex")):
            if any(tag in p.stem for tag in GENERATED):
                continue
            try:
                head = p.read_text(errors="replace")
            except OSError:
                continue
            if "\\documentclass" in head and "\\paraid{" in head:
                out.append(p)
    # duplicate stems: prefer the process_folder copy (the compile-ready
    # environment their pipeline actually ran in)
    by_stem = {}
    for p in out:
        cur = by_stem.get(p.stem)
        if cur is None or ("process_folder" in p.parts
                           and "process_folder" not in cur.parts):
            by_stem[p.stem] = p
    return sorted(by_stem.values(), key=lambda p: p.stem)


def workdir_copy(path):
    """Ensure the article lives under WORKDIR; copy its folder in if not."""
    path = path.resolve()
    if WORKDIR in path.parents:
        return path
    dest = WORKDIR / path.parent.parent.name if \
        path.parent.name == "process_folder" else WORKDIR / path.parent.name
    if not (dest / path.name).exists():
        print(f"copying {path.parent} -> {dest}")
        shutil.copytree(path.parent, dest, dirs_exist_ok=True)
    return dest / path.name


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


def parse_paras(source):
    return {m.group(1): " ".join(
        l.strip() for l in m.group(2).strip().splitlines())
        for m in PARA_RE.finditer(source)}


class ConvEngine:
    """Single-shot convergence engine: preamble resident, body typeset once
    on request (converge-loop.lua), REAL PDF finalized on graceful quit."""

    _seq = 0

    def __init__(self, doc):
        ConvEngine._seq += 1
        self.doc = doc
        self.jobname = f"{doc.stem}-conv{ConvEngine._seq}"
        (doc.dir / f"{doc.stem}-conv.tex").write_text(
            doc.preamble + "\\usepackage{rtcapture}\n\\begin{document}\n"
            "\\directlua{dofile(\""
            + str(ROOT / "engine" / "converge-loop.lua") + "\")}%\n"
            "\\newif\\ifserving \\servingtrue\n"
            "\\loop\\directlua{CONV_ONE()}\\ifserving\\repeat\n"
            "\\end{document}\n")
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(
            ["lualatex", "-interaction=nonstopmode",
             f"-jobname={self.jobname}", f"{doc.stem}-conv.tex"],
            cwd=doc.dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1, env=ENV)
        for line in self.proc.stdout:
            if "CONVREADY" in line:
                break
        else:
            raise RuntimeError("convergence engine never became ready")
        print(f"[{doc.stem}] convergence engine ready in "
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
    def __init__(self, tex_path):
        self.file = workdir_copy(Path(tex_path))
        self.dir = self.file.parent
        self.stem = self.file.stem
        self.lock = threading.Lock()
        self.conv_lock = threading.Lock()
        self.rev = 1
        self.converging = False
        self.conv_timer = None
        self.conv_error = None
        self.last_conv_s = None
        self.source = self.file.read_text()
        self.last_used = time.time()

        print(f"[{self.stem}] compiling reference ({self.file.name})...")
        self.write_live()
        for _ in range(2):
            r = self.compile_tex(f"{self.stem}-live.tex")
        if "RTCAPTURE: wrote" not in r.stdout:
            print(r.stdout[-2500:])
            raise RuntimeError(f"{self.stem}: reference compile failed")
        self.write_serve()
        self.load_capture()
        print(f"[{self.stem}] {len(self.body)} paragraphs, "
              f"{len(self.pages)} pages")
        self.spawn()
        self.preamble = self.split_source(self.source)[0]
        self.pool = []
        self.pool_lock = threading.Lock()
        self.pool_target = POOL_TARGET
        self.pool.append(ConvEngine(self))
        self.prewarm_async()
        self.start_watcher()
        self.pdf_rev = 0
        self.png_dir = None
        self.render_pngs_async(self.dir / f"{self.stem}-live.pdf", self.rev)

    # ---- source plumbing ----
    @staticmethod
    def split_source(source):
        m = re.search(r"(.*?\\begin\{document\})(.*)(\\end\{document\})",
                      source, re.S)
        return (m.group(1)[:-len("\\begin{document}")], m.group(2))

    def write_live(self):
        (self.dir / f"{self.stem}-live.tex").write_text(
            self.source.replace(
                "\\begin{document}",
                "\\usepackage{rtcapture}\n\\begin{document}", 1))

    def write_serve(self):
        m = re.search(r"(.*?)\\begin\{document\}", self.source, re.S)
        (self.dir / f"{self.stem}-serve.tex").write_text(
            m.group(1) + "\\begin{document}\n"
            "\\directlua{dofile(\"" + str(ROOT / "engine" / "serve2.lua")
            + "\")}%\n"
            "\\directlua{RTLOADAUX(\"" + f"{self.stem}-live.aux" + "\")}%\n"
            "\\newif\\ifserving \\servingtrue\n"
            "\\loop\\directlua{SERVE_ONE()}\\ifserving\\repeat\n"
            "\\end{document}\n")

    def compile_tex(self, name):
        return subprocess.run(
            ["lualatex", "-interaction=nonstopmode", name],
            cwd=self.dir, capture_output=True, text=True, env=ENV,
            timeout=300)

    def load_capture(self, path=None):
        data = json.loads(
            (path or self.dir / f"{self.stem}-live-capture.json").read_text())
        self.captured = data["paras"]
        self.ids = data.get("ids", {})
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
                self.caps[pid] = match_para(
                    None, text, [dict(p, attr=None) for p in cands])

    # ---- engine sleep/wake (memory reaper) ----
    def touch(self):
        self.last_used = time.time()

    def sleep_engines(self):
        """Free the ~GBs of resident engines for an idle article; they
        respawn lazily on the next edit (paying startup again)."""
        self.drain_pool()
        if self.srv is not None:
            try:
                self.srv.proc.kill()
            except Exception:
                pass
            self.srv = None
        print(f"[{self.stem}] idle: engines put to sleep")

    def wake_engines(self):
        if self.srv is None:
            print(f"[{self.stem}] waking engines...")
            self.spawn()
            self.prewarm_async()

    # ---- fast-path engine ----
    def spawn(self):
        t0 = time.perf_counter()
        self.srv = Server(self.dir, f"{self.stem}-serve.tex", env=ENV)
        fonts = sorted({(p["font_name"], p["font_size"])
                        for p in self.captured if p["font_name"]})
        self.srv.request(lua_val({"preload": [list(f) for f in fonts]}))
        print(f"[{self.stem}] engine ready in "
              f"{(time.perf_counter() - t0) * 1000:.0f} ms")

    def respawn(self):
        try:
            self.srv.proc.kill()
        except Exception:
            pass
        self.spawn()

    # ---- convergence engine pool ----
    def take_conv_engine(self):
        with self.pool_lock:
            if self.pool:
                return self.pool.pop()
        print(f"[{self.stem}] no warm engine (edit burst): paying preamble")
        return ConvEngine(self)

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
                    eng = ConvEngine(self)
                except Exception as e:
                    print(f"[{self.stem}] prewarm failed: {e}")
                    return
                with self.pool_lock:
                    if pre == self.preamble:
                        self.pool.append(eng)
                    else:
                        eng.kill()
        threading.Thread(target=work, daemon=True).start()

    def shutdown(self):
        """Free all engine processes (LRU eviction)."""
        if self.conv_timer:
            self.conv_timer.cancel()
        try:
            self.srv.proc.kill()
        except Exception:
            pass
        self.drain_pool()
        print(f"[{self.stem}] session shut down (evicted)")

    # ---- supporting-file watcher ----
    def start_watcher(self):
        import glob as globlib
        globs = ["satellite.json"] + [
            g for g in os.environ.get("RT_WATCH", "").split(",") if g]
        drain = os.environ.get("RT_WATCH_PREAMBLE") == "1"

        def snap():
            m = {}
            for g in globs:
                for f in globlib.glob(str(self.dir / g)):
                    try:
                        m[f] = os.path.getmtime(f)
                    except OSError:
                        pass
            return m

        self._watch = snap()
        if self._watch:
            print(f"[{self.stem}] watching: "
                  + ", ".join(Path(f).name for f in self._watch))

        def loop():
            while True:
                time.sleep(1.0)
                if self.converging:
                    self._watch = snap()
                    continue
                cur = snap()
                if cur != self._watch:
                    names = {Path(f).name for f in set(cur) ^ set(self._watch)} | \
                            {Path(f).name for f in cur
                             if f in self._watch and cur[f] != self._watch[f]}
                    self._watch = cur
                    print(f"[{self.stem}] supporting file changed "
                          f"({', '.join(sorted(names))}): repaginating")
                    if drain:
                        self.drain_pool()
                        self.prewarm_async()
                    self.last_stable = False
                    self.schedule(0.2)

        threading.Thread(target=loop, daemon=True).start()

    # ---- differential pagination, tier 0 ----
    @staticmethod
    def sig_vprofile(sig):
        if not sig:
            return None
        y1 = sig[0]["y"]
        return [(l["y"] - y1, l["h"], l["d"]) for l in sig]

    def patch_page(self, pid, sig, fonts):
        attr = next((int(k) for k, v in self.ids.items() if v == pid), None)
        if attr is None or not sig or not sig[0]["g"]:
            return False
        hits = [pg for pg in self.pages if any(g[4] == attr for g in pg["g"])]
        if len(hits) != 1:
            return False
        pg = hits[0]
        idx0 = next(i for i, g in enumerate(pg["g"]) if g[4] == attr)
        first = pg["g"][idx0]
        x0 = first[1] - sig[0]["g"][0][1]
        y0 = first[2] - sig[0]["y"]
        for fid, f in (fonts or {}).items():
            self.fonts["s" + str(fid)] = f
        newg = [[g0[0], x0 + g0[1], y0 + line["y"], "s" + str(g0[2]), attr]
                for line in sig for g0 in line["g"]]
        rest = [g for g in pg["g"] if g[4] != attr]
        at = sum(1 for g in pg["g"][:idx0] if g[4] != attr)
        pg["g"] = rest[:at] + newg + rest[at:]
        self.caps[pid]["sig"] = sig
        return True

    # ---- fast path ----
    def compile(self, pid, text):
        cap = self.caps.get(pid)
        if cap is None:
            return {"error": f"no captured context for '{pid}'"}
        req = build_request(sanitize(text), cap)
        self.touch()
        with self.lock:
            self.wake_engines()
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

    # ---- background convergence ----
    @staticmethod
    def skeleton(source):
        return PARA_RE.sub(lambda m: "\\paraid{%s}<>" % m.group(1), source)

    def schedule(self, delay):
        if self.conv_timer:
            self.conv_timer.cancel()
        self.conv_timer = threading.Timer(delay, self.converge)
        self.conv_timer.daemon = True
        self.conv_timer.start()

    def update_source(self, source):
        self.touch()
        structural = self.skeleton(source) != self.skeleton(self.source)
        self.source = source
        if structural:
            delay = 0.2
            self.last_stable = False
        elif getattr(self, "last_stable", False):
            delay = 25.0
        else:
            delay = 1.2
        self.schedule(delay)
        return {"scheduled": True, "delay": delay,
                "structural": structural, "rev": self.rev}

    def render_pngs_async(self, pdfpath, rev, proc=None):
        def work():
            try:
                if proc is not None:
                    try:
                        for _ in proc.stdout:
                            pass
                    except Exception:
                        pass
                    proc.wait(timeout=120)
                if not Path(pdfpath).exists():
                    return
                outdir = self.dir / f"{self.stem}-pngs-r{rev}"
                outdir.mkdir(exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-png", "-r", "110", str(pdfpath),
                     str(outdir / "p")],
                    capture_output=True, timeout=300)
                old = self.png_dir
                self.png_dir, self.pdf_rev = outdir, rev
                print(f"[{self.stem}] print view ready for rev {rev}")
                if old and old != outdir:
                    shutil.rmtree(old, ignore_errors=True)
                if "-conv" in Path(pdfpath).name:
                    Path(pdfpath).unlink(missing_ok=True)
            except Exception as e:
                print(f"[{self.stem}] png render failed: {e}")
        threading.Thread(target=work, daemon=True).start()

    def converge(self):
        with self.conv_lock:
            self.converging = True
            self.conv_error = None
            t0 = time.perf_counter()
            try:
                pre, body = self.split_source(self.source)
                fast_ok = False
                if pre == self.preamble:
                    (self.dir / f"{self.stem}-body.tex").write_text(body)
                    out = self.dir / f"{self.stem}-conv-capture.json"
                    eng = self.take_conv_engine()
                    r = eng.run(f"{self.stem}-body.tex", out.name,
                                f"{self.stem}-live.aux")
                    if "ms" in r:
                        eng.quit()
                        self.render_pngs_async(
                            self.dir / f"{eng.jobname}.pdf", self.rev + 1,
                            proc=eng.proc)
                    else:
                        eng.kill()
                    self.prewarm_async()
                    if "ms" in r:
                        self.load_capture(out)
                        fast_ok = True
                        self.last_conv_s = round(time.perf_counter() - t0, 2)
                        print(f"[{self.stem}] converged rev {self.rev + 1} "
                              f"in {self.last_conv_s} s (warm engine, "
                              f"body {r['ms']:.0f} ms)")
                    else:
                        print(f"[{self.stem}] warm engine failed "
                              f"({r.get('err')}); falling back")
                else:
                    print(f"[{self.stem}] preamble changed: full compile + "
                          "engine respawn")
                if not fast_ok:
                    self.write_live()
                    r2 = self.compile_tex(f"{self.stem}-live.tex")
                    if "RTCAPTURE: wrote" not in r2.stdout:
                        errs = [l for l in r2.stdout.splitlines()
                                if l.startswith("!")]
                        self.conv_error = (errs[0] if errs
                                           else "compile produced no capture")
                        print(f"[{self.stem}] convergence FAILED: "
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
                    print(f"[{self.stem}] converged rev {self.rev + 1} in "
                          f"{self.last_conv_s} s (full compile)")
                    self.render_pngs_async(
                        self.dir / f"{self.stem}-live.pdf", self.rev + 1)
                self.rev += 1
            finally:
                self.converging = False

    def doc(self):
        self.touch()
        paras = []
        for pid, text in self.body.items():
            cap = self.caps.get(pid)
            paras.append({
                "id": pid,
                "sig": cap["sig"] if cap else [],
                "font_size": cap["font_size"] if cap else 655360,
            })
        return {"name": self.file.name, "rev": self.rev,
                "source": self.source, "paras": paras,
                "ids": self.ids, "pages": self.pages, "fonts": self.fonts}


# ---- session registry (LRU-capped: engines are expensive) ----
CATALOG = discover()


def reaper():
    while True:
        time.sleep(60)
        now = time.time()
        with REG_LOCK:
            docs = list(SESSIONS.values())
        for d in docs:
            if (d.srv is not None
                    and now - getattr(d, "last_used", now) > IDLE_MIN * 60
                    and not d.converging):
                d.sleep_engines()


threading.Thread(target=reaper, daemon=True).start()
SESSIONS = {}          # stem -> Doc
LRU = []               # stems, most recent last
REG_LOCK = threading.Lock()


def get_doc(stem):
    with REG_LOCK:
        if stem in SESSIONS:
            LRU.remove(stem)
            LRU.append(stem)
            return SESSIONS[stem]
    path = next((p for p in CATALOG if p.stem == stem), None)
    if path is None:
        raise KeyError(stem)
    d = Doc(path)
    with REG_LOCK:
        SESSIONS[stem] = d
        LRU.append(stem)
        while len(LRU) > MAX_SESSIONS:
            evict = LRU.pop(0)
            SESSIONS.pop(evict).shutdown()
    return d


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def q(self):
        if "?" not in self.path:
            return {}
        return dict(p.split("=", 1) for p in
                    self.path.split("?", 1)[1].split("&") if "=" in p)

    def do_GET(self):
        try:
            self._get()
        except Exception as e:
            try:
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass

    def do_POST(self):
        try:
            self._post()
        except Exception as e:
            try:
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass

    def _get(self):
        route = self.path.split("?", 1)[0]
        q = self.q()
        if route in ("/", "/index.html"):
            data = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # stale UI code must never survive a server update
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif route == "/api/docs":
            self.send_json([p.stem for p in CATALOG])
        elif route == "/api/doc":
            self.send_json(get_doc(q["doc"]).doc())
        elif route == "/api/rev":
            d = get_doc(q["doc"])
            self.send_json({"rev": d.rev, "converging": d.converging,
                            "conv_s": d.last_conv_s, "pdf_rev": d.pdf_rev,
                            "error": d.conv_error})
        elif route == "/api/page-image":
            d = get_doc(q["doc"])
            i = int(q["i"])
            hit = None
            if d.png_dir:
                for pat in (f"p-{i:02d}.png", f"p-{i:d}.png",
                            f"p-{i:03d}.png"):
                    if (d.png_dir / pat).exists():
                        hit = d.png_dir / pat
                        break
            if hit:
                data = hit.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_json({"error": "not ready"}, 404)
        else:
            self.send_json({"error": "not found"}, 404)

    def _post(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or "{}")
        d = get_doc(body["doc"])
        if self.path == "/api/compile":
            self.send_json(d.compile(body["id"], body["text"]))
        elif self.path == "/api/source":
            self.send_json(d.update_source(body["source"]))
        elif self.path == "/api/restart":
            with d.lock:
                d.respawn()
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    print(f"articles: {', '.join(p.stem for p in CATALOG) or '(none found)'}")
    print(f"demo at http://localhost:{PORT}/  (sessions spawn on first open)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
