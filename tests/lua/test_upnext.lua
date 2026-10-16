-- Skip Intro/Credits, Up Next card, countdown, Cancel, Still watching.  texlua tests/lua/test_upnext.lua
local here = arg[0]:match("(.*/)") or "./"
package.path = here .. "?.lua;" .. package.path
local M = require "fake_mp"

BLOOM_EXPOSE = {}
dofile(here .. "../../src/bloomsarchive/mpv_assets/bloom.lua")
local S = BLOOM_EXPOSE.S

local failures, passed = 0, 0
local function check(cond, what)
    if cond then passed = passed + 1 else failures = failures + 1; print("FAIL: " .. what) end
end
local function find_hit(id) for _, h in ipairs(S.hits) do if h.id == id then return h end end end
local function center(h) return (h.x1 + h.x2) / 2, (h.y1 + h.y2) / 2 end
local function sent_all(name)
    local r = {}
    for _, m in ipairs(M.sent) do if m[1] == name then r[#r + 1] = m end end
    return r
end
local function last_seek()
    for i = #M.commands, 1, -1 do local c = M.commands[i]; if c[1] == "seek" then return c end end
end

local INFO = [[{"title":"Episode 3","subtitle":"Show · S01E03","is_episode":true,"current_id":"e3",
  "has_prev":true,"has_next":true,"autoplay":true,"skip_mode":"button","up_next_seconds":20,
  "still_watching":false,"next":{"id":"e4","label":"S01E04","title":"The Next One"},"seasons":[],"versions":[]}]]

local function new_file(info)
    M.event("file-loaded")
    M.set("osd-dimensions", { w = 1920, h = 1080 })
    M.set("duration", 1440)
    M.set("time-pos", 0)
    M.set("pause", false)
    M.message("bloom-info", info or INFO)
    M.message("bloom-segments", [=[[{"type":"Intro","start":90,"end":180},{"type":"Outro","start":1320,"end":1410}]]=])
    M.advance(0.05)
end

-- ---------------------------------------------------------------- skip intro (button mode)
new_file()
check(M.overlay.data == "", "nothing drawn outside segments while hidden")
M.set("time-pos", 95)
M.advance(0.05)
check(S.visible == false, "controls stay hidden")
check(M.overlay.data:find("Skip Intro", 1, true) ~= nil, "Skip Intro shows during the intro, even with controls hidden")
local b = find_hit("skip")
check(b ~= nil, "skip button clickable")
local x, y = center(b)
M.click(x, y)
local sk = last_seek()
check(sk and tonumber(sk[2]) == 180 and sk[3] == "absolute", "Skip Intro seeks to the intro's end")
M.advance(0.3)
check(M.props.pause == false, "clicking Skip doesn't pause")
M.advance(0.05)
check(M.overlay.data:find("Skip Intro", 1, true) == nil, "button gone after the intro")

-- ---------------------------------------------------------------- credits → Up Next card with countdown
M.set("time-pos", 1321)
M.advance(0.05)
check(M.overlay.data:find("UP NEXT", 1, true) ~= nil, "card appears when the credits start")
check(M.overlay.data:find("S01E04 · The Next One", 1, true) ~= nil, "card names the next episode")
check(M.overlay.data:find("Skip Credits", 1, true) == nil, "no separate Skip Credits while the card is up")
check(M.overlay.data:find("Play now · 20", 1, true) ~= nil, "countdown starts at 20")
M.advance(5.0)
check(M.overlay.data:find("Play now · 15", 1, true) ~= nil, "countdown ticks")
check(#sent_all("bloom-play-next") == 0, "nothing sent before the countdown ends")
M.advance(15.1)
local pn = sent_all("bloom-play-next")
check(#pn == 1 and pn[1][2] == "auto", "countdown end asks the app to play next (auto)")
M.advance(2)
check(#sent_all("bloom-play-next") == 1, "fires once")

-- ---------------------------------------------------------------- Play now / Cancel
new_file()
M.set("time-pos", 1425)                 -- 15 s left: inside the up-next window, after credits
M.advance(0.05)
check(M.overlay.data:find("UP NEXT", 1, true) ~= nil, "card appears in the last 20 s")
M.click(center(find_hit("card-play")))
local pn2 = sent_all("bloom-play-next")
check(#pn2 == 2 and pn2[2][2] == "user", "Play now = user action")

new_file()
M.set("time-pos", 1425)
M.advance(0.05)
M.click(center(find_hit("card-cancel")))
check(#sent_all("bloom-cancel-next") == 1, "Cancel tells the app")
M.advance(0.05)
check(M.overlay.data:find("UP NEXT", 1, true) == nil, "Cancel hides the card for this episode")
M.advance(30)
check(#sent_all("bloom-play-next") == 2, "no countdown after Cancel")

-- ---------------------------------------------------------------- autoplay off: no countdown
new_file(INFO:gsub('"autoplay":true', '"autoplay":false'))
M.set("time-pos", 1425)
M.advance(0.05)
check(M.overlay.data:find("Play now", 1, true) ~= nil and M.overlay.data:find("Play now · ", 1, true) == nil,
    "manual card without countdown")
M.advance(30)
check(#sent_all("bloom-play-next") == 2, "manual card never fires on its own")

-- ---------------------------------------------------------------- still watching?
new_file(INFO:gsub('"still_watching":false', '"still_watching":true'))
M.set("time-pos", 1325)
M.advance(0.05)
check(M.overlay.data:find("STILL WATCHING?", 1, true) ~= nil, "asks after the autoplay limit")
check(M.overlay.data:find("Continue", 1, true) ~= nil and M.overlay.data:find("Stop", 1, true) ~= nil, "Continue / Stop")
M.advance(40)
check(#sent_all("bloom-play-next") == 2, "doesn't auto-continue when asking")
M.click(center(find_hit("card-play")))
local pn3 = sent_all("bloom-play-next")
check(#pn3 == 3 and pn3[3][2] == "user", "Continue plays next as a user action")
M.click(center(find_hit("card-cancel")))
check(M.commands[#M.commands][1] == "quit", "Stop closes the player")

-- ---------------------------------------------------------------- last episode: Skip Credits just ends
new_file(INFO:gsub('"next":{[^}]*}', '"next":null'):gsub('"has_next":true', '"has_next":false'))
M.message("bloom-segments", [=[[{"type":"Outro","start":1320,"end":1440}]]=])
M.set("time-pos", 1330)
M.advance(0.05)
check(M.overlay.data:find("Skip Credits", 1, true) ~= nil, "Skip Credits when there's no next episode")
check(M.overlay.data:find("UP NEXT", 1, true) == nil, "no card without a next episode")
M.click(center(find_hit("skip")))
local sk2 = last_seek()
check(sk2 and tonumber(sk2[2]) == 1440, "Skip Credits on the last episode seeks to the end")

-- ---------------------------------------------------------------- auto-skip mode
new_file(INFO:gsub('"skip_mode":"button"', '"skip_mode":"auto"'))
local before = #M.commands
M.set("time-pos", 92)
M.advance(0.05)
local auto = sent_all("bloom-autoskip")
check(#auto == 1, "auto-skip warns the app first (so it isn't counted as interaction)")
local sk3 = last_seek()
check(sk3 and tonumber(sk3[2]) == 180, "auto-skip jumps past the intro")
check(M.osd[#M.osd] == "Skipped Intro", "and says so")
check(M.overlay.data:find("Skip Intro", 1, true) == nil, "no button in auto mode")
M.set("time-pos", 100)                  -- user seeks back into the intro: don't fight them
M.advance(0.05)
check(#sent_all("bloom-autoskip") == 1, "each segment auto-skips only once")
-- auto mode at the credits with a next episode = play next
M.set("time-pos", 1321)
M.advance(0.05)
local pn4 = sent_all("bloom-play-next")
check(pn4[#pn4][2] == "auto", "auto-skipping credits goes straight to the next episode")
check(#M.commands > before, "commands were issued")

-- ---------------------------------------------------------------- controls visible: extras sit above them
new_file()
M.set("time-pos", 95)
M.move(900, 500)
M.advance(0.05)
local sb = find_hit("skip")
local seek = find_hit("seek")
check(sb and seek and sb.y2 < seek.y1, "skip button sits above the seek bar when controls are shown")

print(string.format("%d passed, %d failed", passed, failures))
os.exit(failures == 0 and 0 or 1)
