# LuaTeX Nodes and Callbacks

Working notes for understanding how texlode (Lode, "Real-Time LuaTeX", TUG 2026
preprint) can extract a display list of positioned glyphs directly from
LuaTeX's node structures after line breaking.

All facts below were checked against the **LuaTeX Reference Manual**
(stable, December 2025, v1.24 — section numbers cited as `§n.n`) and, where
marked *verified*, against live experiments run with **LuaHBTeX/LuaTeX 1.22.0
(TeX Live 2025)** in
[`experiments/02-node-dump/`](../experiments/02-node-dump/). A few of the
trickier claims (font-expansion arithmetic) were additionally confirmed
against the LuaTeX C source (`packaging.c`, `directions.c`).

Contents:

1. [The LuaTeX node model](#1-the-luatex-node-model)
2. [Node attributes](#2-node-attributes)
3. [The node library](#3-the-node-library)
4. [Callbacks that rewrite node lists](#4-callbacks-that-rewrite-node-lists)
5. [Walking a post-line-break paragraph](#5-walking-a-post-line-break-paragraph)
6. [Scaled points and rendering coordinates](#6-scaled-points-and-rendering-coordinates)

---

## 1. The LuaTeX node model

Everything TeX typesets ends up as a **node** in a singly linked list
(doubly linked once `prev` pointers are initialized, see `node.slide`).
Characters become `glyph` nodes, spaces become `glue` nodes, boxes become
`hlist`/`vlist` nodes, and so on. From Lua, nodes are userdata objects of
metatype `luatex.node` with named fields (§8.1, §8.7.1).

Every node has at least:

| field | type | meaning |
|---|---|---|
| `next` | node | next node in the list, or `nil` |
| `prev` | node | previous node — always present but only *initialized* after `node.slide()` or by engine passes; do not trust it blindly (§8.2) |
| `id` | number | the node type (e.g. `node.id("glyph")` = 29) |
| `subtype` | number | type-specific refinement; a dummy 0 for types that don't use it |
| `attr` | node | attribute list (see [§2](#2-node-attributes)); present on almost all node types |

`node.types()` maps ids to names. In LuaTeX 1.22 the ids relevant to typeset
output are: `hlist` (0), `vlist` (1), `rule` (2), `ins` (3), `mark` (4),
`boundary` (6), `disc` (7), `whatsit` (8), `local_par` (9), `dir` (10),
`math` (11), `glue` (12), `kern` (13), `penalty` (14), `margin_kern` (28),
`glyph` (29). (§8.1. Ids 15–27 and 30+ are math/internal types.)

Node memory is **not garbage collected** — you must `node.free`/`flush_list`
what you create and detach (§8.7.1). Comparing two node userdata compares
indices into node memory, which is only safe while neither has been freed.

### 1.1 hlist and vlist nodes (§8.2.1, §8.2.2)

The workhorse container. A paragraph after line breaking is a vertical list
whose lines are `hlist` nodes of subtype 1.

| field | meaning |
|---|---|
| `subtype` | hlist: 0 unknown, **1 line**, 2 box, **3 indent**, 4 alignment, 5 cell, 6 equation, 7 equationnumber, 8+ math fragments. vlist: only 0, 4, 5 |
| `width`, `height`, `depth` | box dimensions in scaled points |
| `shift` | displacement perpendicular to the progression direction (hlist: vertical shift of the box relative to the baseline, positive = down; vlist: horizontal). Used by `\hangindent`, `\moveleft`, `\raise` etc. |
| `glue_set` | the **calculated glue ratio** — a float, one of the very few floats in TeX |
| `glue_sign` | 0 = normal (glue at natural size), 1 = stretching, 2 = shrinking |
| `glue_order` | infinity order of the glue that was set, range **[0, 4]**: 0 = finite, 1 = `fi`, 2 = `fil`, 3 = `fill`, 4 = `filll` (LuaTeX inherits the extra `fi` order from Aleph/Omega — *verified*: a plain `\parfillskip = 0pt plus 1fil` line reports `glue_order = 2`) |
| `head` / `list` | first node of the box content (two names for the same field) |
| `dir` | direction (`TLT` etc., §8.2.15) |

Warning from the manual: never assign a node list to `head` unless its
internal link structure is correct.

### 1.2 glyph nodes (§8.2.12)

| field | meaning |
|---|---|
| `subtype` | bit field: bit 0 = character (still subject to hyphenation/font logic), bit 1 = ligature, bit 2 = ghost, bits 3/4 = left/right boundary variants (§5.2). Values ≥ 256 mean "protected" — font logic is done (`node.protect_glyphs` adds 256) |
| `char` | character index (Unicode code point, or after OpenType shaping a glyph reference) |
| `font` | font id (use `font.getfont(id)` for metrics) |
| `lang`, `left`, `right`, `uchyph` | frozen language/hyphenation parameters |
| `components` | ligature components (node list) |
| `xoffset`, `yoffset` | **virtual** displacements: they move the rendered glyph horizontally/vertically but do **not** change the advance width |
| `width`, `height`, `depth` | glyph metrics — **read-only**, they come from the font |
| `expansion_factor` | hz font-expansion factor for this glyph, assigned by the paragraph builder, applied in the backend; in **millionths of the width** (see [§5.3](#53-font-expansion-microtype-hz)) |
| `data` | free user field |

### 1.3 glue nodes (§8.2.9)

Spaces, `\hskip`, `\vskip`, skips inserted by the engine. Since LuaTeX 0.95
the five glue values live **directly on the glue node** (the shared,
reference-counted `glue_spec` node of TeX82 survives only for glue held in
registers):

| field | meaning |
|---|---|
| `width` | natural size (the key is `width` even for vertical glue) |
| `stretch`, `stretch_order` | stretch amount and its infinity order (0–4) |
| `shrink`, `shrink_order` | shrink amount and order |
| `subtype` | 0 userskip, 1 lineskip, 2 baselineskip, 3 parskip, 8 **leftskip**, 9 **rightskip**, 10 topskip, 13 **spaceskip** (an ordinary word space — *verified*), 15 **parfillskip**, 100–103 leaders |
| `leader` | box or rule for leader glue |

The *effective* width of a glue inside a packed box depends on the parent's
`glue_set`/`glue_sign`/`glue_order`; `node.effective_glue(g, parent)` computes
it for you (§8.2.9) — the manual arithmetic is in [§5.1](#51-resolving-set-glue).
Helpers: `node.setglue(n, w, st, sh, sto, sho)`, `node.getglue(n)` (§8.8).

### 1.4 kern nodes (§8.2.10)

| field | meaning |
|---|---|
| `kern` | fixed horizontal (or vertical) advance in sp |
| `subtype` | 0 = **fontkern** (from the font's kerning table), 1 = userkern (`\kern`), 2 = accentkern, 3 = italiccorrection |
| `expansion_factor` | present on kern nodes too (not in the manual's field table but reported by `node.fields` — *verified*); for kerns it is an **absolute sp amount**, not a ratio (see [§5.3](#53-font-expansion-microtype-hz)) |

### 1.5 disc (discretionary) nodes (§8.2.7)

Produced by the hyphenator, `\-`, `\discretionary`, and explicit `-`.

| field | meaning |
|---|---|
| `subtype` | 0 discretionary, 1 explicit, 2 automatic, 3 **regular** (hyphenator), 4 first, 5 second (the "of-f-ice" cases) |
| `pre` | node list typeset at the end of the line if the break is taken (usually a hyphen glyph) |
| `post` | node list typeset at the start of the next line |
| `replace` | node list typeset if the break is **not** taken |
| `penalty` | break penalty (normally `\hyphenpenalty`/`\exhyphenpenalty`) |

After line breaking, discs that were *not* used as breakpoints remain in the
line; only their `replace` text contributes to the line (often empty, width
0 — *verified* in the node dump). Discs that *were* used are dissolved: `pre`
material is spliced into the end of the line, `post` into the next line.
The `pre`/`post`/`replace` sublists have special internal structure — always
reassign the field after editing the sublist head (§8.2.7), and see
`node.check_discretionary` (§8.9.10).

### 1.6 penalty nodes (§8.2.11)

| field | meaning |
|---|---|
| `penalty` | the penalty value (−10000 = forced break, 10000 = prohibited) |
| `subtype` | informative only: 0 userpenalty, 1 **linebreakpenalty** (accumulated club/widow/interline/broken penalties between lines after par breaking — *verified*: value 300 between lines with plain defaults), 2 linepenalty, 3 wordpenalty, 4 finalpenalty, 6/7 display penalties |

### 1.7 rule nodes (§8.2.3)

| field | meaning |
|---|---|
| `width`, `height`, `depth` | dimensions; the special value −1073741824 (`−2^30`) means "running" (adapt to the enclosing box) |
| `subtype` | 0 normal, 1 box, 2 **image**, 3 empty, 4 user, 9 outline — LuaTeX reuses rules for reusable box objects and images so that the line breaker sees their dimensions |
| `left`, `right`, `dir`, `index`, `transform` | backend shifts / reuse index / outline width |

### 1.8 ins (insert) nodes (§8.2.4)

From `\insert` (footnotes, floats). `subtype` = the insertion class number,
`cost` = the associated penalty, `height`/`depth` dimensions, `head`/`list`
= the inserted vertical material, plus the associated glue as five numeric
fields (`width`, `stretch`, `stretch_order`, `shrink`, `shrink_order`; in
1.22 `node.fields` still also reports a `spec` field). Inserts ride along in
the paragraph's vertical list until the page builder files them into
`\box n` / `\skip n` registers.

### 1.9 mark nodes (§8.2.5)

From `\mark`: `class` = mark class number, `mark` = a token table. Used by
output routines for running heads; they travel in the list and are otherwise
invisible.

### 1.10 whatsit nodes (§8.4–8.6)

The extension escape hatch: all whatsits share `id` 8 and are distinguished
**only by subtype** (`node.whatsits()` maps them). Frontend subtypes: `open`
(0), `write` (1), `close` (2), `special` (3), `save_pos` (7), `late_lua` (8),
`user_defined` (9). PDF backend subtypes: `pdf_literal` (16), `pdf_annot`
(19), `pdf_start_link` (20), `pdf_end_link` (21), `pdf_dest` (22),
`pdf_colorstack` (29), `pdf_setmatrix` (30), `pdf_save` (31), `pdf_restore`
(32), etc.

Whatsits have **no dimensions** — the line breaker and packers step over
them. Packages use them to smuggle rendering instructions to the backend
(color, hyperlinks, literal PDF code). A display-list extractor must decide
which whatsits to interpret (e.g. `pdf_colorstack` for color changes,
`pdf_literal` for raw graphics) and which to ignore. `user_defined` whatsits
(§8.4.4) carry a `user_id`, a `type` and a `value` (number/node/string/token
table) and are ignored by the engine entirely — ideal for private tagging of
positions in the list.

### 1.11 Supporting types you will meet in a broken paragraph

* **local_par** (§8.2.14): inserted at the start of every paragraph; carries
  the paragraph direction and `\localleftbox`/`\localrightbox`. Zero width.
  *Verified*: it is the first node of the first line after line breaking.
* **dir** (§8.2.15): direction change markers (`+TLT` push / `-TLT` pop).
* **boundary** (§8.2.13): `\noboundary`, `\boundary`, `\protrusionboundary`,
  `\wordboundary`; zero-width markers.
* **margin_kern** (§8.2.16): produced by character **protrusion** — see
  [§5.4](#54-protrusion-margin-kerns).
* **math** (§8.2.8): on/off markers for inline math (`subtype` 0 =
  beginmath, 1 = endmath) with `surround` or glue fields.

---

## 2. Node attributes

Attributes are LuaTeX's mechanism for tagging nodes with numbered values so
Lua callbacks can find them later (§2.3).

* **TeX side**: `\attribute⟨16-bit number⟩ = ⟨32-bit value⟩` and
  `\attributedef\name = ⟨number⟩` behave like count registers and obey
  grouping (§2.3.2). The value −"7FFFFFFF (−2147483647) means "unset";
  all attributes start unset.
* **Lua side**: `tex.attribute[i] = v` / `tex.setattribute(i, v)` set the
  current value; `node.set_attribute(n, id, val)`, `node.get_attribute(n,
  id)`, `node.has_attribute(n, id [, val])`, `node.unset_attribute(n, id)`,
  `node.find_attribute(n, id)` operate on individual nodes (§8.9.4–8.9.8).

**Inheritance rule**: every node receives the list of attributes *in effect
at the moment the node is created* (§2.3.3). This moment can be
asynchronous: line boxes made by the paragraph builder get the attributes in
effect at `\par` time, not those of the text in the line. Nodes created
during hyphenation/ligaturing/kerning borrow attributes from surrounding
glyphs. Boxes can be given explicit attributes with the `attr` keyword:
`\hbox attr 999 = 789 to 2cm {...}`.

Internally the `attr` field points to a shared, reference-counted
`attribute_list` node chaining `attribute` nodes (`number`, `value`)
(§8.9.2–8.9.3); always use the helper functions rather than editing these.
`node.current_attr()` returns the currently active list so you can stamp
newly created nodes with the ambient attributes (§8.7.12).

This is exactly how packages coordinate with their callbacks: e.g.
`luacolor` assigns a color attribute to every node and converts it to
`pdf_colorstack` whatsits at shipout; `lua-widow-control` tags paragraphs;
microtype coordinates letterspacing this way. For texlode-style tooling,
attributes are the natural way to map nodes back to source positions
(attribute value = source offset/id), since attributes survive hyphenation,
line breaking and packing.

---

## 3. The node library

(§8.7 — only the parts that matter for display-list extraction.)

### Traversal

```lua
for n in node.traverse(head) do ... end            -- every node
for n in node.traverse_id(node.id("glyph"), head) do ... end
for n, char, font in node.traverse_char(head) do ... end  -- glyphs w/ subtype < 256
for n, char, font in node.traverse_glyph(head) do ... end -- all glyphs
for n, id, sub, list in node.traverse_list(head) do ... end -- hlists+vlists
```

`node.traverse` (§8.7.22) is an iterator over `next` pointers; you may
mutate the list while traversing if you keep the links valid. The iterators
also return extra values (`id`, `subtype`) that save field lookups.

* `node.slide(n)` (§8.7.18): returns the tail **and** rebuilds all `prev`
  pointers — call it before walking backwards. (After some callbacks the
  engine re-slides lists automatically; `node.fix_node_lists(false)` turns
  that off, §8.9.9.)
* `node.tail(n)` (§8.7.19): tail without touching `prev`.
* `node.length(n [, m])`, `node.count(id, n [, m])` (§8.7.20).

### Memory and copying

* `node.new(id [, subtype])` — create (fields zero/nil) (§8.7.8).
* `node.copy(n)` / `node.copy_list(n [, stop])` — deep copy including nested
  box contents; `next` of a single copy is nil (§8.7.10).
* `node.free(n)` (returns the next node), `node.flush_node(n)`,
  `node.flush_list(head)` (§8.7.9). No refcounting, no GC — dangling `next`
  pointers crash the engine, so a display-list extractor that only *reads*
  the lists (texlode's case) should copy nothing and free nothing.
* `node.remove(head, cur)`, `node.insert_before/after(head, cur, new)`
  (§8.7.28–8.7.30).

### Packing and measuring

* `node.hpack(head [, size, "exactly"|"additional" [, dir]])` → `box,
  badness` — runs TeX's hbox packer, computing `width/height/depth` and the
  `glue_set/glue_sign/glue_order` of a new hlist wrapping `head` (§8.7.13).
  Caveats: the box shares the list (freeing one invalidates the other), and
  it can update `\mark`s and inserts.
* `node.vpack(...)` — same for vboxes (§8.7.14).
* `node.dimensions(head [, tail])` → `w, h, d` — natural dimensions of a
  (sub)list. The crucial overload
  `node.dimensions(glue_set, glue_sign, glue_order, head [, tail])`
  measures a segment *as set inside a packed parent*, i.e. with set glue
  resolved — this is the engine-blessed way to compute the x-extent of a
  range of nodes within a line (§8.7.16). `node.rangedimensions(parent,
  first [, last])` is the convenient variant that reads the glue settings
  off `parent` directly.
* `node.effective_glue(g, parent [, round])` — effective width of one glue
  node inside `parent` (§8.2.9).

Beware: `hpack`/`dimensions` are among the few places TeX uses floating
point (`glue_set`), so re-derived widths can differ from packed widths by
rounding (§8.7.16). *Verified*: accumulating widths by hand reproduces
`line.width` to < 1 sp (see [§5](#5-walking-a-post-line-break-paragraph)).

### Direct access: `node.direct.*` (§8.10)

Internally a node is just an index into a memory array. The userdata
interface wraps that index in a heap-allocated, metatable-dispatched object;
every `n.field` access is a metamethod call. The `node.direct` sub-library
exposes the raw integers instead:

```lua
local d = node.direct
local dh = d.todirect(head)           -- userdata -> integer
for g in d.traverse_id(glyph_id, dh) do
  local w  = d.getwidth(g)
  local ch = d.getchar(g)
  ...
end
head = d.tonode(dh)                   -- integer -> userdata
```

Why it is faster: no userdata allocation per visited node, no metatable
lookup chain per field access — just function calls on integers with less
checking (which is also why it is less safe: a stale integer index silently
corrupts node memory). Dedicated getters (`getnext`, `getprev`, `getboth`,
`getid`, `getsubtype`, `getfont`, `getchar`, `getwhd`, `getdisc`, `getlist`,
`getglue`, `getoffsets`, `getfield`, …) and matching setters exist; the
manual's advice is to use the indexed userdata model unless nodes are
accessed "millions of times" (§8.10).

*Verified* microbenchmark
([`direct-bench.lua`](../experiments/02-node-dump/direct-bench.lua)):
traversing 50 000 glyph nodes × 20 reps, summing `id` + `char`:
userdata 0.115 s vs direct 0.075 s ≈ **1.5× faster**; the gap grows with
more field accesses per node. For a texlode-style extractor that touches
every glyph of a paragraph on every keystroke, `node.direct` is the right
choice.

---

## 4. Callbacks that rewrite node lists

Registration (§9.1): `callback.register(name, fn)`; unregister with `nil`;
registering `false` *disables the built-in behaviour* (dangerous for
`linebreak_filter` — deadcycles). Callbacks are global. `callback.list()`
enumerates them. Under LaTeX you must go through
`luatexbase.add_to_callback(name, fn, description)` instead, which
multiplexes several handlers per callback and knows each callback's
composition type (`list`, `data`, `exclusive`, `simple`).

### 4.1 The pipeline, verified

Order in which the node-list callbacks fire for a one-paragraph document
containing an embedded `\hbox` (*verified*,
[`callback-order.tex`](../experiments/02-node-dump/callback-order.tex)):

```
01 buildpage_filter      info=new_graf          paragraph starts
02 hpack_filter          groupcode=hbox         the explicit \hbox is packed
03 pre_linebreak_filter  groupcode=<empty>      full horizontal list, main vertical list
   (built-in line breaker runs; NO hpack_filter for the line boxes)
04 contribute_filter     info=box               line 1 appended to enclosing vlist
05 contribute_filter     info=pre_box           interline glue for line 2
06 contribute_filter     info=box               line 2 ...
...
11 post_linebreak_filter groupcode=<empty>      the finished stack of lines
12 buildpage_filter      info=hmode_par         page builder moves material to page
13 buildpage_filter      info=end               end of job triggers page completion
14 pre_output_filter     groupcode=output       page material vpacked into \box255
15 hpack_filter / vpack_filter ...              boxes built by the output routine
   (LaTeX only: pre_shipout_filter on the shipout box)
18 buildpage_filter      info=after_output      after the output routine
```

Key structural facts:

* `hpack_filter` is **not** called for the line boxes the par builder makes
  ("math items and line boxes are ignored at the moment", §9.5.8) — if you
  want the lines, use `post_linebreak_filter` (or `linebreak_filter`).
* Everything from paragraph text to page is: horizontal list →
  (`pre_linebreak_filter`) → line breaker (`linebreak_filter` replaces it)
  → lines appended (`contribute_filter`, `append_to_vlist_filter`) →
  (`post_linebreak_filter`) → page builder (`buildpage_filter`) → `\box255`
  packing (`pre_output_filter`) → output routine → `\shipout`.

### 4.2 The filters, one by one (§9.5)

Common return protocol for the node filters (§9.5.4): return **`true`** =
"I'm done, keep the list I was given (including in-place mutations)";
return a **node** = replace the head; return **`false`** = discard and flush
the whole list. All of these *mix with* internal code (they do not replace
it), except `linebreak_filter`.

**`pre_linebreak_filter(head, groupcode)`** (§9.5.4) — called just before
the line breaker, *after* `\parfillskip` has been appended. `head` is the
paragraph's complete horizontal list (starting with a `local_par` node);
hyphenation, ligaturing and kerning have *not* run yet at this point (they
run inside the line breaker as needed). `groupcode` values: `""` (main
vertical list), `hbox`, `adjusted_hbox`, `vbox`, `vtop`, `align`, `disc`,
`insert`, `vcenter`, `local_box`, `split_off`, `split_keep`, `align_set`,
`fin_row` — though only a subset can occur here; the full set is shared with
`hpack_filter`/`vpack_filter`.

**`linebreak_filter(head, is_display)`** (§9.5.5) — **replaces** the line
breaker. Receives the same list as `pre_linebreak_filter` (after it);
`is_display` is true when the paragraph is interrupted by display math. Must
return the head of a vertical-mode list containing at least one hbox, or
a fatal error results. The usual pattern is to call the built-in breaker
explicitly via `tex.linebreak(head, params)` (§10.3.17.3), which returns the
list of lines plus an info table (`prevdepth`, `prevgraf`, `looseness`,
`demerits`). `tex.linebreak` is texlode-relevant: it lets you re-break a
single paragraph *outside* the normal typesetting flow — feed it a copied
horizontal list and harvest positioned lines without touching the page.

**`post_linebreak_filter(head, groupcode)`** (§9.5.7) — called just after
the paragraph has been converted into a stack of boxes. *Verified*, `head`
is a vertical list of:

* `hlist` nodes, subtype 1 (the lines), each `\hsize` wide with its
  `glue_set/glue_sign/glue_order` already computed;
* `penalty` nodes, subtype 1 (`linebreakpenalty`), between lines;
* `glue` nodes, subtype 2 (`baselineskip`) / 1 (`lineskip`), between lines;
* possibly `ins`, `mark`, `whatsit` nodes that migrated out of the lines.

This is **the** hook for display-list extraction: the earliest moment where
every glyph's horizontal position within a line is fully determined.

**`hpack_filter(head, groupcode, size, packtype [, dir] [, attributelist])`**
(§9.5.8) — called whenever TeX is about to package a horizontal list into
an hbox (explicit `\hbox`, alignment cells, output-routine boxes...).
`packtype` is `"exactly"` or `"additional"` (`\hbox to` vs `\hbox spread`)
and `size` the corresponding dimension in sp. The list you get is *unpacked*
(no width/glue_set yet); the packer runs after your filter returns.

**`vpack_filter(head, groupcode, size, packtype, maxdepth [, dir]
[, attributelist])`** (§9.5.9) — the vertical twin (with `\maxdepth`).
Not called for the main vertical list (that's `buildpage_filter` /
`pre_output_filter` territory).

**`hpack_quality(incident, detail, head, first, last)`** (§9.5.10) — fires
when packing produced an over/underfull/loose/tight hbox instead of (or, per
source, before) the console warning: `incident` ∈ {"overfull", "underfull",
"loose", "tight"}, `detail` = overflow in sp or badness, `head` = the packed
box, `first`/`last` = source line numbers. May return a node (e.g. a rule)
to append to the box. `vpack_quality` (§9.5.11) is analogous.

**`buildpage_filter(extrainfo)`** (§9.5.2) — called whenever the page
builder is about to move material from the contribution list to the current
page. Receives only a string (`alignment`, `after_output`, `new_graf`,
`vmode_par`, `hmode_par`, `insert`, `penalty`, `before_display`,
`after_display`, `end`); no node list and no return value — you manipulate
`tex.lists.contrib_head` yourself.

**`contribute_filter(extrainfo)`** (§9.5.1) — like `buildpage_filter` but at
the moment material is added to a list: `pre_box` (interline material),
`box` (a typeset box; always called), `adjust`. String argument only.

**`pre_output_filter(head, groupcode, size, packtype, maxdepth [, dir])`**
(§9.5.13) — called when TeX is ready to vpack the accumulated page material
into `\box255` for `\output`. *Verified* `groupcode` = `"output"`. Same
return protocol as the other filters. This is the last engine hook that sees
the page as a plain vertical list before the output routine decorates it.

**`pre_shipout_filter(head)`** — **not an engine callback in LuaTeX**.
*Verified*: `callback.list()` in LuaTeX 1.22 does not contain it, and the
v1.24 manual does not document it (it is sometimes described as an engine
callback added around LuaTeX 1.15 — that does not hold for the TeX Live 2025
binary). Under LaTeX (since the 2020-10 kernel) it
is a **luatexbase user callback created in `latex.ltx`**: the kernel wraps
`\shipout`, and just before shipping calls
`luatexbase.call_callback('pre_shipout_filter', head)` with the *fully
assembled shipout box*; the return value replaces the box (type `list`).
Registered via `luatexbase.add_to_callback("pre_shipout_filter", fn, "…")`.
This is where `luaotfload`, `lua-widow-control` etc. do final-page node
processing, and it sees absolutely everything that will be rendered —
but only once per page, with all positions still *relative* (nested boxes).

Related non-filter hooks worth knowing: `hyphenate`, `ligaturing`,
`kerning` (§9.5.14–16) replace those passes (head+tail arguments, no return
value); `insert_local_par` (§9.5.17) fires when a paragraph's `local_par`
node is created; `append_to_vlist_filter` (§9.5.6) intercepts every box
appended to a vertical list (this is where interline glue decisions can be
overridden); `mlist_to_hlist` (§9.5.18) replaces math typesetting.

---

## 5. Walking a post-line-break paragraph

This is the mechanism texlode uses: after the line breaker has run, each
line is an `hlist` whose content positions are fully determined by node
widths plus resolved glue. Walking the list and accumulating x yields a
display list of `(glyph, font, x, y)` in scaled points — no PDF generation
needed.

Full working code:
[`node-dump.lua`](../experiments/02-node-dump/node-dump.lua) (plain LuaTeX,
run via `node-dump.tex`). *Verified*: the accumulated x equals `line.width`
to well under 1 sp on every line, for both stretched and shrunk lines.

### 5.1 Resolving set glue

When `hpack` packs a line to `\hsize`, it stores the *solution* of the
packing problem on the hlist: `glue_sign` (are we stretching or shrinking?),
`glue_order` (which infinity order won?), `glue_set` (the ratio). Each glue
node's effective width is then:

```lua
local function effective_glue_width(g, parent)
  local w = g.width
  if parent.glue_sign == 1 and g.stretch_order == parent.glue_order then
    w = w + parent.glue_set * g.stretch          -- stretching
  elseif parent.glue_sign == 2 and g.shrink_order == parent.glue_order then
    w = w - parent.glue_set * g.shrink           -- shrinking
  end
  return w
end
```

Notes:

* Glue whose order does not match `glue_order` stays at natural width
  (finite stretch is irrelevant once a `fil` is present, etc.).
* For finite glue, `glue_set` is the fraction of each glue's own
  stretch/shrink used (e.g. 0.6079 = every space is stretched by 60.79 % of
  its `stretch`). For infinite orders, `glue_set` is sp-per-unit-fil
  (*verified*: a `\parfillskip` of `0pt plus 1fil` with
  `glue_set = 125.2776` contributes 125.2776 pt).
* An overfull box has `glue_sign = 2` and `glue_set` clamped to 1.0.
* `glue_set` is a float — the one place rounding can bite. Per-glue results
  agree with `node.effective_glue` exactly (*verified*).
* Equivalent engine-side helper for a whole range:
  `node.dimensions(parent.glue_set, parent.glue_sign, parent.glue_order,
  first, last)`.

### 5.2 The accumulation loop

For each line (`hlist`, subtype 1) walk `line.head` with a cursor `x`
starting at 0 (add `line.shift` for hanging indentation; the indent box of
an indented paragraph appears as an `hlist` subtype 3 *inside the first
line*, and `\leftskip`/`\rightskip` appear as glue subtypes 8/9):

| node | contribution to x | renders? |
|---|---|---|
| `glyph` | `width` × (1 + `expansion_factor`/1 000 000), rounded | yes: glyph `char` from `font` at (x + `xoffset`, baseline − `yoffset`) |
| `glue` | effective width (§5.1) | leaders only |
| `kern` | `kern` + `expansion_factor` (sp) | no |
| `margin_kern` | `width` (negative at the margins) | no |
| `disc` | `node.dimensions(disc.replace)` | render the replace list |
| `hlist`/`vlist` | `width`; recurse into `head` with the child's own glue triple, y shifted by `shift` | children |
| `rule` | `width` (resolve running dims from the parent) | yes |
| `penalty`, `mark`, `boundary`, `local_par`, `dir` | 0 | no |
| `whatsit` | 0 | backend effects (color, links, literals) |
| `math` | `surround`, or glue width when it carries glue | no |

Vertical positioning comes from the enclosing vertical list: maintain a y
cursor; for each line, `y += height` gives the baseline, then `y += depth`,
plus the effective widths of the interline `baselineskip`/`lineskip` glue
(against the *page* box's glue triple) and any inter-line penalties (zero
height). Glyphs sit on the line's baseline; a box's `shift` moves it
down (hlist in vlist: right) by `shift` sp.

Font metrics beyond the glyph node's own `width`/`height`/`depth` come from
`font.getfont(fontid)`, which returns the font table: `name`, `size`
(design size scaled, in sp), `characters[char]` with per-glyph `width`,
`height`, `depth`, `italic`, `kerns`, `ligatures`, `index` (the OpenType
glyph index — what a renderer actually needs to rasterize), `tounicode`.
For rendering you map `(font, char)` → font file + glyph index once, and
cache; the node list itself already contains everything positional.

### 5.3 Font expansion (microtype hz)

With `microtype` expansion enabled (`\adjustspacing = 2`, `\expandglyphsinfont`),
the paragraph builder may expand or shrink all glyphs of a line by a common
ratio to improve breaks. This shows up **per glyph** in `expansion_factor`
(§8.2.12: "assigned in the par builder and used in the backend").

*Verified* semantics (experiments
[`microtype-dump.tex`](../experiments/02-node-dump/microtype-dump.tex),
[`expansion-verify.tex`](../experiments/02-node-dump/expansion-verify.tex),
confirmed in LuaTeX source `packaging.c`/`directions.c`):

* **glyph.expansion_factor is in millionths of the glyph width.** The
  effective advance used by both the packer and the backend is
  `round(width × (1 000 000 + ef) / 1 000 000)` (`pack_width()`:
  `ext_xn_over_d(wd, 1000000+ex_glyph, 1000000)`). E.g. `ef = 30000` ⇒
  +3 %: a 10.278 pt "W" advances 10.5863 pt.
* The value already folds in everything: the line's chosen expansion ratio
  (±1000ths of the font's max), the font's `stretch`/`shrink` limits
  (per-mille, e.g. microtype `stretch=30`), and the per-character `\efcode`
  (per-mille), quantized to the font's expansion `step`
  (`do_subst_font()`: `ex_ratio × efcode × max_stretch / 10⁶`, fixed to the
  step grid, × 1000). So with plain `\efcode` = 1000 everywhere,
  `ef = 30000` means "this line is at the full +3 % stretch";
  `ef = −12000` means "shrunk by 1.2 %". All glyphs of a line share the
  same `ef` unless their `\efcode`s differ.
* **kern.expansion_factor is an absolute correction in sp**, not a ratio
  (`packaging.c`: `ex_kern(p) = kern_stretch(p); x += k`). Effective kern
  advance = `kern + expansion_factor`. *Verified*: a −0.2778 pt font kern in
  a stretched line carries `ef = −546` sp (= 3 % of its width); in a shrunk
  line `ef = −1092` sp (6 %). Note it is the *full* stretch/shrink at the
  font's limit, applied whenever the line stretches/shrinks at all — the
  manual's own comment calls these values "noise" for the backend, but you
  must include them to reproduce `line.width` exactly.
* With both formulas applied, accumulated x matches `line.width` with
  **delta = 0 sp** on every line (*verified*).
* `\adjustspacing = 3` expands glyphs only (no kerns); `= 1` applies
  expansion after breaking (pdfTeX-compatible breaks).

### 5.4 Protrusion (margin kerns)

With `\protrudechars` > 0 (microtype protrusion), characters at line edges
protrude into the margin via `\lpcode`/`\rpcode`. In the node list this
materializes as **`margin_kern` nodes** (id 28, §8.2.16) at the very start
and/or end of a line:

| field | meaning |
|---|---|
| `subtype` | 0 = left, 1 = right |
| `width` | the kern advance (negative to pull the glyph outward) |
| `glyph` | the glyph node the protrusion was computed for |

*Verified*: with microtype, line 1 of a German test paragraph starts with
`margin_kern subtype=0 width=-0.56pt glyph="W"` and ends with
`margin_kern subtype=1 width=-0.30pt glyph="t"`. For position computation
they are ordinary kerns: add `width` to x. Their effect is that the first
glyph starts at slightly negative x (protrudes into the left margin) and the
line's content overshoots `\hsize` on the right by the right protrusion.

### 5.5 What this buys texlode

`post_linebreak_filter` (or a manual `tex.linebreak` call on a copied
horizontal list) plus the §5.1–5.4 arithmetic yields, per paragraph, a flat
array of `(font, glyph, x, y)` records in scaled points — a resolution-
independent display list. Because the walk is pure reading of already-
computed integers (via `node.direct` for speed), it costs microseconds per
paragraph; there is no need to run the page builder, output routine, or PDF
backend to know where every glyph goes. Re-typesetting one edited paragraph
and re-extracting its display list is exactly the ~1 ms path the texlode
preprint describes.

---

## 6. Scaled points and rendering coordinates

TeX's internal length unit is the **scaled point**:

* 1 pt = 65 536 sp (2¹⁶); all node dimensions (`width`, `height`, `kern`,
  `glue.width`, …) are integers in sp.
* 1 in = 72.27 pt = 4 736 286.72 sp; 1 bp (PostScript/PDF point) =
  65 781.76 sp; 1 mm ≈ 186 467.98 sp.
* Maximum dimension: 16383.99998 pt = 2³⁰ − 1 sp ≈ 5.75 m; the "running
  dimension" sentinel is −2³⁰ = −1073741824.
* `tex.sp("10.5pt")` converts a dimension string to sp; `number/65536`
  gives printable pt.

Conversion to rendering coordinates:

```lua
local SP_PER_PT = 65536
local PX_PER_PT = dpi / 72.27          -- device pixels per TeX point
local function sp_to_px(sp) return sp * PX_PER_PT / SP_PER_PT end
```

For PDF output the backend converts sp to bp (1 bp = 72 / 72.27 pt); for a
screen renderer you go straight from sp to device pixels with one
multiplication, choosing the origin (TeX's page origin is conventionally
1 in from the top-left; a display-list renderer can pick its own). Since sp
values are integers, positions are exact and device-independent —
accumulating x in Lua floats is safe (doubles hold integers up to 2⁵³) but
the expansion formula's rounding (§5.3) must be applied per glyph to stay
bit-identical with the engine.

---

## Experiment inventory

All in [`experiments/02-node-dump/`](../experiments/02-node-dump/):

| file | what it shows |
|---|---|
| `node-dump.tex` + `node-dump.lua` | plain-LuaTeX `post_linebreak_filter` dump: every node with type/subtype/width, x accumulated via glue_set arithmetic; matches `line.width` exactly on stretched and shrunk lines |
| `callback-order.tex` + `callback-order.lua` (+ `-nolb` variants) | firing order of all node-list callbacks, with and without a custom `linebreak_filter` |
| `microtype-dump.tex` | expansion_factor on glyphs and margin_kern nodes with microtype expansion+protrusion |
| `expansion-solve.tex` | solves for the actual expansion ratio from packed lines (used to falsify the naive formulas) |
| `expansion-verify.tex` | final source-derived formulas verified to 0 sp; also demonstrates reading `\efcode` from Lua |
| `direct-bench.tex` + `direct-bench.lua` | userdata vs `node.direct` traversal benchmark (~1.5×) |

Manual: LuaTeX Reference Manual v1.24 (Dec 2025) — Nodes ch. 8, Callbacks
ch. 9, TeX library ch. 10 (`tex.linebreak` §10.3.17.3), Attributes §2.3,
hz/protrusion §3.1.4 & §5.2. A copy is checked into
[`sources/luatex-manual-1.24.pdf`](../sources/luatex-manual-1.24.pdf)
(TeX Live 2025 ships LuaTeX 1.22 but not the manual PDF; fetched from CTAN
`systems/doc/luatex/luatex.pdf`).
