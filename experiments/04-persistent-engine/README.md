# Experiment 04 — Persistent LuaTeX paragraph server

End-to-end replication of the texlode fast path (paper §5): all four
architecture pieces working together.

1. **Keeping the engine alive** — `server.tex` loads `book` + `microtype`
   once, then enters a TeX `\loop` that services requests forever. The
   preamble cost is paid once at startup.
2. **Per-paragraph compile** — each request is typeset into
   `\setbox0=\vbox{...\par}`: the paragraph builder and Knuth–Plass line
   breaker run; the page builder and PDF backend never do.
3. **Display-list extraction** — `extract-lib.lua` (from experiment 03) walks
   the box's node structures and produces positioned glyph commands in scaled
   points, engine-exact under microtype.
4. **IPC** — newline-delimited protocol: paragraph text in on stdin, one JSON
   display list out on stdout. `client.py` (stand-in for the browser side)
   measures client-observed round-trip latency.

## How the loop works

`\directlua{SERVE_ONE()}` blocks on `io.read("*l")`. For a paragraph request
it queues `\setbox0=\vbox{<text>\par}\directlua{RESPOND()}` with `tex.sprint`;
TeX consumes those tokens (running the line breaker), then `RESPOND()` walks
the box and writes the JSON line (`io.write` + `io.flush` — flushing matters:
stdout is block-buffered when piped). `QUIT` flips `\servingfalse` and the
document ends normally.

Gotcha found while building this: `lualatex`'s own stdout chatter can share a
line with our handshake (`)READY`), so the client must match line *suffix*,
not the whole line.

## Results (2026-07-21, LuaHBTeX 1.22.0, TeX Live 2025; 30 samples after 5 warmup)

| Quantity                          | This machine | Paper (Table 3)   |
|-----------------------------------|--------------|-------------------|
| Engine startup (spawn → READY)    | 576 ms       | ~1 s              |
| Short round-trip (1 line)         | 0.43 ms      | 0.79 ms           |
| — engine (line break + traversal) | 0.28 ms      | 0.65 ms           |
| — IPC + JSON parse                | 0.15 ms      | 0.12 + 0.02 ms    |
| Medium round-trip (6 lines)       | 2.52 ms      | 6.11 ms (4–5 ln)  |
| — engine                          | 1.99 ms      | 4.99 ms           |
| — IPC + JSON parse                | 0.54 ms      | 1.03 + 0.09 ms    |

Run with: `python3 client.py` (spawns `lualatex server.tex` itself).

## Conclusions

- The paper's architecture reproduces end-to-end in ~150 lines of Lua/TeX/
  Python: **sub-millisecond round-trips for short paragraphs, well inside a
  16 ms frame budget for multi-line paragraphs**, with full microtype.
- Startup amortization is what makes it viable: 576 ms once vs 0.4 ms per
  edit — a persistent engine is ~1000× cheaper per keystroke than
  re-spawning.
- What this PoC does **not** do (the rest of a real texlode): browser
  rendering (opentype.js glyph resolution), the background full-compile
  convergence path with its page-position cache, fast-path bail-out detection
  (footnotes/`\ref`/`\parshape` — see notes/02 §locality), error recovery
  when a paragraph contains invalid TeX, and state isolation between requests
  (a request that changes global state, e.g. `\bfseries` unclosed, would
  poison later requests — a vbox group contains most but not all of it).
