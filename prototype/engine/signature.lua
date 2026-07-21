-- signature.lua — canonical line-break signature, shared by the capture pass
-- (reference, from the full compile) and the server (fast path). Both sides
-- MUST use this exact walk so positions are comparable to the scaled point.
--
-- A signature is a list of lines; each line:
--   { w=<width sp>, h=<height>, d=<depth>, g={ {char, x_sp}, ... } }
-- x is relative to the line's left edge, measured with node.dimensions
-- (engine arithmetic: exact under microtype expansion; see experiments/03).

local M = {}

local GLYPH = node.id("glyph")
local DISC  = node.id("disc")
local HLIST = node.id("hlist")
local VLIST = node.id("vlist")

local function walk(head, x0, set, sign, order, out)
  local x = x0
  for n in node.traverse(head) do
    local adv = node.dimensions(set, sign, order, n, n.next)
    local id = n.id
    if id == GLYPH then
      out[#out + 1] = { n.char, x, n.font }
    elseif id == DISC then
      if n.replace then walk(n.replace, x, set, sign, order, out) end
    elseif id == HLIST then
      walk(n.head, x, n.glue_set, n.glue_sign, n.glue_order, out)
    elseif id == VLIST then
      -- vertical material inside a line (e.g. \vbox in text): record its
      -- glyphs flattened at the box origin x; y structure is not compared
      for m in node.traverse_id(HLIST, n.head) do
        walk(m.head, x, m.glue_set, m.glue_sign, m.glue_order, out)
      end
    end
    x = x + adv
  end
  return x
end

-- signature of one line hlist
function M.line_sig(line)
  local g = {}
  walk(line.head, 0, line.glue_set, line.glue_sign, line.glue_order, g)
  return { w = line.width, h = line.height, d = line.depth, g = g }
end

-- signature of every hlist in a vertical list (post_linebreak head, or a
-- vbox's .head): one entry per line box, with baseline y accumulated from
-- line heights/depths and inter-line glue/kerns (glue at natural width:
-- both sources we walk — post_linebreak lists and natural-height vboxes —
-- have unset interline glue)
function M.vlist_sig(head, y0)
  local lines = {}
  local y = y0 or 0
  for n in node.traverse(head) do
    if n.id == HLIST then
      local l = M.line_sig(n)
      l.y = y + n.height          -- baseline position
      lines[#lines + 1] = l
      y = y + n.height + n.depth
    elseif n.id == VLIST then
      -- e.g. display-math or nested vbox: recurse so structure is visible
      local inner = M.vlist_sig(n.head, y)
      for _, l in ipairs(inner) do lines[#lines + 1] = l end
      y = y + n.height + n.depth
    elseif n.id == node.id("glue") then
      y = y + n.width
    elseif n.id == node.id("kern") then
      y = y + n.kern
    elseif n.id == node.id("rule") then
      y = y + n.height + n.depth
    end
  end
  return lines
end

-- compact JSON for a signature (no external json lib in plain lualatex)
function M.sig_json(lines)
  local lparts = {}
  for _, l in ipairs(lines) do
    local gparts = {}
    for _, gl in ipairs(l.g) do
      gparts[#gparts + 1] = string.format("[%d,%d,%d]", gl[1], gl[2], gl[3] or 0)
    end
    lparts[#lparts + 1] = string.format(
      '{"w":%d,"h":%d,"d":%d,"y":%d,"g":[%s]}',
      l.w, l.h, l.d, l.y or 0, table.concat(gparts, ","))
  end
  return "[" .. table.concat(lparts, ",") .. "]"
end

-- font table for every font id referenced by a signature: id -> name, size
function M.fonts_json(lines)
  local seen, parts = {}, {}
  for _, l in ipairs(lines) do
    for _, gl in ipairs(l.g) do
      local id = gl[3]
      if id and id ~= 0 and not seen[id] then
        seen[id] = true
        local f = font.getfont(id) or font.fonts[id] or {}
        parts[#parts + 1] = string.format('"%d":{"name":%q,"size":%d}',
          id, f.name or "", f.size or 655360)
      end
    end
  end
  return "{" .. table.concat(parts, ",") .. "}"
end

return M
