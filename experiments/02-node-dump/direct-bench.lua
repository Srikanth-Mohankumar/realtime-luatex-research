-- direct-bench.lua -- measure userdata vs direct node access.
-- Builds a list of 50k glyph nodes, then sums char+width over it repeatedly.

local N, REPS = 50000, 20

-- build a list of glyph nodes
local head = nil
local tail = nil
local fnt = font.current()
for i = 1, N do
  local g = node.new("glyph")
  g.char, g.font = 65 + (i % 26), fnt
  if tail then tail.next = g else head = g end
  tail = g
end

local t0 = os.clock()
local sum1 = 0
for r = 1, REPS do
  local n = head
  while n do
    if n.id == 29 then sum1 = sum1 + n.char end
    n = n.next
  end
end
local t1 = os.clock()

local d = node.direct
local dhead = d.todirect(head)
local getid, getchar, getnext = d.getid, d.getchar, d.getnext
local sum2 = 0
for r = 1, REPS do
  local n = dhead
  while n do
    if getid(n) == 29 then sum2 = sum2 + getchar(n) end
    n = getnext(n)
  end
end
local t2 = os.clock()

texio.write_nl(string.format(
  "userdata: %.3fs   direct: %.3fs   speedup: %.2fx   (sums %d/%d, %d nodes x %d reps)",
  t1 - t0, t2 - t1, (t1 - t0) / (t2 - t1), sum1, sum2, N, REPS))
node.flush_list(head)
