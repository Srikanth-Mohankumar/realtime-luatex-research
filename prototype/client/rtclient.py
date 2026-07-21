#!/usr/bin/env python3
"""rtclient.py — fidelity + latency test driver for the real-time LuaTeX
prototype, against real journal templates.

Per template directory (prototype/templates/<t>/):
  1. Full compile of sample.tex (twice, for aux convergence) with the
     rtcapture instrumentation -> sample-capture.json: per-paragraph layout
     context + reference line-break signatures. The wall time of the full
     compile is the baseline an editor would otherwise pay per keystroke.
  2. Parse body-shared.tex for \\RTpara{id}-marked source paragraphs.
  3. Match each source paragraph to its captured record (attribute id +
     glyph-fingerprint similarity — ligatures/refs make exact match wrong).
  4. Spawn the persistent server (server.tex), which loaded the same class
     preamble and the sample's .aux (converged \\ref/\\cite state).
  5. For each paragraph: send {text, ctx}; compare the returned signature
     against the reference (line count, per-line glyph chars, per-glyph x
     in scaled points); measure round-trip latency (median of N).
  6. Edit simulation: perturbed text, latency only.

Usage: python3 rtclient.py <template-dir> [more template-dirs...]
Writes prototype/results/<template>.json and prints a summary table.
"""
import difflib
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPEATS = 11          # 1 fidelity + 10 timing
EDIT_REPEATS = 5

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"


def parse_body(path):
    """{id: paragraph source with lines joined}."""
    text = Path(path).read_text()
    paras = {}
    for m in re.finditer(r"\\RTpara\{(\d+)\}\n(.*?)(?=\n[ \t]*\n)", text, re.S):
        pid = int(m.group(1))
        src = " ".join(l.strip() for l in m.group(2).strip().splitlines())
        paras[pid] = src
    return paras


def source_fingerprint(src):
    """Approximate the glyph fingerprint: printable ASCII, no spaces, TeX
    commands stripped. Ligatures/resolved refs make this fuzzy — used only
    to rank candidates sharing the same attribute id."""
    s = re.sub(r"%.*", "", src)
    s = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}|\\label\{[^}]*\}", " ", s)
    s = re.sub(r"\\[a-zA-Z]+\s*", " ", s)
    s = re.sub(r"[{}$~^_&]", "", s)
    s = re.sub(r"\s+", "", s)
    return "".join(ch for ch in s if 33 <= ord(ch) <= 126)[:40]


def match_para(pid, src, captured):
    cands = [p for p in captured if p["attr"] == pid]
    if not cands:
        return None
    want = source_fingerprint(src)
    return max(cands, key=lambda p: difflib.SequenceMatcher(
        None, want, p["fp"]).ratio())


def lua_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def lua_val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return lua_str(v)
    if isinstance(v, list):
        return "{" + ",".join(lua_val(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ",".join(f"[{lua_str(k)}]={lua_val(x)}"
                              for k, x in v.items() if x is not None) + "}"
    raise TypeError(type(v))


CTX_FIELDS = [
    "hsize", "indent", "hangindent", "hangafter", "parshape", "looseness",
    "leftskip", "rightskip", "parfillskip", "spaceskip", "xspaceskip",
    "pretolerance", "tolerance", "emergencystretch", "linepenalty",
    "hyphenpenalty", "exhyphenpenalty", "adjdemerits",
    "doublehyphendemerits", "finalhyphendemerits", "adjustspacing",
    "protrudechars", "lang", "lhmin", "rhmin", "uchyph",
    "font_name", "font_size",
]


def build_request(text, cap):
    ctx = {k: cap[k] for k in CTX_FIELDS}
    return lua_val({"text": text, "ctx": ctx})


def compare(ref, fast):
    """-> (status, detail). EXACT means identical chars and x to the sp."""
    if len(ref) != len(fast):
        return "LINES", f"ref {len(ref)} lines vs fast {len(fast)}"
    max_dx = 0
    for lr, lf in zip(ref, fast):
        cr = [g[0] for g in lr["g"]]
        cf = [g[0] for g in lf["g"]]
        if cr != cf:
            return "GLYPHS", (f"char sequence differs (line with "
                              f"{len(cr)} vs {len(cf)} glyphs)")
        for gr, gf in zip(lr["g"], lf["g"]):
            max_dx = max(max_dx, abs(gr[1] - gf[1]))
        for k in ("w", "h", "d"):
            max_dx = max(max_dx, abs(lr[k] - lf[k]))
    return ("EXACT" if max_dx == 0 else "POS"), f"max delta {max_dx} sp"


class Server:
    def __init__(self, tpl_dir, texfile="server.tex"):
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(
            ["lualatex", "-interaction=nonstopmode", texfile],
            cwd=tpl_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1)
        for line in self.proc.stdout:
            if line.strip().endswith("RTSERVE READY"):
                break
        else:
            raise RuntimeError("server never became ready")
        self.startup_ms = (time.perf_counter() - t0) * 1000

    def request(self, req_lua):
        t0 = time.perf_counter()
        self.proc.stdin.write(req_lua + "\n")
        self.proc.stdin.flush()
        # The response line can be GLUED to the tail of TeX's terminal
        # chatter: TeX leaves partial lines unterminated, and the server's
        # io.write lands on the same C stream. Search inside the line, not
        # at its start; our JSON is always the end of the line.
        for line in self.proc.stdout:
            for marker in ('{"ms"', '{"error"', '{"preloaded"'):
                i = line.find(marker)
                if i >= 0:
                    return ((time.perf_counter() - t0) * 1000,
                            json.loads(line[i:].strip()))
        raise RuntimeError("server closed stream")

    def quit(self):
        try:
            self.proc.stdin.write("QUIT\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=60)
        except Exception:
            self.proc.kill()


def run_template(tpl_dir):
    tpl_dir = Path(tpl_dir).resolve()
    name = tpl_dir.name
    print(f"\n=== {name} ===")

    env = {"TEXINPUTS": "../../engine:"}
    full_times = []
    for i in range(2):
        t0 = time.perf_counter()
        r = subprocess.run(
            ["lualatex", "-interaction=nonstopmode", "sample.tex"],
            cwd=tpl_dir, capture_output=True, text=True,
            env={**__import__("os").environ, **env})
        full_times.append(time.perf_counter() - t0)
        if "RTCAPTURE: wrote" not in r.stdout:
            print(r.stdout[-3000:])
            raise RuntimeError(f"{name}: capture compile failed")
    full_s = full_times[-1]
    print(f"full compile: {full_s:.2f} s")

    captured = json.loads(
        (tpl_dir / "sample-capture.json").read_text())["paras"]
    body = parse_body(tpl_dir.parent / "body-shared.tex")

    srv = Server(tpl_dir)
    print(f"server startup: {srv.startup_ms:.0f} ms")

    fonts = sorted({(p["font_name"], p["font_size"])
                    for p in captured if p["font_name"]})
    t0 = time.perf_counter()
    srv.request(lua_val({"preload": [list(f) for f in fonts]}))
    print(f"font preload ({len(fonts)} fonts): "
          f"{(time.perf_counter() - t0) * 1000:.0f} ms")

    results = {"template": name, "full_compile_s": full_s,
               "server_startup_ms": srv.startup_ms, "paras": []}
    for pid, src in sorted(body.items()):
        cap = match_para(pid, src, captured)
        row = {"id": pid, "src_prefix": src[:48]}
        if cap is None:
            row["status"] = "UNMATCHED"
            results["paras"].append(row)
            continue
        req = build_request(src, cap)
        rts, engs, resp0 = [], [], None
        error = None
        first_rt = None
        for i in range(REPEATS):
            rt, resp = srv.request(req)
            if "error" in resp:
                error = resp["error"]
                break
            if i == 0:
                resp0, first_rt = resp, rt
            else:
                rts.append(rt)
                engs.append(resp["ms"])
        if error:
            row["status"], row["detail"] = "ERROR", error
        else:
            status, detail = compare(cap["sig"], resp0["sig"])
            row.update(status=status, detail=detail,
                       ref_lines=len(cap["sig"]),
                       first_rt_ms=round(first_rt, 3),
                       rt_ms=round(statistics.median(rts), 3),
                       engine_ms=round(statistics.median(engs), 3))
        results["paras"].append(row)
        print(f"  para {pid:>2}: {row['status']:<7} "
              f"rt={row.get('rt_ms', '-'):>6} ms  "
              f"eng={row.get('engine_ms', '-'):>6} ms  "
              f"{row.get('detail', '')}")

    # edit simulation: perturb three paragraphs, latency only
    edits = []
    for pid in list(body)[:3]:
        cap = match_para(pid, body[pid], captured)
        if cap is None:
            continue
        edited = body[pid].replace("the", "the modified", 1)
        req = build_request(edited, cap)
        rts = []
        for _ in range(EDIT_REPEATS):
            rt, resp = srv.request(req)
            if "error" not in resp:
                rts.append(rt)
        if rts:
            edits.append({"id": pid,
                          "rt_ms": round(statistics.median(rts), 3)})
    results["edits"] = edits
    if edits:
        med = statistics.median(e["rt_ms"] for e in edits)
        print(f"  edit-latency median across {len(edits)} paras: {med:.2f} ms")

    srv.quit()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(results, indent=1))

    n = len(results["paras"])
    exact = sum(1 for p in results["paras"] if p["status"] == "EXACT")
    print(f"{name}: {exact}/{n} paragraphs EXACT; "
          f"full compile {full_s:.2f} s vs "
          f"fast path ~{statistics.median([p['rt_ms'] for p in results['paras'] if 'rt_ms' in p]):.2f} ms")
    return results


if __name__ == "__main__":
    for d in sys.argv[1:]:
        run_template(d)
