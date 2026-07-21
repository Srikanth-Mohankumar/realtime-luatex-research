-- serve2.lua — persistent paragraph server v2 with context injection
-- (blueprint step 2). Loaded by each template's server.tex, which has the
-- SAME preamble as the sample document (same class, fonts, microtype) and
-- has read the sample's .aux (so \ref/\cite resolve to converged values —
-- the "background convergence feeds the fast path" pattern).
--
-- Protocol (newline-delimited on stdin/stdout):
--   request:  one line, a Lua table literal:
--             { text="<paragraph source>", ctx={ ...capture.json fields... } }
--   response: {"ms":..., "sig":[...signature.lua format...]}
--   QUIT ends the session.
--
-- Context injection: the request's ctx is turned into a parameter prologue
-- inside \setbox0=\vbox{...}: hsize, skips, penalties, language, parshape,
-- and the font active at paragraph start. Font resolution: find a loaded
-- font with the same (name, size) — identical preambles load identical
-- fonts — else define one on the fly with \font.

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

local T0 = 0
local REQ

-- glue tuple {w, st, sto, sh, sho} -> TeX glue text.
-- LuaTeX stretch orders: 0=finite, 1=fi, 2=fil, 3=fill, 4=filll
-- (one more than TeX82: the Aleph "fi" order — notes/01).
local ORDER = { [0] = "", [1] = "fi", [2] = "fil", [3] = "fill", [4] = "filll" }
local function glue_tex(t)
  local s = string.format("%dsp", t[1])
  if t[2] ~= 0 then
    s = s .. (t[3] == 0 and string.format(" plus %dsp", t[2])
              or string.format(" plus %.5f%s", t[2] / 65536, ORDER[t[3]]))
  end
  if t[4] ~= 0 then
    s = s .. (t[5] == 0 and string.format(" minus %dsp", t[4])
              or string.format(" minus %.5f%s", t[4] / 65536, ORDER[t[5]]))
  end
  return s
end

local function find_font(name, size)
  for id = 1, font.max() do
    local f = font.fonts[id]
    if f and f.name == name and f.size == size then return id end
  end
end

local function prologue(ctx)
  local p = {}
  local function add(fmt, ...) p[#p + 1] = string.format(fmt, ...) end
  add("\\hsize=%dsp", ctx.hsize)
  add("\\leftskip=%s", glue_tex(ctx.leftskip))
  add("\\rightskip=%s", glue_tex(ctx.rightskip))
  add("\\parfillskip=%s", glue_tex(ctx.parfillskip))
  add("\\spaceskip=%s", glue_tex(ctx.spaceskip))
  add("\\xspaceskip=%s", glue_tex(ctx.xspaceskip))
  if ctx.baselineskip then
    add("\\baselineskip=%s", glue_tex(ctx.baselineskip))
    add("\\lineskip=%s", glue_tex(ctx.lineskip))
    add("\\lineskiplimit=%dsp", ctx.lineskiplimit)
  end
  add("\\pretolerance=%d \\tolerance=%d", ctx.pretolerance, ctx.tolerance)
  add("\\emergencystretch=%dsp", ctx.emergencystretch)
  add("\\linepenalty=%d \\hyphenpenalty=%d \\exhyphenpenalty=%d",
      ctx.linepenalty, ctx.hyphenpenalty, ctx.exhyphenpenalty)
  add("\\adjdemerits=%d \\doublehyphendemerits=%d \\finalhyphendemerits=%d",
      ctx.adjdemerits, ctx.doublehyphendemerits, ctx.finalhyphendemerits)
  add("\\adjustspacing=%d \\protrudechars=%d",
      ctx.adjustspacing, ctx.protrudechars)
  add("\\language=%d \\lefthyphenmin=%d \\righthyphenmin=%d \\uchyph=%d",
      ctx.lang, ctx.lhmin, ctx.rhmin, ctx.uchyph)
  add("\\hangindent=%dsp \\hangafter=%d", ctx.hangindent, ctx.hangafter)
  if ctx.looseness ~= 0 then add("\\looseness=%d", ctx.looseness) end
  if ctx.parshape then
    local rows = {}
    for _, r in ipairs(ctx.parshape) do
      rows[#rows + 1] = string.format("%dsp %dsp", r[1], r[2])
    end
    add("\\parshape %d %s", #ctx.parshape, table.concat(rows, " "))
  end
  -- font at paragraph start
  local fid = find_font(ctx.font_name, ctx.font_size)
  if fid then
    add("\\setfontid%d", fid)
  else
    add("\\font\\RTctxfont=%s at %dsp\\RTctxfont", ctx.font_name, ctx.font_size)
  end
  -- indentation: replicate the captured indent box (nil = \noindent)
  if ctx.indent then
    add("\\parindent=%dsp\\indent", ctx.indent)
  else
    add("\\noindent")
  end
  return table.concat(p, " ")
end

function SERVE_ONE()
  local line = io.read("*l")
  if line == nil or line == "QUIT" then
    tex.sprint([[\servingfalse]])
    return
  end
  local chunk, err = load("return " .. line)
  if not chunk then
    io.write(string.format('{"error":%q}\n', "parse: " .. tostring(err)))
    io.flush()
    return
  end
  local ok, req = pcall(chunk)
  if not ok or type(req) ~= "table" then
    io.write(string.format('{"error":%q}\n', "eval: " .. tostring(req)))
    io.flush()
    return
  end
  if req.preload then
    -- warm the font loader once at session start: define every (name, size)
    -- the capture saw, so per-request find_font always hits a loaded font
    -- and no request pays a cold luaotfload lookup
    local defs = {}
    for i, f in ipairs(req.preload) do
      if not find_font(f[1], f[2]) then
        defs[#defs + 1] = string.format("\\font\\RTpre%s=%s at %dsp",
          string.rep("i", i), f[1], f[2])
      end
    end
    tex.sprint(table.concat(defs, " ")
               .. "\\directlua{io.write('{\\string\"preloaded\\string\":true}\\string\\n') io.flush()}")
    return
  end
  REQ = req
  T0 = os.gettimeofday()
  -- three separate lines (tex.print, not sprint): a stray % in user text
  -- then only eats to the end of ITS line, not the closing \par}\directlua
  tex.print("\\setbox0=\\vbox{" .. prologue(req.ctx) .. "%",
            req.text,
            "\\par}\\directlua{RESPOND()}")
end

function RESPOND()
  local box = tex.box[0]
  local lines = sig.vlist_sig(box.head)
  local ms = (os.gettimeofday() - T0) * 1000
  io.write(string.format('{"ms":%.3f,"fonts":%s,"sig":%s}\n',
                         ms, sig.fonts_json(lines), sig.sig_json(lines)))
  io.flush()
  tex.box[0] = nil  -- frees the box and its list; lwc lesson (notes/04): free or leak
end

-- Load converged cross-reference state from the sample's aux file.
-- \@input{sample.aux} mid-document fails on \newlabel with this kernel
-- ("can be used only in preamble"), so define \r@<key> directly: this is
-- the background-convergence pattern — the full compile's global state
-- feeds the fast path.
function RTLOADAUX(path)
  local fh = io.open(path, "r")
  if not fh then
    texio.write_nl("RTSERVE: no aux at " .. path)
    return
  end
  local n = 0
  for line in fh:lines() do
    local key, val = line:match("^\\newlabel{(.-)}{(.*)}%s*$")
    if key then
      token.set_macro("r@" .. key, val, "global")
      n = n + 1
    end
    -- \bibcite fails mid-document under natbib-based classes (cas-dc);
    -- define \b@<key> directly (natbib's name too, when \@extra@binfo
    -- is empty — the normal single-bibliography case)
    local bkey, bval = line:match("^\\bibcite{(.-)}{(.*)}%s*$")
    if bkey then
      token.set_macro("b@" .. bkey, bval, "global")
      n = n + 1
    end
  end
  fh:close()
  texio.write_nl(string.format("RTSERVE: %d labels/cites loaded from %s", n, path))
end

io.write("RTSERVE READY\n")
io.flush()
