# Paper Summary — "Real-Time LuaTeX: Recompiling Large Documents in 1ms"

**Author:** Clemens Lode (LODE Publishing, Düsseldorf) — clemens (at) lodepublishing dot com
**Venue:** TUG 2026 preprint (TUGboat draft, June 13 2026)
**Source:** https://tug.org/tug2026/preprints/lode-realtime.pdf (local copy: [`sources/lode-realtime.pdf`](../sources/lode-realtime.pdf))
**Product:** `texlode` — browser-based book-authoring tool, output identical to standard LuaLaTeX. Public release scheduled October 2026. Details: texlode.com. Benchmark scripts: github.com/texlode/luatex-benchmark

## Thesis

Pixel-perfect LaTeX output and real-time editing are not mutually exclusive.
The key structural observation (from the Knuth–Plass algorithm):

- **Line breaking is paragraph-local.** Given the same `\hsize` and content, TeX
  produces identical line breaks regardless of what appears before or after the
  paragraph. Changing one paragraph does not affect how any other paragraph
  breaks into lines.
- **Page breaking is global** — a paragraph's height change can cascade
  (paragraphs shift pages, floats move, page numbers change) — **but not
  perceptually immediate**, so it need not block the editing loop.

This is exactly the split word processors (Word, InDesign, Google Docs) have
exploited for decades: recompile only the edited paragraph on each keystroke;
let a background process reconverge global layout.

The question the paper asks is not "how do we make LuaTeX faster?" but
"**is full compilation even necessary during editing?**"

## The measurement (§3)

Method: wrap paragraph text between two `\directlua` timing calls using
`os.gettimeofday()` (Figure 1 — replicated in
[`experiments/01-paragraph-benchmark/`](../experiments/01-paragraph-benchmark/)).
Because one compile is faster than the timer resolution, the systematic
benchmark amortizes: each sample = mean of 100 consecutive in-session compiles;
median/P5/P95 over 30 samples after 5-sample warmup.

**Table 1 — per-paragraph line-breaking time, vanilla LuaLaTeX (Intel Core
i7-1355U, TeX Live 2025, with full `microtype`):**

| Paragraph type       | Median  | P5      | P95     |
|----------------------|---------|---------|---------|
| Short (1 line)       | 0.17 ms | 0.03 ms | 0.96 ms |
| Medium (4–5 lines)   | 0.70 ms | 0.65 ms | 0.89 ms |
| Long (10+ lines)     | 1.99 ms | 1.87 ms | 2.41 ms |
| Inline math          | 0.20 ms | 0.17 ms | 0.27 ms |
| Display math         | 0.11 ms | 0.10 ms | 0.13 ms |

Dominant cost is **line breaking, not content complexity** (a one-liner with
inline math is faster than a plain five-line paragraph; display math is fast
because it yields a single-line box with no break-point search).

**Table 2** — 500 consecutive paragraph compiles in one LuaTeX session show no
degradation across compile windows (1–50 vs 451–500): compile time is
independent of session history/document state. → **O(1) per-paragraph**.

**Our local replication (2026-07-21, LuaHBTeX 1.22.0 / TeX Live 2025):**
single short paragraph = **0.31 ms** — consistent with Table 1.

## Why one paragraph is enough (§4)

Locality claim boundaries — cases that CANNOT be compiled paragraph-locally:

- **Footnotes** couple a paragraph to page breaking (inserts).
- **Counter-dependent content** (`\ref`, `\thepage`) requires global state.
- **`\parshape` set by surrounding code** is unavailable in isolation.

These fall to the *background convergence path* (a normal full compile that
resolves page numbers and float placement). The fast path handles body text —
where the user spends most editing time and where latency matters most.

## Architecture of texlode (§5)

1. **Keeping the engine alive.** LuaTeX startup ≈ 1 s for a book-class preamble
   with OpenType fonts. A persistent LuaTeX process accepts paragraph content,
   compiles it, returns the result without restarting.
2. **Capturing output without PDF.** Generating a PDF for one paragraph would
   add significant overhead. Instead, extract the display list directly from
   LuaTeX's node structures after line breaking: a stream of positioned glyph
   commands with coordinates in scaled points. The traversal must correctly
   replicate TeX's internal position calculations, **including all `microtype`
   adjustments**, to produce output identical to standard LuaLaTeX. "TeX was
   never designed to expose its internal rendering state; extracting a
   pixel-identical display list from post-line-break node structures required
   reverse-engineering internal conventions with no prior precedent."
3. **Rendering in the browser.** Glyph stream → HTML5 Canvas via `opentype.js`.
   Each glyph command maps to a Canvas path at engine-specified coordinates;
   rules (filled rectangles) and color operations complete the primitives.
4. **Background convergence.** A full document compile runs periodically,
   producing a complete page-position cache. Fast-path paragraphs are overlaid
   at cached positions: the user sees live line breaking with correct global
   layout. Page numbers, cross-references, float placement converge through
   this path, typically within seconds of an edit.

**Table 3 — single per-paragraph round-trip breakdown:**

| Pipeline stage                       | Short   | Medium  |
|--------------------------------------|---------|---------|
| LuaTeX (line break + traversal)      | 0.65 ms | 4.99 ms |
| IPC + serialization                  | 0.12 ms | 1.03 ms |
| Deserialization + expansion          | 0.02 ms | 0.09 ms |
| **Total round-trip**                 | **0.79 ms** | **6.11 ms** |

Over 80 % of time is Knuth–Plass line breaking (the useful work); engineering
overhead stays under 20 %.

texlode also provides: CRDT-based collaborative editing, Word manuscript
import, proceedings management, cover design tools, print-ready PDF output
identical to standard LuaLaTeX.

## Comparison to prior approaches (§2, §6)

All prior approaches assume the **unit of compilation is the document** and
only make that unit cheaper:

- **TeXpresso** (F. Bour): near-real-time XeTeX via process snapshotting —
  fork() copy-on-write checkpoints every ~500 ms; edits resume from the most
  recent checkpoint before the change. Requires a modified XeTeX engine and
  fork(); Linux/macOS only.
- **SwiftLaTeX / BusyTeX**: TeX Live compiled to WebAssembly, client-side
  compilation in the browser — but still full-document compiles.
- **Typst**: engine rebuilt around incremental compilation; every element
  depends only on explicit inputs → automatic engine-level memoization. Fast
  and clean, but incompatible with LaTeX and still scales linearly.

**Table 4 — editing the same medium paragraph (Typst 0.14.2 watch mode vs
per-paragraph LuaLaTeX):**

| Document size | Typst 0.14.2 | LuaLaTeX (texlode fast path) |
|---------------|--------------|------------------------------|
| 10 pages      | 12.6 ms      | 0.70 ms                      |
| 100 pages     | 76 ms        | 0.70 ms                      |
| 300 pages     | 206 ms       | 0.70 ms                      |

Typst reconverges global layout on every edit *by design* (global consistency
guarantee on every run); texlode only needs the visible paragraph correct now,
with the rest converging soon. Typst's 206 ms ≈ 5 fps — usable but visibly
laggy for smooth drag interactions.

HCI framing: 100 ms = threshold for "instantaneous" (Card/Moran/Newell); one
60 Hz frame = 16 ms budget for *continuous* visual feedback. At 0.1–2 ms per
paragraph, LuaLaTeX sits **inside the frame budget**: text reflows around a
figure as you reposition it, line breaks settle keystroke-by-keystroke, with
full microtype in every update.

## Tradeoff

Temporary inconsistency: pages the user is not viewing may lag until a
background compile converges — the same pattern word processors have used for
decades.

## Conclusion (§7)

Knuth's line-breaking algorithm was always fast enough to run on every
keystroke; the missing piece was engineering: persistent engines, display-list
extraction from node structures never designed as an output format, browser
rendering, background layout convergence. "We just had to stop asking it to
compile entire documents."

## References cited by the paper

1. F. Bour — *TeXpresso: Live rendering and error reporting for LaTeX*, TUGboat 44(2):185–192, 2023. tug.org/TUGboat/tb44-2/tb138bour-texpresso.pdf
2. Card, Moran, Newell — *The Psychology of Human-Computer Interaction*, 1983.
3. M. Haug — *Fast typesetting with incremental compilation*, Master's thesis, TU Berlin, 2022. user.tu-berlin.de/mhaug/fast-typesetting-incremental-compilation.pdf
4. Knuth & Plass — *Breaking paragraphs into lines*, Software—Practice & Experience 11(11):1119–1184, 1981.
5. C. Lode — *How to Publish Your Book with LaTeX*, LODE Publishing, 2026.
6. R. Schlicht — the `microtype` package. ctan.org/pkg/microtype
7. SwiftLaTeX — github.com/SwiftLaTeX/SwiftLaTeX
8. TeXlyre — *BusyTeX: TeX live compiled to WebAssembly*, 2026. github.com/TeXlyre/texlyre-busytex
