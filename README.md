# Real-Time LuaTeX — research repo

Research into **per-paragraph real-time recompilation of LuaLaTeX
documents**, following Clemens Lode's TUG 2026 preprint *"Real-Time LuaTeX:
Recompiling Large Documents in 1 ms"* (the `texlode` system). Goal: understand
the relevant TeX/LuaTeX internals deeply enough to build the same
architecture, and verify the paper's claims by replication.

**Status (2026-07-21): every quantitative claim we tested replicates, and the
full fast path (persistent engine → per-paragraph compile → node-structure
display-list extraction → IPC) is reproduced end-to-end in
[experiments/04](experiments/04-persistent-engine/) at 0.4–2.5 ms round-trip.**

## The idea in three lines

1. Line breaking (Knuth–Plass) is **paragraph-local**: it depends only on the
   paragraph's content + a finite parameter set, never on surrounding text.
2. Page breaking is global but not perceptually urgent — word processors have
   deferred it to a background pass for decades.
3. So: recompile only the edited paragraph on each keystroke (~1 ms, inside a
   60 Hz frame budget, full microtype), extract positioned glyphs straight
   from LuaTeX's node structures (no PDF), render in the browser, and let a
   periodic full compile converge pages/refs/floats.

## Layout

| Path | Content |
|------|---------|
| [notes/00-paper-summary.md](notes/00-paper-summary.md) | Full summary of the preprint: measurements, architecture, comparisons |
| [notes/01-luatex-nodes-and-callbacks.md](notes/01-luatex-nodes-and-callbacks.md) | Node model (hlist/vlist/glyph/disc/glue/kern/penalty/ins/whatsit), attributes, node library, callback pipeline |
| [notes/02-output-routine-marks-inserts.md](notes/02-output-routine-marks-inserts.md) | Page builder, `\output`/`\@makecol`/`\@outputdblcol`, inserts (`\footins`, floats), marks & ltmarks, `\vsplit`, penalties |
| [notes/03-tex-engine-paragraph-and-page.md](notes/03-tex-engine-paragraph-and-page.md) | Paragraph builder & Knuth–Plass, page builder, box model, glue/kern, insertions — the locality property, with verified callback order |
| [notes/04-lua-widow-control-analysis.md](notes/04-lua-widow-control-analysis.md) | Case study: callback-based node-list surgery in a production package; `tex.linebreak()` from Lua |
| [notes/05-prior-art-and-ecosystem.md](notes/05-prior-art-and-ecosystem.md) | TeXpresso, SwiftLaTeX, BusyTeX, Typst/comemo, BaKoMa, Texifier, WhizzyTeX, HINT, ETAP, Overleaf; **monoref**; comparison table |
| [notes/06-architecture-blueprint.md](notes/06-architecture-blueprint.md) | **Synthesis: how to build it** — components, bail-out predicates, build plan |
| [experiments/01-paragraph-benchmark/](experiments/01-paragraph-benchmark/) | Replication of paper §3: 0.12 ms (short) / 1.61 ms (7-line) median per paragraph |
| [experiments/02-node-dump/](experiments/02-node-dump/) | Node-list dumps: what a paragraph looks like after line breaking |
| [experiments/03-display-list-extraction/](experiments/03-display-list-extraction/) | Positioned-glyph extraction from node structures, engine-exact under microtype (≤4 sp) |
| [experiments/04-persistent-engine/](experiments/04-persistent-engine/) | Persistent LuaTeX paragraph server + client: paper Table 3 replication |
| [sources/](sources/) | The preprint, TUGboat PDFs, monoref docs, cloned `luatex-benchmark` (gitignored) |

## Replication scorecard (LuaHBTeX 1.22.0, TeX Live 2025, Linux)

| Paper claim | Paper | Here | Verdict |
|---|---|---|---|
| Single short paragraph compile | < 1 ms | 0.31 ms | ✅ |
| Short paragraph, amortized median | 0.17 ms | 0.12 ms | ✅ |
| Long-ish paragraph, amortized median | 0.70–1.99 ms | 1.61 ms | ✅ |
| No degradation across a session | Table 2 | stable P95 | ✅ |
| Display list extractable, pixel-exact w/ microtype | §5 | ≤ 4 sp error | ✅ (needs `node.dimensions`) |
| Round-trip incl. IPC, short/medium | 0.79 / 6.11 ms | 0.43 / 2.52 ms | ✅ |
| Engine startup amortized once | ~1 s | 0.58 s | ✅ |

Caveats found during replication: the paper's TeXpresso citation URL 404s
(TUGboat 44:2 is tb137); glyph positions are only exact if you use the
engine's own arithmetic (`node.dimensions`) rather than accumulating
`glyph.width` — microtype expansion is invisible in the metric widths.

## Requirements

TeX Live 2025 (lualatex + microtype), Python 3 for the client. Everything
runs offline; no engine modifications, vanilla TeX Live — that is the paper's
point.
