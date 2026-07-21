# lua-widow-control: A Case Study in Callback-Based Node-List Surgery

**Package**: `lua-widow-control` (lwc) v3.0.1 (2024-03-11), Max Chernoff, MPL-2.0+
**Sources analyzed** (installed, TeX Live 2025):

- Lua core: `/usr/local/texlive/2025/texmf-dist/tex/luatex/lua-widow-control/lua-widow-control.lua` (1792 lines — all line numbers below refer to this file unless noted)
- LaTeX wrapper: `/usr/local/texlive/2025/texmf-dist/tex/lualatex/lua-widow-control/lua-widow-control.sty`
- Plain TeX wrapper: `/usr/local/texlive/2025/texmf-dist/tex/luatex/lua-widow-control/lua-widow-control.tex`
- ConTeXt module: `/usr/local/texlive/2025/texmf-dist/tex/context/third/lua-widow-control/t-lua-widow-control.mkxl`
- OpTeX wrapper: `/usr/local/texlive/2025/texmf-dist/tex/optex/lua-widow-control/lua-widow-control.opm`
- Documentation (sources in `/usr/local/texlive/2025/texmf-dist/source/luatex/lua-widow-control/`):
  - `tb133chernoff-widows.ltx` — TUGboat 43:1 (2022), pp. 28–39, "Automatically removing widows and orphans with lua-widow-control", DOI 10.47397/tb/43-1/tb133chernoff-widows
  - `tb135chernoff-lwc.ltx` — TUGboat 43:3 (2022), pp. 340–342, "Updates to lua-widow-control" (adds the insert/footnote mechanism)
  - `lwc-manual.tex` — the ConTeXt-typeset user/developer manual

Why we study it: lwc is the best-documented real-world example of a package that
(a) re-runs TeX's paragraph breaker **from Lua** with altered parameters,
(b) intercepts a fully-built page **before the output routine** and splices new
material into it, and (c) uses attributes to maintain identity between a
node-level object and its re-typeset replacement. These are precisely the three
primitives a texlode-style real-time recompiler needs.

---

## 1. The problem: widows, orphans, and why `\widowpenalty` is all-or-nothing

A **widow** is the last line of a paragraph stranded at the top of the next
page/column; an **orphan** ("club" in the TeXbook) is the first line stranded
at the bottom; a **broken hyphen** is a page break inside a hyphenated word.
TeX discourages all three with penalties (`\widowpenalty`, `\clubpenalty`,
`\brokenpenalty`) inserted between lines during paragraph breaking.

The TUGboat article (tb133, §"Pagination in TeX") explains why penalties alone
are a bad tool for prose:

- Under `\flushbottom`, a high penalty makes TeX **stretch vertical glue** to
  push the offending line to the next page. On a math-heavy page with
  displays/headings this is invisible; on a page of pure prose there is almost
  no vertical glue to stretch, so the page becomes visibly gappy.
- Under `\raggedbottom`, a high penalty makes TeX **shorten the page by one
  line**, producing uneven text-block heights across spreads.
- With `\widowpenalty=10000` the break is simply forbidden (infinite), i.e.
  the mechanism is effectively all-or-nothing: either you accept the widow or
  you accept glue stretching/short pages. There is no mode in which TeX
  reconsiders the *paragraph breaking* of material already contributed to the
  page — "once TeX begins breaking a page, it never goes back to modify any
  content on the page. Page breaking is a localized algorithm, without any
  backtracking." (tb133, §4.1)

**lwc's approach** is the third option Knuth described manually in TeXbook
ch. 14: set `\looseness=1` on some paragraph so it runs one line longer, which
pulls the widow back onto its page (or pushes an orphan forward). Doing this
by hand requires re-editing after every revision. lwc automates it entirely in
Lua:

1. It sets `\widowpenalty`, `\clubpenalty`, `\displaywidowpenalty`,
   `\brokenpenalty` to the **sentinel value 1** (see `lua-widow-control.tex`
   lines 22–25 and the `.sty` key defaults, lines 87–99): small enough that TeX
   stretches nothing and breaks the page normally, but nonzero so the chosen
   `\outputpenalty` *identifies* that the break happened at a widow/orphan.
2. As every paragraph is typeset, lwc **also breaks a copy of it one line
   longer** (`\looseness=1` via `tex.linebreak`) and stores the result in a
   "paragraph store" keyed to the page.
3. Just before the output routine runs, if `\outputpenalty` matches a
   widow/orphan sentinel, it **replaces** the cheapest stored paragraph on the
   page with its lengthened version, then **pushes the last line of the page**
   back onto the recent-contributions list so it becomes the first line of the
   next page.

Net effect: flush bottom, constant spacing, no glue stretch — the page trades
one line with a paragraph, at page-break time, after the fact. This is
localized re-typesetting of one paragraph triggered by a global (page-level)
event — structurally the same operation as texlode's "re-typeset only the
edited paragraph" fast path, just triggered by pagination instead of edits.

---

## 2. Callback usage: the full inventory

lwc registers **five** callbacks. From the `lwc.callbacks` table
(lines 1534–1568):

```lua
lwc.callbacks = {
    disable_box_warnings = register_callback({
        callback = "hpack_quality",
        func     = function() end,
        name     = "disable_box_warnings",
        lowlevel = true,
    }),
    remove_widows = register_callback({
        callback = "pre_output_filter",
        func     = lwc.remove_widows,
        name     = "remove_widows",
        lowlevel = true,
    }),
    save_paragraphs = register_callback({
        callback = "pre_linebreak_filter",
        func     = lwc.save_paragraphs,
        name     = "save_paragraphs",
        category = "processors",
        position = "after",
    }),
    mark_paragraphs = register_callback({
        callback = "post_linebreak_filter",
        func     = lwc.mark_paragraphs,
        name     = "mark_paragraphs",
        category = "finalizers",
        position = "after",
    }),
    show_costs = register_callback({
        callback = "pre_shipout_filter",
        func     = lwc.show_costs,
        name     = "show_costs",
        category = "shipouts",
        position = "finishers",
    }),
}
```

| Callback | Function | Purpose | Lifetime |
|---|---|---|---|
| `pre_linebreak_filter` | `lwc.save_paragraphs` (line 542) | Receives the *unbroken* hlist of every paragraph; breaks a **copy** naturally and a copy with `looseness=1`; stores the long version + cost | Enabled/disabled by `\lwcenable`/`\lwcdisable` (lines 1573–1600) |
| `post_linebreak_filter` | `lwc.mark_paragraphs` (line 759) | Receives the *broken* paragraph (list of line hlists); tags first/last line with a paragraph attribute; copies + tags inserts | Same on/off pair as above |
| `pre_output_filter` | `lwc.remove_widows` (line 1256) | Receives `\box255`'s content just before `\output` expands; detects widow via `tex.outputpenalty`; splices in the long paragraph; moves last line to `contrib_head` | **Always active** from load (line 1787), even when lwc is "disabled" — earlier saved paragraphs must remain usable (comment at lines 1593–1595) |
| `pre_shipout_filter` | `lwc.show_costs` (line 1348) | Draft-mode only: walks the final shipout box recursively and plants cost labels in the margins | Always active (line 1788); no-op unless `showcosts` |
| `hpack_quality` | empty function (line 1537) | Temporarily silences "underfull hbox" warnings while lwc speculatively breaks its long copies | Enabled *around each* speculative break (lines 551–557, 580–582) |

Notably **absent**: `buildpage_filter`, `contribute_filter`, `linebreak_filter`,
and `\output` itself. lwc never replaces TeX's line breaker or output routine;
it only *observes* paragraphs (pre/post linebreak) and *edits* the finished
page box (pre-output). The manual stresses this compatibility posture: "It
doesn't modify the output routine, `\everypar`, and it doesn't insert any
whatsits" (lwc-manual.tex, §Compatibility). Interaction with the page builder
happens not through a callback but through direct access to the builder's
internal lists: `tex.lists.contrib_head` and `tex.lists.hold_head`
(lines 162–192, 1126–1133).

### luatexbase vs. raw `callback.register` vs. ConTeXt actions

`register_callback` (lines 1486–1530) is a small portability shim returning
`{enable=..., disable=...}` closures per format:

- **Plain/LaTeX**: `luatexbase.add_to_callback(t.callback, t.func, t.name)` /
  `remove_from_callback` (lines 1487–1495). Named registration lets multiple
  packages coexist on the same callback.
- **ConTeXt, normal case**: no raw callbacks at all — ConTeXt freezes them.
  Instead lwc appends to ConTeXt's node-task pipelines:
  `nodes.tasks.appendaction(category, position, "lwc."..name)` with
  `enableaction`/`disableaction` (lines 1496–1507). Hence the `category` /
  `position` fields ("processors"/"after" for save, "finalizers"/"after" for
  mark, "shipouts"/"finishers" for costs).
- **ConTeXt, `lowlevel = true`**: `pre_output_filter` and `hpack_quality`
  have **no ConTeXt action equivalent**, so lwc falls back to the raw engine
  API `callback.register(t.callback, t.func)` (lines 1508–1519). The author
  flags the fragility explicitly (comment, lines 1509–1515): "ConTeXt leaves
  some LuaTeX callbacks unregistered and unfrozen. … This is fragile though,
  because a future ConTeXt update may decide to register one of these
  functions, in which case lwc will crash with a cryptic error message."
- **OpTeX**: `callback.add_to_callback` / `callback.remove_from_callback`
  (lines 1520–1529) — OpTeX ships its own luatexbase-alike in the `callback`
  namespace.

### Callback-ordering concerns the source documents

- **Other `hpack_quality` users**: before nulling the warning callback, lwc
  checks `#luatexbase.callback_descriptions("hpack_quality") == 0`
  (lines 552–553), citing its issue #18 and `michal-h21/linebreaker#3` — if
  another package (e.g. `linebreaker`) already owns `hpack_quality`, lwc must
  not stomp it. This is the canonical example of two node-manipulating
  packages colliding on a callback.
- **luatexbase log spam**: enabling/disabling `hpack_quality` around *every
  paragraph* floods the log with luatexbase "Inserting/Removing" info lines,
  so lwc patches luatexbase's private `luatexbase_log` **upvalue** via the
  `debug` library (`silence_luatexbase`, lines 1733–1764) — self-described as
  "almost certainly a terrible idea", with a `LWC_NO_DEBUG` escape hatch and a
  graceful bail-out on TL24+ where `debug` is unavailable (lines 1738–1743).
- **Reentrancy guards**: `save_paragraphs` refuses to run when
  `status.output_active` (never re-break paragraphs during the output routine)
  or when `tex.nest.ptr > 1` (never inside boxes — a paragraph inside an
  `\hbox`/`\vbox` is not a candidate for page-level surgery) (lines 543–548).
  `mark_paragraphs` also checks `status.output_active` (line 655). These
  guards define which paragraphs are *in scope* for the technique.
- **Plain TeX lacks `pre_shipout_filter`** as a luatexbase callback, so lwc
  creates it: `luatexbase.create_callback('pre_shipout_filter', 'list')`
  (lines 266–269) and gives Plain users `\lwcpreshipout<boxnum>` to call from
  their own `\output` routine (Lua side lines 1708–1714; usage documented in
  lwc-manual.tex §Draft Mode with a full sample output routine).

---

## 3. Node-level techniques

### 3.1 Local aliasing of the node library (lines 110–137)

The prologue localizes every node-API function used, doubling as a
LuaTeX/LuaMetaTeX compatibility map:

```lua
local copy = node.copy
local copy_list = node.copy_list or node.copylist
local find_attribute = node.find_attribute or node.findattribute
local free = node.free
local free_list = node.flush_list or node.flushlist
local get_attribute = node.get_attribute or node.getattribute
local last = node.slide
local linebreak = tex.linebreak
local new_node = node.new
local remove = node.remove
local set_attribute = node.set_attribute or node.setattribute
local traverse = node.traverse
local traverse_id = node.traverse_id or node.traverseid
local vpack = node.vpack
```

Note: lwc uses the **userdata** node API throughout, *not* `node.direct`. The
author's stated priority is portability across 7 format/engine combinations;
the `or`-chains above (underscore vs. no-underscore names) are the LuaTeX ↔
LMTX bridge. Node type IDs are resolved once via `node.id()` but **sub**type
IDs are hardcoded (`baselineskip_subid = 2`, `line_subid = 1`,
`linebreakpenalty_subid = 1`, parfillskip subids 16–19; lines 90–108) with the
comment "We need to hardcode the subid's sadly" — subtype numbers have no
lookup API.

### 3.2 Speculative re-breaking with `tex.linebreak` (the key trick)

`lwc.save_paragraphs` (lines 542–642) runs on the **pre**-linebreak list, i.e.
the raw hlist of the paragraph before TeX breaks it. It breaks *two copies* in
the background, without disturbing the real typesetting:

```lua
local function long_paragraph(head, parfillskip)
    -- We can't modify the original paragraph
    head = copy_list(head)

    prepare_linebreak(head)          -- LMTX-only: tex.preparelinebreak

    local n = last(head)             -- the \parfillskip glue node
    n.width = parfillskip[1]
    n.stretch = parfillskip[2]
    n.shrink = parfillskip[3]
    ...
    -- Break the paragraph 1 line longer than natural
    local long_node, long_info = linebreak(head, {
        looseness = 1,
        emergencystretch = tex_dimen[emergencystretch],
    })
```
(lines 440–468; the natural-length twin `natural_paragraph` is lines 475–486)

Key observations:

- **`tex.linebreak(head, params)` is callable at almost any time** — here it
  runs inside `pre_linebreak_filter`, i.e. *while TeX itself is entering its
  own line breaker*. It returns `(broken_list, info)` where `info` carries
  `demerits`, `prevgraf` (line count), `prevdepth`, `looseness` results. The
  natural copy is broken purely to obtain `prevgraf`/`demerits` and is
  immediately freed (`free_list(natural_node)`, line 483).
- **Parameters are per-call overrides**, not global state: `looseness = 1` and
  a custom `emergencystretch` are passed in the options table without touching
  the real registers. This is exactly the "re-run the paragraph builder with
  my own parameters" API.
- **`\parfillskip` manipulation instead of ties**: to avoid an ultra-short
  last line in the lengthened version, lwc rewrites the paragraph's trailing
  parfillskip glue to `0.75\hsize plus 0.05\hsize minus 0.75\hsize`
  (lines 561–567, crediting Olšák's TBN p. 234), forcing the loosened last
  line to be ≥ ~20% of the measure. If `looseness=1` accidentally yields
  **two** extra lines (a documented failure mode of the parfillskip trick,
  lines 569–578), it frees the result and retries with a softer
  `{0, 0.8*hsize, ...}` setting.
- **Validation of the speculative result**: the long version is accepted only
  if `long_info.prevgraf == natural_info.prevgraf + 1`; and if its cost is
  suspiciously low (`< 10`, "free to expand is suspicious", lines 599–604) it
  is priced at `math.maxinteger` — i.e. never used. Cost function:
  `demerits / math.sqrt(lines)` (`lwc.paragraph_cost`, lines 345–347; the
  manual documents it as user-replaceable, and derives the √l compromise in
  tb133 §"Choosing the best paragraph").
- **`prevdepth` compensation at store time**: the natural and long versions
  end with different depths, so a glue node of width
  `natural_info.prevdepth - long_info.prevdepth` is appended to the stored
  copy (lines 584–589) so that splicing it in later doesn't shift the
  following baselineskip. (Skipped in ConTeXt grid mode, where depth is
  quantized differently.)

### 3.3 The paragraph store and memory discipline

Page-scoped state (lines 292–296):

```lua
local paragraphs = {} -- The expanded paragraphs on each page
local inserts = {}    -- Copies of all the inserts on each page
local costs = {}      -- All of the paragraph costs for the document
local pagenum = 1     -- The current page/column number
```

The stored object is `{cost = long_cost, node = copy_list(saved_node)}`
(lines 614–617), where `saved_node` starts at the first line-subtype hlist of
the long version (initial glue can vanish in ConTeXt grid mode, lines 606–608).
The scratch long list itself is freed right after copying (line 619).

`reset_state` (lines 818–830) is called at the end of *every*
`remove_widows` invocation (success, failure, or no-widow):

```lua
local function reset_state()
    for _, paragraph in ipairs(paragraphs) do
        free_list(paragraph.node)
    end
    paragraphs = {}
    for _, insert in ipairs(inserts) do
        free(insert)
    end
    inserts = {}
    pagenum = pagenum + 1
end
```

The docstring says it plainly: "This function is *vital* to ensure that we
don't leak any nodes. If we do leak nodes, then very large documents will slow
down and eventually fail to compile." tb135 §11.3 confirms this was learned
the hard way: early versions leaked, causing slowdowns past a few hundred
pages; after adding a leak-testing suite, lwc "can now easily compile
documents >10 000 pages long." **For a long-running real-time daemon, node
lifetime management is a first-class correctness concern, not a nicety.**
Also note the pattern for freeing a `vpack` result without freeing its
content: `orig_vpack.list = nil; free(orig_vpack)` (lines 1277–1279).

### 3.4 Attribute tagging: identity across pipeline stages

lwc allocates two attributes per format (`luatexbase.new_attribute`,
`attributes.public`, or `alloc.new_attribute`; lines 207–249):
`lua-widow-control_paragraph` and `lua-widow-control_insert`.

`mark_paragraphs` (lines 653–690) runs in `post_linebreak_filter` and encodes
**(page, paragraph-index, start/end)** into a single integer attribute value:

- first line of paragraph *k* on page *p*: `k + 100*p` (`PAGE_MULTIPLE = 100`)
- last line: `-(k + 100*p)`
- single-line paragraph (one node must carry both roles):
  `k + 100*p + 50` (`SINGLE_LINE = 50`)

The insert attribute packs even more: class, first index, and last index of
the inserts hanging off a line, as
`class*1e6 + first*1e3 + last` (lines 739–745). This integer-multiplexing is
forced by the constraint that **a node carries only one value per attribute**
(comment at lines 682–684). The page number term exists to detect paragraphs
*split across pages*: at page-break time, any first-paragraph attribute whose
`value // PAGE_MULTIPLE == pagenum - 1` began on the previous (already
shipped) page and is skipped as un-expandable (lines 884–896 — see §5).

Retrieval uses `node.find_attribute` (fast scan to the next node carrying the
attribute) rather than full traversal — see `replace_paragraph` (lines
1170–1236) and `get_inserts` (lines 951–1049).

### 3.5 Page surgery in `pre_output_filter`

`lwc.remove_widows` (lines 1256–1333) receives the head of the material about
to become `\box255`. Sequence:

1. **Trigger test**: `lwc.should_remove_widows(tex.outputpenalty, ...)`
   (lines 807–809) → `is_matching_penalty` (lines 771–796), which subtracts
   `\interlinepenalty` and compares against all *sums* of
   widow/club/displaywidow/broken penalties (a page break can sit between two
   lines that carry, e.g., both a clubpenalty and a widowpenalty; adapted from
   Mittelbach's widows-and-orphans package, comment line 779). Not a match →
   `reset_state(); return head` unchanged.
2. **Height bookkeeping**: `vpack(head)` to record the original
   `height - \vsize` discrepancy before any surgery (lines 1274–1279).
3. **Select** the cheapest stored paragraph *fully on this page*
   (`best_paragraph`, lines 914–944; respects `max_cost` and rejects the
   *last* paragraph on the page — lengthening the widowed paragraph itself
   can't help).
4. **`move_last_line`** (lines 1061–1136): walk **backwards** from
   `last(head)` skipping glue; if an infinite penalty (≥ 10000) guards the
   last line, a heading precedes it — apply the `nobreak_behaviour` policy
   (`keep`: move heading+line together; `split`: move just the line; `warn`:
   abort) (lines 1066–1096). Then check whether removing this line would
   *create* a new widow/orphan (the `potential_penalty` check, lines
   1098–1113 — it warns "Making a new widow/orphan/broken hyphen"). Finally:

   ```lua
   last_line = copy_list(n)
   ...
   -- Add back in the content from the next page
   last(last_line).next = copy_list(tex_lists[contrib_head])
   free_list(n.prev.prev.next)
   n.prev.prev.next = nil          -- truncate the page before glue+penalty
   free_list(tex_lists[contrib_head])
   tex_lists[contrib_head] = last_line   -- prepend to next page's material
   ```
   (lines 1115–1133) — the moved line is *prepended to the page builder's
   contribution list* by direct assignment to `tex.lists.contrib_head`. This
   is how you hand material back to the page builder from Lua.
5. **`replace_paragraph`** (lines 1146–1243): copies the stored long
   paragraph (minus its insert nodes — they'd "upset LuaMetaLaTeX", lines
   1147–1163), then scans the page via `find_attribute` for the start and end
   tags. At the start tag it repairs vertical spacing:

   ```lua
   -- Fix the `\baselineskip` glue between paragraphs
   height_difference = (first line height of old) - (first line height of new)
   local prev_bls = next_of_type(n, glue_id,
       { subtype = baselineskip_subid, reverse = true })
   if prev_bls then
       prev_bls.width = prev_bls.width + height_difference
   end
   n.prev.next = target_node        -- splice: bypass old paragraph
   ```
   (lines 1188–1207), and at the end tag reconnects `target_node_last.next =
   n.next` (grid mode instead inserts a depth-compensating glue, lines
   1219–1228), sets `n.next = nil`, and frees the detached original lines
   (`free_list(free_nodes_begin)`, lines 1238–1239). **Splicing typeset lines
   requires manual repair of interline glue** — baselineskip glue was computed
   against the *old* line's height and is now stale.
6. **Final height correction**: re-`vpack`, compare against the original
   discrepancy, and if the net difference is nonzero but < ¼`\baselineskip`,
   append a compensating glue at the bottom so `\box255` is exactly `\vsize`
   and no over/underfull warnings fire (lines 1300–1321). Differences larger
   than that are deliberately allowed to surface as warnings.

### 3.6 Insert (footnote) handling — the hardest part

tb135 §13.4 explains the ordering problem: `pre_output_filter` runs *after*
the page builder has already stripped insert nodes off the lines and packed
their content into `\box<class>` (and split remainders into the internal
`hold_head` list). So if you move a line, its footnote *text* is already gone
from the line — only the mark remains.

lwc's solution spans both ends of the pipeline:

- At `post_linebreak_filter` time, `mark_inserts` (lines 700–751) **copies
  every insert node** (`inserts[#inserts+1] = copy(insert)`), tags the
  insert's content lines with the insert attribute (every line, "since TeX
  can split the insert between pages at any point", lines 709–713), and tags
  the first element of the *carrying line* with the packed
  class/first/last integer.
- At `pre_output_filter` time, `get_inserts` (lines 951–1049) decodes the
  attribute on the moved line, then surgically deletes the matching content
  from `tex.box[class]` (LMTX: `tex.getinsertcontent(class)`) **and** from
  `tex.lists.hold_head` (the split-insert holdover list), voiding the box if
  it becomes empty "so that any `\ifvoid` tests work correctly in the output
  routine" (lines 1027–1031). The saved copies of the insert nodes are then
  re-appended after the moved line (lines 1120–1123), so the *next* page's
  builder re-processes them normally.
- Documented limitation: multiple insert *classes* on one line aren't
  supported (tb135 "Known issues"; source comment lines 732–738 — "I don't
  think that happens in real-world documents").

### 3.7 Whatsits, colors, and draft-mode annotation

Draft mode (`colour_list`, lines 495–533) shows the third injection pattern:
wrapping arbitrary line content in `pdf_colorstack` whatsit pairs
(`command=1` push / `command=2` pop, `set_whatsit_field`), with format
dispatch — OpTeX's `optex.set_node_color`, ConTeXt's
`nodes.tracers.colors.setlist`, raw whatsits elsewhere. `lwc.show_costs`
(lines 1348–1470) demonstrates full-page geometric traversal at
`pre_shipout_filter` time: a recursive walk accumulating x-positions (glue via
`node.effective_glue`, `shift` semantics differing under hlist vs. vlist
parents, lines 1361–1382), then planting `hpack`ed glyph-node labels in the
margins. Draft-mode glyphs are built by hand from `node.new("glyph")` with
hardcoded font ids (lines 278–290, 1406–1420) — a tiny display-list generator.

---

## 4. Cross-engine/format abstraction

One Lua file serves 7 combinations: Plain LuaTeX, LuaLaTeX, ConTeXt MkIV
(LuaTeX), ConTeXt MkXL (LuaMetaTeX), OpTeX, plus preliminary LuaMetaLaTeX /
LuaMetaPlain. The dispatch happens once at load:

- **Format detection** by `tex.formatname` string-matching, engine detection
  by `status.luatex_engine == "luametatex"` (lines 65–83).
- **Engine field-name map**: LMTX renamed list/field accessors, so lwc
  parameterizes them: `contrib_head` vs `contributehead`, `hold_head` vs
  `holdhead`, `page_head` vs `pagehead`, `stretch_order`/`shrink_order`
  vs `stretchorder`/`shrinkorder`, `node.setfield` vs `node.setwhatsitfield`
  (lines 176–192). LMTX also requires `tex.preparelinebreak(head)` before
  calling the line breaker, with a paranoid count of the four
  `par(fill/init)(left/right)skip` glues (lines 404–429), and reads insert
  class from `insert.index` instead of `insert.subtype` (lines 726–730).
- **Reporting**: ConTeXt `logs.reporter` (with a push/pop-target hack to keep
  info out of the terminal, lines 197–206), luatexbase
  `module_warning`/`module_info`, or raw `texio.write_nl` for OpTeX
  (lines 239–247).
- **Attribute allocation**: `luatexbase.new_attribute` /
  `attributes.public` / `alloc.new_attribute` (lines 207–249).
- **Register access by name**: option registers are read through
  `tex.dimen[name]`/`tex.count[name]`, with the *names* chosen per format —
  expl3 names (`g__lwc_emergencystretch_dim`) when the LaTeX package defined
  them (probed via `tex.isdimen`, lines 216–224), plain names
  (`\lwcemergencystretch`) otherwise, ConTeXt names (`lwc_emergency_stretch`)
  under ConTeXt. The TeX wrappers allocate these registers; Lua never owns
  option state.
- **Command registration**: `register_tex_cmd` (lines 1636–1673) defines the
  TeX-facing commands from Lua: OpTeX `define_lua_command`, Plain/LaTeX
  `luatexbase.new_luafunction` + `lua.get_functions_table()[i] = f` +
  `token.set_lua(name, i)`, ConTeXt `interfaces.implement{public=true,...}`.
  Argument scanning composes `token.scan_*` functions (lines 1641–1655), and
  names are mangled per format's convention (`lwc@foo` / `_lwc_foo` /
  `lwc_foo` / `__lwc_foo:n`, lines 1617–1627).
- **Conditionals via token injection**: `\iflwc` is implemented by pushing a
  real `\iftrue`/`\iffalse` token back into the input stream with
  `token.put_next` (lines 140–141, 1602–1609) — a clean Lua→TeX boolean
  channel.
- **Callback registration** differences: see §2 (luatexbase vs.
  `nodes.tasks` vs. raw `callback.register` vs. OpTeX `callback.*`).

The `.sty` adds LaTeX-specific machinery worth noting: automatic
`microtype` loading (font expansion "is required for the lengthened
paragraphs to not have terrible spacing", `.sty` lines 126–139), and
hook-based disabling around sectioning commands (`\g__lwc_disablecmds_cl`
defaults cover `\@sect`, memoir, titlesec; `cmd/#1/before`/`after` hooks
increment/decrement a disable counter — `.sty` lines 106–226). The Plain
wrapper achieves the same by `\meaning`-decomposition and `\scantokens`
redefinition of `\beginsection` (`lua-widow-control.tex` lines 75–101).

---

## 5. Gotchas the author documents — the boundaries of locality

These are the cases where "just re-break one paragraph" is *not* safe, and
they map one-to-one onto the invalidation conditions a texlode-style
per-paragraph fast path must detect:

1. **Paragraphs that straddle a page boundary cannot be remeasured.**
   `first_last_paragraphs` (lines 884–896):
   > "If the first complete paragraph on the page was initially broken on the
   > previous page, then we can't expand it here. Why…? Expanding it will
   > nearly always change how the first few lines are printed, but we can't
   > modify those since they've already been shipped out."

   And lwc-manual §Known Issues: "lua-widow-control can only expand
   paragraphs that fit completely on a page… you can't modify the bottom half
   of a paragraph since its top half has already shipped out." *Shipped-out
   material is immutable; locality ends at the last shipout.*

2. **Paragraphs inside boxes and during the output routine are out of scope**
   (`tex.nest.ptr > 1` / `status.output_active` guards, lines 543–548). A
   paragraph inside a box was measured under a different `\hsize`/context and
   doesn't participate in page composition as lines.

3. **`\parshape`-family state must be reconstructed before re-breaking.**
   `tex.linebreak` on a copied head requires the paragraph's break parameters
   to be re-supplied; lwc handles the parfillskip/parinitskip quartet
   explicitly (`prepare_linebreak`, lines 404–429, warning "Weird
   par(fill/init)skips found!") and overrides `\parfillskip` per call. More
   generally, the *info table returned by one break feeds the next stage*
   (prevgraf, prevdepth) — paragraph breaking has entry state (prevdepth,
   hangindent, parshape, looseness) and exit state that a recompiler must
   thread through, or splices will be geometrically wrong.

4. **Interline glue is derived state.** When replacing lines whose heights
   differ, the preceding baselineskip glue must be corrected by the height
   difference (lines 1188–1204), and depth differences need trailing
   compensation glue in grid mode (lines 1219–1228) or a prevdepth-offset
   glue at store time (lines 584–589). Any line-level splice invalidates the
   adjacent baselineskip computation.

5. **Inserts/footnotes break naive line movement** (§3.6). Insert content is
   *aggregated per page* by the page builder before the interception point;
   moving a line means un-aggregating (`tex.box[class]`, `hold_head`) and
   re-injecting. Marks would pose the analogous problem (lwc doesn't handle
   marks explicitly; its moved line rarely contains `\mark`s in practice).
   For texlode: **paragraphs containing inserts/marks are not
   self-contained** — their display list has side channels into page state.

6. **Penalties are ambiguous signals.** `is_matching_penalty` must consider
   sums of penalties (lines 779–795), and eTeX's `\widowpenalties`-style
   arrays are explicitly unsupported (tb133 §"eTeX penalties": lines matching
   the sentinel values "will be treated exactly as a widow/orphan…likely to
   lead to some unexpected behaviour"). Interpreting the meaning of a break
   from `\outputpenalty` alone is heuristic.

7. **Headings couple to following lines through infinite penalties** — the
   `nobreak_behaviour` machinery (lines 1052–1096) exists because a moved
   line may drag a `\nobreak`-guarded heading with it. Vertical-list locality
   is bounded by penalty structure, not just by paragraph boundaries.

8. **Whatsit/color state is positional.** Draft-mode coloring must inject
   push/pop `pdf_colorstack` pairs around every moved/replaced segment
   (§3.7); by the same token, any splice that crosses a color push without
   its pop corrupts the PDF graphics stack. (lwc itself avoids inserting
   whatsits in normal operation — a compatibility claim it makes explicitly.)

9. **Some paragraphs simply cannot be loosened** (no stretch available;
   `looseness=1` returns the same prevgraf, or only at absurd cost). lwc
   prices these at `math.maxinteger` (lines 599–604) and has a whole
   fail-path (`remove_widows_fail`, lines 836–858) that warns and colors the
   offending lines instead of producing bad output. A remeasuring system
   needs an explicit "cannot do better; keep original" outcome.

10. **Speculative breaking has observable side effects to suppress**: the
    underfull-hbox warnings from the badly-stretched trial breaks
    (`hpack_quality` nulling, §2) and historical `\vfuzz` warnings from
    prevdepth compensation (tb133 Known Issues; fixed by the vpack height
    correction, lines 1300–1321).

---

## 6. Lessons for real-time recompilation (texlode)

What lwc *proves is possible* today, in stock LuaTeX, from Lua callbacks:

1. **`tex.linebreak(head, params)` is a production-quality, reentrant,
   per-paragraph recompiler entry point.** lwc calls it twice per paragraph
   of every document it processes, *concurrently with TeX's own breaking of
   the same paragraph* (from inside `pre_linebreak_filter`), with per-call
   parameter overrides (`looseness`, `emergencystretch`) and with hand-edited
   glue in the head list. Cost: "modern computers break paragraphs
   near-instantaneously… lwc runs entirely in a single pass" (tb133
   §Performance). **This is the API a texlode per-paragraph fast path would
   call**: keep the paragraph's pre-linebreak node list (or rebuild it from
   the edited text via `node.copy_list` of cached material + re-shaped
   glyphs), call `tex.linebreak` with the paragraph's saved parameters, and
   splice the resulting lines into the stored page. The contract details
   lwc teaches: copy before breaking (the breaker consumes the list), free
   what you don't keep, thread `prevdepth`/`prevgraf` through, and on LMTX
   call `tex.preparelinebreak` first.

2. **Pages are interceptable and editable as node lists after page breaking
   and before shipout** (`pre_output_filter` receives would-be `\box255`;
   `pre_shipout_filter` receives the final page box). At that point the page
   is exactly texlode's "display list": a vlist of line hlists. lwc shows both
   read access (geometric traversal with accumulated offsets, `show_costs`)
   and write access (splice a re-broken paragraph, repair baselineskip, repack
   with `vpack` to verify height). It also shows the escape route *back into*
   the page builder: assigning `tex.lists.contrib_head` to push material onto
   the next page.

3. **Attributes are the identity/caching mechanism.** One integer attribute,
   set at `post_linebreak_filter` time, survives packing, page breaking, and
   output, and lets `pre_output_filter`/`pre_shipout_filter` re-locate a
   specific paragraph's lines inside arbitrarily nested box structure
   (`find_attribute` scan). The integer-multiplexing scheme
   (page*100 + index, sign for start/end, +50 for single-line) is a
   workable — if cramped — way to encode (object-id, role) pairs; a texlode
   implementation would likely allocate several attributes instead. This is
   precisely how a display-list extractor can key page fragments back to
   source paragraphs for incremental replacement.

4. **The paragraph store is a working prototype of a paragraph-level cache**:
   `{cost, node = copy_list(lines)}` per paragraph per page, plus copied
   insert nodes, freed deterministically at each page boundary. texlode's
   cache differs in lifetime (persistent across edits rather than per-page)
   but inherits the same discipline: `copy_list` on write, `flush_list` on
   invalidation, never leak (tb135: leaks → failure on large documents;
   fixed versions handle >10 000 pages).

5. **The failure boundaries are enumerable and checkable.** Every condition
   in §5 (straddling paragraphs, in-box paragraphs, inserts/marks, parshape
   state, baselineskip repair, color stack, penalty coupling to headings) is
   detected by lwc with cheap structural tests (attribute-page arithmetic,
   `tex.nest.ptr`, `status.output_active`, penalty scanning, subtype checks).
   A texlode fast path can use the same tests as *bail-out predicates*: take
   the 1 ms local path when a paragraph is self-contained, fall back to a full
   recompile when any lwc-style boundary condition trips. lwc's
   `remove_widows_fail`/`max_cost` design shows the right shape: locality
   violations are runtime-detected and degrade gracefully, never silently
   mis-typeset.

6. **Coexistence is a real constraint.** The `hpack_quality` collision check,
   the frozen-callback problem in ConTeXt, the luatexbase log-upvalue hack,
   and the "raw `callback.register` may crash after a ConTeXt update" warning
   all show that a system doing aggressive node surgery must be a *named,
   well-ordered participant* in the callback ecosystem (luatexbase names,
   ConTeXt task categories/positions), not an owner of raw callbacks — except
   where no managed hook exists, which is itself a documented risk.

**Bottom line**: lua-widow-control demonstrates, in ~1800 lines of shipping
code exercised by CTAN users since 2021, the full loop texlode needs —
*capture paragraph → re-break on demand with `tex.linebreak` → locate by
attribute → splice into a finished page → hand the remainder back to the page
builder* — along with a field-tested catalogue of exactly which document
features (inserts, marks-adjacent state, cross-page paragraphs, grid/prevdepth
coupling, color whatsits, nobreak-coupled headings) break paragraph-level
locality and must gate the fast path.
