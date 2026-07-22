# Phase 3 — Template-keyed engines: the scalability answer

**Status: NEXT TARGET (not started).** Recorded 2026-07-22 after review
feedback. Pick up here.

## Why this phase exists

Review pushback from engineering leads/architects on Phases 1–2d:
*"~2.5 GB warm state per article is not scalable — many users may come, we
cannot cache every article."*

The objection conflates two things (see rebuttal below), but its kernel is
real: **the 2–3 GB is mostly TEMPLATE state (fonts, packages, tagging
machinery), not article state — and today we duplicate it per article.**
Phase 3 removes that duplication so warm memory scales with *distinct
templates in active use* (single digits per customer), not with articles or
users.

## The rebuttal, for the record (measured 2026-07-22 on the dev stack)

- Warm state is a **working set of actively-edited articles**, not a cache of
  the corpus: an operator edits one article at a time, LRU caps + 15-min idle
  reapers return memory when typing stops. Observed: idle system ≈ **0.1 GB**
  resident (xml2tex worker only; all engines reaped).
- Durable per-article state is **3–9 MB of disk** (workspace + capture), not
  2.5 GB. The RAM is processes, and processes embody template machinery.
- Overflow past the caps = today's exact behavior (~20 s cold build) while an
  engine warms behind the operator. Warm capacity is a QoS tier, never a
  correctness requirement.
- Capacity math: memory = concurrent *active editors* × warm-set cost.
  64 GB host ≈ ~20 simultaneously-typing operators today — before Phase 3.

## The levers, in build order

### Lever 1 — Template-keyed SERVE engines  ← START HERE
Biggest win / effort ratio. Kills the "per-article × many users"
multiplication for previews entirely.

Design sketch:
- Serve engines currently keyed `customer/JIDaid`; re-key by
  **preamble hash** (same `preamble_key` machinery as the build pool, minus
  the article component).
- Everything the `\vbox` needs is already injected per request (31 ctx params
  + font). The one per-article piece is the **aux transplant**
  (`\newlabel`/`\bibcite` via `token.set_macro` at spawn). Move it
  per-request: resolve the paragraph's `\ref`/`\cite` keys server-side (from
  the article's converged aux in its workspace) and inject the needed
  `r@...`/`b@...` macros in the request prologue, then reset them after the
  box (token.set_macro again — attributes-style hygiene; beware stickiness).
- Registry: `serve_engines[preamble_hash]`; per-article `serve_fonts` stays
  keyed by article (font ids are per-engine — after re-keying, per
  (engine, id); revisit `preview-font` resolution accordingly).
- Eviction/reaper/caps logic carries over; expect FAR fewer engines.

Acceptance:
- preview-smoke battery 14/14 against TWO articles of the same journal
  served by ONE engine (extend the battery: second dataset copy).
- `\ref`/`\cite` paragraph from article A then article B in sequence —
  correct resolution both ways (no bleed).
- Memory: two-article steady state shows one serve engine, not two.

### Lever 2 — Template-keyed BUILD engines
- Preamble key already hashes preamble; drop the article component from the
  pool key so a warm engine is interchangeable between articles whose
  generated preambles are byte-identical (verify how often that holds per
  journal — measure first: hash preambles across a journal's articles).
- Care: engine cwd is fixed at spawn (workspace-bound). Options: spawn
  engines in a template-neutral dir with TEXINPUTS pointing at the article
  workspace at RUN time, or accept chdir-at-RUN via Lua `lfs.chdir` before
  body (test openout/openin behavior!). Assets (images) resolve relative —
  this is the risky bit; golden gate must pass per journal.
- Payoff: build memory scales with templates-in-use; prewarm-at-open becomes
  nearly free when the template is already warm from another article.

### Lever 3 — Preamble slimming (template team handoff)
- notes/07 profiling: acs-fla typesetmodel spends 5.7 s CPU; STIX Two Math
  parsed TWICE from two paths (~2.3 s + RAM). File a ticket with the
  template team; free win for cold path too.

### Lever 4 — (research) CRIU checkpoint/restore
- Format dump (`\dump`) FAILS under luaotfload (66 MB fmt, no output —
  already tried, do not retry). Process-level checkpoint of a template-warm
  engine → 2.5 GB disk, sub-second restore. Ops-heavy (container privileges);
  only if Levers 1–2 are insufficient.

### Lever 5 — Warm capacity as a SaaS tier
- `WARM_ENGINES` is already per-environment; per-customer-plan warm quotas
  are a product decision, not engineering. Mention only if asked.

## Pick-up checklist (day 1 of resuming)

1. Re-read this doc + engineering guide §8 (serve engine) and §15 gotchas
   4/11/12 (openout paths, attribute stickiness, profile comparison).
2. Measure: preamble-hash collision rate across one journal's articles
   (are same-journal preambles byte-identical after xml2tex?) — decides how
   much Lever 2 pays.
3. Implement Lever 1 on a new branch off `feature/warm-pipeline`
   (`feature/template-keyed-serve`).
4. Extend `scripts/preview-smoke.py` to two same-journal datasets, one engine.
5. Then decide Lever 2 based on the step-2 measurement.

## Where everything is

- Code: `/data/neopage/repos/mr-bean` branch `feature/warm-pipeline`
  (13 local commits, user pushes to git.tnq.co.in).
- Serve engine: `backend/app/warm/serve-mrbean.lua` + `serve_engine.py`;
  registry in `compiler.py` (`WarmRuntime.serve_engines`,
  `get_serve_engine`, `_serve_spawn_locks`, `serve_fonts`).
- Reference docs: `docs-production/warm-pipeline-engineering-guide.md`
  (§8, §11, §16), stakeholder brief, both papers in `paper/`.
