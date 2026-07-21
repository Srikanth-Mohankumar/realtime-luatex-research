# Experiment 03 — Display-list extraction from node structures

Proof of concept for the paper's core mechanism (§5, "Capturing output
without PDF"): typeset a paragraph, then walk LuaTeX's node structures after
line breaking and emit a *display list* — a stream of positioned glyph
commands with coordinates in scaled points — without generating a PDF.
This is what `texlode` sends to its browser renderer (Canvas + opentype.js).

## Files

- `extract.lua` — the extractor: walks a `\vbox` (vlist of line hlists),
  accumulates y per line (height/depth/interline glue), accumulates x within
  each line, and writes `display-list.json` with
  `{type:"glyph", char, font, x, y, xoffset, yoffset, expansion}` commands,
  rules, and a font table (`font.getfont()` → name/size/filename) so a
  renderer can resolve glyph outlines.
- `extract-driver.tex` — typesets one `book`-class + `microtype` paragraph
  into `\box0` and runs the extractor.
- `extract-driver-nomt.tex` — same without microtype (control).
- `probe-expansion.lua` / `probe-driver.tex` — probes the engine's own width
  arithmetic (`node.dimensions`).

Sanity check: for each justified line, accumulated x-advance must equal the
hlist's `width` (= `\hsize`). Reconstruction error is printed per line.

## What we learned (the hard-won part the paper alludes to)

The paper: *"the traversal must correctly replicate TeX's internal position
calculations, including all microtype adjustments ... required
reverse-engineering internal conventions with no prior precedent."*
Our replication of that reverse engineering, in three steps:

1. **Naive accumulation** (`glyph.width` + `kern.kern` + glue resolved as
   `width ± glue_set × stretch/shrink` when orders match): **exact without
   microtype** (≤0.03 sp error, float rounding of `glue_set`), but wrong by
   **up to ±6.5 pt per line with microtype** — a visible, catastrophic error.
2. **Cause**: microtype font expansion. `glyph.expansion_factor` (units of
   1/1,000,000; e.g. 20000 = 2 % wider) is *not* included in `glyph.width`.
   Applying `width × (1 + ef/1e6)` manually reduces the error to ≤0.011 pt —
   still not exact, because the engine also expands **font kerns** and rounds
   each expanded width to integer scaled points with its own arithmetic.
3. **Engine-exact method**: `node.dimensions(glue_set, glue_sign, glue_order,
   n, n.next)` measures a node span using the engine's own C arithmetic —
   including set glue, expanded glyphs, and expanded font kerns. Verified:
   `node.dimensions` over a whole stretched line reproduces the hlist width
   with **diff = 0 sp** even under full microtype. Using it per node leaves
   only independent-rounding residue: **≤ 4 sp = 0.00006 pt** per line
   (≈ 0.2 nanometers on paper; sp-exactness would need prefix-span
   measurement or replicating the engine's glue-rounding order).

Other node-level facts the extractor must handle (all hit in this experiment):

- `\parindent` materializes as a leading empty hlist of width 15 pt — nested
  boxes have their own `glue_set/glue_sign/glue_order`.
- Post-line-break `disc` nodes remaining inside a line contribute their
  `replace` list (the no-break rendering); hyphenated break points have
  already been materialized into the lines by the line breaker.
- microtype protrusion appears as `margin_kern` nodes at line edges; their
  width is part of the advance.
- The last line of a justified paragraph is stretched at fil order
  (`\parfillskip`), so finite interword glue stays at natural width there.
- y-advance per line = `height + depth` of each line hlist **plus** the
  interline glue (`\baselineskip` regulation) between them; baseline of a
  line = cumulative y + line height.

## Results (2026-07-21, LuaHBTeX 1.22.0, TeX Live 2025)

| Method                              | Worst line error (with microtype) |
|-------------------------------------|-----------------------------------|
| naive accumulation                  | 426894 sp (6.51 pt) — visible     |
| + manual expansion_factor           | 715 sp (0.011 pt)                 |
| per-node `node.dimensions`          | **4 sp (0.00006 pt)**             |
| (control: no microtype, naive)      | 0.03 sp                           |

**Conclusion:** pixel-identical display-list extraction from post-line-break
node structures is achievable in ~40 lines of Lua *if* you let the engine do
the width arithmetic via `node.dimensions`. The JSON output
(`display-list.json`: 230 glyph commands for a 4-line paragraph, with font id
→ `lmroman10-regular` mapping) is directly renderable by opentype.js/Canvas.
