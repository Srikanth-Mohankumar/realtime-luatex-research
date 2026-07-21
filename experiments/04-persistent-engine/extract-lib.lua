-- extract-lib.lua — library version of experiment 03's extractor.
-- EXTRACT_LIST(boxnum) walks the vbox and returns
--   { commands = {...glyph/rule...}, fonts = {...}, nlines = n }
-- Coordinates in scaled points; per-node advances measured with
-- node.dimensions(set, sign, order, n, n.next) — the engine's own
-- arithmetic, exact under microtype expansion (see experiment 03 README).

local display_list, nlines

local walk_vlist  -- forward declaration

local function walk_hlist(head, x0, y, set, sign, order)
  local x = x0
  for n in node.traverse(head) do
    local id = n.id
    local advance = node.dimensions(set, sign, order, n, n.next)
    if id == node.id("glyph") then
      display_list[#display_list + 1] = {
        type = "glyph", char = n.char, font = n.font, x = x, y = y,
        xoffset = n.xoffset, yoffset = n.yoffset,
        expansion = n.expansion_factor or 0,
      }
    elseif id == node.id("rule") then
      display_list[#display_list + 1] = {
        type = "rule", x = x, y = y,
        width = n.width, height = n.height, depth = n.depth,
      }
    elseif id == node.id("disc") then
      walk_hlist(n.replace, x, y, set, sign, order)
    elseif id == node.id("hlist") then
      walk_hlist(n.head, x, y + n.shift, n.glue_set, n.glue_sign, n.glue_order)
    elseif id == node.id("vlist") then
      walk_vlist(n, x, y + n.shift - n.height)
    end
    x = x + advance
  end
  return x
end

walk_vlist = function(box, x0, y0)
  local y = y0
  for n in node.traverse(box.head) do
    local id = n.id
    if id == node.id("hlist") then
      nlines = nlines + 1
      walk_hlist(n.head, x0 + n.shift, y + n.height,
                 n.glue_set, n.glue_sign, n.glue_order)
      y = y + n.height + n.depth
    elseif id == node.id("vlist") then
      walk_vlist(n, x0 + n.shift, y)
      y = y + n.height + n.depth
    elseif id == node.id("glue") then
      y = y + node.dimensions(box.glue_set, box.glue_sign, box.glue_order, n, n.next)
    elseif id == node.id("kern") then
      y = y + n.kern
    elseif id == node.id("rule") then
      y = y + n.height + n.depth
    end
  end
  return y
end

function EXTRACT_LIST(boxnum)
  display_list, nlines = {}, 0
  local box = tex.box[boxnum]
  assert(box, "box " .. boxnum .. " is void")
  walk_vlist(box, 0, 0)
  local fonts_used, fonts = {}, {}
  for _, cmd in ipairs(display_list) do
    if cmd.type == "glyph" and not fonts_used[cmd.font] then
      fonts_used[cmd.font] = true
      local f = font.getfont(cmd.font)
      fonts[#fonts + 1] = { id = cmd.font, name = f.name, size = f.size }
    end
  end
  return { commands = display_list, fonts = fonts, nlines = nlines }
end
