-- bench.lua — amortized per-paragraph timing, replicating Section 3 of the
-- paper: each sample = mean of 100 consecutive in-session compiles; report
-- median/P5/P95 over 30 samples after a 5-sample warmup.
--
-- The token stream is generated up front with tex.print(); the interleaved
-- \directlua timing calls execute in document order as TeX consumes the
-- stream, so each sample times exactly the typesetting of its 100 paragraphs.
-- Each sample is typeset inside \setbox0=\vbox{...} so the page builder and
-- shipout never run: we measure line breaking (+ node processing) only.

BENCH = { short = {}, med = {}, t0 = 0 }

local shortpar = [[The quick brown fox jumps over the lazy dog.\par]]
local medpar = [[Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed
do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad
minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea
commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit
esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat
non proident, sunt in culpa qui officia deserunt mollit anim id est
laborum.\par]]

local SAMPLES, REPS, WARMUP = 35, 100, 5

function BENCH.report()
  local function stats(s, name)
    local t = {}
    for i = WARMUP + 1, #s do t[#t + 1] = s[i] end
    table.sort(t)
    local n = #t
    local function pct(p)
      return t[math.max(1, math.min(n, math.floor(p * n + 0.5)))]
    end
    local function fmt(x) return string.format("%.2f", x) end
    texio.write_nl("term and log", string.format(
      "BENCH %-6s median=%s ms  P5=%s ms  P95=%s ms  (n=%d samples of %d paragraphs)",
      name, fmt(pct(0.5)), fmt(pct(0.05)), fmt(pct(0.95)), n, REPS))
  end
  stats(BENCH.short, "short")
  stats(BENCH.med, "medium")
end

local out = {}
local function emit(s) out[#out + 1] = s end

for _, cfg in ipairs({ { key = "short", text = shortpar },
                       { key = "med",   text = medpar } }) do
  for _ = 1, SAMPLES do
    emit([[\directlua{BENCH.t0 = os.gettimeofday()}]])
    emit([[\setbox0=\vbox{]])
    for _ = 1, REPS do emit(cfg.text) end
    emit("}")
    -- *10 = (*1000 ms) / (100 paragraphs)
    emit(string.format(
      [[\directlua{table.insert(BENCH.%s, (os.gettimeofday() - BENCH.t0) * 1000 / %d)}]],
      cfg.key, REPS))
  end
end
emit([[\directlua{BENCH.report()}]])

tex.print(out)
