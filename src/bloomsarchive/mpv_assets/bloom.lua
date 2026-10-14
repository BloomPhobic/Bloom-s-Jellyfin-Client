-- bloom.lua — Bloom's Archive in-player controls for mpv.
--
-- A self-contained overlay (no uosc/fonts needed): top bar with back + title,
-- seek bar with hover time, buffer and chapter ticks, transport buttons,
-- and menus for audio, subtitles, subtitle style, episodes and versions.
-- Your own mpv keybinds keep working; this only adds clickable controls.
--
-- Talks to the app with script messages:
--   app → us : bloom-info <json>    titles, prev/next, favourite, autoplay, seasons/episodes, versions
--   us  → app: bloom-prev, bloom-next, bloom-play-item <id>, bloom-favorite,
--              bloom-autoplay <yes|no>, bloom-version <source id>
-- Written for Lua 5.1/LuaJIT (mpv's Lua).

local mp = require "mp"
local utils = require "mp.utils"
local msg = require "mp.msg"

local HIDE_AFTER = 2.5
local FONT = "sans-serif"

-- colours are ASS BGR
local C = {
    text = "F7E8EC", muted = "B5909A", accent = "F65C8B", accent2 = "FA8BA7",
    bg = "140A0D", raised = "30171F", pink = "B672F4", white = "FFFFFF", black = "000000",
}

local S = {
    w = 1920, h = 1080, sc = 1,
    visible = false, last_move = 0, mx = -1, my = -1, hover_window = true,
    hits = {}, pressed = nil, drag = nil, pending_click = nil,
    menu = nil,
    info = { title = "", subtitle = "", has_prev = false, has_next = false, favorite = false,
             autoplay = true, is_episode = false, seasons = {}, versions = {}, current_id = "" },
    p = { ["time-pos"] = 0, duration = 0, pause = false, volume = 100, mute = false, fullscreen = true,
          ["track-list"] = {}, ["chapter-list"] = {}, ["demuxer-cache-time"] = 0, speed = 1,
          ["sub-scale"] = 1, ["sub-bold"] = false, ["sub-font"] = "", ["sub-border-size"] = 3,
          ["sub-pos"] = 100, ["sub-ass-override"] = "scale", ["sub-delay"] = 0, ["media-title"] = "" },
}

local overlay = mp.create_osd_overlay("ass-events")
local dirty = true
local keep_while_paused = false

-- ===================================================================== helpers

local function now() return mp.get_time() end

local function f1(v) return string.format("%.1f", v) end

local function esc(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\239\187\191")
    s = s:gsub("{", "\\{")
    s = s:gsub("}", "\\}")
    s = s:gsub("\n", " ")
    return s
end

local function fmt_time(t)
    t = math.max(0, math.floor(t or 0))
    local h, m, s = math.floor(t / 3600), math.floor(t % 3600 / 60), t % 60
    if h > 0 then return string.format("%d:%02d:%02d", h, m, s) end
    return string.format("%d:%02d", m, s)
end

local function ulen(s)
    local _, n = s:gsub("[^\128-\191]", "")
    return n
end

local function clip(s, px, size)
    -- width estimate: ~0.5 em per character for the UI font; counts characters, not bytes
    s = tostring(s or "")
    local max = math.floor(px / (size * 0.5))
    if ulen(s) <= max or max < 4 then return s end
    local outc, count = {}, 0
    for ch in s:gmatch("[%z\1-\127\194-\244][\128-\191]*") do
        if count >= max - 1 then break end
        outc[#outc + 1] = ch
        count = count + 1
    end
    return table.concat(outc) .. "…"
end

local LANG = { eng = "English", en = "English", jpn = "Japanese", ja = "Japanese", tur = "Turkish", tr = "Turkish",
    ger = "German", deu = "German", de = "German", fre = "French", fra = "French", fr = "French",
    spa = "Spanish", es = "Spanish", ita = "Italian", it = "Italian", por = "Portuguese", pt = "Portuguese",
    rus = "Russian", ru = "Russian", chi = "Chinese", zho = "Chinese", zh = "Chinese", kor = "Korean",
    ko = "Korean", ara = "Arabic", ar = "Arabic", pol = "Polish", pl = "Polish", dut = "Dutch", nld = "Dutch",
    nl = "Dutch", swe = "Swedish", sv = "Swedish", hin = "Hindi", hi = "Hindi", tha = "Thai", th = "Thai",
    vie = "Vietnamese", vi = "Vietnamese", ind = "Indonesian", id = "Indonesian", may = "Malay", msa = "Malay",
    ms = "Malay", und = "Unknown" }
local REGION = { ["419"] = "Latin America", br = "Brazil", pt = "Portugal", mx = "Mexico", es = "Spain",
    us = "US", gb = "UK", cn = "China", tw = "Taiwan", hk = "Hong Kong" }

local function lang_parts(code)
    if not code or code == "" then return "Unknown", "Unknown" end
    local c = code:lower()
    local base, region = c:match("^(%a+)[-_](%w+)$")
    local name = LANG[base or c] or code:upper()
    if region then
        return name .. " (" .. (REGION[region] or region:upper()) .. ")", name
    end
    return name, name
end

local function lang_name(code)
    return (lang_parts(code))
end

-- ===================================================================== drawing

local out = {}

local function push(line) out[#out + 1] = line end

local function style(bgr, alpha)
    return string.format("\\1c&H%s&\\1a&H%02X&", bgr, alpha or 0)
end

local function shape(path, st)
    push("{\\an7\\pos(0,0)\\bord0\\shad0\\blur0" .. (st or style(C.white)) .. "\\p1}" .. path .. "{\\p0}")
end

local function outline(path, bgr, width, alpha)
    push(string.format("{\\an7\\pos(0,0)\\shad0\\blur0\\1a&HFF&\\3c&H%s&\\3a&H%02X&\\bord%s\\p1}%s{\\p0}",
        bgr, alpha or 0, f1(width), path))
end

local function rrect_path(x, y, w, h, r)
    r = math.max(0, math.min(r, w / 2, h / 2))
    local c = r * 0.4477
    return table.concat({
        "m", f1(x + r), f1(y),
        "l", f1(x + w - r), f1(y),
        "b", f1(x + w - c), f1(y), f1(x + w), f1(y + c), f1(x + w), f1(y + r),
        "l", f1(x + w), f1(y + h - r),
        "b", f1(x + w), f1(y + h - c), f1(x + w - c), f1(y + h), f1(x + w - r), f1(y + h),
        "l", f1(x + r), f1(y + h),
        "b", f1(x + c), f1(y + h), f1(x), f1(y + h - c), f1(x), f1(y + h - r),
        "l", f1(x), f1(y + r),
        "b", f1(x), f1(y + c), f1(x + c), f1(y), f1(x + r), f1(y),
    }, " ")
end

local function rect(x, y, w, h, bgr, alpha, r)
    shape(rrect_path(x, y, w, h, r or 0), style(bgr, alpha))
end

local function circle_path(cx, cy, r)
    return rrect_path(cx - r, cy - r, 2 * r, 2 * r, r)
end

local function text(x, y, s, size, an, bgr, alpha, bold)
    push(string.format("{\\an%d\\pos(%s,%s)\\fn%s\\fs%s\\bord0\\shad0\\blur0%s\\b%d}%s",
        an or 7, f1(x), f1(y), FONT, f1(size), style(bgr or C.text, alpha), bold and 1 or 0, esc(s)))
end

-- soft dark fades behind the bars: one big rectangle, edge-blurred by libass
local function fade_top(height)
    shape(rrect_path(-60, -height, S.w + 120, height * 1.75, 0),
        style(C.bg, 70) .. string.format("\\blur%s", f1(height * 0.42)))
end

local function fade_bottom(height)
    shape(rrect_path(-60, S.h - height * 0.75, S.w + 120, height * 1.75, 0),
        style(C.bg, 50) .. string.format("\\blur%s", f1(height * 0.42)))
end

-- icons in a 24×24 box, transformed into place
local ICONS = {
    play = "m 7 4 l 20 12 l 7 20",
    pause = "m 6 4 l 10 4 l 10 20 l 6 20 m 14 4 l 18 4 l 18 20 l 14 20",
    prev = "m 5 4 l 8 4 l 8 20 l 5 20 m 20 4 l 9 12 l 20 20",
    next = "m 19 4 l 16 4 l 16 20 l 19 20 m 4 4 l 15 12 l 4 20",
    back = "m 3 12 l 10.5 4.5 l 12.3 6.3 l 7.9 10.7 l 21 10.7 l 21 13.3 l 7.9 13.3 l 12.3 17.7 l 10.5 19.5",
    heart = "m 12 21 b 3 14 1 9.5 3 6.3 b 5 3 9.5 3 12 7 b 14.5 3 19 3 21 6.3 b 23 9.5 21 14 12 21",
    fs_enter = "m 3 3 l 10 3 l 10 5.5 l 5.5 5.5 l 5.5 10 l 3 10 m 21 3 l 21 10 l 18.5 10 l 18.5 5.5 l 14 5.5 l 14 3 " ..
        "m 21 21 l 14 21 l 14 18.5 l 18.5 18.5 l 18.5 14 l 21 14 m 3 21 l 3 14 l 5.5 14 l 5.5 18.5 l 10 18.5 l 10 21",
    fs_exit = "m 8 3 l 10.5 3 l 10.5 10.5 l 3 10.5 l 3 8 l 8 8 m 13.5 3 l 16 3 l 16 8 l 21 8 l 21 10.5 l 13.5 10.5 " ..
        "m 13.5 21 l 13.5 13.5 l 21 13.5 l 21 16 l 16 16 l 16 21 m 3 13.5 l 10.5 13.5 l 10.5 21 l 8 21 l 8 16 l 3 16",
    speaker = "m 3 9 l 7 9 l 12 4.5 l 12 19.5 l 7 15 l 3 15 m 15 9.2 l 16.6 9.2 l 16.6 14.8 l 15 14.8 " ..
        "m 18.6 6.5 l 20.2 6.5 l 20.2 17.5 l 18.6 17.5",
    muted = "m 3 9 l 7 9 l 12 4.5 l 12 19.5 l 7 15 l 3 15 m 15 9.5 l 16.2 8.3 l 18.5 10.6 l 20.8 8.3 l 22 9.5 " ..
        "l 19.7 11.8 l 22 14.1 l 20.8 15.3 l 18.5 13 l 16.2 15.3 l 15 14.1 l 17.3 11.8",
    list = "m 3 5 l 6 5 l 6 8 l 3 8 m 9 5.5 l 21 5.5 l 21 7.5 l 9 7.5 m 3 10.5 l 6 10.5 l 6 13.5 l 3 13.5 " ..
        "m 9 11 l 21 11 l 21 13 l 9 13 m 3 16 l 6 16 l 6 19 l 3 19 m 9 16.5 l 21 16.5 l 21 18.5 l 9 18.5",
    audio = "m 3 10 l 5 10 l 5 14 l 3 14 m 7 7 l 9 7 l 9 17 l 7 17 m 11 4 l 13 4 l 13 20 l 11 20 " ..
        "m 15 8 l 17 8 l 17 16 l 15 16 m 19 10.5 l 21 10.5 l 21 13.5 l 19 13.5",
    tick = "m 4 12.5 l 6 10.5 l 9.5 14 l 18 5.5 l 20 7.5 l 9.5 18",
    tri_left = "m 9 2 l 9 8 l 4 5",
    tri_right = "m 15 2 l 15 8 l 20 5",
    chevron = "m 15 4 l 17 6 l 11 12 l 17 18 l 15 20 l 7 12",
}

local function xform(path, x0, y0, s)
    local o, isx = {}, true
    for tok in path:gmatch("%S+") do
        local v = tonumber(tok)
        if v then
            o[#o + 1] = f1(isx and (x0 + v * s) or (y0 + v * s))
            isx = not isx
        else
            o[#o + 1] = tok
            isx = true
        end
    end
    return table.concat(o, " ")
end

local function icon(name, cx, cy, size, bgr, alpha)
    local s = size / 24
    shape(xform(ICONS[name], cx - size / 2, cy - size / 2, s), style(bgr or C.text, alpha))
end

local function icon_outline(name, cx, cy, size, bgr, width)
    local s = size / 24
    outline(xform(ICONS[name], cx - size / 2, cy - size / 2, s), bgr or C.text, width)
end

-- ===================================================================== hit areas

local function hit(x1, y1, x2, y2, id, fn, extra)
    S.hits[#S.hits + 1] = { x1 = x1, y1 = y1, x2 = x2, y2 = y2, id = id, fn = fn, extra = extra }
end

local function hit_at(x, y)
    for i = #S.hits, 1, -1 do                       -- last drawn = on top
        local h = S.hits[i]
        if x >= h.x1 and x <= h.x2 and y >= h.y1 and y <= h.y2 then return h end
    end
end

local function hovering(id)
    -- uses the previous frame's areas: this frame's are still being built
    local list = S.prev_hits or {}
    for i = #list, 1, -1 do
        local h = list[i]
        if S.mx >= h.x1 and S.mx <= h.x2 and S.my >= h.y1 and S.my <= h.y2 then return h.id == id end
    end
    return false
end

-- ===================================================================== actions

local function send(name, arg)
    if arg ~= nil then mp.commandv("script-message", name, tostring(arg))
    else mp.commandv("script-message", name) end
end

local function seek_to_fraction(f, exact)
    local d = S.p.duration or 0
    if d <= 0 then return end
    f = math.max(0, math.min(1, f))
    mp.commandv("seek", tostring(f * d), exact and "absolute+exact" or "absolute+keyframes")
end

local open_menu   -- forward declaration

local function toggle_pause() mp.commandv("cycle", "pause") end

-- ===================================================================== menus

local function track_label(t)
    local full, base = lang_parts(t.lang)
    local bits = { full }
    -- skip titles that only repeat the language ("German", "Spanish[LAT]", "Portuguese[BR]")
    if t.title and t.title ~= "" and not t.title:lower():find(base:lower(), 1, true) then
        bits[#bits + 1] = t.title
    end
    local codec = (t.codec or ""):upper():gsub("SUBRIP", "SRT")
    if t.type == "audio" and t["demux-channel-count"] then
        local ch = t["demux-channel-count"]
        codec = codec .. " " .. (ch == 2 and "2.0" or ch == 6 and "5.1" or ch == 8 and "7.1" or (ch .. "ch"))
    end
    if codec ~= "" then bits[#bits + 1] = codec end
    local flags = {}
    if t.forced then flags[#flags + 1] = "forced" end
    if t.external then flags[#flags + 1] = "external" end
    local s = table.concat(bits, " · ")
    if #flags > 0 then s = s .. "  (" .. table.concat(flags, ", ") .. ")" end
    return s
end

local function tracks_of(kind)
    local r = {}
    for _, t in ipairs(S.p["track-list"] or {}) do
        if t.type == kind then r[#r + 1] = t end
    end
    return r
end

local MENUS = {}

function MENUS.audio()
    local items = {}
    for _, t in ipairs(tracks_of("audio")) do
        items[#items + 1] = { label = track_label(t), checked = t.selected,
            fn = function() mp.set_property_number("aid", t.id) end }
    end
    if #items == 0 then items[1] = { label = "No audio tracks", dim = true } end
    return { title = "Audio", items = items }
end

function MENUS.subs()
    local items = { { label = "Off", checked = (mp.get_property("sid") == "no"),
        fn = function() mp.set_property("sid", "no") end } }
    for _, t in ipairs(tracks_of("sub")) do
        items[#items + 1] = { label = track_label(t), checked = t.selected,
            fn = function() mp.set_property_number("sid", t.id) end }
    end
    return { title = "Subtitles", items = items }
end

local FONTS = { "", "Inter", "Noto Sans", "Roboto", "Open Sans", "DejaVu Sans", "Arial" }
local OVERRIDE = { no = "Keep their styling", scale = "Size only", force = "Force my style" }
local OVERRIDE_NEXT = { no = "scale", scale = "force", force = "no" }

local function nudge(prop, delta, lo, hi)
    local v = (mp.get_property_number(prop) or 0) + delta
    mp.set_property_number(prop, math.max(lo, math.min(hi, v)))
end

function MENUS.style()
    local p = S.p
    local font = (p["sub-font"] ~= "" and p["sub-font"]) or "Default"
    return { title = string.format("Subtitle style · %d%% · outline %s", math.floor((p["sub-scale"] or 1) * 100 + 0.5),
        f1(p["sub-border-size"] or 0)), keep_open = true, items = {
        { label = "Bigger text", fn = function() nudge("sub-scale", 0.1, 0.3, 4) end },
        { label = "Smaller text", fn = function() nudge("sub-scale", -0.1, 0.3, 4) end },
        { label = "Bold", checked = p["sub-bold"], fn = function() mp.commandv("cycle", "sub-bold") end },
        { label = "Font: " .. font, submenu = "fonts" },
        { label = "Thicker outline", fn = function() nudge("sub-border-size", 0.5, 0, 10) end },
        { label = "Thinner outline", fn = function() nudge("sub-border-size", -0.5, 0, 10) end },
        { label = "Move up", fn = function() nudge("sub-pos", -2, 0, 150) end },
        { label = "Move down", fn = function() nudge("sub-pos", 2, 0, 150) end },
        { label = "Styled (ASS) subs: " .. (OVERRIDE[p["sub-ass-override"]] or p["sub-ass-override"]),
          fn = function() mp.set_property("sub-ass-override", OVERRIDE_NEXT[p["sub-ass-override"]] or "scale") end },
        { label = "Reset style", fn = function()
            mp.set_property_number("sub-scale", 1); mp.set_property_bool("sub-bold", false)
            mp.set_property("sub-font", ""); mp.set_property_number("sub-border-size", 3)
            mp.set_property_number("sub-pos", 100); mp.set_property("sub-ass-override", "scale")
        end },
    } }
end

function MENUS.fonts()
    local items = { { label = "‹ Back", back = "style" } }
    for _, f in ipairs(FONTS) do
        items[#items + 1] = { label = f == "" and "Default" or f, checked = (S.p["sub-font"] or "") == f,
            fn = function() mp.set_property("sub-font", f) end, back_after = "style" }
    end
    return { title = "Subtitle font", keep_open = true, items = items }
end

function MENUS.episodes()
    local items = {}
    for si, season in ipairs(S.info.seasons or {}) do
        local current = false
        for _, e in ipairs(season.episodes or {}) do if e.id == S.info.current_id then current = true end end
        local watched = 0
        for _, e in ipairs(season.episodes or {}) do if e.played then watched = watched + 1 end end
        items[#items + 1] = { label = string.format("%s · %d episodes · %d watched", season.name,
            #(season.episodes or {}), watched), checked = current, submenu = "season", arg = si }
    end
    if #items == 0 then items[1] = { label = "No other episodes", dim = true } end
    return { title = "Episodes", items = items }
end

function MENUS.season(si)
    local season = (S.info.seasons or {})[si] or { name = "", episodes = {} }
    local items = { { label = "‹ All seasons", back = "episodes" } }
    for _, e in ipairs(season.episodes) do
        local label = (e.number and (e.number .. ". ") or "") .. (e.name or "")
        if e.played then label = "✓ " .. label end
        items[#items + 1] = { label = label, checked = (e.id == S.info.current_id),
            fn = function() if e.id ~= S.info.current_id then send("bloom-play-item", e.id) end end }
    end
    return { title = season.name, items = items, scroll_to_checked = true }
end

function MENUS.sync()
    local d = S.p["sub-delay"] or 0
    local function shift(by)
        return function()
            local v = (mp.get_property_number("sub-delay") or 0) + by
            mp.set_property_number("sub-delay", math.floor(v * 10 + 0.5) / 10)
        end
    end
    return { title = string.format("Subtitle timing · %+.1f s", d), keep_open = true, items = {
        { label = "Show subtitles earlier  · −1 s", fn = shift(-1) },
        { label = "Show subtitles earlier  · −0.1 s", fn = shift(-0.1) },
        { label = "Show subtitles later  · +0.1 s", fn = shift(0.1) },
        { label = "Show subtitles later  · +1 s", fn = shift(1) },
        { label = "Reset to 0", checked = math.abs(d) < 0.05, fn = function() mp.set_property_number("sub-delay", 0) end },
    } }
end

function MENUS.versions()
    local items = {}
    for _, v in ipairs(S.info.versions or {}) do
        items[#items + 1] = { label = v.label, checked = v.current,
            fn = function() if not v.current then send("bloom-version", v.id) end end }
    end
    return { title = "Version", items = items }
end

local function menu_bind(on)
    if on then
        mp.add_forced_key_binding("ESC", "bloom-menu-esc", function() S.menu = nil; dirty = true end)
        mp.add_forced_key_binding("WHEEL_UP", "bloom-menu-up", function()
            if S.menu then S.menu.scroll = math.max(0, S.menu.scroll - 1); dirty = true end end)
        mp.add_forced_key_binding("WHEEL_DOWN", "bloom-menu-down", function()
            if S.menu then S.menu.scroll = S.menu.scroll + 1; dirty = true end end)
    else
        mp.remove_key_binding("bloom-menu-esc")
        mp.remove_key_binding("bloom-menu-up")
        mp.remove_key_binding("bloom-menu-down")
    end
end

open_menu = function(kind, anchor_x, arg)
    local build = MENUS[kind]
    if not build then return end
    local m = build(arg)
    m.kind, m.arg = kind, arg
    m.anchor_x = anchor_x or (S.menu and S.menu.anchor_x) or S.w - 40
    m.scroll = 0
    if m.scroll_to_checked then
        for i, it in ipairs(m.items) do if it.checked then m.scroll = math.max(0, i - 3) end end
    end
    local was_open = S.menu ~= nil
    S.menu = m
    if not was_open then menu_bind(true) end
    dirty = true
end

-- menus that belong to the same button (submenus count as "open")
local FAMILY = { style = "style", fonts = "style", episodes = "episodes", season = "episodes",
                 audio = "audio", subs = "subs", versions = "versions", sync = "sync" }

local close_menu

local function toggle_menu(kind, anchor_x)
    if S.menu and FAMILY[S.menu.kind] == FAMILY[kind] then
        close_menu()
    else
        open_menu(kind, anchor_x)
    end
end

close_menu = function()
    if S.menu then
        S.menu = nil
        menu_bind(false)
        dirty = true
    end
end

local function menu_is(kind)
    return S.menu ~= nil and FAMILY[S.menu.kind] == kind
end

local function refresh_menu()
    if S.menu then
        local keep = S.menu
        local m = MENUS[keep.kind](keep.arg)
        m.kind, m.arg, m.anchor_x, m.scroll = keep.kind, keep.arg, keep.anchor_x, keep.scroll
        S.menu = m
    end
end

local function menu_pick(item)
    if item.dim then return end
    if item.back then open_menu(item.back); return end
    if item.submenu then open_menu(item.submenu, nil, item.arg); return end
    if item.fn then item.fn() end
    if item.back_after then open_menu(item.back_after); return end
    if S.menu and S.menu.keep_open then
        -- property changes arrive asynchronously; rebuild shortly after
        mp.add_timeout(0.08, function() refresh_menu(); dirty = true end)
    else
        close_menu()
    end
end

-- ===================================================================== render

local function draw_button(id, name, cx, cy, size, fn, opts)
    opts = opts or {}
    local r = size * 0.85
    local hot = hovering(id)
    if hot or opts.active then
        shape(circle_path(cx, cy, r), style(opts.active and C.accent or C.raised, hot and 40 or 90))
    end
    if opts.outline then
        icon_outline(name, cx, cy, size, hot and C.accent2 or C.text, 1.8 * S.sc)
    else
        icon(name, cx, cy, size, opts.color or (hot and C.accent2 or C.text))
    end
    hit(cx - r, cy - r, cx + r, cy + r, id, fn)
end

local function draw_text_button(id, label, cx, cy, size, fn, active)
    local hot = hovering(id)
    local w = (#label * size * 0.62) + size * 1.1
    local h = size * 1.55
    rect(cx - w / 2, cy - h / 2, w, h, active and C.accent or C.raised, hot and 30 or (active and 60 or 110), h * 0.3)
    push(string.format("{\\an7\\pos(0,0)\\1a&HFF&\\3c&H%s&\\3a&H%02X&\\bord%s\\shad0\\p1}%s{\\p0}",
        hot and C.accent2 or C.muted, hot and 0 or 120, f1(1.2 * S.sc), rrect_path(cx - w / 2, cy - h / 2, w, h, h * 0.3)))
    text(cx, cy, label, size, 5, hot and C.white or C.text, 0, true)
    hit(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, id, fn)
    return w
end

local function draw_skip(id, cx, cy, size, seconds, fn)
    local hot = hovering(id)
    local col = hot and C.accent2 or C.text
    local r = size * 0.85
    if hot then shape(circle_path(cx, cy, r), style(C.raised, 40)) end
    outline(circle_path(cx, cy, size * 0.42), col, 1.8 * S.sc)
    icon(seconds < 0 and "tri_left" or "tri_right", cx, cy, size, col)
    text(cx, cy + size * 0.04, tostring(math.abs(seconds)), size * 0.42, 5, col, 0, true)
    hit(cx - r, cy - r, cx + r, cy + r, id, fn)
end

local function render_menu(row_top)
    local m = S.menu
    if not m then return end
    local sc = S.sc
    local mw = 440 * sc
    local ih = 50 * sc
    local th = 58 * sc
    local maxv = math.max(3, math.floor((S.h * 0.62 - th) / ih))
    local n = #m.items
    local vis = math.min(n, maxv)
    m.scroll = math.max(0, math.min(m.scroll, n - vis))
    local ph = th + vis * ih + 10 * sc
    local x = math.max(16 * sc, math.min(S.w - mw - 16 * sc, m.anchor_x - mw / 2))
    local y = row_top - ph - 14 * sc
    rect(x, y, mw, ph, C.bg, 25, 14 * sc)
    push(string.format("{\\an7\\pos(0,0)\\1a&HFF&\\3c&H%s&\\bord%s\\shad0\\p1}%s{\\p0}",
        C.accent, f1(1.2 * sc), rrect_path(x, y, mw, ph, 14 * sc)))
    text(x + 20 * sc, y + th / 2, clip(m.title, mw - 40 * sc, 23 * sc), 23 * sc, 4, C.text, 0, true)
    hit(x, y, x + mw, y + ph, "menu-bg", function() end)          -- clicks on the panel don't fall through
    for i = 1, vis do
        local idx = i + m.scroll
        local it = m.items[idx]
        local iy = y + th + (i - 1) * ih
        local id = "menu-" .. idx
        local hot = hovering(id)
        if hot and not it.dim then rect(x + 6 * sc, iy, mw - 12 * sc, ih - 2 * sc, C.raised, 0, 8 * sc) end
        local col = it.dim and C.muted or (it.checked and C.accent2 or C.text)
        local right_pad = (it.checked or it.submenu) and 44 * sc or 18 * sc
        text(x + 20 * sc, iy + ih / 2, clip(it.label, mw - 20 * sc - right_pad, 21 * sc), 21 * sc, 4, col, 0,
            it.checked or false)
        if it.checked then icon("tick", x + mw - 28 * sc, iy + ih / 2, 22 * sc, C.accent2) end
        if it.submenu and not it.checked then text(x + mw - 24 * sc, iy + ih / 2, "›", 26 * sc, 5, C.muted) end
        hit(x + 6 * sc, iy, x + mw - 6 * sc, iy + ih - 2 * sc, id, function() menu_pick(it) end)
    end
    if n > vis then                                                   -- scroll indicator
        local track = vis * ih
        local bar = math.max(24 * sc, track * vis / n)
        local by = y + th + (track - bar) * (m.scroll / math.max(1, n - vis))
        rect(x + mw - 5 * sc, by, 3 * sc, bar, C.muted, 60, 1.5 * sc)
    end
end

local function render()
    out = {}
    S.prev_hits = S.hits
    S.hits = {}
    if not S.visible then
        overlay.data = ""
        overlay:update()
        return
    end
    local w, h, sc = S.w, S.h, S.sc
    local p = S.p
    local margin = 34 * sc
    local isz = 26 * sc

    -- ---- top bar
    fade_top(150 * sc)
    draw_button("back", "back", margin + 14 * sc, 50 * sc, isz, function() mp.command("quit") end)
    local tx = margin + 52 * sc
    local title = S.info.title ~= "" and S.info.title or p["media-title"] or ""
    if S.info.subtitle and S.info.subtitle ~= "" then
        text(tx, 31 * sc, clip(S.info.subtitle, w * 0.6, 19 * sc), 19 * sc, 4, C.muted)
        text(tx, 60 * sc, clip(title, w * 0.6, 29 * sc), 29 * sc, 4, C.text, 0, true)
    else
        text(tx, 50 * sc, clip(title, w * 0.6, 29 * sc), 29 * sc, 4, C.text, 0, true)
    end

    -- ---- bottom area
    fade_bottom(210 * sc)
    local row_y = h - 44 * sc
    local bar_y = h - 96 * sc
    local bx1, bx2 = margin, w - margin
    local dur = p.duration or 0
    local pos = p["time-pos"] or 0
    local frac = dur > 0 and math.min(1, pos / dur) or 0
    local bar_hot = (S.my >= bar_y - 14 * sc and S.my <= bar_y + 14 * sc and S.mx >= bx1 and S.mx <= bx2)
        or (S.drag ~= nil and S.drag.kind == "seek")
    local bh = (bar_hot and 8 or 5) * sc

    rect(bx1, bar_y - bh / 2, bx2 - bx1, bh, C.white, 175, bh / 2)
    local cache = p["demuxer-cache-time"] or 0
    if dur > 0 and cache > pos then
        rect(bx1, bar_y - bh / 2, (bx2 - bx1) * math.min(1, cache / dur), bh, C.white, 140, bh / 2)
    end
    rect(bx1, bar_y - bh / 2, (bx2 - bx1) * frac, bh, C.accent, 0, bh / 2)
    for _, ch in ipairs(p["chapter-list"] or {}) do                -- chapter ticks
        if dur > 0 and ch.time and ch.time > 0 then
            local cx = bx1 + (bx2 - bx1) * ch.time / dur
            rect(cx - 1 * sc, bar_y - bh / 2, 2 * sc, bh, C.bg, 40)
        end
    end
    local kx = bx1 + (bx2 - bx1) * frac
    shape(circle_path(kx, bar_y, (bar_hot and 9 or 6) * sc), style(C.white))
    hit(bx1, bar_y - 14 * sc, bx2, bar_y + 14 * sc, "seek", nil, { seek = true, x1 = bx1, x2 = bx2 })
    if bar_hot and dur > 0 then                                     -- hover time (+ chapter)
        local hx = math.max(bx1, math.min(bx2, S.mx))
        local t = dur * (hx - bx1) / (bx2 - bx1)
        local label = fmt_time(t)
        for _, ch in ipairs(p["chapter-list"] or {}) do
            if ch.time and ch.time <= t and ch.title and ch.title ~= "" then label = fmt_time(t) .. "  ·  " .. ch.title end
        end
        local tw = #label * 17 * sc * 0.56 + 26 * sc
        local tx2 = math.max(8 * sc, math.min(w - tw - 8 * sc, hx - tw / 2))
        rect(tx2, bar_y - 52 * sc, tw, 34 * sc, C.bg, 30, 9 * sc)
        text(tx2 + tw / 2, bar_y - 35 * sc, label, 17 * sc, 5, C.text)
    end

    -- ---- transport (left)
    local gap = 58 * sc
    local x = margin + 16 * sc
    if S.info.has_prev then
        draw_button("prev", "prev", x, row_y, isz * 0.9, function() send("bloom-prev") end)
        x = x + gap
    end
    draw_skip("rew", x, row_y, isz * 1.05, -10, function() mp.commandv("seek", "-10") end)
    x = x + gap
    draw_button("pause", p.pause and "play" or "pause", x, row_y, isz * 1.15, toggle_pause)
    x = x + gap
    draw_skip("fwd", x, row_y, isz * 1.05, 30, function() mp.commandv("seek", "30") end)
    x = x + gap
    if S.info.has_next then
        draw_button("next", "next", x, row_y, isz * 0.9, function() send("bloom-next") end)
        x = x + gap
    end
    x = x + 6 * sc
    local time_label = fmt_time(pos) .. " / " .. fmt_time(dur)
    text(x, row_y, time_label, 20 * sc, 4, C.text)
    if dur > 0 then
        local speed = p.speed or 1
        local ends = os.date("%H:%M", os.time() + math.floor((dur - pos) / (speed > 0 and speed or 1)))
        text(x + #time_label * 20 * sc * 0.56 + 20 * sc, row_y, "Ends at " .. ends, 17 * sc, 4, C.muted)
    end

    -- ---- right group (laid out right → left)
    local rx = w - margin - 14 * sc
    draw_button("fs", p.fullscreen and "fs_exit" or "fs_enter", rx, row_y, isz * 0.9,
        function() mp.commandv("cycle", "fullscreen") end)
    rx = rx - gap - 70 * sc
    -- volume slider
    local vw = 96 * sc
    local vx1 = rx - vw / 2 + 20 * sc
    local vol = math.max(0, math.min(130, p.volume or 100))
    local vol_hot = hovering("vol") or (S.drag ~= nil and S.drag.kind == "vol")
    rect(vx1, row_y - 2 * sc, vw, 4 * sc, C.white, 170, 2 * sc)
    rect(vx1, row_y - 2 * sc, vw * math.min(1, vol / 100), 4 * sc, p.mute and C.muted or C.accent, 0, 2 * sc)
    shape(circle_path(vx1 + vw * math.min(1, vol / 100), row_y, (vol_hot and 8 or 6) * sc), style(C.white))
    hit(vx1 - 6 * sc, row_y - 14 * sc, vx1 + vw + 6 * sc, row_y + 14 * sc, "vol", nil, { vol = true, x1 = vx1, x2 = vx1 + vw })
    draw_button("mute", p.mute and "muted" or "speaker", vx1 - 32 * sc, row_y, isz * 0.9,
        function() mp.commandv("cycle", "mute") end)
    rx = vx1 - 32 * sc - gap
    if #(S.info.versions or {}) > 1 then
        local label = "VERSION"
        for _, v in ipairs(S.info.versions) do if v.current then label = v.short or label end end
        local bw = draw_text_button("version", label, rx - 10 * sc, row_y, 12 * sc,
            function() toggle_menu("versions", rx) end, menu_is("versions"))
        rx = rx - bw / 2 - gap * 0.75
    end
    if S.info.is_episode then
        local bw = draw_text_button("autoplay", "AUTO", rx - 6 * sc, row_y, 12 * sc, function()
            S.info.autoplay = not S.info.autoplay
            send("bloom-autoplay", S.info.autoplay and "yes" or "no")
        end, S.info.autoplay)
        rx = rx - bw / 2 - gap * 0.75
    end
    local delay = S.p["sub-delay"] or 0
    local sync_label = math.abs(delay) < 0.05 and "SYNC" or string.format("%+.1fs", delay)
    local sync_w = draw_text_button("sync", sync_label, rx, row_y, 12 * sc, nil, menu_is("sync"))
    local sync_x = rx
    S.hits[#S.hits].fn = function() toggle_menu("sync", sync_x) end
    rx = rx - sync_w / 2 - gap * 0.85
    local style_x = rx
    draw_text_button("style", "Aa", style_x, row_y, 14 * sc, function() toggle_menu("style", style_x) end,
        menu_is("style"))
    rx = rx - gap
    local cc_x = rx
    draw_text_button("subs", "CC", cc_x, row_y, 13 * sc, function() toggle_menu("subs", cc_x) end,
        menu_is("subs"))
    rx = rx - gap
    local au_x = rx
    draw_button("audio", "audio", au_x, row_y, isz * 0.9, function() toggle_menu("audio", au_x) end,
        { active = menu_is("audio") })
    rx = rx - gap
    if S.info.is_episode and #(S.info.seasons or {}) > 0 then
        local ep_x = rx
        draw_button("episodes", "list", ep_x, row_y, isz * 0.9, function() toggle_menu("episodes", ep_x) end,
            { active = menu_is("episodes") })
        rx = rx - gap
    end
    if S.info.current_id ~= "" then
        draw_button("fav", "heart", rx, row_y, isz * 0.9, function()
            S.info.favorite = not S.info.favorite
            send("bloom-favorite", S.info.favorite and "yes" or "no")
        end, { outline = not S.info.favorite, color = S.info.favorite and C.pink or nil })
    end

    render_menu(bar_y - 18 * sc)

    overlay.res_x, overlay.res_y = w, h
    overlay.data = table.concat(out, "\n")
    overlay:update()
end

-- ===================================================================== visibility

local function set_visible(v)
    if v == S.visible then return end
    S.visible = v
    mp.set_property("cursor-autohide", v and "no" or "always")
    if not v then close_menu() end
    dirty = true
end

local function over_controls()
    return S.my >= S.h - 220 * S.sc or S.my <= 110 * S.sc
end

local function tick()
    if S.visible then
        local idle = now() - S.last_move
        if not S.menu and not S.drag and not keep_while_paused and idle > HIDE_AFTER
            and not (S.hover_window and over_controls() and idle < HIDE_AFTER * 3) then
            set_visible(false)
        end
    end
    if dirty then
        dirty = false
        render()
    end
end

-- ===================================================================== input

local function drag_update(final)
    local d = S.drag
    if not d then return end
    local h = d.area
    local f = (S.mx - h.extra.x1) / (h.extra.x2 - h.extra.x1)
    f = math.max(0, math.min(1, f))
    if h.extra.seek then
        local t = now()
        if final or t - (d.last or 0) > 0.08 then
            d.last = t
            seek_to_fraction(f, final)
        end
    elseif h.extra.vol then
        mp.set_property_number("volume", math.floor(f * 100 + 0.5))
        if S.p.mute then mp.set_property_bool("mute", false) end
    end
    dirty = true
end

local function on_click(e)
    local ev = e.event
    if ev == "down" then
        S.last_move = now()
        if not S.visible then set_visible(true) end
        render()                                  -- refresh hit areas for this frame
        local h = hit_at(S.mx, S.my)
        S.pressed = h
        if h and h.extra and (h.extra.seek or h.extra.vol) then
            S.drag = { area = h, kind = h.extra.seek and "seek" or "vol" }
            drag_update(false)
        end
    elseif ev == "up" then
        if S.drag then
            drag_update(true)
            S.drag = nil
            S.pressed = nil
            dirty = true
            return
        end
        local h = hit_at(S.mx, S.my)
        local pressed = S.pressed
        S.pressed = nil
        if h and pressed and h.id == pressed.id then
            if h.fn then h.fn() end
            dirty = true
            return
        end
        if S.menu then close_menu(); return end
        if not h then
            -- click on the picture: toggle pause, unless it turns out to be a double-click
            if S.pending_click then S.pending_click:kill() end
            S.pending_click = mp.add_timeout(0.25, function() S.pending_click = nil; toggle_pause() end)
        end
    end
end

local function on_double()
    if S.pending_click then S.pending_click:kill(); S.pending_click = nil end
    local h = hit_at(S.mx, S.my)
    if not h then mp.commandv("cycle", "fullscreen") end
end

mp.add_forced_key_binding("MBTN_LEFT", "bloom-click", on_click, { complex = true })
mp.add_forced_key_binding("MBTN_LEFT_DBL", "bloom-dbl", on_double)

-- ===================================================================== properties

local function obs(name, kind)
    mp.observe_property(name, kind or "native", function(_, v)
        if v == nil then return end
        S.p[name] = v
        if name == "pause" then
            keep_while_paused = v
            if v then set_visible(true) end
        end
        if S.menu and (name:sub(1, 4) == "sub-" or name == "track-list") then refresh_menu() end
        if name == "sub-delay" then dirty = true end
        if S.visible then dirty = true end
    end)
end

for _, name in ipairs({ "time-pos", "duration", "pause", "volume", "mute", "fullscreen", "track-list",
    "chapter-list", "demuxer-cache-time", "speed", "sub-scale", "sub-bold", "sub-font", "sub-border-size",
    "sub-pos", "sub-ass-override", "sub-delay", "media-title" }) do
    obs(name)
end

mp.observe_property("osd-dimensions", "native", function(_, d)
    if d and d.w and d.w > 0 then
        S.w, S.h = d.w, d.h
        S.sc = math.max(0.55, math.min(2.2, d.h / 1080))
        dirty = true
    end
end)

mp.observe_property("mouse-pos", "native", function(_, m)
    if not m then return end
    local moved = m.x ~= S.mx or m.y ~= S.my
    S.mx, S.my = m.x, m.y
    S.hover_window = m.hover ~= false
    if moved then
        S.last_move = now()
        if not S.visible then set_visible(true) end
        if S.drag then drag_update(false) end
        dirty = true
    end
end)

mp.register_script_message("bloom-info", function(json)
    local info = utils.parse_json(json or "")
    if type(info) == "table" then
        for k, v in pairs(info) do S.info[k] = v end
        if S.menu then refresh_menu() end
        dirty = true
    else
        msg.warn("bloom-info: bad json")
    end
end)

mp.register_script_message("bloom-show", function() S.last_move = now(); set_visible(true) end)

mp.add_periodic_timer(1 / 30, tick)

-- test hook (the app never sets this)
if rawget(_G, "BLOOM_EXPOSE") then
    BLOOM_EXPOSE.S = S
    BLOOM_EXPOSE.render = render
    BLOOM_EXPOSE.tick = tick
    BLOOM_EXPOSE.open_menu = open_menu
    BLOOM_EXPOSE.overlay = overlay
end
