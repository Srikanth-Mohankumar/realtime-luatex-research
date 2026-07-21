-- converge-loop.lua — persistent convergence engine.
--
-- Loaded inside \begin{document} of a generated <stem>-conv.tex that
-- contains ONLY the document's preamble plus a service loop. The 13 s
-- template preamble is paid once; after that each request re-typesets the
-- document BODY in-session and writes a fresh page-position capture:
--
--   stdin:  RUN <bodyfile> <output.json> <auxfile>\n     | QUIT\n
--   stdout: CONVREADY once, then CONVDONE <ms> | CONVERR <msg> per run
--
-- Between runs, state that pagination depends on is reset:
--   * count/dimen registers 0..1999 snapshot/restore (LaTeX counters:
--     section/figure/footnote/equation numbering, page number, ...)
--   * RTCAPTURE state + paragraph attribute (fresh id registry per run)
--   * \r@/\b@ label macros re-seeded from the reference aux, so \ref and
--     \cite resolve without a real aux cycle
-- Macro-level and template-Lua state is NOT snapshotted; the validation
-- harness compares this engine's output against a fresh full compile.

local T0 = 0

-- ---- register snapshot (taken once, at preamble-complete) ----
local NREG = 2000
local SNAP = { count = {}, dimen = {} }
for i = 0, NREG - 1 do
  SNAP.count[i] = tex.count[i]
  SNAP.dimen[i] = tex.dimen[i]
end

local function restore_registers()
  for i = 0, NREG - 1 do
    if tex.count[i] ~= SNAP.count[i] then
      tex.setcount("global", i, SNAP.count[i])
    end
    if tex.dimen[i] ~= SNAP.dimen[i] then
      tex.setdimen("global", i, SNAP.dimen[i])
    end
  end
end

-- ---- label state from the reference aux (as in serve2.lua) ----
local function load_aux(path)
  local fh = io.open(path, "r")
  if not fh then return 0 end
  local n = 0
  for line in fh:lines() do
    local key, val = line:match("^\\newlabel{(.-)}{(.*)}%s*$")
    if key then
      token.set_macro("r@" .. key, val, "global")
      n = n + 1
    end
    local bkey, bval = line:match("^\\bibcite{(.-)}{(.*)}%s*$")
    if bkey then
      token.set_macro("b@" .. bkey, bval, "global")
      n = n + 1
    end
  end
  fh:close()
  return n
end

function CONV_ONE()
  local line = io.read("*l")
  if line == nil or line == "QUIT" then
    tex.sprint([[\servingfalse]])
    return
  end
  local body, out, aux = line:match("^RUN%s+(%S+)%s+(%S+)%s+(%S+)")
  if not body then
    io.write("CONVERR bad request\n")
    io.flush()
    return
  end
  T0 = os.gettimeofday()
  restore_registers()
  RTCAPTURE.reset()
  load_aux(aux)
  -- typeset the body; \clearpage ships the final page so the capture is
  -- complete, then CONV_END reports. A TeX error inside the body is
  -- survivable in nonstopmode; the watchdog upstream handles wedges.
  tex.print("\\input{" .. body .. "}",
            "\\clearpage\\directlua{CONV_END('" .. out .. "')}")
end

function CONV_END(out)
  RTCAPTURE.finish(out)
  io.write(string.format("CONVDONE %.0f\n", (os.gettimeofday() - T0) * 1000))
  io.flush()
end

io.write("CONVREADY\n")
io.flush()
