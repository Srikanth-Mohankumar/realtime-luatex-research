-- callback-order.lua -- trace the order in which node-list callbacks fire.
local seq = 0
local function trace(name, is_linebreak)
  return function(head, a, b, c)
    seq = seq + 1
    local extra = ""
    if type(head) == "string" then extra = " info=" .. head
    elseif type(a) == "string" then extra = " groupcode=" .. (a == "" and "<empty>" or a)
    elseif type(a) == "boolean" then extra = " is_display=" .. tostring(a)
    end
    texio.write_nl(string.format("%02d %s%s", seq, name, extra))
    if is_linebreak then
      -- must return a vertical list: delegate to the built-in line breaker
      local par = tex.linebreak(head)
      return par
    end
    if type(head) ~= "string" then return true end
  end
end
callback.register("pre_linebreak_filter",  trace("pre_linebreak_filter"))
callback.register("linebreak_filter",      trace("linebreak_filter", true))
callback.register("post_linebreak_filter", trace("post_linebreak_filter"))
callback.register("hpack_filter",          trace("hpack_filter"))
callback.register("vpack_filter",          trace("vpack_filter"))
callback.register("pre_output_filter",     trace("pre_output_filter"))
callback.register("buildpage_filter",      trace("buildpage_filter"))
callback.register("contribute_filter",     trace("contribute_filter"))
