# 05 — Prior Art and Ecosystem

Survey of the systems cited in Clemens Lode's TUG 2026 preprint *"Real-Time LuaTeX:
Recompiling Large Documents in 1 ms"* (`sources/lode-realtime.pdf`), plus adjacent
prior art the paper does not cite. Research date: 2026-07-21.

The preprint's §2 ("Prior approaches") names exactly three lines of work — TeXpresso,
SwiftLaTeX/BusyTeX, and Typst — and frames them all as sharing one assumption: *"that
the unit of compilation is the document."* This note documents each of those in depth,
then the wider ecosystem (ETAP, HINT, BaKoMa, Texifier/TexpadTeX, WhizzyTeX, Overleaf,
latexmk, ltmarks, lua-widow-control, monoref), and closes with a comparison table.

---

## 1. TeXpresso (Frédéric Bour)

- Repo: https://github.com/let-def/texpresso (MIT-intended, ~750 stars, "still in an
  early development phase")
- Editor plugins: Emacs (`emacs/texpresso.el`, in-repo),
  Neovim (https://github.com/let-def/texpresso.vim),
  VS Code (https://github.com/DominikPeters/texpresso-vscode)
- Related: Tectonic discussion of the original proof-of-concept:
  https://github.com/tectonic-typesetting/tectonic/discussions/1029 ;
  "Path to TeXpresso 1.0": https://github.com/let-def/texpresso/discussions/61

### Citation problem in the preprint

The preprint's reference [1] cites *"F. Bour. TeXpresso: Live rendering and error
reporting for LaTeX. TUGboat 44(2):185–192, 2023.
tug.org/TUGboat/tb44-2/tb138bour-texpresso.pdf"*. **This URL 404s and the article does
not appear to exist.** TUGboat 44:2 is issue **tb137** (tb138 is 44:3); neither issue's
table of contents (https://tug.org/TUGboat/Contents/contents44-2.html,
contents44-3.html) lists a Bour/TeXpresso article, and "Bour, Frédéric" is absent from
the TUGboat author index. The reference appears to conflate the GitHub project with a
TUGboat publication that was never printed (pages 185–192 of 44:2 belong to other
articles). The PDF therefore could **not** be downloaded into `sources/`; the
description below is assembled from the repository (`README.md`, `doc/pres/pres.tex`,
protocol docs) and secondary sources.

### Architecture

Four cooperating components (README "Design" section):

1. **A modified XeTeX engine** (`texpresso-xetex` helper binary) that renders to DVI
   and is instrumented to talk to the TeXpresso driver instead of doing ordinary file
   I/O. The proof-of-concept was originally built on a fork of **Tectonic** (Rust
   XeTeX); it was later rebased onto plain XeTeX/TeX Live to simplify the codebase and
   drop the Rust toolchain dependency.
2. **MuPDF** as the renderer — a custom DVI interpreter drives MuPDF to rasterize
   pages ("Not perfect but quite compatible", per Bour's own slides in
   `doc/pres/pres.tex`; TikZ and graphics work, **shell-escape is disabled**, so e.g.
   `minted` does not render live).
3. **A libSDL viewer** window (page navigation, zoom, crop, dark-mode/theming,
   stay-on-top).
4. **The driver** (`texpresso` binary, the actual repo): talks to the editor over a
   JSON protocol (`EDITOR-PROTOCOL.md`, `SERVER-PROTOCOL.md`), "maintains an
   incremental view of the document and the rendering process (supporting
   incrementality, rollback, error recovery, etc.)", re-runs the engine over modified
   portions, and synchronizes the viewer.

### Incrementality: process snapshotting via fork(2)

The core trick, as summarized in the Lode preprint §2 and confirmed by Bour's slides
("Snapshotting is done using *fork(2)*"):

> Every ~500 ms of processing, the driver forks the engine, creating a copy-on-write
> checkpoint. When an edit arrives, it finds the most recent checkpoint before the
> change and re-runs from there.

So the unit of incrementality is a **time-sliced engine checkpoint**, not a document
structure: TeXpresso replays a *suffix* of the document from the nearest snapshot.
Consequences:

- Worst case (edit near the top of a large document) approaches a full recompile;
  best case (edit near the end) is nearly free. Latency is "almost immediate" for
  human perception but is bounded by ~500 ms checkpoint granularity plus replay time,
  not ~1 ms.
- Requires POSIX `fork()` with copy-on-write semantics → **Linux and macOS only**
  (AMD64 and Apple Silicon); no Windows, no browser.
- Requires a **modified engine** (patched XeTeX) so that file reads, output, and time
  can be virtualized/rolled back by the driver.
- Output is genuine XeTeX DVI → high fidelity, but a custom DVI→MuPDF path rather
  than the standard xdvipdfmx PDF path.

### Editor integration

Bidirectional SyncTeX-like synchronization: clicking in the viewer jumps the editor to
the source position, and the viewer follows the editor position (forward sync). Emacs
integration streams buffer deltas live; for other editors TeXpresso can also re-read
changed files on `SIGUSR1`. Errors and warnings for the *current page* are reported
live into the editor (Emacs `texpresso-display-output`, Vim quickfix).

---

## 2. SwiftLaTeX

- Repo: https://github.com/SwiftLaTeX/SwiftLaTeX (~2.3k stars, AGPL-3.0,
  last release February 2022 — effectively dormant)

**What it is.** XeTeX and PdfTeX compiled to **WebAssembly** with Emscripten, exposed
as JavaScript engine classes (`XeTeXEngine`, `PdfTeXEngine`) that run entirely
client-side in the browser ("all computation is done strictly locally"). A
WYSIWYG-ish IDE component ("pretty similar to Overleaf, except users are allowed to
edit pdf output directly") exists but was left work-in-progress.

**Architecture.**
- The WASM engines run in Web Workers with a virtual filesystem.
- Missing packages/fonts are fetched **on demand from CTAN or a custom "Texlive
  On-Demand" mirror server** rather than shipping a full TeX Live tree.
- Format files can be pre-generated and cached via a `compileFormat()` API, which
  removes INITEX/format-building cost from the edit loop.

**What it does *not* cache:** any compilation state. Every keystroke-triggered build
is a **full cold-ish document compile** inside the WASM engine (aux files persist in
the virtual FS, so it behaves like repeated `xelatex` runs, not like a resident
engine). No paragraph- or page-level incrementality. The Lode preprint's one-line
verdict is accurate: "compilation still processes the full document each time."

---

## 3. BusyTeX and TeXlyre (texlyre-busytex)

- Upstream: https://github.com/busytex/busytex (MIT)
- TeXlyre fork/package: https://github.com/TeXlyre/texlyre-busytex (AGPL-3.0) — this
  is reference [8] of the preprint
- TeXlyre itself (local-first collaborative LaTeX editor): https://texlyre.github.io/texlyre/

**BusyTeX** compiles TeX Live programs into "a single fully static binary
(x86_64-linux / WASM)" via Emscripten — busybox-style, all tools in one binary:
pdfTeX, XeTeX, **LuaHBTeX**, BibTeX8, xdvipdfmx, makeindex, kpsewhich (no Biber; too
heavy for WASM so far). TeX Live trees are shipped as precompiled **data packages**
(texlive-basic, latex-extra, latex-recommended, science) loaded modularly into the
browser filesystem, with a JS pipeline + worker for running compile jobs.

**texlyre-busytex** wraps this as a TypeScript/npm package for the TeXlyre editor:
XeLaTeX/PdfLaTeX/LuaLaTeX toolchains (each paired with bibtex8 and the appropriate
DVI/PDF stage), Web Worker execution, multi-file projects, and persistence of the
downloaded data packs via Emscripten's IndexedDB `EM_PRELOAD_CACHE`. Its docs are
explicit that this caching covers **assets only** — "does not implement any
additional caching layer" — i.e. **no compilation incrementality whatsoever**; every
compile is a full `latexmk`-style document run in WASM (typically seconds for real
documents, dominated by engine startup + full typesetting).

Relevance to texlode: BusyTeX/SwiftLaTeX prove browser-resident TeX is practical and
solve distribution (fonts/packages over HTTP), but keep the batch model. texlode
inverts this: native LuaTeX server-side kept alive, with only the *display list*
shipped to the browser.

---

## 4. Typst's incremental compilation (Martin Haug, comemo)

- Thesis: M. Haug, *Fast Typesetting with Incremental Compilation*, Master's thesis,
  TU Berlin, 2022. DOI: 10.13140/RG.2.2.15606.88642.
  Announcement: https://mha.ug/post/get-my-other-thesis/
  **The PDF is no longer fetchable** — the URL cited by the preprint and by Haug's own
  page (https://www.user.tu-berlin.de/mhaug/fast-typesetting-incremental-compilation.pdf)
  returned 404 at research time; ResearchGate hosts a copy behind its viewer
  (https://www.researchgate.net/publication/364622490_Fast_Typesetting_with_Incremental_Compilation).
  Not downloaded into `sources/`.
- Companion thesis (Mädje): https://laurmaedje.github.io/programmable-markup-language-for-typesetting.pdf
- comemo crate write-up: https://laurmaedje.github.io/posts/comemo/ ;
  code: https://github.com/typst/comemo

**Thesis contributions** (per Haug's abstract/announcement): (1) an **incremental
parser** for a context-sensitive markup language, and (2) a **constraint- and
memoization-based optimization for typesetting**. Reported results: Typst sped up by
**4.5–91×**, and edit-recompiles **3.4–9895× faster than LaTeX** on comparable
documents.

**How comemo ("constrained memoization") works:**
- Pure-looking functions like `layout(node: &Node, world: Tracked<dyn World>) -> Frame`
  are memoized at the **engine level**.
- Dependencies that *could* be consulted (files, fonts, styles) are wrapped in
  `Tracked<T>` handles. Every method call through a tracked handle is recorded as a
  **constraint**: (argument tuple → 128-bit SipHash of the return value).
- On recompilation, a cached result is reused iff the new world still satisfies the
  recorded constraints — i.e. the function is revalidated against **what it actually
  read**, not against everything that changed. This is the "constrained inputs" idea:
  elements depend only on explicit, tracked inputs, so memoization is automatic and
  sound.
- The same idea appears at multiple granularities: incremental re-parsing of the
  source, memoized module evaluation, and memoized layout subtrees.

**Why it is fast, and why it still scales O(n):** for small documents Typst recompiles
in single-digit milliseconds. But Typst **guarantees globally consistent output on
every run** — every edit reconverges the whole document's layout. Even when most
layout subtrees hit the cache, cache *validation* and the global page-assembly pass
walk state proportional to document size, so interactive recompile time grows roughly
linearly with page count. The preprint's Table 4 (Typst 0.14.2, watch mode, editing a
mid-document paragraph): **12.6 ms @ 10 pages, 76 ms @ 100 pages, 206 ms @ 300
pages**, versus texlode's constant 0.70 ms. Two honest caveats recorded in the
`luatex-benchmark` repo: incremental times are **strongly version-dependent** (0.15.0
measured; "0.12, 0.14 are several times faster"), and edit *position* matters for
Typst (mid-document edit ≈ half-document reflow cascade; end-of-document edit is its
best case). And of course Typst is a different language — "inherently incompatible
with LaTeX".

---

## 5. texlode itself

- Site: https://texlode.com — **unreachable at research time** (connection
  reset/empty response on both apex and www). Consistent with the preprint: the
  product "is scheduled for public release in October 2026", so the site likely
  isn't publicly serving yet. Per the preprint, texlode is a browser-based book
  authoring tool by Clemens Lode (LODE Publishing, Düsseldorf;
  https://www.lodepublishing.com/) with CRDT collaborative editing, Word manuscript
  import, proceedings management, cover design, and print-ready PDF identical to
  standard LuaLaTeX.

- Benchmark repo: https://github.com/texlode/luatex-benchmark — **exists, MIT,
  cloned to `sources/luatex-benchmark/`**. Contents:
  - `paragraph-benchmark.tex` — the paper's Fig. 1 MWE: one paragraph wrapped in
    `\directlua` `os.gettimeofday()` calls; prints per-paragraph time to terminal.
  - `systematic-benchmark.tex` — reproduces the §3 table (short/medium/long,
    inline/display math; median/P5/P95 over 30 amortized samples of 100 in-session
    compiles each, 5-sample warmup).
  - `stability-benchmark.tex` — 500 consecutive in-session compiles; reports medians
    for windows 1–50 / 226–275 / 451–500 (no degradation claim).
  - `bench-lib.lua` — shared stats helpers (kept in a real `.lua` file so `%`/`#`
    survive TeX catcodes); percentile rule matches texlode's internal harness.
  - `typst-comparison/bench.mjs` — Node script: binary-searches a Lorem document to a
    target page count, cold-compiles, then drives `typst watch` and edits one
    mid-document paragraph N times, parsing watch's "compiled in X" lines. Supports
    `mid` vs `end` edit position.
  - README methodology notes: measurements are **in-session** (cold ~1 s LuaLaTeX
    startup excluded), line breaking is isolated inside a `\vbox` (page
    builder/output routine excluded), and the repo deliberately contains **no texlode
    code** — it times "public engines doing public operations" to corroborate the
    paper's pipeline breakdown (~80% of the round trip = line break + traversal), not
    to reproduce the product pipeline.

---

## 6. The monoref package

**monoref exists on CTAN**: https://ctan.org/pkg/monoref — *"Single-pass
cross-references, page totals, and a table of contents for LuaLaTeX"*. Version 1.3,
2026-07-07 (first announced on CTAN 2026-07-09); author/maintainer **Srikanth
Mohankumar**; LPPL 1.3c; experimental; LuaLaTeX-only (errors on other engines); in
TeX Live and MiKTeX. Repo: https://github.com/Srikanth-Mohankumar/latex-monoref.
Package documentation downloaded to `sources/monoref.pdf`.

**What it does.** Removes the classic `.aux` two-pass round-trip for a useful subset:
`\ref`, `\pageref` (forward *and* backward), `\lastpage` (total body pages), and a
multi-level ToC prepended at the end with roman folios — all in **one LuaLaTeX run**.

**Mechanism (two ideas, per its README):**
1. **Fixed-width slots** — a not-yet-known value is typeset as a fixed-width `\hbox`;
   since the box's outer width is frozen at paragraph-breaking time, back-patching the
   value later can never reflow the line.
2. **Shipout-accurate held pages** — every finished page is *held* in memory via the
   `shipout/before` hook + `\DiscardShipoutBox`; `\label` drops an invisible
   `\special` marker that is scanned when the page is held (avoiding the
   asynchronous-page-builder off-by-one of reading `\value{page}` at `\label` time);
   at `\end{document}` unknown values are patched into the held pages, which are then
   shipped. Hyperref links stay single-pass via the package's own named destinations.

**Relation to our agenda.** Our research agenda note ("Inserts: `\footins`, floats as
insertions, class/split; pairing callout↔note — check … lua-widow-control and
monoref") guessed monoref might pair callouts with notes. It does not do
callout↔note pairing per se — it is about **reference/target consistency without a
second pass**. But it is squarely relevant to real-time LuaTeX: its fixed-width-slot
back-patching is a *synchronous, single-run* alternative to texlode's *asynchronous
background-convergence* for exactly the global state the preprint lists as fast-path
boundaries (`\ref`, `\thepage`, page totals). The held-shipout-page technique is also
a working demonstration of treating finished pages as mutable in-memory node
structures — the same "node lists as output format" territory texlode operates in.

**Closest neighbors on CTAN** (for the callout↔note side of the agenda):
- `zref` (https://ctan.org/pkg/zref) — extensible property-list label/ref system; the
  standard substrate for custom paired references.
- `cleveref` (https://ctan.org/pkg/cleveref) — type-aware references.
- `marginnote` (https://ctan.org/pkg/marginnote), `sidenotes`
  (https://ctan.org/pkg/sidenotes), `snotez` — margin/side notes positioned relative
  to their callout.
- `fnpct` (https://ctan.org/pkg/fnpct) — footnote-marker/punctuation interaction.
- `lua-widow-control` — see §7 below.
- Kernel `ltmarks` — see §7 below.

---

## 7. Other relevant ecosystem

### ETAP (Didier Verna) — real-time paragraph typesetting for experimentation
TUGboat 44:2 (2023), *"Interactive and real-time typesetting for demonstration and
experimentation: ETAP"*, doi:10.47397/tb/44-2/tb137verna-realtime — downloaded to
`sources/tb137verna-realtime.pdf`. A Common Lisp GUI platform whose unit is exactly
**one paragraph**: source text re-typeset **in real time** as you type or drag
sliders, with switchable algorithms (Fixed/Fit/Barnett/Duncan/**Knuth–Plass**),
live-tweakable penalties/demerits, and visual overlays (hyphenation points, boxes,
baselines, badness tooltips). Not a document processor — an experimentation bench —
but it independently validates the preprint's core premise that *paragraph-level
(re)formatting is cheap enough to be continuous*. Verna's related-work paragraph is
also a handy map of the WYSIWYG-TeX space (Overleaf, BaKoMa, LyX, TeXworks, TeXmacs).

### HINT / HiTeX (Martin Ruckert) — reflowable TeX output
*"News from the HINT project: 2023"*, TUGboat 44:2,
doi:10.47397/tb/44-2/tb137ruckert-hint23 — downloaded to
`sources/tb137ruckert-hint23.pdf`; project site https://hint.userweb.mwn.de/.
HiTeX (in TeX Live since 2022) writes a **HINT file**: TeX's contribution list frozen
*before* page breaking, so the **viewer** runs the page builder at view time for the
actual screen size (variable page sizes, links/labels/outlines, text extraction).
Complementary prior art: HINT moves *page breaking* to interaction time on-device,
while texlode moves *line breaking* to interaction time and defers page breaking to a
background compile.

### BaKoMa TeX (Basil K. Malyshev) — the historical true-WYSIWYG TeX
- Overview (mirror): https://bakoma-tex.daniel-aldrich.ca/about/programs/overview and
  http://www.bakoma-tex.com/menu/aboutall.php ; TeXWord description:
  https://bakoma-tex.daniel-aldrich.ca/about/programs/texword
- Commercial, Windows-centric (later Mac/Linux); development ceased after the
  author's death; the original site survives via mirrors. Fonts live on as
  https://ctan.org/pkg/bakoma.

BaKoMa TeX Word was a **true WYSIWYG** editor: the caret sits *inside the typeset
page* ("the illusion of working in the DVI file") and edits are re-typeset **on each
keystroke** with claimed 100% native-TeX output compatibility, because the display
*is* TeX output. Crucially for prior art, its documentation states that on reload it
applies **"incremental formatting instead of full processing of the document"** — a
proprietary, undocumented incremental TeX pass, decades before TeXpresso/texlode. It
also synchronized a source editor (Centaur) with the WYSIWYG view via
save-on-focus-loss. The closest historical precedent to texlode's goal (keystroke
granularity, real TeX fidelity), but closed-source, single-platform, and its exact
incremental mechanism was never published.

### Texifier (formerly Texpad) / TexpadTeX — "live typeset"
- https://www.texifier.com/ ; typesetter docs:
  https://www.texifier.com/docs/apps/typesetting/typesetters/texpadtex ; package
  coverage: https://www.texifier.com/docs/tutorials/tex/typesetters/texpadtex/package-coverage

TexpadTeX is a **custom-built TeX engine** (macOS/iOS) doing live typeset on each
keystroke. Its incrementality is **viewport-anchored**: instead of typesetting from
`\begin{document}` to the end, it "typesets only the part of the document between the
user's cursor and the end of what is visible in the output viewer" — i.e. it
checkpoints state up to the cursor and re-runs a visible *slice*. It also takes input
directly from the editor buffer and renders directly to the viewer (no disk I/O), with
GPU-accelerated rendering via Metal (Texpad 1.9). Errors are reported live. Fidelity
caveat: TexpadTeX supports a large but incomplete package set (its docs maintain a
coverage list), so output is not guaranteed identical to TeX Live — the standard
tradeoff texlode avoids by using vanilla LuaTeX. Apple-platform-only.

### WhizzyTeX (Didier Rémy) — incremental previewing by slicing, 2001-era
- http://cambium.inria.fr/~remy/whizzytex/whizzytex.html (manual),
  https://www.emacswiki.org/emacs/WhizzyTeX

Emacs minor mode for "incremental viewing" of LaTeX: it **dumps a format** with the
document's preamble/macros preloaded (a checkpoint, by `\dump` rather than `fork()`),
then on each pause recompiles only the current **slice** (e.g. the section around the
cursor) against that format, displaying it in xdvi/Active-DVI with cursor tracking and
back-pointing. The direct conceptual ancestor of both TeXpresso (checkpoint + partial
re-run) and Texifier (slice around cursor). Limitations: slice-local output only
(global layout/pagination wrong by construction), DVI toolchain, Unix.

### Overleaf — the cloud full-recompile model
- How compiles run: latexmk orchestrates pdflatex/xelatex/lualatex + bibtex/biber
  reruns server-side; per-user **cache of aux files** (`.aux`, `.toc`, `.bbl`, …) so
  reruns are warm, but every compile is still a **full document compile**:
  https://www.overleaf.com/learn/how-to/Clearing_the_cache ,
  https://www.overleaf.com/blog/216-examples-techniques-and-tips-on-how-to-use-latexmkrc-with-overleaf
- Compile timeouts (20 s on free plan as of 2025-2026; the preprint's motivating pain
  point — "client books with complex fonts routinely hit Overleaf's compile
  timeout"): https://docs.overleaf.com/troubleshooting-and-support/fixing-and-preventing-compile-timeouts
- Overleaf's own answer to interactivity is a **rich source editor**, not a live
  typeset: *"Bumpy road towards a good LaTeX visual editor at Overleaf"* (Davies),
  TUGboat 44:2, doi:10.47397/tb/44-2/tb137davies-visual — downloaded to
  `sources/tb137davies-visual.pdf`. The Visual Editor renders LaTeX-ish structure in
  CodeMirror 6 decorations; the PDF preview still comes from full recompiles.

### latexmk -pvc — the canonical watch loop
https://ctan.org/pkg/latexmk (John Collins). `-pvc` ("preview continuously") watches
source files and re-runs the full latexmk dependency-resolving compile on every
change, refreshing the viewer. It is the baseline UX texlode/TeXpresso compete with:
seconds-to-minutes latency, perfect fidelity, zero engine modification. `-pvc` plus
draft mode / `\includeonly` is the traditional mitigation for large books.

### ltmarks — the LaTeX kernel's new mark mechanism
Part of the LaTeX kernel since the **2022-06-01 release** (ltmarks v1.0d; announced in
ltnews35: https://ctan.csail.mit.edu/macros/latex/base/ltnews35.pdf; docs:
https://ctan.math.illinois.edu/macros/latex-dev/base/ltmarks-doc.pdf — it is a kernel
module, not a standalone CTAN package; https://ctan.org/pkg/ltmarks 404s).
Provides **independent mark classes** (`\NewMarkClass`, `\InsertMark`,
`\TopMark`/`\FirstMark`/`\LastMark` per class and per region) on top of the `\marks`
ε-TeX primitive, with correct top-mark semantics. Relevant here because marks are the
kernel-blessed channel for recovering *page-scoped state* (running heads, current
section, first/last anything) from boxed material — exactly the kind of global state a
background-convergence pass must replay, and one of the mechanisms a page-position
cache (texlode's overlay model) has to respect when re-associating paragraphs with
pages.

### lua-widow-control (Max Chernoff)
https://ctan.org/pkg/lua-widow-control (v3.0.1, 2024-03-11) + TUGboat articles by the
author. Removes widows/orphans by asking Knuth–Plass (via Lua callbacks,
`pre_output_filter`/`post_linebreak_filter`) to **lengthen a nearby paragraph by one
line** instead of stretching the page. Cited in our agenda because it is the
best-documented example of *paragraph-level node-list surgery in LuaTeX from the Lua
side* — the same substrate texlode's display-list extraction lives on — and because it
quantifies how paragraph re-breaking interacts with page-level constraints (inserts,
`\footins`, floats), i.e. exactly the fast-path boundary cases the preprint concedes
to the background pass.

### Brief mentions
- **GladTeX** (https://github.com/humenda/GladTeX): converts LaTeX formulas embedded
  in HTML into images. Batch, not real-time — not actually prior art for live TeX
  despite the name coming up in brainstorms.
- **TeXmacs** (https://www.texmacs.org): real-time WYSIWYG structured editor with
  TeX-quality output, but **not TeX** — its own typesetting engine (C++/Scheme).
  Proves real-time high-quality typesetting is feasible if you abandon the TeX
  engine; Typst is the modern iteration of that bet.
- **LyX** (https://www.lyx.org): WYSIWYM front end; preview still via batch LaTeX runs
  (instant-preview snippets are batch-compiled images).
- **preview-latex / AUCTeX**: batch-compiles environment snippets to inline images in
  Emacs; snippet-level granularity, seconds latency.

---

## 8. Comparison table

| System | Unit of incrementality | Engine modification | Interactive latency | Scaling with doc size | Fidelity | Platform |
|---|---|---|---|---|---|---|
| **texlode** (Lode 2026) | Single paragraph (line breaking only); global layout via periodic background full compile | **None** (vanilla TeX Live LuaTeX, kept resident; display list read from node structures) | ~0.1–2 ms/paragraph; ~0.8–6 ms full round trip incl. IPC + Canvas | **O(1)** for fast path (constant 0.70 ms @ 10/100/300 pp); background pass O(n) but off the keystroke path | Claimed pixel-identical to LuaLaTeX incl. microtype; refs/footnotes/floats temporarily stale until convergence | Browser front end (Canvas + opentype.js), native LuaTeX process backend |
| **TeXpresso** (Bour) | Time-sliced checkpoint: replay from last fork() snapshot (~500 ms granularity) | **Yes** — patched XeTeX (`texpresso-xetex`), originally Tectonic fork | "Almost immediate"; bounded by snapshot spacing + suffix replay | O(edit-to-end distance); worst case ≈ full compile | Real XeTeX DVI via MuPDF; no shell-escape; minor DVI-interpreter gaps | Linux/macOS only (needs fork()); Emacs/Neovim/VS Code |
| **Typst** (Haug/Mädje) | Memoized subtrees (comemo constrained memoization) + incremental parsing; global relayout every edit | N/A — engine built for it (not TeX) | Single-digit ms (small docs); 12.6→206 ms for 10→300 pp (0.14.2, mid edit) | **O(n)** — global reconvergence each run | Globally consistent every run; **not LaTeX** | Rust; native + WASM (typst.app in browser) |
| **TexpadTeX** (Texifier) | Slice from cursor to end of visible viewport | **Yes** — proprietary custom TeX engine, in-memory I/O, Metal rendering | Per-keystroke, "almost realtime" | ~O(visible slice), after checkpointed prefix | Incomplete package coverage (documented list); not guaranteed TeX Live-identical | macOS/iOS only |
| **BaKoMa TeX Word** | Document reload with proprietary "incremental formatting"; edits applied in the typeset view per keystroke | **Yes** — proprietary integrated TeX | Per-keystroke (claimed) | Unpublished; incremental reformat on reload | Claimed 100% native LaTeX output (display *is* TeX output) | Windows (historical; discontinued, unmaintained) |
| **WhizzyTeX** | Slice (section/page around cursor) against a `\dump`ed preamble format | No engine patch (stock TeX + format dump + macro instrumentation) | Sub-second per slice on pause | O(slice); global layout wrong by construction | Slice-local only; DVI toolchain | Unix + Emacs |
| **ETAP** (Verna) | One paragraph (experimentation bench, not a document processor) | N/A — reimplementation (Common Lisp) of KP et al. | Real-time on parameter/text change | N/A (single paragraph) | Algorithm-faithful, not a TeX replacement | Cross-platform (Lisp) |
| **HINT/HiTeX** (Ruckert) | Page breaking deferred to **view time** (reflow on device); typesetting itself batch | **Yes** — HiTeX engine variant + new output format | Page reflow interactive in viewer | Viewer-side page building ~O(visible) | TeX line breaking preserved; pagination recomputed per device | HiTeX in TeX Live; viewers incl. mobile |
| **SwiftLaTeX** | None (full recompile; format file precompiled) | Ported (XeTeX/PdfTeX → WASM), unmodified semantics | Seconds (full doc in WASM) | O(n) per keystroke | Real XeTeX/pdfTeX output | Any browser; dormant since 2022 |
| **BusyTeX / texlyre-busytex** | None (full recompile; asset packs cached in IndexedDB) | Ported (pdfTeX/XeTeX/LuaHBTeX → WASM), unmodified | Seconds (full doc in WASM) | O(n) per compile | Real TeX Live 2023 engines incl. LuaHBTeX | Any browser |
| **Overleaf** | None (server-side latexmk full compile; per-user aux cache) | No | Seconds–minutes; 20 s free-plan timeout | O(n), warm aux files | Full TeX Live | Cloud/browser |
| **latexmk -pvc** | None (watch loop, full dependency-resolved recompile) | No | Seconds–minutes | O(n) | Full TeX Live | Anywhere |

**Reading of the table.** Prior systems either (a) shrink the *replayed prefix/suffix*
of a batch run (TeXpresso, WhizzyTeX, TexpadTeX — all needing engine patches or
custom engines, all still O(document tail)), (b) memoize inside a non-TeX engine built
for it (Typst — still O(n) global reconvergence), (c) keep full compiles and optimize
distribution (SwiftLaTeX, BusyTeX, Overleaf, latexmk), or (d) move one layout stage to
interaction time (HINT: page breaking; ETAP: paragraph demo). texlode's distinguishing
combination is: **paragraph as the unit + unmodified engine + eventual global
consistency**, which is the word-processor architecture (and BaKoMa's unpublished
promise) realized on stock LuaTeX. Its honest costs, per the preprint itself:
temporary inconsistency of non-viewed pages, and fast-path boundaries at footnotes,
`\ref`/`\thepage`, and externally-set `\parshape` — precisely where kernel machinery
like ltmarks/inserts and packages like monoref and lua-widow-control become the
relevant toolbox.

---

## Downloaded sources (this session)

| File | What |
|---|---|
| `sources/lode-realtime.pdf` | The preprint (pre-existing) |
| `sources/luatex-benchmark/` | Clone of github.com/texlode/luatex-benchmark |
| `sources/monoref.pdf` | monoref v1.3 package documentation (CTAN) |
| `sources/tb137verna-realtime.pdf` | Verna, ETAP, TUGboat 44:2 |
| `sources/tb137ruckert-hint23.pdf` | Ruckert, HINT 2023, TUGboat 44:2 |
| `sources/tb137davies-visual.pdf` | Davies, Overleaf visual editor, TUGboat 44:2 |

**Not fetchable** (404 at research time): the preprint's TeXpresso TUGboat citation
`tug.org/TUGboat/tb44-2/tb138bour-texpresso.pdf` (article appears not to exist — see
§1), and Haug's thesis PDF at `user.tu-berlin.de/mhaug/…` (link rot; DOI
10.13140/RG.2.2.15606.88642 via ResearchGate is the surviving copy).
