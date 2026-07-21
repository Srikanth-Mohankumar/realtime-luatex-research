# Experiment 01 — Per-paragraph compile time (replication of paper §3)

Replicates the measurement methodology of Lode, *Real-Time LuaTeX* (TUG2026
preprint), Figure 1 and Table 1, on this machine.

**Environment:** LuaHBTeX 1.22.0, TeX Live 2025, Linux. `book` class +
`microtype` (same setup as the paper; paper hardware was Intel i7-1355U).

## Files

- `paragraph-benchmark.tex` — verbatim Figure 1 from the paper: one paragraph
  timed with `os.gettimeofday()` inside the session.
- `amortized-benchmark.tex` + `bench.lua` — systematic version: each sample =
  mean of 100 consecutive in-session compiles; median/P5/P95 over 30 samples
  after 5-sample warmup (paper's exact protocol). Each 100-paragraph batch is
  typeset inside `\setbox0=\vbox{...}` so the page builder / shipout never
  runs — we time line breaking + node processing only. Repetition is driven
  from Lua via `tex.print` (nested plain-TeX `\loop`s don't nest without
  grouping — our first attempt silently ran once; see git history).

Run with: `lualatex -interaction=nonstopmode amortized-benchmark.tex`

## Results (2026-07-21)

| Paragraph type            | This machine (median) | P5   | P95  | Paper (median) |
|---------------------------|-----------------------|------|------|----------------|
| Short (1 line)            | 0.12 ms               | 0.09 | 0.18 | 0.17 ms        |
| Medium-long (~7 lines)    | 1.61 ms               | 1.45 | 1.96 | 0.70 ms (4–5 ln) / 1.99 ms (10+ ln) |

Single-shot Figure 1 replication: **0.31 ms** (paper reports "consistently
under 1 ms").

## Conclusions

- The paper's central measurement replicates: **per-paragraph typesetting in
  vanilla LuaLaTeX with full microtype is well under 2 ms**, inside a 16 ms
  frame budget.
- Cost scales with line count (break-point search), matching the paper's
  observation that line breaking, not content complexity, dominates.
- Timing is stable across a session (P95 close to median after warmup) —
  consistent with the paper's Table 2 claim of no degradation over 500
  consecutive compiles.
