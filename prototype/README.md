# Prototype — real-time per-paragraph recompilation, tested on journal templates

Working implementation of blueprint steps 1, 2 and 5
([notes/06](../notes/06-architecture-blueprint.md)), tested against four
production journal classes from TeX Live 2025: **elsarticle** (Elsevier 5p
two-column), **IEEEtran** (journal), **acmart** (sigconf, OpenType Libertine
via fontspec), and **cas-dc** (Elsevier CAS double-column).

## Architecture

```
                 full compile (background convergence)
 sample.tex ──► lualatex + rtcapture.sty ──► sample-capture.json
                │  pre/post_linebreak_filter    per paragraph:
                │  + insert_local_par           - layout context (hsize, font,
                │  + \RTpara{id} attributes       skips, penalties, language,
                ▼                                 hang/parshape, indent)
             sample.aux                         - reference line signature
                │                                 (per-line glyph chars + x)
                ▼
 server.tex (persistent lualatex, same preamble)
    │  RTLOADAUX: \r@/\b@ macros from aux  ◄── converged \ref/\cite state
    │  font preload from capture
    ▼
 request {text, ctx} ──► \setbox0=\vbox{<ctx prologue> text\par}
                          └─► signature of box 0 ──► JSON response
                              (identical walk to capture: signature.lua)

 rtclient.py: matches source paragraphs to captures (attribute id +
 fingerprint), diffs fast-path signatures against references, measures
 round-trip latency.
```

- `engine/capture.lua` + `engine/rtcapture.sty` — context capture (step 1)
- `engine/signature.lua` — canonical line signature, shared by both sides
- `engine/serve2.lua` — persistent server with context injection (step 2)
- `client/rtclient.py` — fidelity + latency driver
- `templates/*/` — per-class preamble/frontmatter/sample/server; common
  marked body in `templates/body-shared.tex`
- `results/*.json` — per-template results

Run: `python3 client/rtclient.py templates/<name>` (compiles the sample
itself; needs TeX Live 2025 with the class installed).

## Interactive demo

```
cd prototype/demo && python3 server.py      # then open http://localhost:8123
```

`demo/server.py` (stdlib only) bridges a browser page to the persistent
engines — one lualatex process per template, spawned on first use. The page
shows the article source on the left (editable per paragraph) and the **real
document pages** on the right: full page display lists captured at shipout
(`pre_shipout_filter` walk in capture.lua) — title block, abstract,
two-column body, floats, footnotes, rules — every glyph at its absolute
page position, keyed by paragraph attribute.

Both halves of the architecture run live:

- **Fast path** — every keystroke round-trips through real LuaTeX (~1–3 ms):
  the edited paragraph is re-broken with its captured context and *overlaid*
  (dark red) at its cached page origin.
- **Background convergence** — after 1.5 s idle, the bridge substitutes all
  edits into the body, runs a real full compile (`sample-live.tex`), and
  serves the fresh page cache; the client polls a revision counter and
  re-renders — page breaks, column balance, and positions converge in ~2 s,
  and the overlay merges back into the page.

Honesty note: glyph *positions* are engine-exact scaled points; glyph
*outlines* use the browser's serif font as a stand-in (pixel-true outlines
would need the actual font files shipped to the page à la texlode's
opentype.js renderer — Type1 `.pfb` fonts used by these classes can't be
loaded by opentype.js directly). Mid-edit input is survivable: the bridge
strips comments, balances `$`/braces, and a 10 s watchdog respawns a wedged
engine (⟳ button forces it).

## Results (2026-07-21, LuaHBTeX 1.22.0, TeX Live 2025, Linux)

12 marked body paragraphs per template; fidelity = fast-path recompile
reproduces the full compile's line breaks and glyph positions **to 0 scaled
points** (signature: per-line glyph chars + x offsets + line w/h/d).

| Template   | Exact | Full compile | Fast path median rt | Speedup | Boundary cases |
|------------|-------|--------------|---------------------|---------|----------------|
| elsarticle | 10/12 | 0.81 s       | ~1.6 ms             | ~500×   | footnote, display eq |
| IEEEtran   | 10/12 | 0.90 s       | ~3.3 ms             | ~270×   | footnote, display eq |
| acmart     | 10/12 | 4.97 s       | ~3.0 ms             | ~1650×  | footnote, display eq |
| cas-dc     | 10/12 | 1.25 s       | ~3.1 ms             | ~400×   | footnote, display eq |

Server startup (preamble, paid once): 0.6 s (elsarticle/IEEEtran/cas-dc) to
2.6 s (acmart). Font preload from the capture: 8–23 ms. Edit-simulation
latency (perturbed text): 1.7–3.6 ms median.

Every EXACT paragraph includes full microtype (protrusion/expansion),
two-column measures, resolved `\ref`/`\cite` from the aux, hyphenation, and
in acmart's case OpenType fonts — all at **max delta 0 sp**.

The two non-exact paragraphs per template are the boundaries predicted in
notes/02 §7 and the paper §4 — they are *detectable* (ins nodes in the list;
display-math partial-paragraph structure) and would be routed to the
convergence path by a real editor:

- **Footnote paragraph**: the recompiled box carries the footnote insert
  content and a wrong footnote number (global counter) — insert coupling.
- **Display-math paragraph**: the full compile breaks it as separate partial
  paragraphs around the display (`\prevgraf`-indexed), so the reference
  signature has a different line structure than one isolated vbox.

## What we had to solve (and a real editor would too)

1. **Font at paragraph START, not end** — `pre_linebreak_filter` sees the
   end-of-paragraph state; capture the start font in `insert_local_par`
   (stack for nesting).
2. **Context is ~25 parameters, not 3** — hsize/indent/hangindent/parshape/
   leftskip/rightskip/parfillskip/spaceskip/tolerance/emergencystretch/
   demerits/language/lhmin/rhmin/adjustspacing/protrudechars/looseness…
   All injectable as a prologue inside the vbox group. LuaTeX glue orders
   run 0–4 (`fi` at 1), which matters when re-emitting captured glue.
3. **Aux state must bypass LaTeX** — `\newlabel`/`\bibcite` refuse to run
   mid-document under the 2025 kernel/natbib ("can be used only in
   preamble"); the server defines `\r@<key>`/`\b@<key>` directly via
   `token.set_macro`. With that, `\ref`/`\cite` paragraphs are byte-exact —
   the "background convergence feeds the fast path" pattern working.
4. **Font identity across sessions** — fonts are matched by (name, size)
   against the server's loaded fonts, preloaded once from the capture set;
   `\setfontid` selects them per request (works for TFM and fontspec/OTF).
5. **IPC framing** — TeX leaves unterminated partial lines on stdout, so
   response JSON can be glued to chatter; the client must search inside the
   line, not anchor at start. (Cost a debugging session: the same bytes
   through a shell pipe *looked* fine because grep matches anywhere.)

## Honest limitations (next steps if this becomes real)

- Context capture keys paragraphs by planted `\RTpara` markers; a real
  editor derives ids from its document model instead.
- One environment class untested per request: paragraphs inside lists
  (hangindent/leftskip are captured and injected, but item *labels* are
  generated by `\item`, not the paragraph source).
- No error recovery for syntactically broken mid-edit input (unbalanced
  braces would wedge the vbox); a real server needs input validation +
  a rollback protocol.
- Global state can leak between requests (`\global` assignments in body
  text); TeXpresso-style checkpointing is the airtight fix.
- The fast path returns signatures; a real renderer consumes the display
  list of experiments/03-04 instead (same walk, richer payload).
