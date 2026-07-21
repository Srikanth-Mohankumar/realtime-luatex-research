-- capture.lua — context-capture pass (blueprint step 1).
--
-- Runs inside the FULL compile of a real document (loaded by rtcapture.sty).
-- For every paragraph the line breaker processes, records:
--   * the complete layout-context parameter set Knuth-Plass depends on
--     (the locality property: notes/03 §1 — hsize, shape, font, language,
--     penalties/demerits, skips), captured at the moment linebreak runs;
--   * the font active at paragraph START (via insert_local_par — the value
--     at pre_linebreak time is the font at paragraph END, wrong for
--     re-typesetting from source);
--   * the \RTpara attribute (paragraph id planted in the source), so the
--     client can key recompiles to source paragraphs — the same
--     attribute-tagging technique lua-widow-control uses (notes/04);
--   * a reference SIGNATURE of the broken lines (signature.lua) — ground
--     truth the fast path must reproduce.
-- Writes <jobname>-capture.json at \AtEndDocument.

-- signature.lua lives next to this file. kpse finds it via TEXINPUTS
-- (production templates nil out the debug library, so no debug.getinfo).
local function locate(name)
  local p = kpse and kpse.find_file and kpse.find_file(name, "tex")
  if p then return p end
  local d = debug and debug.getinfo
            and debug.getinfo(1, "S").source:match("^@(.*)[/\\]")
  return (d or "../../engine") .. "/" .. name
end
local sig = dofile(locate("signature.lua"))

local M = { paras = {}, attr = luatexbase.new_attribute("RTpara") }

local start_stack = {}   -- font at paragraph start, LIFO across nesting
local current            -- record between pre_ and post_linebreak (atomic)

local GLYPH = node.id("glyph")
local HLIST = node.id("hlist")
local LOCAL_PAR = node.id("local_par")

function M.setpara(id)
  tex.setattribute(M.attr, id)
end

-- production markup: string paragraph ids ("para10") -> attribute ints
M.idmap, M.nextid = {}, 0
function M.setparaid(s)
  local n = M.idmap[s]
  if not n then
    M.nextid = M.nextid + 1
    n = M.nextid
    M.idmap[s] = n
  end
  tex.setattribute(M.attr, n)
end

local function glue_t(g)
  if not g then return { 0, 0, 0, 0, 0 } end
  return { g.width or 0, g.stretch or 0, g.stretch_order or 0,
           g.shrink or 0, g.shrink_order or 0 }
end

local function first_glyph(head)
  for n in node.traverse_id(GLYPH, head) do return n end
end

-- indent box: an hlist with empty list right after the local_par node
local function indent_width(head)
  for n in node.traverse(head) do
    if n.id ~= LOCAL_PAR then
      if n.id == HLIST and n.head == nil then return n.width end
      return nil
    end
  end
end

local function on_local_par(lp, location)
  start_stack[#start_stack + 1] = font.current()
  return lp
end

local function on_pre(head, groupcode)
  local fstart = table.remove(start_stack) or font.current()
  local g = first_glyph(head)
  local f = font.getfont(fstart) or font.fonts[fstart] or {}
  current = {
    idx = #M.paras + 1,
    group = groupcode,
    attr = g and node.get_attribute(g, M.attr) or nil,
    -- geometry & shape (END-of-paragraph values: what linebreak reads)
    hsize = tex.hsize,
    indent = indent_width(head),        -- nil = \noindent
    hangindent = tex.hangindent, hangafter = tex.hangafter,
    parshape = tex.parshape,            -- array of {indent, width} or nil
    looseness = tex.looseness,
    leftskip = glue_t(tex.leftskip), rightskip = glue_t(tex.rightskip),
    parfillskip = glue_t(tex.parfillskip),
    spaceskip = glue_t(tex.spaceskip), xspaceskip = glue_t(tex.xspaceskip),
    -- interline spacing: line breaking doesn't read these, but the vertical
    -- packing of the resulting lines does — without them the fast-path vbox
    -- gets the server session's leading, not the document's
    baselineskip = glue_t(tex.baselineskip), lineskip = glue_t(tex.lineskip),
    lineskiplimit = tex.lineskiplimit,
    -- badness / demerit parameters
    pretolerance = tex.pretolerance, tolerance = tex.tolerance,
    emergencystretch = tex.emergencystretch,
    linepenalty = tex.linepenalty,
    hyphenpenalty = tex.hyphenpenalty, exhyphenpenalty = tex.exhyphenpenalty,
    adjdemerits = tex.adjdemerits,
    doublehyphendemerits = tex.doublehyphendemerits,
    finalhyphendemerits = tex.finalhyphendemerits,
    -- microtype engine switches
    adjustspacing = tex.adjustspacing, protrudechars = tex.protrudechars,
    -- language (per-glyph in LuaTeX; take the first glyph's)
    lang = g and g.lang or tex.language,
    lhmin = g and g.left or tex.lefthyphenmin,
    rhmin = g and g.right or tex.righthyphenmin,
    uchyph = g and (g.uchyph and 1 or 0) or tex.uchyph,
    -- font at paragraph start
    font_id = fstart,
    font_name = f.name or "", font_size = f.size or 0,
    -- fingerprint: first ASCII glyph chars, for source matching
    fp = (function()
      local t = {}
      for n in node.traverse_id(GLYPH, head) do
        if n.char >= 33 and n.char <= 126 then
          t[#t + 1] = string.char(n.char)
          if #t >= 40 then break end
        end
      end
      return table.concat(t)
    end)(),
  }
  M.paras[current.idx] = current
  return true
end

local function on_post(head, groupcode)
  if current then
    current.sig = sig.vlist_sig(head)
    current = nil
  end
  return true
end

-- ---------------------------------------------------------------------
-- Page capture at shipout: absolute-positioned glyph display list per
-- page (the "page-position cache" of the architecture). Every glyph
-- carries its \RTpara attribute so the client can locate paragraphs on
-- the page and overlay live recompiles.

local pages = {}
local page_fonts = {}
local RULE = node.id("rule")
local GLUE = node.id("glue")
local KERN = node.id("kern")
local DISC = node.id("disc")
local VLIST = node.id("vlist")

local vwalk_abs  -- forward

local function hwalk_abs(head, x, y, set, sign, order, out)
  for n in node.traverse(head) do
    local adv = node.dimensions(set, sign, order, n, n.next)
    local id = n.id
    if id == GLYPH then
      page_fonts[n.font] = true
      out.g[#out.g + 1] = { n.char, x, y, n.font,
                            node.get_attribute(n, M.attr) or -1 }
    elseif id == DISC then
      if n.replace then hwalk_abs(n.replace, x, y, set, sign, order, out) end
    elseif id == HLIST then
      hwalk_abs(n.head, x, y + n.shift,
                n.glue_set, n.glue_sign, n.glue_order, out)
    elseif id == VLIST then
      vwalk_abs(n.head, x, y + n.shift - n.height,
                n.glue_set, n.glue_sign, n.glue_order, out)
    elseif id == RULE then
      -- subtype 2 = image (LuaTeX represents \includegraphics as an image
      -- rule); the renderer draws those as placeholders, not solid rules
      if n.width > 0 and n.width < 1073741824 then
        out.r[#out.r + 1] = { x, y - n.height, n.width, n.height + n.depth,
                              n.subtype or 0 }
      end
    end
    x = x + adv
  end
end

-- resolved width of vertical glue under the parent box's glue setting
local function vglue(g, set, sign, order)
  local w = g.width
  if sign == 1 and g.stretch_order == order then
    w = w + set * g.stretch
  elseif sign == 2 and g.shrink_order == order then
    w = w - set * g.shrink
  end
  return w
end

-- y is the TOP edge of the vertical material. NOTE: node.dimensions()
-- measures HORIZONTAL spans and must not be used here — vertical advance
-- is height+depth for boxes/rules, resolved width for glue, kern for
-- kerns (learned the hard way: pages rendered with everything at the
-- bottom because glue/box advances were nonsense).
vwalk_abs = function(head, x, y, set, sign, order, out)
  for n in node.traverse(head) do
    local id = n.id
    if id == HLIST then
      hwalk_abs(n.head, x + n.shift, y + n.height,
                n.glue_set, n.glue_sign, n.glue_order, out)
      y = y + n.height + n.depth
    elseif id == VLIST then
      vwalk_abs(n.head, x + n.shift, y,
                n.glue_set, n.glue_sign, n.glue_order, out)
      y = y + n.height + n.depth
    elseif id == RULE then
      if n.width > 0 and n.width < 1073741824 then
        out.r[#out.r + 1] = { x, y, n.width, n.height + n.depth,
                              n.subtype or 0 }
      end
      y = y + n.height + n.depth
    elseif id == GLUE then
      y = y + vglue(n, set, sign, order)
    elseif id == KERN then
      y = y + n.kern
    end
  end
end

local IN = 4736287  -- 1in in sp: TeX's page origin offset

local function on_shipout(head)
  local out = { g = {}, r = {} }
  -- head is the shipout box's content (or the box itself); walk whatever
  -- vertical material we find, origin at TeX's 1in+offset convention
  local x0 = IN + tex.hoffset
  local y0 = IN + tex.voffset
  for n in node.traverse(head) do
    if n.id == HLIST then
      hwalk_abs(n.head, x0 + n.shift, y0 + n.height,
                n.glue_set, n.glue_sign, n.glue_order, out)
      y0 = y0 + n.height + n.depth
    elseif n.id == VLIST then
      vwalk_abs(n.head, x0 + n.shift, y0,
                n.glue_set, n.glue_sign, n.glue_order, out)
      y0 = y0 + n.height + n.depth
    elseif n.id == GLUE then
      y0 = y0 + n.width
    elseif n.id == KERN then
      y0 = y0 + n.kern
    end
  end
  out.w = tex.pagewidth > 0 and tex.pagewidth or 39158276   -- a4 fallback
  out.h = tex.pageheight > 0 and tex.pageheight or 55380996
  pages[#pages + 1] = out
  return true
end

-- reset between body re-runs in the persistent convergence engine: same
-- callbacks, fresh capture state, fresh paragraph-id registry
function M.reset()
  M.paras, M.idmap, M.nextid = {}, {}, 0
  pages, page_fonts = {}, {}
  start_stack, current = {}, nil
  tex.setattribute(M.attr, -2147483647)   -- "unset"
end

function M.start()
  luatexbase.add_to_callback("insert_local_par", on_local_par, "rtcapture.localpar")
  luatexbase.add_to_callback("pre_linebreak_filter", on_pre, "rtcapture.pre")
  luatexbase.add_to_callback("post_linebreak_filter", on_post, "rtcapture.post")
  local ok = pcall(luatexbase.add_to_callback,
                   "pre_shipout_filter", on_shipout, "rtcapture.shipout")
  if not ok then
    texio.write_nl("RTCAPTURE: no pre_shipout_filter — page capture disabled")
  end
end

local function num_or_null(v) return v ~= nil and tostring(v) or "null" end

local function glue_json(t)
  return string.format("[%d,%d,%d,%d,%d]", t[1], t[2], t[3], t[4], t[5])
end

function M.finish(path)
  local out = {}
  for _, p in ipairs(M.paras) do
    local ps = "null"
    if p.parshape then
      local pp = {}
      for _, row in ipairs(p.parshape) do
        pp[#pp + 1] = string.format("[%d,%d]", row[1], row[2])
      end
      ps = "[" .. table.concat(pp, ",") .. "]"
    end
    out[#out + 1] = string.format(
      '{"idx":%d,"group":"%s","attr":%s,"hsize":%d,"indent":%s,'
      .. '"hangindent":%d,"hangafter":%d,"parshape":%s,"looseness":%d,'
      .. '"leftskip":%s,"rightskip":%s,"parfillskip":%s,"spaceskip":%s,'
      .. '"xspaceskip":%s,"baselineskip":%s,"lineskip":%s,'
      .. '"lineskiplimit":%d,"pretolerance":%d,"tolerance":%d,'
      .. '"emergencystretch":%d,"linepenalty":%d,"hyphenpenalty":%d,'
      .. '"exhyphenpenalty":%d,"adjdemerits":%d,"doublehyphendemerits":%d,'
      .. '"finalhyphendemerits":%d,"adjustspacing":%d,"protrudechars":%d,'
      .. '"lang":%d,"lhmin":%d,"rhmin":%d,"uchyph":%d,'
      .. '"font_name":%q,"font_size":%d,"fp":%q,"sig":%s}',
      p.idx, p.group or "", num_or_null(p.attr), p.hsize,
      num_or_null(p.indent), p.hangindent, p.hangafter, ps, p.looseness,
      glue_json(p.leftskip), glue_json(p.rightskip),
      glue_json(p.parfillskip), glue_json(p.spaceskip),
      glue_json(p.xspaceskip), glue_json(p.baselineskip),
      glue_json(p.lineskip), p.lineskiplimit, p.pretolerance, p.tolerance,
      p.emergencystretch, p.linepenalty, p.hyphenpenalty,
      p.exhyphenpenalty, p.adjdemerits, p.doublehyphendemerits,
      p.finalhyphendemerits, p.adjustspacing, p.protrudechars,
      p.lang, p.lhmin, p.rhmin, p.uchyph,
      p.font_name, p.font_size, p.fp, p.sig and sig.sig_json(p.sig) or "[]")
  end
  -- pages: absolute display lists from shipout
  local pparts = {}
  local function I(v) return math.floor(v + 0.5) end  -- glue math yields floats
  for _, pg in ipairs(pages) do
    local gp, rp = {}, {}
    for _, g in ipairs(pg.g) do
      gp[#gp + 1] = string.format("[%d,%d,%d,%d,%d]", g[1], I(g[2]), I(g[3]), g[4], g[5])
    end
    for _, r in ipairs(pg.r) do
      rp[#rp + 1] = string.format("[%d,%d,%d,%d,%d]",
        I(r[1]), I(r[2]), I(r[3]), I(r[4]), r[5] or 0)
    end
    pparts[#pparts + 1] = string.format(
      '{"w":%d,"h":%d,"g":[%s],"r":[%s]}',
      pg.w, pg.h, table.concat(gp, ","), table.concat(rp, ","))
  end
  local fparts = {}
  for id in pairs(page_fonts) do
    local f = font.getfont(id) or font.fonts[id] or {}
    fparts[#fparts + 1] = string.format('"%d":{"name":%q,"size":%d}',
      id, f.name or "", f.size or 655360)
  end
  local idparts = {}
  for s, n in pairs(M.idmap) do
    idparts[#idparts + 1] = string.format('"%d":%q', n, s)
  end
  local fh = io.open(path, "w")
  fh:write('{"paras":[\n' .. table.concat(out, ",\n") .. "\n],\n")
  fh:write('"ids":{' .. table.concat(idparts, ",") .. '},\n')
  fh:write('"fonts":{' .. table.concat(fparts, ",") .. '},\n')
  fh:write('"pages":[\n' .. table.concat(pparts, ",\n") .. "\n]}\n")
  fh:close()
  texio.write_nl("term and log",
    string.format("RTCAPTURE: wrote %d paragraphs, %d pages to %s",
                  #M.paras, #pages, path))
end

return M
