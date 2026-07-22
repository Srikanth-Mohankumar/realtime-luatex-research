# Production flow — working branch

Branch for integrating the real-time pagination system into the TNQ
production pipeline. The standard demo (main branch) stays frozen on the
IEEEtran sample article; everything production-specific lands here.

## Already proven on production content (see notes/, prototype/README)

- \paraid / \tagStructPara wrapper convention is the paragraph key
  end-to-end (capture attribute, fast path, page cache, overlays)
- Customer templates work unmodified: /app/simba (ACS) and
  /app/lamput/elsevier validated; rtcapture chains the production \paraid
- satellite.json is read at body time -> watcher-driven repagination works
- Latency on production articles: fast path ~10-20 ms; repagination ~3 s
  (10 pp) to ~10 s (16 pp); true PDF a few seconds behind
- Memory: ~2.4-3 GB per open article (3 resident engines); knobs
  RT_POOL / RT_SESSIONS / RT_IDLE_MIN

## Open items awaiting input

- [ ] Which pipeline stage hosts the editor (watcher? correction UI?)
      and what triggers session open/close
- [ ] Paragraph-id contract: are \paraid values stable across correction
      cycles? who assigns ids for inserted paragraphs?
- [ ] satellite.json write-back: should editor edits update satellite
      (float moves) or is that upstream-only?
- [ ] Boundary handling policy: footnote/display-math/list paragraphs —
      route to repagination silently, or flag to the operator?
- [ ] Multi-user: one article per operator, or concurrent editing?
- [ ] Deployment shape: per-operator local engines vs shared server;
      resource budget per seat
- [ ] Template team follow-ups from notes/07-preamble-profile.md
      (5.7 s in acs-fla config; STIXTwoMath double load)

## Known boundaries to engineer through

- list-item paragraphs (labels come from the environment)
- abstract/frontmatter paragraphs (not isolated-compilable)
- galley re-cut for sub-second repagination (research milestone)
