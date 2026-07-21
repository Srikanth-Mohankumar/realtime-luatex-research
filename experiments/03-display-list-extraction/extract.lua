-- extract.lua — proof of concept for the paper's core mechanism:
-- "extract the display list directly from LuaTeX's node structures after
--  line breaking: a stream of positioned glyph commands with coordinates in
--  scaled points" (Lode, TUG2026, section 5).
--
-- We typeset a paragraph into \box0 (a \vbox), then walk the box:
--   vlist traversal accumulates y (baseline positions per line),
--   hlist traversal accumulates x, resolving set glue via
--   glue_set / glue_sign / glue_order arithmetic.
-- The result is a JSON display list of {char, font, x_sp, y_sp} glyph
-- commands plus rules, written to display-list.json.
--
-- Sanity check printed to the terminal: for each fully-justified line the
-- accumulated natural-plus-set width must equal the hlist's width (=\hsize).
-- That equality holding to the scaled point is exactly the "pixel-perfect"
-- property the paper relies on.

local SP_PER_PT = 65536

local function glue_width(g, set, sign, order)
  -- effective width of a glue node inside a box with glue setting (set, sign, order)
  local w = g.width
  if sign == 1 and g.stretch_order == order then      -- stretching
    w = w + set * g.stretch
  elseif sign == 2 and g.shrink_order == order then   -- shrinking
    w = w - set * g.shrink
  end
  return w
end

local display_list = {}
local lines_report = {}

local function walk_hlist(head, x0, y, set, sign, order)
  -- Per-node advance is measured with node.dimensions(set, sign, order,
  -- n, n.next): the engine's OWN arithmetic, which resolves set glue AND
  -- microtype font expansion / expanded font kerns exactly (integer sp).
  -- Manual accumulation (glyph.width + kern.kern + resolved glue) is exact
  -- without microtype but drifts by up to ~7pt/line with font expansion,
  -- because glyph.width reports the unexpanded metric width — verified
  -- empirically, see README.
  local x = x0
  for n in node.traverse(head) do
    local id = n.id
    local advance = node.dimensions(set, sign, order, n, n.next)
    if id == node.id("glyph") then
      display_list[#display_list + 1] = {
        type = "glyph", char = n.char, font = n.font,
        x = x, y = y,
        xoffset = n.xoffset, yoffset = n.yoffset,
        expansion = n.expansion_factor or 0,
      }
    elseif id == node.id("rule") then
      display_list[#display_list + 1] = {
        type = "rule", x = x, y = y,
        width = n.width, height = n.height, depth = n.depth,
      }
    elseif id == node.id("disc") then
      -- after line breaking, discs remaining IN a line contribute their
      -- replace list (the no-break rendering); glyphs inside must be
      -- emitted individually (their advances are inside `advance`)
      walk_hlist(n.replace, x, y, set, sign, order)
    elseif id == node.id("hlist") then
      -- nested box: shift moves it down; it has its own glue setting
      walk_hlist(n.head, x, y + n.shift, n.glue_set, n.glue_sign, n.glue_order)
    elseif id == node.id("vlist") then
      walk_vlist(n, x, y + n.shift - n.height)
    end
    -- kern / glue / math / margin_kern advances are all covered by
    -- node.dimensions above
    x = x + advance
  end
  return x
end

function walk_vlist(box, x0, y0)
  local y = y0
  for n in node.traverse(box.head) do
    local id = n.id
    if id == node.id("hlist") then
      local baseline = y + n.height
      local xend = walk_hlist(n.head, x0 + n.shift, baseline,
                              n.glue_set, n.glue_sign, n.glue_order)
      lines_report[#lines_report + 1] = {
        box_width = n.width, accumulated = xend - (x0 + n.shift),
        glue_sign = n.glue_sign,
      }
      y = y + n.height + n.depth
    elseif id == node.id("vlist") then
      walk_vlist(n, x0 + n.shift, y)
      y = y + n.height + n.depth
    elseif id == node.id("glue") then
      y = y + glue_width(n, box.glue_set, box.glue_sign, box.glue_order)
    elseif id == node.id("kern") then
      y = y + n.kern
    elseif id == node.id("rule") then
      y = y + n.height + n.depth
    end
  end
  return y
end

local function tojson(v, indent)
  indent = indent or ""
  if type(v) == "table" then
    if v[1] ~= nil or next(v) == nil then
      local parts = {}
      for _, item in ipairs(v) do
        parts[#parts + 1] = indent .. "  " .. tojson(item, indent .. "  ")
      end
      return "[\n" .. table.concat(parts, ",\n") .. "\n" .. indent .. "]"
    else
      local parts = {}
      for k, val in pairs(v) do
        parts[#parts + 1] = string.format('"%s": %s', k, tojson(val, indent))
      end
      table.sort(parts)
      return "{" .. table.concat(parts, ", ") .. "}"
    end
  elseif type(v) == "number" then
    if v == math.floor(v) then return string.format("%d", v) end
    return string.format("%.6f", v)
  else
    return string.format("%q", tostring(v))
  end
end

function EXTRACT(boxnum)
  display_list, lines_report = {}, {}
  local box = tex.box[boxnum]
  assert(box, "box " .. boxnum .. " is void")
  walk_vlist(box, 0, 0)

  -- report
  texio.write_nl("term and log", string.format(
    "EXTRACT: %d display-list commands, %d lines", #display_list, #lines_report))
  local worst = 0
  for i, l in ipairs(lines_report) do
    local err = l.accumulated - l.box_width
    if math.abs(err) > math.abs(worst) then worst = err end
    texio.write_nl("term and log", string.format(
      "  line %d: box width=%.3fpt accumulated=%.3fpt error=%+.4fsp (%s)",
      i, l.box_width / SP_PER_PT, l.accumulated / SP_PER_PT, err,
      l.glue_sign == 1 and "stretched" or l.glue_sign == 2 and "shrunk" or "natural"))
  end
  texio.write_nl("term and log", string.format(
    "EXTRACT: worst line-width reconstruction error = %+.4f sp (%.6f pt)",
    worst, worst / SP_PER_PT))

  -- collect font map so a renderer could resolve glyphs
  local fonts_used = {}
  for _, cmd in ipairs(display_list) do
    if cmd.type == "glyph" and not fonts_used[cmd.font] then
      local f = font.getfont(cmd.font)
      fonts_used[cmd.font] = { name = f.name, size = f.size, filename = f.filename }
    end
  end
  local fontarr = {}
  for id, f in pairs(fonts_used) do
    fontarr[#fontarr + 1] = { id = id, name = f.name, size = f.size,
                              filename = f.filename or "" }
  end

  local fh = io.open("display-list.json", "w")
  fh:write(tojson({ unit = "scaled points (65536 sp = 1 pt)",
                    fonts = fontarr, commands = display_list }))
  fh:close()
  texio.write_nl("term and log", "EXTRACT: wrote display-list.json")
end
