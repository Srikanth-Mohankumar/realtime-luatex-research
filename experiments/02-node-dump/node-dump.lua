-- node-dump.lua
-- Registers a post_linebreak_filter that dumps every node of every line
-- produced by the line breaker, and recomputes glyph x-positions by walking
-- the hlist and resolving set glue (glue_set / glue_sign / glue_order).
--
-- Run with:  luatex --interaction=nonstopmode node-dump.tex

local SP = 65536  -- scaled points per TeX point

local function pt(sp)
  return string.format("%.4fpt", sp / SP)
end

-- Resolve the effective width of a glue node inside a packed parent box.
-- This is the same arithmetic the backend uses (and what node.effective_glue
-- does for you).
local function effective_glue_width(g, parent)
  local w = g.width
  local sign  = parent.glue_sign    -- 0 = normal, 1 = stretching, 2 = shrinking
  local set   = parent.glue_set     -- float ratio computed by hpack
  local order = parent.glue_order   -- 0 = finite, 1..4 = fil...filll
  if sign == 1 and g.stretch_order == order then
    w = w + set * g.stretch
  elseif sign == 2 and g.shrink_order == order then
    w = w - set * g.shrink
  end
  return w
end

local GLYPH   = node.id("glyph")
local GLUE    = node.id("glue")
local KERN    = node.id("kern")
local HLIST   = node.id("hlist")
local VLIST   = node.id("vlist")
local RULE    = node.id("rule")
local DISC    = node.id("disc")
local PENALTY = node.id("penalty")
local MKERN   = node.id("margin_kern")
local WHATSIT = node.id("whatsit")

local function dump_line(line, lineno)
  texio.write_nl(string.format(
    "LINE %d: hlist subtype=%d width=%s height=%s depth=%s glue_set=%.6f glue_sign=%d glue_order=%d",
    lineno, line.subtype, pt(line.width), pt(line.height), pt(line.depth),
    line.glue_set, line.glue_sign, line.glue_order))

  local x = 0   -- cursor in sp, relative to the left edge of the line box
  for n in node.traverse(line.head) do
    local t = node.type(n.id)
    if n.id == GLYPH then
      texio.write_nl(string.format(
        "  x=%-12s glyph  char=U+%04X %q font=%d width=%s xoffset=%s yoffset=%s ef=%d",
        pt(x), n.char, utf8.char(n.char), n.font, pt(n.width),
        pt(n.xoffset), pt(n.yoffset), n.expansion_factor))
      -- expansion_factor is in thousandths of an efcode-permille:
      -- effective advance = width * (1 + expansion_factor/1000000)
      x = x + n.width * (1 + n.expansion_factor / 1000000)
    elseif n.id == GLUE then
      local ew = effective_glue_width(n, line)
      texio.write_nl(string.format(
        "  x=%-12s glue   subtype=%d width=%s stretch=%s shrink=%s -> effective=%s (node.effective_glue=%s)",
        pt(x), n.subtype, pt(n.width), pt(n.stretch), pt(n.shrink),
        pt(ew), pt(node.effective_glue(n, line))))
      x = x + ew
    elseif n.id == KERN then
      texio.write_nl(string.format("  x=%-12s kern   subtype=%d kern=%s",
        pt(x), n.subtype, pt(n.kern)))
      x = x + n.kern
    elseif n.id == MKERN then
      texio.write_nl(string.format("  x=%-12s margin_kern subtype=%d width=%s glyph=U+%04X",
        pt(x), n.subtype, pt(n.width), n.glyph.char))
      x = x + n.width
    elseif n.id == DISC then
      -- After line breaking only unbroken discs remain inline; their
      -- 'replace' text contributes to the line width.
      local w = n.replace and node.dimensions(n.replace) or 0
      texio.write_nl(string.format("  x=%-12s disc   subtype=%d replace-width=%s",
        pt(x), n.subtype, pt(w)))
      x = x + w
    elseif n.id == HLIST or n.id == VLIST then
      texio.write_nl(string.format("  x=%-12s %s  subtype=%d width=%s shift=%s",
        pt(x), t, n.subtype, pt(n.width), pt(n.shift)))
      x = x + n.width
    elseif n.id == RULE then
      texio.write_nl(string.format("  x=%-12s rule   width=%s", pt(x), pt(n.width)))
      x = x + n.width
    elseif n.id == PENALTY then
      texio.write_nl(string.format("  x=%-12s penalty value=%d", pt(x), n.penalty))
    elseif n.id == WHATSIT then
      texio.write_nl(string.format("  x=%-12s whatsit subtype=%d (%s)",
        pt(x), n.subtype, node.whatsits()[n.subtype] or "?"))
    else
      texio.write_nl(string.format("  x=%-12s %s subtype=%d", pt(x), t, n.subtype or -1))
    end
  end
  texio.write_nl(string.format(
    "  END: accumulated x=%s vs hlist width=%s (delta=%.2fsp)",
    pt(x), pt(line.width), x - line.width))
end

luatexbase = luatexbase  -- nil under plain luatex; we register directly
callback.register("post_linebreak_filter", function(head, groupcode)
  texio.write_nl("== post_linebreak_filter, groupcode=" .. tostring(groupcode) .. " ==")
  local lineno = 0
  for n in node.traverse(head) do
    local t = node.type(n.id)
    if n.id == HLIST then
      lineno = lineno + 1
      dump_line(n, lineno)
    else
      texio.write_nl(string.format("(vertical) %s subtype=%d %s",
        t, n.subtype or -1,
        n.id == GLUE and ("width=" .. pt(n.width)) or
        n.id == PENALTY and ("penalty=" .. n.penalty) or ""))
    end
  end
  return true  -- "done, keep the (possibly modified) list"
end)
