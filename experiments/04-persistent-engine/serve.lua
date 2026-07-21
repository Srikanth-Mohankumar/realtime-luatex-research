-- serve.lua — Lua side of the persistent paragraph server (see server.tex).
--
-- SERVE_ONE() is called from a TeX \loop. It reads one request from stdin.
-- For a paragraph request it queues (via tex.sprint):
--     \setbox0=\vbox{<text>\par}\directlua{RESPOND()}
-- so TeX typesets the paragraph and then calls RESPOND(), which walks the
-- box and emits the display list as one JSON line on stdout.
--
-- Timing note: T0 is taken when the request arrives; RESPOND() runs after
-- line breaking, so ms covers tokenization + paragraph building + line
-- breaking + node traversal — the "LuaTeX (line break + traversal)" row of
-- the paper's Table 3. Client-side round-trip adds IPC + JSON parse.

dofile("extract-lib.lua")  -- provides EXTRACT_LIST(boxnum) -> table

local T0 = 0

function SERVE_ONE()
  local line = io.read("*l")
  if line == nil or line == "QUIT" then
    tex.sprint([[\servingfalse]])
    return
  end
  T0 = os.gettimeofday()
  tex.sprint("\\setbox0=\\vbox{" .. line .. "\\par}\\directlua{RESPOND()}")
end

local function esc(s)
  return (s:gsub('[%c"\\]', function(c)
    return string.format("\\u%04x", string.byte(c))
  end))
end

function RESPOND()
  local list = EXTRACT_LIST(0)
  local ms = (os.gettimeofday() - T0) * 1000
  local parts = {}
  for _, g in ipairs(list.commands) do
    if g.type == "glyph" then
      parts[#parts + 1] = string.format(
        '{"c":%d,"f":%d,"x":%d,"y":%d}', g.char, g.font, g.x, g.y)
    end
  end
  local fonts = {}
  for _, f in ipairs(list.fonts) do
    fonts[#fonts + 1] = string.format('{"id":%d,"name":"%s","size":%d}',
      f.id, esc(f.name), f.size)
  end
  io.write(string.format(
    '{"ms":%.3f,"lines":%d,"fonts":[%s],"glyphs":[%s]}\n',
    ms, list.nlines, table.concat(fonts, ","), table.concat(parts, ",")))
  io.flush()
end

-- handshake: preamble is loaded once we get here
io.write("READY\n")
io.flush()
