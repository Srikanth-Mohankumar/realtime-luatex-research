# Preamble profile — why the production template costs ~13 s

Motivation: on the ACS production article (AM_4c13605), the full compile is
~18 s of which the **preamble alone is 13 s** (measured with a
preamble-only document). The persistent convergence engine hides this from
the editing loop (prototype/README), but the preamble cost hits every
compile in the production pipeline, so it is worth knowing where it goes.

Method: `strace -ttt -e trace=openat` over a preamble-only compile
(`lualatex preamble-only.tex`), attributing the gap before each file open
to the previously opened path. 3597 file opens, 14.9 s traced
(2026-07-22, warm OS caches, warm luaotfload caches).

## Where the time goes

| Time | Attributed to | Interpretation |
|------|---------------|----------------|
| 5.7 s | `/app/simba/typesetmodels/acs-fla` | CPU-bound processing right after the typesetmodel opens (no file I/O). `acs-fla-config.sty` is 4,698 lines; the burn is inside it or what it triggers (font family setup?). **Biggest single target — needs internal profiling by the template team.** |
| 1.18 s | `texmf-dist/.../stix2-otf/STIXTwoMath-Regular.otf` | Math font load — from **TeX Live's copy** |
| 1.09 s | `/app/simba/common/fonts/STIXTwoMath-Regular.otf` | Math font load — **again, from the template's own copy**. The same font is parsed twice via two paths (~2.3 s combined). Deduplicating to one path ≈ 1 s saved. |
| 0.50 s | `stixtwomath-regular.lua`/`.luc` caches | Loading luaotfload's cached tables for the math font (inherent; math fonts are huge) |
| 0.32 s | `arialuni.luc` | Arial Unicode cache load — is this font actually needed for every article? |
| 0.30 s | `lualatex.fmt` | Engine format load (fixed cost) |
| ~1.5 s | `.` (cwd) + long tail | Hundreds of small package/module opens |

## Recommendations (for the template team)

1. **Profile inside `acs-fla-config.sty`** — 5.7 s of pure CPU during its
   processing is the dominant cost. Bisect with `\directlua`
   `os.gettimeofday()` timestamps between its sections.
2. **Stop loading STIXTwoMath twice** — it is read both from TeX Live and
   from `/app/simba/common/fonts`. One consistent path saves ~1 s.
3. **Audit font eagerness** — Arial Unicode + every TnqAP/Myriad face load
   up front; faces not used by a given article could be declared lazily.
4. Even halving the preamble helps every production compile; for the
   real-time editor specifically it also halves the engine-pool prewarm
   time (currently the only path where a user can be made to wait).

Trace and probe files: generated under `prototype/testdocs/` (gitignored);
method reproducible with the strace one-liner above.
