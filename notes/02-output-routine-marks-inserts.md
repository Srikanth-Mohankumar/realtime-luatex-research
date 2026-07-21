# The Output Routine, Marks, and Inserts: What Cannot Be Recompiled Paragraph-Locally

Research notes for the texlode study (TUG2026 preprint "Real-Time LuaTeX: Recompiling
Large Documents in 1ms"). The paper's fast path recompiles a single paragraph in ~1ms
and lets a background full compile converge page breaking. This note documents *why*
the page-level machinery — the page builder, the output routine, inserts (footnotes),
marks (running heads), and floats — is inherently document-global, and therefore what
the fast path must exclude.

Primary sources used:

- `tex.web` semantics as documented in the TeXbook (ch. 15, "How TeX Makes Lines into
  Pages"; appendix D "Dirty Tricks") and Eijkhout, *TeX by Topic* (chapters on the
  page builder, output routines, inserts, and marks; installed at
  `/usr/local/texlive/2025/texmf-dist/doc/plain/texbytopic/TeXbyTopic.pdf`).
- The LaTeX kernel as shipped in TeX Live 2025:
  `/usr/local/texlive/2025/texmf-dist/tex/latex/base/latex.ltx`
  (LaTeX2e `<2025-06-01>` patch level 1). Line numbers below refer to this file.
  Documented sources: `ltoutput.dtx`, `ltmarks.dtx`, `ltfloat.dtx`, `ltspace.dtx`
  in `/usr/local/texlive/2025/texmf-dist/source/latex/base/` (or `texdoc source2e`).

---

## 1. The page builder

### 1.1 Contributions list vs. current page

TeX's main vertical list (MVL) is physically two lists:

- the **current page**: material already committed to the page being built, with
  running totals (`\pagetotal` etc.) and a record of the best break seen so far;
- the **recent contributions**: material appended to the MVL that the page builder
  has not yet examined.

The **page builder** is the routine (`build_page` in `tex.web`) that moves nodes
one at a time from the contributions list to the current page. It is *not* run
continuously; it is "exercised" at specific moments when TeX is in outer vertical
mode:

- after a paragraph is finished and its lines (plus migrated material, §4.2) are
  appended to the MVL;
- after a `\box`/`\hbox`/`\vbox`/rule/`\halign` result is appended in vertical mode;
- when a penalty, glue, kern, `\mark`, `\insert`, or whatsit is appended in outer
  vertical mode;
- at the end of a display math formula;
- after `\output` finishes and returns leftover material;
- at `\end`.

Between these moments, material can pile up in the contributions list. Inside boxes
the page builder never runs — this is why nothing inside a `\vbox` can cause a page
break.

### 1.2 The page-so-far registers

While moving material, TeX maintains (all read-only except `\pagegoal`, which an
output routine may adjust):

| Register       | Meaning |
|----------------|---------|
| `\pagegoal`    | target height of the current page (= `\vsize` frozen at first-box time, minus insert corrections) |
| `\pagetotal`   | accumulated natural height of the current page |
| `\pagestretch` | accumulated finite stretch; `\pagefilstretch`, `\pagefillstretch`, `\pagefilllstretch` track infinite orders |
| `\pageshrink`  | accumulated shrink |
| `\pagedepth`   | depth of the last box contributed, truncated to `\maxdepth` (excess depth is converted to height) |

### 1.3 `\vsize` is sampled at first-box time

While the current page is empty, `\pagegoal = \maxdimen` and glue/penalties/kerns
that would come before the first box are discarded. The moment the **first box or
rule** of a page is contributed:

1. `\topskip` glue is inserted above it (reduced by the box's height, min 0), so the
   first baseline sits `\topskip` below the top of type area;
2. `\pagegoal` is set to the *current* value of `\vsize`.

Changing `\vsize` mid-page therefore has **no effect on the current page**; only the
next page sees it. To change the goal of the page in progress you must assign to
`\pagegoal` directly (or, in LaTeX, use `\enlargethispage`, which fiddles with the
`\@kludgeins` insert — see §3.5). LaTeX exploits the first-box sampling in its output
routine: after handling floats it does `\global\vsize\@colroom` (latex.ltx:20313,
20316) so the *next* column's goal reflects space stolen by floats.

Each `\insert` also *lowers* `\pagegoal` (§3.2) — the page goal is a moving target
that depends on every footnote contributed so far.

### 1.4 The page-break cost formula

A legal page breakpoint is (same as line breaking): a penalty `p < 10000`, glue
preceded by non-discardable material, or a kern followed by glue. At each legal
breakpoint the page builder computes a badness `b` from the page-so-far totals
against `\pagegoal`:

- if `\pagetotal < \pagegoal`: `b = 0` when infinite stretch is present, else
  `b = badness(\pagegoal − \pagetotal, \pagestretch)`;
- if `\pagetotal − \pagegoal > \pageshrink`: `b = awful_bad` (2³⁰−1, "overfull");
- otherwise `b = badness(\pagetotal − \pagegoal, \pageshrink)`.

Then, with `q = \insertpenalties` (accumulated floating penalties of split/held-over
insertions, §3.4), the cost is (tex.web §1005; TeXbook p. 124):

```
c = p                if p ≤ −10000            (forced break)
c = b + p + q        if b < 10000
c = 100000           if 10000 ≤ b < awful_bad ("deplorable")
c = awful_bad        if b = awful_bad
```

(and `c = awful_bad` whenever `\insertpenalties ≥ 10000`). The page builder
remembers the breakpoint with the smallest `c` seen so far (`least_page_cost`,
ties broken by *last* smallest). It keeps contributing material until either

- `c = awful_bad` (the page has become overfull), or
- `p ≤ −10000` (a forced break),

at which point it **fires up the output routine**, breaking at the *best remembered
breakpoint*, not necessarily the current one. Note the greedy, first-fit character:
unlike line breaking there is no global optimization over page breaks — each page is
broken at the locally best point. (LuaTeX keeps this model; only paragraphs are
globally optimized.)

### 1.5 Firing `\output`: `\box255` and `\outputpenalty`

When the page builder fires up (tex.web `fire_up`):

1. Material from the top of the current page up to the chosen break is packed into
   **`\box255`** (as an unset vbox of height `\pagegoal`... more precisely, `\vsize`
   for the vpack target is the remembered `best_size = \pagegoal`); material after
   the break stays in / returns to the contributions list.
2. If the break is at a penalty node, its value is placed in **`\outputpenalty`** and
   the node is removed; otherwise `\outputpenalty = 10000`. This is how "signal
   penalties" (§2.1, §6) communicate with the output routine.
3. `\topmark`/`\firstmark`/`\botmark` are updated from the mark nodes in `\box255`
   (§4).
4. Unless `\holdinginserts > 0`, insertion nodes in the page are **removed and their
   contents moved into the corresponding `\box n` registers** (splitting if needed,
   §3.4). With `\holdinginserts=1` they stay in `\box255` untouched.
5. `\deadcycles` is incremented (reset to 0 by `\shipout`); if it exceeds
   `\maxdeadcycles` (default 25) TeX complains — the guard against output routines
   that never ship anything.
6. The token list `\output` is executed *as a group in internal vertical mode*. The
   routine must make `\box255` void (ship it, unbox it, or store it elsewhere);
   leftover material on the routine's vertical list is prepended back onto the
   contributions list, and the page builder runs again.

The default `\output` is `\shipout\box255`. Everything LaTeX does with headers,
footers, footnotes, floats, and columns is layered on top of this via its own
`\output` token list.

---

## 2. LaTeX's output routine (`ltoutput.dtx`)

### 2.1 The dispatcher

LaTeX's `\output` (latex.ltx:20294–20321) dispatches on `\outputpenalty`:

```tex
\output {%
  \let \par \@@par
  \ifnum \outputpenalty<-\@M          % < -10000: a signal penalty
    \@specialoutput
  \else                               % a real page break
    \@makecol
    \@opcol
    \@startcolumn
    \@whilesw \if@fcolmade \fi {\@opcol\@startcolumn}%   % emit pending float pages
  \fi
  \ifnum \outputpenalty>-\@Miv        % normal case: restore \vsize from float room
    ...\global \vsize \@colroom ...
  \else
    \global \vsize \maxdimen          % \@Miv signal: gobble everything next round
  \fi
}
```

In LaTeX `\@cclv` *is* box 255 (`\chardef`'d to 255; the name is Roman-numeral CCLV)
and `\@outputbox` is the box in which the finished column/page body is assembled.
The signal penalties (`\@M`=10000, `\@Mi`=10001, `\@Mii`=10002, `\@Miii`=10003,
`\@Miv`=10004, `\@MM`=20000):

| `\outputpenalty` | emitted by | meaning |
|---|---|---|
| `> −10000` or `= −10000` | natural break, `\newpage` (`\vfil\penalty-\@M`, latex.ltx:20238) | real column break |
| `−\@Mi` (−10001) | `\clearpage` (`\vbox{}\penalty-\@Mi`, latex.ltx:20200–20212) | flush deferred floats (`\@doclearpage`) |
| `−\@Mii` (−10002) | marginpar signal (`\@floatpenalty-\@Mii`, latex.ltx:17273) | process a marginpar |
| `−\@Miii` (−10003) | float signal (`\@floatpenalty-\@Miii`, latex.ltx:17275) | process a float just encountered |
| `−\@Miv` (−10004) | `\vadjust{\penalty-\@Miv ...}` for floats/marginpars inside paragraphs (latex.ltx:17350) | reopen page, collect penalized item |

`\@specialoutput` (latex.ltx:20322–20368) handles the float/marginpar cases: it
unboxes `\@cclv` back into a holding box `\@holdpg`, reinserts held footnotes with
`\@reinserts`, decides float placement (`\@addtocurcol`/`\@addmarginpar`), and
returns everything to the MVL — i.e. the page is *reopened* and page building
continues. This "fire output, inspect, put it all back" pattern is the standard
LaTeX idiom and is exactly why insertions need re-inserting (§3.3):

```tex
\gdef \@reinserts{%
  \ifvoid\footins\else\insert\footins{\unvbox\footins}\fi
  \ifvbox\@kludgeins\insert\@kludgeins{\unvbox\@kludgeins}\fi
}                                                    % latex.ltx:20642
```

### 2.2 `\@makecol`: assembling one column

`\@makecol` (latex.ltx:20426–20442) turns `\box255` + footnotes + column floats into
`\@outputbox`:

```tex
\def \@makecol {%
  \UseHook {build/column/before}%
  \setbox\@outputbox \box\@cclv          % grab box 255
  \@outputbox@removebskip
  \UseTaggingSocket{build/column/outputbox}%
  \let\@elt\relax
  \xdef\@freelist{\@freelist\@midlist}%  % recycle in-text float registers
  \global \let \@midlist \@empty
  \UseSocket {build/column/outputbox}%   % footnotes + floats, order pluggable
  \ifvbox\@kludgeins
     \@make@specialcolbox               % \enlargethispage in effect
  \else
     \@make@normalcolbox                % \vbox to \@colht {...\@textbottom}
  \fi
  \global \maxdepth \@maxdepth
  \UseHook {build/column/after}%
}
```

Since the 2023 kernel this is socket-based; the default plug
`footnotes-floats-legacy` (latex.ltx:20617–20631) reproduces the classic order:
reinsert bottom glue, then append footnotes, then attach floats. Footnote
attachment (latex.ltx:20521–20537):

```tex
\def\@outputbox@appendfootnotes {%
   \ifvoid\footins \else
     \@makecol@handlesplitfootnotes
     \@outputbox@append{%
       \vskip \skip\footins             % \skip\footins glue above footnotes
       \footnoterule                    % the rule
       \unvbox \footins                 % the accumulated footnote material
      }%
  \fi}
```

Top floats are prepended and bottom floats appended by
`\@outputbox@attachfloats` → `\@cflt`/`\@cflb` (latex.ltx:20753–20783), which walk
`\@toplist`/`\@botlist` (LaTeX's own float queues, §3.5) inserting `\floatsep` /
`\textfloatsep` glue. Finally `\@make@normalcolbox` (latex.ltx:20444) packs to the
column height:

```tex
\setbox\@outputbox \vbox to\@colht {%
    \@texttop \unvbox\@outputbox \vskip-\@outputbox@depth \@textbottom}%
```

`\@colht` is `\textheight` minus space already reserved for double-column floats;
`\@textbottom` is `\vfil`-like glue under `\raggedbottom`.

### 2.3 `\@outputpage`: page assembly and shipout

`\@opcol` (latex.ltx:20415–20425) routes to `\@outputdblcol` (two-column) or
`\@outputpage`, after updating the new mark structures
(`\@expl@@@mark@update@dblcol@structures@@` / `...singlecol...`, §4.4).

`\@outputpage` (latex.ltx:20666–20746) builds the physical page: it sanitizes
catcodes and `\protect` (deferred `\write`s in the page get expanded at shipout
time!), selects odd/even header/footer, then

```tex
\shipout \vbox{%
    ...
    \vskip \topmargin
    \moveright\@themargin \vbox {%
      \setbox\@tempboxa \vbox to\headheight {...\hb@xt@\textwidth{\@thehead}...}%
      \dp \@tempboxa \z@
      \box \@tempboxa
      \vskip \headsep
      \box \@outputbox                  % the assembled column(s)
      \baselineskip \footskip
      ...\hb@xt@\textwidth{\@thefoot}...}}
  \global \@colht \textheight
  \stepcounter{page}%
```

Note `\stepcounter{page}` happens here — the page number is only known at shipout,
which is precisely why `\pageref` needs the `.aux` round-trip.

### 2.4 Two-column: `\@outputdblcol`

Two-column mode is a state machine over `\if@firstcolumn` (latex.ltx:21288–21309):

```tex
\def\@outputdblcol{%
  \if@firstcolumn
    \global\@firstcolumnfalse
    \global\setbox\@leftcolumn\copy\@outputbox   % stash column 1, ship nothing
  \else
    \global\@firstcolumntrue
    \setbox\@outputbox\vbox{%
     \hb@xt@\textwidth{%
        \hb@xt@\columnwidth{\box\@leftcolumn \hss}%
        \hfil {\normalcolor\vrule \@width\columnseprule}\hfil
        \hb@xt@\columnwidth{\box\@outputbox \hss}}}%
    \@combinedblfloats                           % add \dbltoplist floats
    \@outputpage
    ...}}
```

The first firing of `\output` for a page produces the left column and **stashes it
in `\@leftcolumn`** (a box register allocated at latex.ltx:20196); no shipout
happens. The second firing pastes both columns side by side and calls
`\@outputpage`. `\@makecol` is used identically for each column — footnotes from
`\footins` land at the bottom of *their own column*. Consequences: (a) the last
page's left column can hang around unshipped until `\clearpage` balances things,
(b) marks read at shipout span *both* columns' material, which the legacy mark
mechanism gets wrong and `ltmarks` fixes (§4.4).

---

## 3. Inserts

### 3.1 `\newinsert` and the four-register convention

An insertion **class** is a number `n` (0–254 in classic TeX; class 255 is
reserved for the page). Class `n` is governed by four like-numbered registers:

| Register    | Role |
|-------------|------|
| `\box n`    | receives the insertion material when the output routine fires |
| `\count n`  | magnification factor `f`: 1000 means page cost = size (e.g. 500 for `\twocolumn` footnotes: two columns of footnote per unit of page height) |
| `\dimen n`  | maximum total size of class-`n` insertions per page |
| `\skip n`   | extra glue added to the page the first time class `n` appears on it (footnote-rule allowance) |

`\newinsert` (latex.ltx:452–476) allocates a class number counting *down* from 254
(`\insc@unt`) and checks that count/dimen/skip/box registers with that number are
all still free; the LaTeX kernel additionally spills into the extended e-TeX pool
via `\extrafloats` (latex.ltx:431–446) when the classic pool is exhausted —
float box registers and insert classes are drawn from the same allocator.

`\footins` in LaTeX (latex.ltx:17466–17469):

```tex
\newinsert\footins
\skip\footins=\bigskipamount % space added when footnote is present
\count\footins=1000          % footnote magnification factor (1 to 1)
\dimen\footins=8in           % maximum footnotes per page
```

Other kernel inserts: `\@mpfootins` (minipage footnotes, latex.ltx:16180) and
`\@kludgeins` (`\enlargethispage`, latex.ltx:21134).

### 3.2 What `\insert n {...}` does to the page

`\insert n {⟨vertical material⟩}` creates an insertion node carrying the material
*plus a snapshot of* `\splittopskip`, `\splitmaxdepth`, and `\floatingpenalty` as
they were when the insert was created. In horizontal mode the node **migrates** to
the enclosing vertical list after line breaking (attached after the line containing
it). When the page builder contributes an insertion node of class `n`:

- if this is the first class-`n` insert on the page, `\pagegoal` is decreased by the
  width of `\skip n` (its stretch/shrink joins the page totals);
- `\pagegoal` is decreased by `h × f/1000` where `h` is the insert's height and
  `f = \count n` — the material "costs" page room without being on the page yet;
- if adding it would exceed `\dimen n` or overfill the page, TeX **splits** the
  insertion (§3.4).

This is why LaTeX's `\@footnotetext` (latex.ltx:17495–17509) sets the split
parameters *inside* the insert:

```tex
\long\def\@footnotetext#1{\insert\footins{%
    \reset@font\footnotesize
    \interlinepenalty\interfootnotelinepenalty
    \splittopskip\footnotesep
    \splitmaxdepth \dp\strutbox \floatingpenalty \@MM
    \hsize\columnwidth \@parboxrestore
    ...
    \@makefntext{...#1...}\par ...}}
```

At output time (unless held, §3.3) all class-`n` material that fits is moved into
`\box n`; LaTeX then unboxes `\box\footins` under the `\footnoterule` in
`\@makecol` (§2.2).

### 3.3 `\holdinginserts` and page re-inspection

`\holdinginserts` (a TeX 3 addition made *for* multi-pass output routines): when
positive, `fire_up` leaves insertion nodes inside `\box255` instead of distributing
them into their box registers. An output routine that wants to *look at* the page
and then put it back (`\unvbox\@cclv`, re-contribute, let the page builder try
again) needs this, because otherwise:

- the insertions have already been stripped out and split; putting the page back
  loses the association between a footnote and its reference line;
- re-inserting via `\insert\footins{\unvbox\footins}` (LaTeX's `\@reinserts`,
  multicol's `\reinsert@footnotes`, latex.ltx:20642, multicol.sty:418) mostly works
  but re-runs the splitting logic with the *current* parameters and loses the
  original `\floatingpenalty`/`\splittopskip` snapshots, and a footnote that was
  split once can be split differently or migrate to the wrong column.

Packages that set `\holdinginserts=1` while re-inspecting pages in TeX Live 2025
include `lineno.sty`, REVTeX's `ltxgrid.sty`, `fwlw.sty` (first/last word), and
`tamefloats.sty` (grep over `texmf-dist/tex/latex/`). `multicol` predates reliable
use of it and instead uses the empty-insert trick plus `\vsplit`-based column
balancing; `longtable`'s multi-chunk `\output{...\global\setbox... \unvbox\@cclv}`
routines are the same genre of page re-inspection. The universal rule: **any output
routine that unboxes `\box255` and returns it to the page builder must either hold
inserts or manually re-insert them** — a whole-page, stateful operation.

### 3.4 Insert splitting across pages

If a class-`n` insertion cannot be placed entirely (page full, or `\dimen n`
reached), TeX performs an internal `\vsplit`-style break of the insertion's
vertical list (using the snapshotted `\splittopskip`/`\splitmaxdepth`): the part
that fits goes to `\box n`, the remainder becomes a **held-over insertion** that is
contributed at the top of the *next* page. Each split or held insertion adds its
snapshotted `\floatingpenalty` to `\insertpenalties`, which enters the page cost
formula as `q` (§1.4) — so splitting a footnote makes the breakpoint less
attractive but not forbidden. LaTeX sets `\floatingpenalty\@MM` (20000) for
footnotes: since a single insertion *group* between two breakpoints counts its
floating penalty once, 20000 ≥ 10000 forbids a *second* footnote in the same
paragraph line from beginning to float, while still allowing the split itself.
Inside the output routine, `\insertpenalties` instead reports the *number* of
held-over insertions — LaTeX's `\@doclearpage` and multicol use this to detect
"there are still pending footnotes".

The tail of a split footnote appears on the next page with no footnote mark; the
`\@makecol@handlesplitfootnotes` hook (latex.ltx:20523, default `\@empty`, set by
the `footmisc`/`splitfoot`-style code) exists for packages that decorate split
continuations.

### 3.5 Floats: plain TeX inserts vs. LaTeX's float queue

**Plain TeX** implements `\topinsert`/`\midinsert`/`\pageinsert` directly as
insertion classes (`\topins` with `\count=1000, \dimen=\maxdimen, \skip=0pt`): a
top insert is literally an `\insert\topins{...}` and the engine's insert machinery
does the placement bookkeeping; the output routine just unboxes `\box\topins`
above the page. Elegant, but placement policy is frozen in the engine: an insert
can only go on *this* page (possibly split) or be held over to the *next* — it can
never jump backward, be reordered, or be deferred past several pages by policy.

**LaTeX** deliberately does *not* use the insert mechanism for floats (only for
footnotes and `\enlargethispage`). A float is packed into a box register drawn from
`\@freelist` (registers allocated through the same `\extrafloats` pool,
latex.ltx:421–444), and its existence is signalled to the output routine by a
penalty: `\@xfloat` sets `\@floatpenalty-\@Miii` (floats) or `-\@Mii` (marginpars)
(latex.ltx:17273–17275), and `\end@float` emits `\penalty\@floatpenalty` in
vertical mode or `\vadjust{\penalty-\@Miv\vbox{}\penalty\@floatpenalty}` inside a
paragraph (latex.ltx:17340–17368). The output routine fires on these signals
(`\@specialoutput`, §2.1) and runs LaTeX's own placement algorithm over macro-level
queues:

- `\@currlist` — floats currently being processed;
- `\@toplist`, `\@botlist`, `\@midlist` — floats placed on the current column;
- `\@dbltoplist` — double-column floats for the current page;
- `\@deferlist`, `\@dbldeferlist` — floats postponed to later pages;
- `\@freelist` — recycled box registers.

`\@startcolumn`/`\@tryfcolumn` attempt to build float pages/columns from
`\@deferlist` before ordinary text resumes; `\@doclearpage` (latex.ltx:20375–20414)
flushes everything at `\clearpage` (signal `−\@Mi`). Float placement is thus pure
macro policy (`\topfraction`, `topnumber`, `!` specifiers, `\@fpmin`...), at the
price of the two-place limitation of inserts being replaced by a genuinely
*global* deferral queue: a float encountered on page 12 may be shipped on page 15,
and everything between depends on it.

---

## 4. Marks

### 4.1 `\mark` and the three page marks

`\mark{⟨tokens⟩}` appends a mark node (tokens expanded at mark time) to the current
list. When a page is boxed into `\box255`, TeX sets, for the shipped page:

- `\botmark` — the last mark on the page;
- `\firstmark` — the first mark on the page;
- `\topmark` — the value `\botmark` had at the end of the *previous* page.

If the page contains no marks, all three become equal to the previous `\botmark`.
These are readable only meaningfully inside the output routine (values are frozen
at `fire_up` time). The canonical use is running heads: dictionary-style
`\topmark`–`\botmark` ranges, or LaTeX's `\leftmark` (= "botmark of the left-hand
page", actually first component of `\botmark` at even shipout) and `\rightmark`
(first-mark based).

### 4.2 Migration from horizontal lists

A `\mark` (like `\insert` and `\vadjust`) occurring in a paragraph would be trapped
inside an `\hbox` line where the page builder could never see it. So after line
breaking, these nodes **migrate** out of the horizontal list and are appended to
the enclosing vertical list immediately after the line that contained them.
Migration happens only through *one* level of paragraph packaging: a mark inside an
explicit `\hbox`/`\vbox` (e.g. inside a tabular cell or a float) does **not**
migrate and is invisible to the page builder. This is why LaTeX's sectioning
commands issue `\markboth`/`\markright` at the outer level, and why marks inside
boxes are a classic source of lost running heads.

### 4.3 `\vsplit` marks and mark classes

`\vsplit` (§5) sets `\splitfirstmark` and `\splitbotmark` to the first/last marks
in the split-off fragment — the mechanism multi-column balancers use to recover
column-accurate running heads.

e-TeX generalizes single marks to **classes**: `\marks⟨n⟩{...}`,
`\topmarks⟨n⟩`/`\firstmarks⟨n⟩`/`\botmarks⟨n⟩`/`\splitfirstmarks⟨n⟩`/
`\splitbotmarks⟨n⟩`, with classes 0–32767 (`\marks0` ≡ `\mark`). **LuaTeX** widens
this to 65535 (classes 0–65535) and adds `\clearmarks⟨n⟩`; marks are ordinary
`mark` nodes with a `class` field visible to Lua callbacks. LaTeX allocates classes
with `\newmarks` (latex.ltx:21652):

```tex
\def\newmarks{%
  \e@alloc\marks \e@alloc@chardef{\count256}\m@ne\e@alloc@top}
```

### 4.4 LaTeX 2022/2023+ mark mechanism (`ltmarks.dtx`)

The legacy `\topmark`/`\botmark` model conflates "page" with "invocation of the
output routine", which breaks in two-column mode (the OR fires per *column*) and
gives no way to ask "first mark of this *column*". The new kernel mechanism
(latex.ltx:17641–18116, `ltmarks.dtx`, in the kernel since 2022-06, refined 2023+)
fixes this with **named classes and named regions**:

```tex
\NewMarkClass{⟨class⟩}                    % latex.ltx:18005
\InsertMark{⟨class⟩}{⟨text⟩}
\TopMark  [⟨region⟩]{⟨class⟩}             % expandable; region default: page
\FirstMark[⟨region⟩]{⟨class⟩}
\LastMark [⟨region⟩]{⟨class⟩}
```

Regions: `page`, `previous-page`, `column`, `previous-column`, `first-column`,
`last-column`, and `mcol-1`…`mcol-20` for multicol (initialized per class in
`\__mark_new_class:nn`, latex.ltx:17663–17688). Each class gets a private e-TeX
mark class via `\newmarks` (latex.ltx:17661); mark *values* are token lists
`\g__mark_⟨region⟩_⟨top|first|last⟩_⟨class⟩_tl` maintained by the kernel.

The extraction trick (latex.ltx:17703–17751): instead of relying on the engine's
page-mark snapshots, the OR hooks (`\@expl@@@mark@update@singlecol@structures@@` /
`...dblcol...`, called from `\@opcol`, latex.ltx:20417/20420) unpack `\@outputbox`
into a temp box and **`\vsplit` it to `\maxdimen`** (with `\vbadness`/`\vfuzz`
maxed to silence diagnostics), then read `\splitfirstmarks`/`\splitbotmarks` for
every class (latex.ltx:17758–17781). `top` of the new region is the previous
region's `last`. The dblcol updater (latex.ltx:18052–18093) then *rehomes* column
data into page data: `page/top` comes from `first-column/top`, `page/first` from
the first column unless it had no marks (then from the last column), `page/last`
from `last-column/last` — i.e. page-level marks are correct even though the OR ran
twice. `multicol ≥ v1.9` does the same per balanced column via
`\mark_get_marks_for_reinsertion:nNN` (latex.ltx:17783, multicol.sty:1094–1124),
which extracts marks from each split-off column and re-inserts synthetic
`\InsertMark` nodes so the engine-level state stays consistent.

Each mark value carries a global sequence number (`\g__mark_int`,
`\__mark_value:nn{⟨id⟩}{⟨text⟩}`) so `\IfMarksEqualTF` can distinguish "same text"
from "same mark" — used for dict-head style logic.

---

## 5. `\vsplit` and box splitting

`\setbox⟨n⟩ = \vsplit⟨m⟩ to ⟨dimen⟩` runs the *page-breaking* algorithm (same
badness/cost machinery as §1.4, minus inserts) on the vertical list in `\box m`:

1. TeX finds the least-cost breakpoint for a fragment of height ⟨dimen⟩.
2. The material **above** the break is vpacked `to ⟨dimen⟩` with `\boxmaxdepth`
   semantics governed by **`\splitmaxdepth`** (excess depth pushed back as height)
   and becomes the value assigned to `\box n`. Its `\splitfirstmark(s)` /
   `\splitbotmark(s)` are set from marks inside it (§4.3).
3. The material **below** the break is "pruned": **discardable items at the break
   are dropped** — glue, kerns, and penalties are removed until the first
   box/rule/whatsit/mark/insert is reached (exactly like the top of a new page) —
   and **`\splittopskip` glue is inserted above the first box** of the remainder.
   The remainder is repacked at natural size and stays in `\box m` (void if
   nothing remains).

So the kept part is the split-off fragment; the discarded tokens are only the
inter-item glue/penalties straddling the break (content is never lost). Forced
penalties (≤ −10000) inside the box force the split there; `\penalty10000`
forbids it.

Balanced multi-column layouts are built on this. `multicol`'s balancing loop
(multicol.sty:433–465, 604–620) is the canonical shape:

```tex
\splittopskip\topskip           % every column top like a page top
\vbadness\@M \splitmaxdepth\maxdepth
⟨guess a column height \dimen@⟩
\setbox1\vsplit\@cclv to\dimen@   % column 1
\setbox2\vsplit\@cclv to\dimen@   % column 2 (remainder of the remainder) ...
⟨measure the columns; if too unbalanced, increase \dimen@ and retry from a copy⟩
```

Setting `\splittopskip=\topskip` makes each column's first baseline sit where a
page's would; the retry loop must operate on `\copy`s because `\vsplit` consumes
its box. The same idiom appears in `longtable` (splitting a too-tall head/foot
chunk), in `\@doclearpage`'s `\vsplit\@cclv to\z@` (latex.ltx:20380 — a zero-size
split purely to flush marks), and in `ltmarks`' split-to-`\maxdimen` mark
extraction (§4.4).

---

## 6. Penalties as break signals

Penalty nodes are the *only* programmable communication channel into the
line/page-breaking cost function, and — via `\outputpenalty` — into the output
routine.

- `\penalty p` with `−10000 < p < 10000`: a legal breakpoint of cost contribution
  `p` (negative = encouraged).
- `\penalty 10000` (and anything ≥ 10000): **infinite** — not a legal breakpoint at
  all. `\nobreak` ≡ `\penalty\@M`.
- `\penalty −10000` (and anything ≤ −10000): **forced** break; the page builder
  fires immediately (cost `c = p`, §1.4). Values below −10000 are equally forced
  but remain distinguishable in `\outputpenalty`, which is how LaTeX multiplexes
  `−10001`…`−10004` as float/clearpage signals (§2.1) and plain TeX uses
  `\supereject`'s −20000.

LaTeX user level (`ltspace.dtx`, latex.ltx:9091–9146):

```tex
\DeclareRobustCommand\pagebreak{\@testopt{\@no@pgbk-}4}
\DeclareRobustCommand\nopagebreak{\@testopt\@no@pgbk4}
\def\@getpen#1{\ifcase #1 \z@ \or \@lowpenalty\or \@medpenalty \or \@highpenalty
         \else \@M\fi}
```

`\pagebreak[n]`/`\nopagebreak[n]` emit `\penalty ∓⟨getpen⟩` (via `\vadjust` when in
horizontal mode, so the penalty lands *between lines* after migration): `[4]` gives
∓10000 (forced/forbidden), `[1..3]` use the class-set `\@lowpenalty`/`\@medpenalty`
/`\@highpenalty` (151/301/601 in the standard classes). `\samepage`
(latex.ltx:9095–9103) simply sets every inter-item penalty parameter to 10000:

```tex
\DeclareRobustCommand\samepage{\interlinepenalty\@M
   \predisplaypenalty\@M \postdisplaypenalty\@M \interdisplaylinepenalty\@M
   \@beginparpenalty\@M \@endparpenalty\@M \@itempenalty\@M
   \@secpenalty\@M \interfootnotelinepenalty\@M}
```

Automatic inter-line penalties, inserted by the paragraph builder *between the
lines it contributes to the MVL* (so they are page-break parameters even though
they are set by paragraphs):

| Parameter | Inserted | Plain/LaTeX default |
|---|---|---|
| `\interlinepenalty` | between every pair of lines | 0 |
| `\clubpenalty` | after line 1 (orphan control) | 150 |
| `\widowpenalty` | before the last line (widow control) | 150 |
| `\displaywidowpenalty` | before the last line preceding a display | 50 |
| `\brokenpenalty` | after a line ending in a hyphenated break | 100 |
| `\predisplaypenalty` | before a math display | **10000** |
| `\postdisplaypenalty` | after a math display | 0 |
| `\interdisplaylinepenalty` | between lines of `eqnarray`-style displays | 100 |

These are cumulative at a given position (e.g. club+widow in a two-line
paragraph). LaTeX's `\@secpenalty`, `\@beginparpenalty`, `\@itempenalty` etc. are
macro-level knobs emitted by `\addpenalty`, which merges a penalty into preceding
vertical glue correctly. Setting widow/club penalties to 10000
(`\usepackage[defaults]{widows-and-orphans}`-style or `\samepage`) converts soft
preferences into hard constraints that can force earlier, looser pages — another
purely page-global effect of paragraph-level parameters.

---

## 7. Implications for per-paragraph recompilation (texlode)

The engine facts above delimit exactly what a 1ms paragraph-local fast path can and
cannot promise. A single paragraph, re-typeset with the same `\hsize` and font
state, yields the same horizontal-list-to-lines mapping — that part is genuinely
local (LuaTeX's `linebreak` callback can be run on a stored node list in
isolation). Everything else in this note is a channel by which one paragraph's
content changes *other pages*:

1. **The page builder is greedy and stateful.** Page breaks are chosen
   first-fit against `\pagegoal`/`\pagetotal` (§1.4); a one-line change in
   paragraph *k* shifts `\pagetotal` for every subsequent breakpoint, moving page
   breaks arbitrarily far forward. There is no paragraph-local approximation of
   "which page am I on" — that is precisely the quantity the background full
   compile must converge. The fast path can only *re-render the paragraph in
   place*; page-boundary motion is deferred by construction.

2. **Footnotes (`\footins`) couple content to geometry non-locally.** An `\insert`
   changes `\pagegoal` for its own page (§3.2), can be split with the remainder
   held over to the next page (§3.4), and contributes `\insertpenalties` to break
   costs. Editing a paragraph *with a footnote* changes the available text height
   of its page and potentially the footnote distribution of following pages. The
   fast path must treat any paragraph containing `\insert` nodes (footnotes,
   marginpars via `\@Mii`, `\enlargethispage`) as page-dirty: render locally for
   feedback, but flag the page chain for the convergence pass. Note also that any
   custom OR trickery (multicol, longtable, lineno) assumes `\holdinginserts`
   discipline (§3.3) — replaying `\box255` outside a real page-builder run cannot
   reproduce insert splitting.

3. **Marks are shipout-time state.** `\topmark`/`\firstmark`/`\botmark` — and the
   ltmarks region structures (§4.4) — are computed per column/page *in the output
   routine*, from marks that migrated out of paragraphs (§4.2). A section title
   edit changes the running head of every page up to the next mark, and the
   ltmarks dblcol rehoming means even "which column did the mark land in" matters.
   Running heads/feet are therefore full-compile artifacts; the fast path should
   freeze the previously computed head/foot for the edited page.

4. **Floats are a document-global queue, not a page property.** LaTeX floats live
   in `\@deferlist`/`\@dbltoplist` across output-routine invocations (§3.5); a
   float displaced by a paragraph edit can cascade placement changes (and
   `\@colroom`/`\vsize` adjustments, §1.3) for many pages, and `\clearpage`
   semantics (`−\@Mi` signal) flush state accumulated since the last barrier. Any
   edit in a float-bearing region invalidates placement from that point to the
   next `\clearpage` — those are the natural convergence-pass checkpoints (as are
   `\include` boundaries, which the kernel brackets with `\clearpage`,
   latex.ltx:9469/9492).

5. **Cross-references are two-pass by design.** `\pageref` depends on
   `\stepcounter{page}` at shipout (§2.3); `\ref`/`\cite` go through the `.aux`
   file with deferred `\write` whatsits that are *expanded at shipout time* inside
   `\@outputpage`. The fast path can substitute cached label values (last
   converged `.aux`) and must tolerate them being stale until the background pass
   rewrites them — the same "rerun to get cross-references right" fixpoint LaTeX
   already has, now running continuously.

6. **Penalties leak paragraph decisions into page costs.** Club/widow/broken
   penalties are emitted *between lines* by the paragraph builder (§6), so even a
   reflow that keeps the same line count can change the penalty sequence and hence
   the neighbouring page break. A fast path that keeps the old page break is
   showing a page that the converged document may not contain — acceptable for
   editing feedback, but it means "pixel-stable until convergence" can only be
   guaranteed for edits that preserve the paragraph's total height *and* its
   boundary penalties.

**Summary rule for the fast path.** A paragraph edit is safely local iff the
re-typeset paragraph (a) has the same total vertical extent (height+depth and
inter-line glue) as before, (b) contains no `\insert`, `\mark`/`\marks`, `\vadjust`,
or whatsit (`\write`/`\special`) nodes, and (c) does not change boundary penalties.
Everything else — footnotes, floats, marks, `\ref`/`\pageref`, `\enlargethispage`,
column balancing — flows through the page builder and output routine, whose state
(`\pagegoal`, insert boxes, float queues, mark structures, `.aux`) is global and
sequential. That is the precise sense in which texlode's background full compile is
not an optimization but a semantic necessity: TeX's page model has no
paragraph-local factorization.
