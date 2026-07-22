# Phase 2 foundation validated: pgcid pre-staging gives stable paragraph identity

Experiment (2026-07-22) on the real converter (`xml2tex2.0`, sage customer,
article CCH1434288, 46 paragraphs), inserting one new `<p>` mid-document:

| Scenario | Pre-existing paraids that shifted |
|---|---|
| Today (no pgcid in source XML) | **39 / 46** — positional renumbering |
| pgcid pre-staged on every `<p>` | **0 / 46** — all identities preserved |

Details:
- The inserted (unlabeled) paragraph was topped-up as `para48`
  (max existing + 1) by `paragraphNumberingProcessor`'s idempotent pass —
  exactly the behavior predicted by the code survey.
- Pre-staging changes nothing else: the staged conversion's paragraph
  texts are identical to the unstaged baseline (ids aside).
- Zero converter changes required on the 2.0 path.

Consequence for Phase 2 (live editing): stamping `pgcid` into the XML once
at document load — frontend or a backend load-time pass — makes `\paraid`
a stable key across all edit cycles, which is the contract the real-time
fast path, page cache, and overlays are keyed on. The remaining owner
decision is only WHERE the stamping lives, not whether it works.

Reproduction: the experiment script pattern lives in the session history;
essence: stamp `pgcid="paraN"` on `<p>` in document order, insert a new
unlabeled `<p>`, convert both variants, map `\paraid{...}` to following
text, diff.
