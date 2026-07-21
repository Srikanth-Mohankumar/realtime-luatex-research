#!/usr/bin/env python3
"""client.py — drives the persistent LuaTeX paragraph server (server.tex).

Measures, from the client side, the paper's Table 3 quantities:
  - engine startup (process spawn -> READY, i.e. preamble load), paid once
  - per-request round-trip: write paragraph -> read display-list JSON
  - the engine-internal ms reported in each response (line break + traversal)
IPC overhead = round-trip - engine-internal.
"""
import json
import statistics
import subprocess
import sys
import time

SHORT = "The quick brown fox jumps over the lazy dog."
MEDIUM = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
          "eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim "
          "ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut "
          "aliquip ex ea commodo consequat. Duis aute irure dolor in "
          "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
          "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
          "culpa qui officia deserunt mollit anim id est laborum.")

def main():
    t_spawn = time.perf_counter()
    proc = subprocess.Popen(
        ["lualatex", "-interaction=nonstopmode", "server.tex"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, bufsize=1)

    # lualatex chatters on stdout (banner, file lists); wait for READY.
    # endswith(): the preceding file-close parens can share the line, e.g.
    # ")READY".
    for line in proc.stdout:
        if line.strip().endswith("READY"):
            break
    startup = (time.perf_counter() - t_spawn) * 1000
    print(f"engine startup (spawn -> preamble ready): {startup:.0f} ms")

    def request(text):
        t0 = time.perf_counter()
        proc.stdin.write(text + "\n")
        proc.stdin.flush()
        for line in proc.stdout:
            line = line.strip()
            if line.startswith('{"ms"'):
                rt = (time.perf_counter() - t0) * 1000
                return rt, json.loads(line)
        raise RuntimeError("server died:\n" + (proc.stdout.read() or ""))

    for name, text in (("short", SHORT), ("medium", MEDIUM)):
        rts, engines, glyphs, nlines = [], [], 0, 0
        for i in range(35):
            rt, resp = request(text)
            if i >= 5:  # warmup
                rts.append(rt)
                engines.append(resp["ms"])
            glyphs, nlines = len(resp["glyphs"]), resp["lines"]
        rt_med = statistics.median(rts)
        eng_med = statistics.median(engines)
        print(f"{name:7s} lines={nlines} glyphs={glyphs}  "
              f"round-trip median={rt_med:.2f} ms  "
              f"engine={eng_med:.2f} ms  ipc+parse={rt_med - eng_med:.2f} ms")

    proc.stdin.write("QUIT\n")
    proc.stdin.flush()
    proc.wait(timeout=30)
    print("server exited", proc.returncode)

if __name__ == "__main__":
    sys.exit(main())
