-- A tiny stand-in for mpv's Lua API, enough to drive bloom.lua in tests.
local M = { props = {}, observers = {}, bindings = {}, messages = {}, sent = {}, commands = {},
            timers = {}, clock = 0, overlay = nil }

-- ---------------------------------------------------------------- json (decode only)
local function decode(s)
    local i = 1
    local function ws() while true do local c = s:sub(i, i); if c == " " or c == "\n" or c == "\t" or c == "\r" then i = i + 1 else break end end end
    local val
    local function str()
        i = i + 1
        local buf = {}
        while true do
            local c = s:sub(i, i)
            if c == '"' then i = i + 1; break end
            if c == "\\" then
                local n = s:sub(i + 1, i + 1)
                local map = { n = "\n", t = "\t", r = "\r", b = "\b", f = "\f", ['"'] = '"', ["\\"] = "\\", ["/"] = "/" }
                if n == "u" then
                    local code = tonumber(s:sub(i + 2, i + 5), 16)
                    buf[#buf + 1] = (utf8 and utf8.char or string.char)(code < 256 and code or 63); i = i + 6
                else buf[#buf + 1] = map[n]; i = i + 2 end
            else buf[#buf + 1] = c; i = i + 1 end
        end
        return table.concat(buf)
    end
    function val()
        ws()
        local c = s:sub(i, i)
        if c == "{" then
            i = i + 1; local t = {}; ws()
            if s:sub(i, i) == "}" then i = i + 1; return t end
            while true do
                ws(); local k = str(); ws(); i = i + 1; t[k] = val(); ws()
                local d = s:sub(i, i); i = i + 1
                if d == "}" then return t end
            end
        elseif c == "[" then
            i = i + 1; local t = {}; ws()
            if s:sub(i, i) == "]" then i = i + 1; return t end
            while true do
                t[#t + 1] = val(); ws()
                local d = s:sub(i, i); i = i + 1
                if d == "]" then return t end
            end
        elseif c == '"' then return str()
        elseif s:sub(i, i + 3) == "true" then i = i + 4; return true
        elseif s:sub(i, i + 4) == "false" then i = i + 5; return false
        elseif s:sub(i, i + 3) == "null" then i = i + 4; return nil
        else
            local num = s:match("^-?%d+%.?%d*[eE]?[-+]?%d*", i)
            i = i + #num
            return tonumber(num)
        end
    end
    local ok, r = pcall(val)
    if ok then return r end
    return nil
end

-- ---------------------------------------------------------------- mp
local mp = {}
function mp.get_time() return M.clock end
function mp.observe_property(name, _kind, fn)
    M.observers[name] = M.observers[name] or {}
    table.insert(M.observers[name], fn)
end
function mp.get_property(name) local v = M.props[name]; if v == nil then return nil end; return tostring(v) end
function mp.get_property_number(name) return tonumber(M.props[name]) end
function mp.get_property_native(name) return M.props[name] end
local function setp(name, v) M.set(name, v) end
function mp.set_property(name, v) setp(name, v) end
function mp.set_property_number(name, v) setp(name, v) end
function mp.set_property_bool(name, v) setp(name, v) end
function mp.commandv(...) local a = { ... }; table.insert(M.commands, a)
    if a[1] == "script-message" then table.insert(M.sent, { table.unpack(a, 2) }) end
    if a[1] == "cycle" then M.set(a[2], not M.props[a[2]]) end
    if a[1] == "seek" and a[3] and a[3]:find("absolute") then M.set("time-pos", tonumber(a[2])) end
end
function mp.command(c) table.insert(M.commands, { c }) end
function mp.add_forced_key_binding(key, name, fn, opts) M.bindings[key] = { name = name, fn = fn, opts = opts } end
function mp.add_key_binding(key, name, fn, opts) M.bindings[key] = { name = name, fn = fn, opts = opts } end
function mp.remove_key_binding(name)
    for k, b in pairs(M.bindings) do if b.name == name then M.bindings[k] = nil end end
end
local function timer(fn, period, oneshot)
    local t = { fn = fn, period = period, next = M.clock + period, oneshot = oneshot, alive = true }
    function t:kill() self.alive = false end
    function t:stop() self.alive = false end
    function t:resume() self.alive = true end
    table.insert(M.timers, t)
    return t
end
function mp.add_periodic_timer(p, fn) return timer(fn, p, false) end
function mp.add_timeout(p, fn) return timer(fn, p, true) end
function mp.register_script_message(name, fn) M.messages[name] = fn end
M.events = {}
M.osd = {}
function mp.register_event(name, fn) M.events[name] = M.events[name] or {}; table.insert(M.events[name], fn) end
function mp.osd_message(text) table.insert(M.osd, text) end
function mp.create_osd_overlay(fmt)
    local o = { format = fmt, data = "", res_x = 0, res_y = 0, updates = 0 }
    function o:update() self.updates = self.updates + 1 end
    function o:remove() self.data = "" end
    M.overlay = o
    return o
end
function mp.get_osd_size() return 1920, 1080 end

-- ---------------------------------------------------------------- harness helpers
function M.set(name, v)
    M.props[name] = v
    for _, fn in ipairs(M.observers[name] or {}) do fn(name, v) end
end
function M.advance(seconds)
    local target = M.clock + seconds
    while true do
        local soonest
        for _, t in ipairs(M.timers) do
            if t.alive and t.next <= target and (not soonest or t.next < soonest.next) then soonest = t end
        end
        if not soonest then break end
        M.clock = soonest.next
        if soonest.oneshot then soonest.alive = false else soonest.next = soonest.next + soonest.period end
        soonest.fn()
    end
    M.clock = target
end
function M.move(x, y) M.set("mouse-pos", { x = x, y = y, hover = true }) end
function M.click(x, y)
    M.move(x, y)
    local b = M.bindings["MBTN_LEFT"]
    b.fn({ event = "down" })
    b.fn({ event = "up" })
end
function M.key(k) local b = M.bindings[k]; if b then b.fn({ event = "press" }) end end
function M.message(name, ...) M.messages[name](...) end
function M.event(name) for _, fn in ipairs(M.events[name] or {}) do fn({ event = name }) end end

package.preload["mp"] = function() return mp end
package.preload["mp.utils"] = function() return { parse_json = decode } end
package.preload["mp.msg"] = function()
    local function log(...) end
    return { warn = log, info = log, debug = log, error = log, verbose = log }
end

return M
