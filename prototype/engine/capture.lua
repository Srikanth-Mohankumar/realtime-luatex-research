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

local sig = dofile("../../engine/signature.lua")

local M = { paras = {}, attr = luatexbase.new_attribute("RTpara") }

local start_stack = {}   -- font at paragraph start, LIFO across nesting
local current            -- record between pre_ and post_linebreak (atomic)

local GLYPH = node.id("glyph")
local HLIST = node.id("hlist")
local LOCAL_PAR = node.id("local_par")

function M.setpara(id)
  tex.setattribute(M.attr, id)
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

function M.start()
  luatexbase.add_to_callback("insert_local_par", on_local_par, "rtcapture.localpar")
  luatexbase.add_to_callback("pre_linebreak_filter", on_pre, "rtcapture.pre")
  luatexbase.add_to_callback("post_linebreak_filter", on_post, "rtcapture.post")
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
      .. '"xspaceskip":%s,"pretolerance":%d,"tolerance":%d,'
      .. '"emergencystretch":%d,"linepenalty":%d,"hyphenpenalty":%d,'
      .. '"exhyphenpenalty":%d,"adjdemerits":%d,"doublehyphendemerits":%d,'
      .. '"finalhyphendemerits":%d,"adjustspacing":%d,"protrudechars":%d,'
      .. '"lang":%d,"lhmin":%d,"rhmin":%d,"uchyph":%d,'
      .. '"font_name":%q,"font_size":%d,"fp":%q,"sig":%s}',
      p.idx, p.group or "", num_or_null(p.attr), p.hsize,
      num_or_null(p.indent), p.hangindent, p.hangafter, ps, p.looseness,
      glue_json(p.leftskip), glue_json(p.rightskip),
      glue_json(p.parfillskip), glue_json(p.spaceskip),
      glue_json(p.xspaceskip), p.pretolerance, p.tolerance,
      p.emergencystretch, p.linepenalty, p.hyphenpenalty,
      p.exhyphenpenalty, p.adjdemerits, p.doublehyphendemerits,
      p.finalhyphendemerits, p.adjustspacing, p.protrudechars,
      p.lang, p.lhmin, p.rhmin, p.uchyph,
      p.font_name, p.font_size, p.fp, p.sig and sig.sig_json(p.sig) or "[]")
  end
  local fh = io.open(path, "w")
  fh:write('{"paras":[\n' .. table.concat(out, ",\n") .. "\n]}\n")
  fh:close()
  texio.write_nl("term and log",
    string.format("RTCAPTURE: wrote %d paragraphs to %s", #M.paras, path))
end

return M
