-- Run with: texlua tests/lua/test_overlay.lua   (any Lua 5.1+ works)
local here = arg[0]:match("(.*/)") or "./"
package.path = here .. "?.lua;" .. package.path
local M = require "fake_mp"

BLOOM_EXPOSE = {}
dofile(here .. "../../src/bloomsarchive/mpv_assets/bloom.lua")
local B = BLOOM_EXPOSE
local S = B.S

local failures, passed = 0, 0
local function check(cond, what)
    if cond then passed = passed + 1 else failures = failures + 1; print("FAIL: " .. what) end
end
local function find_hit(id)
    for _, h in ipairs(S.hits) do if h.id == id then return h end end
end
local function center(h) return (h.x1 + h.x2) / 2, (h.y1 + h.y2) / 2 end
local function sent(name)
    for _, m in ipairs(M.sent) do if m[1] == name then return m end end
end

-- ---- environment as mpv would report it
M.set("osd-dimensions", { w = 1920, h = 1080 })
M.set("duration", 3299)
M.set("time-pos", 1924)
M.set("pause", false)
M.set("volume", 80)
M.set("mute", false)
M.set("fullscreen", true)
M.set("sid", "1")
M.set("track-list", {
    { id = 1, type = "video", ["ff-index"] = 0 },
    { id = 1, type = "audio", lang = "jpn", codec = "flac", ["demux-channel-count"] = 2, selected = true },
    { id = 2, type = "audio", lang = "eng", codec = "aac", ["demux-channel-count"] = 6 },
    { id = 1, type = "sub", lang = "eng", codec = "ass", title = "Full", selected = true },
    { id = 2, type = "sub", lang = "tur", codec = "subrip", external = true },
    { id = 3, type = "sub", lang = "es-419", codec = "ass", title = "Spanish[LAT]" },
})
M.set("chapter-list", { { time = 0, title = "Intro" }, { time = 90, title = "Part A" } })

M.message("bloom-info", [[{"title":"Sniper","subtitle":"As If · S3E2","has_prev":true,"has_next":true,
  "favorite":false,"autoplay":true,"is_episode":true,"current_id":"e2",
  "seasons":[{"name":"Season 3","episodes":[{"id":"e1","number":1,"name":"First","played":true},
                                            {"id":"e2","number":2,"name":"Sniper","played":false},
                                            {"id":"e3","number":3,"name":"Third","played":false}]}],
  "versions":[{"id":"v1","label":"1080p","short":"1080p","current":true},{"id":"v2","label":"4K","short":"4K","current":false}]}]])

-- ---- hidden until the mouse moves
M.advance(0.1)
check(M.overlay.data == "", "overlay starts hidden")
M.move(900, 500)
M.advance(0.05)
check(M.overlay.data:find("Sniper", 1, true) ~= nil, "title drawn after mouse move")
check(M.overlay.data:find("As If", 1, true) ~= nil, "subtitle drawn")
check(M.overlay.data:find("32:04 / 54:59", 1, true) ~= nil, "time label")
check(M.overlay.data:find("Ends at", 1, true) ~= nil, "ends-at label")
check(M.props["cursor-autohide"] == "no", "cursor shown with the controls")
for _, id in ipairs({ "back", "prev", "rew", "pause", "fwd", "next", "seek", "fs", "vol", "mute",
                      "version", "autoplay", "sync", "style", "subs", "audio", "episodes", "fav" }) do
    check(find_hit(id) ~= nil, "button present: " .. id)
end

-- every button sits inside the window
for _, h in ipairs(S.hits) do
    check(h.x1 >= -1 and h.x2 <= 1921 and h.y1 >= -1 and h.y2 <= 1081, "in bounds: " .. h.id)
end
-- buttons on the bottom row don't overlap each other
local row = {}
for _, h in ipairs(S.hits) do
    if h.y1 > 980 and h.id ~= "seek" then row[#row + 1] = h end
end
for i = 1, #row do for j = i + 1, #row do
    local a, b = row[i], row[j]
    local overlap = a.x1 < b.x2 - 1 and b.x1 < a.x2 - 1
    check(not overlap, "no overlap: " .. a.id .. " / " .. b.id)
end end

-- ---- transport
local x, y = center(find_hit("pause"))
M.click(x, y)
check(M.props.pause == true, "pause button pauses")
M.advance(0.05)
x, y = center(find_hit("next"))
M.click(x, y)
check(sent("bloom-next") ~= nil, "next sends bloom-next")
x, y = center(find_hit("fwd"))
M.click(x, y)
local last = M.commands[#M.commands]
check(last[1] == "seek" and last[2] == "30", "+30 seeks")

-- ---- seek bar click
M.advance(0.05)
local sh = find_hit("seek")
M.click(sh.x1 + (sh.x2 - sh.x1) * 0.5, (sh.y1 + sh.y2) / 2)
local seek
for _, c in ipairs(M.commands) do if c[1] == "seek" and c[3] and c[3]:find("absolute") then seek = c end end
check(seek ~= nil and math.abs(tonumber(seek[2]) - 1649.5) < 3, "seek bar seeks to the middle")

-- ---- audio menu
M.advance(0.05)
x, y = center(find_hit("audio"))
M.click(x, y)
M.advance(0.05)
check(S.menu ~= nil and S.menu.kind == "audio", "audio menu opens")
check(M.overlay.data:find("Japanese · FLAC 2.0", 1, true) ~= nil, "audio track label")
local item2 = find_hit("menu-2")
check(item2 ~= nil, "second audio item clickable")
x, y = center(item2)
M.click(x, y)
check(M.props.aid == 2, "choosing English sets aid=2")
check(S.menu == nil, "menu closes after choosing")

-- ---- subtitles menu: Off
M.advance(0.05)
x, y = center(find_hit("subs"))
M.click(x, y)
M.advance(0.05)
check(M.overlay.data:find("Turkish · SRT  (external)", 1, true) ~= nil, "external sub label")
check(M.overlay.data:find("Spanish (Latin America) · ASS", 1, true) ~= nil, "regional code label, no repeated title")
x, y = center(find_hit("menu-1"))
M.click(x, y)
check(M.props.sid == "no", "Off turns subtitles off")

-- ---- style menu stays open and changes size
M.advance(0.05)
M.set("sub-scale", 1)
x, y = center(find_hit("style"))
M.click(x, y)
M.advance(0.05)
x, y = center(find_hit("menu-1"))
M.click(x, y)
check(math.abs((M.props["sub-scale"] or 0) - 1.1) < 1e-6, "Bigger text → 1.1")
M.advance(0.2)
check(S.menu ~= nil and S.menu.kind == "style", "style menu stays open")
check(M.overlay.data:find("110%%") ~= nil, "style title shows new size")
-- font submenu and back
x, y = center(find_hit("menu-4"))
M.click(x, y)
M.advance(0.05)
check(S.menu.kind == "fonts", "font submenu")
x, y = center(find_hit("menu-3"))     -- "Inter"
M.click(x, y)
check(M.props["sub-font"] == "Inter", "font chosen")
M.advance(0.05)
check(S.menu and S.menu.kind == "style", "back to style after choosing font")
M.key("ESC")
check(S.menu == nil, "ESC closes menu")

-- ---- episodes menu → season → play another episode
M.advance(0.05)
x, y = center(find_hit("episodes"))
M.click(x, y)
M.advance(0.05)
check(M.overlay.data:find("Season 3 · 3 episodes · 1 watched", 1, true) ~= nil, "season row")
x, y = center(find_hit("menu-1"))
M.click(x, y)
M.advance(0.05)
check(S.menu.kind == "season", "season opens episode list")
check(M.overlay.data:find("✓ 1. First", 1, true) ~= nil, "watched episode marked")
x, y = center(find_hit("menu-4"))     -- ‹ All seasons, e1, e2, e3
M.click(x, y)
local play = sent("bloom-play-item")
check(play ~= nil and play[2] == "e3", "picking an episode asks the app to play it")

-- ---- a button closes its own menu (and the family of its submenus)
M.advance(0.05)
x, y = center(find_hit("audio"))
M.click(x, y)
M.advance(0.05)
check(S.menu and S.menu.kind == "audio", "audio opens")
x, y = center(find_hit("audio"))
M.click(x, y)
M.advance(0.05)
check(S.menu == nil, "clicking audio again closes it")
x, y = center(find_hit("subs"))
M.click(x, y)
M.advance(0.05)
x, y = center(find_hit("audio"))
M.click(x, y)
M.advance(0.05)
check(S.menu and S.menu.kind == "audio", "another button switches menus")
x, y = center(find_hit("audio"))
M.click(x, y)
M.advance(0.05)
x, y = center(find_hit("style"))
M.click(x, y)
M.advance(0.05)
x, y = center(find_hit("menu-4"))      -- Font: …
M.click(x, y)
M.advance(0.05)
check(S.menu and S.menu.kind == "fonts", "in the font submenu")
x, y = center(find_hit("style"))
M.click(x, y)
M.advance(0.05)
check(S.menu == nil, "Aa closes its submenu too")

-- ---- subtitle timing
M.set("sub-delay", 0)
M.advance(0.05)
x, y = center(find_hit("sync"))
M.click(x, y)
M.advance(0.05)
check(S.menu and S.menu.kind == "sync", "sync menu opens")
check(M.overlay.data:find("Subtitle timing · +0.0 s", 1, true) ~= nil, "sync title")
x, y = center(find_hit("menu-3"))      -- +0.1 s
M.click(x, y); M.click(x, y); M.click(x, y)
check(math.abs(M.props["sub-delay"] - 0.3) < 1e-6, "three × +0.1 s = +0.3 s")
x, y = center(find_hit("menu-1"))      -- −1 s
M.click(x, y)
check(math.abs(M.props["sub-delay"] + 0.7) < 1e-6, "−1 s → −0.7 s")
M.advance(0.2)
check(S.menu and S.menu.kind == "sync", "sync menu stays open while adjusting")
check(M.overlay.data:find("-0.7s", 1, true) ~= nil, "button shows the current offset")
x, y = center(find_hit("menu-5"))      -- reset
M.click(x, y)
check(M.props["sub-delay"] == 0, "reset to 0")
M.key("ESC")

-- ---- favourite, autoplay, versions
M.advance(0.05)
x, y = center(find_hit("fav"))
M.click(x, y)
local fav = sent("bloom-favorite")
check(fav ~= nil and fav[2] == "yes", "favourite toggles")
M.advance(0.05)
x, y = center(find_hit("autoplay"))
M.click(x, y)
local ap = sent("bloom-autoplay")
check(ap ~= nil and ap[2] == "no", "autoplay toggles off")
M.advance(0.05)
x, y = center(find_hit("version"))
M.click(x, y)
M.advance(0.05)
x, y = center(find_hit("menu-2"))
M.click(x, y)
local ver = sent("bloom-version")
check(ver ~= nil and ver[2] == "v2", "version switch")

-- ---- volume drag + mute
M.advance(0.05)
local vh = find_hit("vol")
M.click(vh.x1 + (vh.x2 - vh.x1) * 0.25 + 6, (vh.y1 + vh.y2) / 2)
check(M.props.volume and M.props.volume > 10 and M.props.volume < 45, "volume slider sets volume")

-- ---- click on the picture toggles pause after the double-click window
M.set("pause", false)
M.advance(0.05)
M.click(960, 540)
check(M.props.pause == false, "not paused immediately (double-click guard)")
M.advance(0.3)
check(M.props.pause == true, "single click on the picture pauses")
-- double click → fullscreen, no pause
M.set("pause", false)
M.click(960, 540)
M.bindings["MBTN_LEFT_DBL"].fn()
M.advance(0.3)
check(M.props.pause == false, "double-click doesn't pause")
check(M.props.fullscreen == false, "double-click toggles fullscreen")

-- ---- auto-hide (playing, mouse in the middle)
M.set("fullscreen", true)
M.set("pause", false)
M.move(960, 500)
M.advance(3.0)
check(S.visible == false, "controls hide after idle")
check(M.overlay.data == "", "overlay cleared when hidden")
check(M.props["cursor-autohide"] == "always", "cursor hidden with the controls")
-- paused keeps them visible
M.set("pause", true)
M.advance(5)
check(S.visible == true, "paused keeps controls visible")

-- ---- different window size still lays out
M.set("osd-dimensions", { w = 1280, h = 720 })
M.move(100, 100)
M.advance(0.05)
for _, h in ipairs(S.hits) do
    check(h.x1 >= -1 and h.x2 <= 1281 and h.y1 >= -1 and h.y2 <= 721, "in bounds at 720p: " .. h.id)
end

-- ---- hostile text can't inject ASS tags
M.message("bloom-info", [[{"title":"{\\pos(0,0)}Evil \\N title"}]])
M.advance(0.05)
check(M.overlay.data:find("{\\pos(0,0)}Evil", 1, true) == nil, "braces in titles are escaped")

print(string.format("%d passed, %d failed", passed, failures))
os.exit(failures == 0 and 0 or 1)
