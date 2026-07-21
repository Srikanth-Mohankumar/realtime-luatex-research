# How to achieve it — architecture blueprint for real-time LuaTeX

Synthesis of the paper ([00-paper-summary](00-paper-summary.md)), the
internals notes (01–03), the lua-widow-control case study (04), prior art
(05), and our own working replications
([experiments/](../experiments/)). This is the document to start from if we
build the thing.

## The one-sentence design

Keep a LuaTeX process alive; on every keystroke recompile only the edited
paragraph and ship its post-line-break node list to the UI as positioned
glyphs; meanwhile a background full compile keeps a page-position cache fresh
so fast-path paragraphs can be overlaid at their true page positions.

## Why it works (the locality property)

Knuth–Plass line breaking is a pure function of:
paragraph content × (`\hsize`, `\parshape`/`\hangindent`, fonts, language,
`\tolerance`/`\emergencystretch`/penalty params, `\parfillskip`,
`\looseness`). It does **not** depend on document position or surrounding
content. Page breaking, by contrast, is a stateful greedy pass over
everything contributed so far (see notes/02 and 03). So:

- per-keystroke: line breaking = O(paragraph) ≈ 0.1–2 ms — verified in
  [experiments/01](../experiments/01-paragraph-benchmark/).
- eventually-consistent: page breaking, floats, `\ref`, page numbers = full
  compile in the background, seconds.

## The four components (all replicated here)

### 1. Persistent engine ([experiments/04](../experiments/04-persistent-engine/))

- One-time cost: preamble load (book + microtype = 576 ms here, ~1 s paper).
- A TeX-level `\loop` that blocks on `io.read` in `\directlua`, then
  `tex.sprint`s each request into `\setbox0=\vbox{<text>\par}` — line breaker
  runs, page builder and PDF backend do not.
- Production concerns beyond our PoC:
  - **State isolation** between requests: a `\vbox` group contains local
    assignments, but `\global` ones, counters, and Lua globals leak. texlode
    presumably snapshots/restores or forbids. (TeXpresso solves the same
    problem with fork() copy-on-write checkpoints — heavier, but airtight.)
  - **Error recovery**: mid-edit paragraphs are syntactically broken most of
    the time (unbalanced braces, half-typed `\emph{`). The server needs
    a pre-parse or TeX error trapping so a bad request doesn't wedge the
    loop.
  - **Context injection**: the request must carry the paragraph's *context
    parameters* (current font/size at paragraph start, `\hsize` in that
    environment — e.g. inside `quote`/lists, `\parshape` state, language) or
    line breaks will differ from the real document. This is the hard 20%.

### 2. Display-list extraction ([experiments/03](../experiments/03-display-list-extraction/))

Walk the vbox: vlist traversal accumulates y (line `height` + `depth` +
interline glue), hlist traversal accumulates x. **Do not do the width
arithmetic yourself**: measure each node's advance with
`node.dimensions(glue_set, glue_sign, glue_order, n, n.next)` — the engine's
own arithmetic, which is what makes the result exact under microtype font
expansion and expanded font kerns (naive accumulation is off by ±6.5 pt/line;
engine-measured is ≤4 sp ≈ 0.2 nm). Handle: nested boxes (own glue setting,
`shift`), `disc.replace`, `margin_kern` (protrusion), rules,
`xoffset`/`yoffset` on glyphs. Emit `{char, font, x, y}` in scaled points +
a font table (`font.getfont(id)` → name/size/filename).

### 3. Client-side rendering

texlode: glyph stream → HTML5 Canvas via opentype.js; each glyph command =
one Canvas path at engine coordinates; rules = filled rects; colors from the
color stack. Convert sp → CSS px (`px = sp / 65536 / 72.27 * 96 * zoom`).
Font files must be shipped to the browser once per font id (the extractor
reports `filename`). Nothing here is TeX-specific — it's a trivial 2D display
list. Our JSON is directly consumable by such a renderer.

### 4. Background convergence

- Run a normal full `lualatex` compile (or an in-engine full pass)
  periodically / debounced after idle.
- Its product is a **page-position cache**: for every paragraph, which page
  and at what (x, y) its first line sits, plus page furniture (headers,
  footnotes, floats, page numbers) rendered as ordinary output.
- The UI overlays fast-path paragraph display lists at cached positions;
  everything not currently edited comes from the cache. Cross-refs, page
  numbers, float placement "converge, typically within seconds."
- Identity/keying: lua-widow-control demonstrates the technique — tag every
  paragraph's lines with an **attribute** carrying a paragraph id
  (notes/04); attributes survive line breaking and page building, so the
  full compile can report where each source paragraph landed. A
  `pre_shipout_filter` walk of each page box yields the cache in one pass.

## Fast-path bail-out predicates (when a keystroke must take the slow path)

From notes/02 §7, notes/04 §5, and the paper §4 — detectable cheaply by
scanning the paragraph's node list / source:

| Trigger                          | Why it breaks locality                              |
|----------------------------------|-----------------------------------------------------|
| `ins` nodes (footnotes)          | insert material couples to the page builder          |
| `mark` nodes (`\mark`, sections in headers) | marks are resolved at page-assembly time  |
| `\ref`/`\pageref`/`\thepage`/counters | global, order-dependent state                  |
| `\parshape`/`\hangindent` from context | shape unavailable in isolation (but can be *captured* and injected) |
| whatsits carrying state (color push/pop straddling the paragraph, `\write`) | order-dependent side effects |
| paragraph straddles a page break in the cache | its lines interact with `\vsplit`-like page decisions |
| vertical-extent change (line count changed) | page positions below are now stale → schedule reconverge |

Note the last one is not a bail-out — the paragraph still renders correctly
now — it just *dirties* the page cache below it (word-processor behavior).

## What lua-widow-control proves is possible (notes/04)

- `tex.linebreak(head, {parameters})` re-runs Knuth–Plass from Lua on a
  copied node list with overridden parameters — a second, callback-side way
  to build the fast path (no `tex.sprint` round-trip through the mouth).
- `pre_output_filter` / direct `tex.lists.contrib_head` access lets Lua do
  page surgery after the page is built but before the output routine.
- Deterministic `node.flush_list` discipline is what keeps a long-running
  engine from leaking (lwc's early versions failed on 10k-page documents).

## Alternatives considered and rejected by the paper (notes/05)

| System   | Unit of incrementality | Why not sufficient                       |
|----------|------------------------|-------------------------------------------|
| TeXpresso | document, resumed from ~500 ms fork checkpoints | full rerun from checkpoint; modified XeTeX; Linux/macOS only |
| SwiftLaTeX / BusyTeX | document (WASM in browser) | full recompiles, just relocated client-side |
| Typst    | element-level memoization | O(n) global reconvergence per edit: 206 ms @ 300 pp vs texlode's 0.7 ms |
| Overleaf | document, server-side  | 10–30 s for large books, draft-mode advice |
| BaKoMa / Texifier TexpadTeX | true WYSIWYG / viewport slice | closed engines, fidelity compromises |

Also relevant: **monoref** (CTAN, 2026) attacks the *other* half of the
problem — it makes `\ref`/page-total/ToC single-pass in LuaLaTeX via
fixed-width back-patched slots and held shipout pages, which is exactly the
kind of machinery that could shrink the background convergence pass (fewer
reruns to fix cross-references).

## Build plan, if we take this further

1. **Context capture** (the missing hard part): in the full compile, per
   paragraph, record `\hsize`, font at entry, language, parshape, penalty
   params (attribute-keyed, via `pre_linebreak_filter`). Store alongside the
   page-position cache.
2. **Fast-path server v2**: request = (paragraph id, new text); server
   injects captured context (`\hsize`, font selection) around the vbox;
   respond with display list + line count + height/depth per line.
3. **Renderer**: opentype.js canvas page that composites cache pages +
   live paragraph overlay.
4. **Dirty tracking / convergence daemon**: debounce full recompiles,
   diff the new page-position cache against the old, invalidate overlays.
5. **Bail-out detection**: scan request source for footnote/ref/math-display
   markers; route to slow path.
6. Measure against the paper's Tables 1–4 with
   [sources/luatex-benchmark](../../sources/luatex-benchmark/) (the author's
   own scripts).
