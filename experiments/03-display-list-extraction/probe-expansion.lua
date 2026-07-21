-- probe how the engine itself measures an expanded line:
-- compare node.dimensions() (engine arithmetic) on a stretched hlist
-- against manual accumulation, and inspect kern subtypes + expansion.
function PROBE(boxnum)
  local box = tex.box[boxnum]
  for line in node.traverse_id(node.id("hlist"), box.head) do
    local w = node.dimensions(line.glue_set, line.glue_sign, line.glue_order, line.head)
    texio.write_nl(string.format("PROBE line: box=%dsp node.dimensions=%dsp diff=%d",
      line.width, w, w - line.width))
    for n in node.traverse(line.head) do
      if n.id == node.id("kern") and n.subtype == 0 then
        texio.write_nl(string.format("  fontkern %dsp (expansion? n/a)", n.kern))
      end
    end
    break
  end
end
