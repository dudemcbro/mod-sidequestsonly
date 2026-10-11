-------------------------------------------------------------------------------
-- SideQuestsOnly v2.1.0
-- WoW 3.3.5a (WotLK private server)
--
-- Adds a coloured category badge to every quest in your quest log,
-- shows the classification when you pick up a quest at an NPC,
-- and adds a badge to the quest log detail panel.
-- Categories: [Story] [Lore] [Attune] [Class] [Profession] [Daily] [PvP] [Holiday] [Event]
--             [Dungeon][Story] [Dungeon][Lore] [Dungeon][Side]
--             [Raid][Story] [Raid][Lore] [Raid][Side] [Side] [?]
--
-- The categories come from the server (mod-sidequestsonly): once loaded, the
-- addon runs .sqo sync over the AzerothCore addon channel and the server
-- answers with addon whispers, prefix SQO (see src/SideQuestsOnly.h).
--
-- Slash commands:
--   /sqo            - show addon status
--   /sqo check      - classify every quest currently in your log
--   /sqo <id>       - look up a specific quest ID
--   /sqo sync       - ask the server for the categories again
-------------------------------------------------------------------------------

local ADDON   = "SideQuestsOnly"
local VERSION = "2.1.0"
local PREFIX  = "SQO"

-- This client uses QuestLogScrollFrameButton1..N instead of QuestLogTitle1..N
local BUTTON_PREFIX = "QuestLogScrollFrameButton"
local NUM_BUTTONS   = nil   -- detected lazily when quest log is first open

local BADGE = {
    intro          = "|cFF5BC0DE[Intro]|r",                             -- Sky blue - retired, only in older server data
    story          = "|cFFFFD700[Story]|r",                              -- Gold    - drives zone/expansion narrative
    lore           = "|cFF40C040[Lore]|r",                              -- Green   - world-building, non-critical
    attune         = "|cFFFF8000[Attune]|r",                            -- Orange  - required for raid/dungeon access
    class          = "|cFFB048FF[Class]|r",                             -- Purple  - class-specific chain
    profession     = "|cFFCD853F[Profession]|r",                        -- Bronze  - tradeskill / profession-related
    daily          = "|cFF00FFFF[Daily]|r",                             -- Cyan    - repeatable daily
    pvp            = "|cFFFF4040[PvP]|r",                               -- Red     - battleground / Wintergrasp / PvP quest
    holiday        = "|cFFFF69B4[Holiday]|r",                           -- Pink    - seasonal event (Hallow's End, Brewfest, etc.)
    event          = "|cFFFF9900[Event]|r",                             -- Amber   - world/patch event (Scourge Invasion, AQ War Effort)
    dungeon_story  = "|cFF00B0B0[Dungeon]|r|cFFFFD700[Story]|r",       -- Teal+Gold  - dungeon quest with narrative
    dungeon_lore   = "|cFF00B0B0[Dungeon]|r|cFF40C040[Lore]|r",        -- Teal+Green - dungeon quest with lore content
    dungeon_side   = "|cFF00B0B0[Dungeon]|r|cFF888888[Side]|r",        -- Teal+Gray  - dungeon quest, filler content
    raid_story     = "|cFF4080FF[Raid]|r|cFFFFD700[Story]|r",          -- Blue+Gold  - raid quest with narrative
    raid_lore      = "|cFF4080FF[Raid]|r|cFF40C040[Lore]|r",           -- Blue+Green - raid quest with lore content
    raid_side      = "|cFF4080FF[Raid]|r|cFF888888[Side]|r",           -- Blue+Gray  - raid quest, filler content
    side           = "|cFF888888[Side]|r",                              -- Gray    - filler, no narrative value
    unknown        = "|cFF555555[?]|r",                                 -- Dark gray - NPC dialog only; truly unclassified
}

-- Strip WoW colour/link escape sequences so text comparisons work even when
-- the HD client sets styled text (e.g. "|cFF...|Quest Title|r").
local function stripEscapes(s)
    if not s then return nil end
    s = s:gsub("|c%x%x%x%x%x%x%x%x", "")  -- |cAARRGGBB colour start
    s = s:gsub("|r", "")                    -- colour reset
    s = s:gsub("|H[^|]+|h([^|]*)|h", "%1") -- hyperlink → keep display text
    s = s:gsub("|T[^|]+|t", "")            -- inline texture
    return strtrim(s)
end

local badges        = {}
local hooked        = false   -- QuestLog_Update hook
local hooked_detail = false   -- QuestLog_SetSelection + POI hooks

-- Reverse-lookup: lowercase quest title -> quest ID.
-- Built as quests are seen in the log and as the server names the quest of
-- each NPC quest dialog.
local titleToID = {}

-- questID -> category, from the server.  dataLoaded is false until the
-- first full list has arrived; until then nothing is badged [?].
local categories = {}
local dataLoaded = false
local incoming   = nil     -- the list being received (begin .. end)

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------

local function getNumButtons()
    if NUM_BUTTONS then return NUM_BUTTONS end
    local n = 0
    for i = 1, 40 do
        if _G[BUTTON_PREFIX .. i] then n = i else break end
    end
    if n > 0 then NUM_BUTTONS = n end
    return n
end

local function questIDFromIndex(logIndex)
    local ok, link = pcall(GetQuestLink, logIndex)
    if ok and link then
        return tonumber(link:match("|Hquest:(%d+):"))
    end
end

-- Returns the category string ("story", "lore", "attune", "class",
-- "profession", "daily", "pvp", "holiday", "event", "dungeon_story", "dungeon_lore",
-- "dungeon_side", "raid_story", "raid_lore", "raid_side", "side", or the retired "intro")
-- for a known quest ID, or nil when:
--   (a) the server's list has not arrived yet, OR
--   (b) the server has no category for the quest (truly unknown).
-- Callers distinguish these two nil meanings with dataLoaded:
-- nil + dataLoaded → unknown → show [?] in NPC dialog only.
local function classify(questID)
    if questID then
        return categories[questID]
    end
end

-- Scan the live quest log for a title match and return its ID.
-- Used as a last-resort fallback when the runtime cache misses
-- (e.g. first tooltip hover before the quest log has been opened).
local function titleToIDScan(title)
    if not title then return nil end
    local lkey = title:lower()
    local n = GetNumQuestLogEntries and GetNumQuestLogEntries() or 0
    for i = 1, n do
        local t, _, _, isHeader = GetQuestLogTitle(i)
        if t and isHeader ~= true and isHeader ~= 1 and t:lower() == lkey then
            local qid = questIDFromIndex(i)
            if qid then
                titleToID[lkey] = qid  -- cache it for next time
                return qid
            end
        end
    end
end

-- Look up a quest ID by title: runtime cache → live scan.
local function titleLookup(title)
    if not title then return nil end
    return titleToID[title:lower()] or titleToIDScan(title)
end

-------------------------------------------------------------------------------
-- Quest log badge overlay
-------------------------------------------------------------------------------

local function ensureBadges(n)
    for i = 1, n do
        if not badges[i] then
            local btn = _G[BUTTON_PREFIX .. i]
            if btn then
                local fs = btn:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
                fs:SetPoint("RIGHT", btn, "RIGHT", -6, 0)
                fs:SetJustifyH("RIGHT")
                badges[i] = fs
            end
        end
    end
end

local function refreshBadges()
    NUM_BUTTONS = nil
    local n = getNumButtons()
    if n == 0 then return end

    ensureBadges(n)

    for i = 1, n do
        local badge = badges[i]
        if badge then
            badge:SetText("")
            local btn = _G[BUTTON_PREFIX .. i]
            if btn and btn:IsShown() then
                local logIndex = btn:GetID()
                if logIndex and logIndex > 0 then
                    local title, _, _, isHeader = GetQuestLogTitle(logIndex)
                    if title and isHeader ~= true and isHeader ~= 1 then
                        local qid = questIDFromIndex(logIndex)
                        -- Zone-header entries have no quest link so questIDFromIndex
                        -- returns nil for them.  Guard here instead of relying on the
                        -- isHeader field, whose value varies between HD client builds.
                        if qid then
                            titleToID[title:lower()] = qid
                            local qtype = classify(qid)
                            if qtype then
                                badge:SetText(BADGE[qtype])
                                -- Find the tag text region "(Complete)", "(Group)", etc.
                                -- Scan btn's direct regions first; if not found, check
                                -- one level of child frames (HD client may store the tag
                                -- in a child frame rather than a direct region).
                                local tagRegion = nil
                                local function findTag(f)
                                    local ok, regions = pcall(function() return {f:GetRegions()} end)
                                    if ok then
                                        for _, r in ipairs(regions) do
                                            if r ~= badge and r.GetText then
                                                local rt = r:GetText()
                                                if rt and rt ~= "" and rt:sub(1, 1) == "(" then
                                                    tagRegion = r
                                                    return
                                                end
                                            end
                                        end
                                    end
                                end
                                findTag(btn)
                                if not tagRegion then
                                    local ok2, ch = pcall(function() return {btn:GetChildren()} end)
                                    if ok2 then
                                        for _, child in ipairs(ch) do
                                            findTag(child)
                                            if tagRegion then break end
                                        end
                                    end
                                end
                                badge:ClearAllPoints()
                                if tagRegion then
                                    badge:SetPoint("RIGHT", tagRegion, "LEFT", -4, 0)
                                else
                                    badge:SetPoint("RIGHT", btn, "RIGHT", -6, 0)
                                end
                            end
                        end
                    end
                end
            end
        end
    end
end

-------------------------------------------------------------------------------
-- NPC quest-dialog badge
--
-- HD clients use a fully custom frame for the NPC quest accept dialog - it is
-- NOT the standard QuestFrame.  We use a one-frame-delay scan: on QUEST_DETAIL
-- we store the pending quest, show a hidden ticker frame, and on its very next
-- OnUpdate tick (after the custom dialog has been shown) we walk the visible
-- UIParent tree looking for any frame whose text region exactly matches the
-- quest title, skipping known unrelated frames (quest log, world map, etc.).
-- Badges are stored by frame reference so they survive repeated shows.
-------------------------------------------------------------------------------

local _npcPendingTitle = nil
local _npcPendingType  = nil
local _npcDebug        = false  -- enabled by /sqo npc; auto-clears after one scan
local _sqoFromGreeting = false  -- set by QUEST_GREETING; tells QUEST_DETAIL to delay

-- Single overlay frame parented to UIParent so it is NEVER clipped by the
-- scroll-frame stencil inside the HD quest-accept dialog.  We position it
-- relative to the matched title frame via SetPoint, which works across frame
-- hierarchies without causing clip inheritance.
local _npcBadgeOverlay = nil
local function getNpcBadgeOverlay()
    if _npcBadgeOverlay then return _npcBadgeOverlay end
    local f = CreateFrame("Frame", nil, UIParent)
    f:SetFrameStrata("TOOLTIP")
    f:SetSize(120, 22)
    f:Hide()
    local fs = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    fs:SetAllPoints(f)
    fs:SetJustifyH("RIGHT")
    f._text = fs
    _npcBadgeOverlay = f
    return f
end

local _npcScanFrame = CreateFrame("Frame")
_npcScanFrame:Hide()

-- Scroll-change detector: polls button logIndices at ~20 Hz when the quest
-- log is open.  Used because OnVerticalScroll may not fire in HD clients that
-- implement scrolling without the standard ScrollFrame widget.
local _scrollPoll         = CreateFrame("Frame")
local _scrollPollTimer    = 0
local _scrollPollIndices  = {}
_scrollPoll:Hide()
_scrollPoll:SetScript("OnUpdate", function(self, elapsed)
    _scrollPollTimer = _scrollPollTimer + elapsed
    if _scrollPollTimer < 0.05 then return end  -- ~20 Hz
    _scrollPollTimer = 0
    local changed = false
    local n = getNumButtons()
    for i = 1, n do
        local btn = _G[BUTTON_PREFIX .. i]
        local idx = btn and btn:GetID() or 0
        if _scrollPollIndices[i] ~= idx then
            _scrollPollIndices[i] = idx
            changed = true
        end
    end
    if changed then refreshBadges() end
end)

-- Scan the visible UI tree for all FontStrings that exactly match `title`,
-- then badge the one with the highest on-screen Y coordinate.
--
-- WHY "highest Y":  the quest title is always the TOP-MOST piece of text in
-- the NPC dialog.  Description and objectives text (which may contain the NPC
-- name or other short strings identical to the title) appears lower.  Picking
-- the highest-Y match reliably selects the title regardless of DFS order.
local function scanForNPCQuestBadge(title, qtype)
    local overlay = getNpcBadgeOverlay()
    overlay:Hide()
    if not title then return nil end

    local lkey = title:lower()
    if _npcDebug then
        print(string.format("|cFFFFD700[SQO-NPC]|r Scanning for: \"%s\"", title))
    end

    -- Frames whose subtrees we skip (quest log, world map, tracker, chat).
    -- Chat frames MUST be skipped: they render previous [SQO] chat messages
    -- as FontStrings, causing false title matches against the quest title.
    local skip = {}
    if QuestLogFrame    then skip[QuestLogFrame]    = true end
    if WorldMapFrame    then skip[WorldMapFrame]    = true end
    if WatchFrame       then skip[WatchFrame]       = true end
    if MiniMapCluster   then skip[MiniMapCluster]   = true end
    for i = 1, 10 do
        local cf = _G["ChatFrame" .. i]
        if cf then skip[cf] = true end
    end

    -- Best match found so far: the candidate whose FontString is highest
    -- on screen (largest GetTop() value = closest to the top of the dialog).
    local bestFrame    = nil   -- frame that owns the winning FontString
    local bestRegion   = nil   -- the winning FontString itself
    local bestTopFrame = nil   -- UIParent-level child that contains bestFrame
    local bestY        = -math.huge

    -- Recursive scan.  Does NOT short-circuit: we visit the entire reachable
    -- tree so we can compare ALL matches and keep the topmost one.
    local function scan(f, depth, topFrame)
        if depth > 10 or skip[f] then return end

        local okR, regions = pcall(function() return {f:GetRegions()} end)
        if okR then
            for _, r in ipairs(regions) do
                if r.GetText then
                    local raw = r:GetText()
                    local t   = stripEscapes(raw)
                    if _npcDebug and t and #t > 2 then
                        print(string.format("|cFFAAAAAA[SQO-NPC]|r [d%d] \"%s\"", depth, t))
                    end
                    if t and t:lower() == lkey then
                        -- If qtype is still unknown, try recovering the quest ID
                        -- from a hyperlink embedded in the raw FontString text.
                        -- HD clients often encode the title as |Hquest:ID:lvl|h[Title]|h.
                        if not qtype then
                            local extractedQID = raw and
                                tonumber(raw:match("|Hquest:(%d+):"))
                            if extractedQID then
                                titleToID[lkey] = extractedQID
                                qtype = classify(extractedQID)
                                if _npcDebug then
                                    print(string.format(
                                        "|cFFAAAAAA[SQO-NPC]|r Extracted QID=%d qtype=%s",
                                        extractedQID, tostring(qtype)))
                                end
                            end
                        end
                        -- Keep the match that is highest on screen.
                        local ry = r:GetTop() or -math.huge
                        if _npcDebug then
                            print(string.format(
                                "|cFF00FF00[SQO-NPC]|r Match [d%d] Y=%.0f qtype=%s",
                                depth, ry, tostring(qtype)))
                        end
                        if ry > bestY then
                            bestY        = ry
                            bestFrame    = f
                            bestRegion   = r
                            bestTopFrame = topFrame
                        end
                    end
                end
            end
        end

        local okC, children = pcall(function() return {f:GetChildren()} end)
        if okC then
            for _, child in ipairs(children) do
                -- No IsShown() filter: HD scroll children often report
                -- IsShown()=false even while their content is visible.
                if not skip[child] then
                    scan(child, depth + 1, topFrame)
                end
            end
        end
    end

    -- Primary: UIParent tree.  Pass the top-level child as `topFrame` so
    -- scan() can record which dialog frame ultimately contained the match.
    local ok, children = pcall(function() return {UIParent:GetChildren()} end)
    if ok then
        for _, child in ipairs(children) do
            -- child.AddMessage identifies chat frames (including non-standard
            -- names used in some HD client builds) - skip their subtrees.
            if child:IsShown() and not skip[child] and not child.AddMessage then
                scan(child, 0, child)
            end
        end
    end

    -- Fallback: WorldFrame (some HD clients parent dialogs here instead)
    if not bestFrame and WorldFrame then
        local ok2, wfc = pcall(function() return {WorldFrame:GetChildren()} end)
        if ok2 then
            for _, child in ipairs(wfc) do
                if child:IsShown() and not skip[child] and not child.AddMessage then
                    scan(child, 0, child)
                end
            end
        end
    end

    if bestFrame then
        overlay:ClearAllPoints()
        -- Position absolutely via UIParent so we aren't affected by bestFrame's
        -- own size or the scrollbar that sits at bestFrame's right edge.
        -- X: dialog's true right edge (bestTopFrame) minus 85 px to clear the
        --    scrollbar (~30 px), frame border art (~10 px), and the torn-parchment
        --    decorative edge (~15 px) so the badge reads cleanly on plain content.
        -- Y: bestY is the screen Y of the matching FontString's top edge, which
        --    is exactly where we want the badge's top to sit.
        local rx = ((bestTopFrame or bestFrame):GetRight() or 0) - 85
        local ry = bestY
        overlay:SetPoint("TOPRIGHT", UIParent, "BOTTOMLEFT", rx, ry)
        -- Show [?] when DB is loaded but this quest was never classified.
        -- [?] is intentionally scoped to the NPC accept dialog only -
        -- it does NOT appear in the quest log (refreshBadges already omits nil).
        local dbLoaded = dataLoaded
        overlay._text:SetText(
            qtype and BADGE[qtype] or
            (dbLoaded and BADGE.unknown or ""))
        overlay:Show()
        if _npcDebug then
            print(string.format(
                "|cFF00FF00[SQO-NPC]|r Badge placed - bestY=%.0f qtype=%s",
                bestY, tostring(qtype)))
        end
    elseif _npcDebug then
        print("|cFFFF4444[SQO-NPC]|r Scan complete: NO MATCH found.")
    end

    if _npcDebug then _npcDebug = false end  -- auto-reset after one trace
    return bestFrame
end

_npcScanFrame:SetScript("OnUpdate", function(self)
    -- For single-quest NPCs the detail dialog is immediate (startAt=2).
    -- For multi-quest NPCs we came through QUEST_GREETING first; wait ~30
    -- frames for the dialog to finish transitioning before the first scan.
    -- After the start delay, retry every other frame for up to ~3 s total.
    self._count = (self._count or 0) + 1
    local startAt = self._startAt or 2
    if self._count < startAt then return end
    if (self._count - startAt) % 2 ~= 0 then return end
    local found = scanForNPCQuestBadge(_npcPendingTitle, _npcPendingType)
    if found then
        -- Badge placed; stop scanning. Overlay is hidden by QUEST_FINISHED.
        self:Hide()
        self._count  = 0
        self._startAt = nil
        _npcPendingTitle = nil
        _npcPendingType  = nil
    elseif self._count >= (startAt + 90) then
        -- Timed out - dialog may not contain the title we expected.
        self:Hide()
        self._count  = 0
        self._startAt = nil
        _npcPendingTitle = nil
        _npcPendingType  = nil
    end
end)

-- Legacy path: still try QuestFrame in case the client uses the standard frame
local questFrameBadge = nil

local function ensureQuestFrameBadge()
    if questFrameBadge then return end
    local parent = (QuestTitleText and QuestTitleText:GetParent())
                or (QuestFrameAcceptButton and QuestFrameAcceptButton:GetParent())
                or (QuestDetailScrollChildFrame and QuestDetailScrollChildFrame:GetParent())
                or QuestFrame
    if not parent then return end
    local fs = parent:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    fs:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -16, -8)
    fs:SetJustifyH("RIGHT")
    questFrameBadge = fs
end

local function updateQuestFrameBadge(qtype)
    ensureQuestFrameBadge()
    if questFrameBadge then
        questFrameBadge:SetText(qtype and BADGE[qtype] or "")
    end
end

-------------------------------------------------------------------------------
-- Quest log detail panel badge (right side of the quest log)
-------------------------------------------------------------------------------

local questLogDetailBadge = nil  -- FontString next to the quest title

local function ensureQuestLogDetailBadge()
    if questLogDetailBadge then return end
    local fs
    if QuestLogQuestTitle then
        -- Standard client: anchor right next to the title text
        local parent = QuestLogQuestTitle:GetParent()
        if parent then
            fs = parent:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
            fs:SetPoint("LEFT", QuestLogQuestTitle, "RIGHT", 6, 0)
        end
    else
        -- HD client: anchor to the TOP-RIGHT of the detail area so the badge
        -- never overlaps the quest title text (which renders at top-left).
        local parent = QuestLogDetailScrollChildFrame
                    or QuestLogDescriptionFrame
                    or QuestLogFrame
        if parent then
            fs = parent:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
            fs:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -10, -8)
        end
    end
    questLogDetailBadge = fs
end

local function updateQuestLogDetailBadge(qtype)
    ensureQuestLogDetailBadge()
    if questLogDetailBadge then
        questLogDetailBadge:SetText(qtype and BADGE[qtype] or "")
    end
end

-------------------------------------------------------------------------------
-- Tooltip (quest log list hover)
-------------------------------------------------------------------------------


-------------------------------------------------------------------------------
-- Generic frame-tree badge scanner
--
-- HD clients use custom, unnamed frames for the world-map quest list and the
-- bottom-left quest detail panel.  Instead of hooking specific frame names we
-- walk the entire visible subtree of a root frame, find any frame whose text
-- regions contain a known quest title, and stamp a [Main]/[Side] badge onto
-- it.  Badges are keyed by frame reference so they survive repeated refreshes.
-------------------------------------------------------------------------------

local mapBadges  = {}   -- [frame ref] → FontString  (WorldMapFrame tree)
local watchBadges = {}  -- [frame ref] → FontString  (WatchFrame tree)
local watchHooked = false

-- Scan a frame tree, badge any visible frame whose region text is a quest title.
-- `store`  : the table to persist badge FontStrings in (mapBadges or watchBadges)
-- `anchor` : "RIGHT" or "TOPRIGHT" - where to attach the badge on the frame
-- `offx/y` : x/y offset for SetPoint
local function scanTreeForQuestTitles(root, store, anchor, offx, offy, depth)
    if depth > 10 then return end

    -- Check this frame's own text regions.
    -- If a region's text exactly matches a quest title, badge this frame and
    -- STOP recursing - we don't want to badge both a container and its title
    -- child, which would produce two overlapping badges for the same quest.
    local okR, regions = pcall(function() return {root:GetRegions()} end)
    if okR then
        for _, r in ipairs(regions) do
            if r.GetText then
                local t = stripEscapes(r:GetText())
                if t and t ~= "" then
                    local qid = titleLookup(t)
                    if qid then
                        -- Ensure badge exists on this frame
                        if not store[root] then
                            local fs = root:CreateFontString(
                                nil, "OVERLAY", "GameFontNormalSmall")
                            fs:SetPoint(anchor, root, anchor, offx, offy)
                            fs:SetJustifyH("RIGHT")
                            store[root] = fs
                        end
                        local qtype = classify(qid)
                        store[root]:SetText(qtype and BADGE[qtype] or "")
                        return   -- matched: do NOT recurse into children
                    end
                end
            end
        end
    end

    -- No match at this level - recurse into visible children
    local okC, children = pcall(function() return {root:GetChildren()} end)
    if okC then
        for _, child in ipairs(children) do
            if child:IsShown() then
                scanTreeForQuestTitles(child, store, anchor, offx, offy, depth + 1)
            end
        end
    end
end

local function refreshWatchBadges()
    -- Clear
    for _, fs in pairs(watchBadges) do fs:SetText("") end
    -- WatchFrame (standard WoW objective tracker)
    if WatchFrame and WatchFrame:IsShown() then
        scanTreeForQuestTitles(WatchFrame, watchBadges, "RIGHT", -2, 0, 0)
    end
end

local function refreshWorldMapBadges()
    -- Clear
    for _, fs in pairs(mapBadges) do fs:SetText("") end
    if not (WorldMapFrame and WorldMapFrame:IsShown()) then return end

    -- Only scan top-level children of WorldMapFrame whose screen centre is in
    -- the RIGHT half of the map frame.  This excludes the bottom-left
    -- quest-detail popup (shown when clicking a POI) while still covering
    -- the right-side quest-tracker panel.
    local mapLeft  = WorldMapFrame:GetLeft()  or 0
    local mapWidth = WorldMapFrame:GetWidth() or 0
    local midX     = mapLeft + mapWidth * 0.5

    local ok, children = pcall(function() return {WorldMapFrame:GetChildren()} end)
    if not ok then return end
    for _, child in ipairs(children) do
        if child:IsShown() then
            local cx = child:GetCenter()
            if cx and cx > midX then
                scanTreeForQuestTitles(child, mapBadges, "TOPRIGHT", -10, -8, 0)
            end
        end
    end
end


-------------------------------------------------------------------------------
-- Event / hook registration
-------------------------------------------------------------------------------

local function setupHooks()
    -- Quest log list refresh
    if not hooked and QuestLog_Update then
        hooksecurefunc("QuestLog_Update", refreshBadges)
        hooked = true
    end
    -- Start the scroll-change poll when the quest log is visible.
    -- The poll checks button logIndices at ~20 Hz and calls refreshBadges()
    -- when any index changes.  This is framework-agnostic: it works even when
    -- the HD client's scroll does not fire OnVerticalScroll.
    if QuestLogFrame and not QuestLogFrame._sqoScrollPollHooked then
        QuestLogFrame:HookScript("OnShow", function() _scrollPoll:Show() end)
        QuestLogFrame:HookScript("OnHide", function() _scrollPoll:Hide() end)
        QuestLogFrame._sqoScrollPollHooked = true
    end
    if QuestLogFrame and QuestLogFrame:IsShown() then _scrollPoll:Show() end

    if not hooked_detail then
        -- Quest log detail panel: update badge when the user selects a quest
        if QuestLog_SetSelection then
            hooksecurefunc("QuestLog_SetSelection", function(index)
                if not index or index == 0 then
                    updateQuestLogDetailBadge(nil)
                    return
                end
                local title, _, _, isHeader = GetQuestLogTitle(index)
                if not title or isHeader == 1 or isHeader == true then
                    updateQuestLogDetailBadge(nil)
                    return
                end
                local qid = questIDFromIndex(index) or titleLookup(title)
                updateQuestLogDetailBadge(classify(qid))
            end)
        end

        hooked_detail = true
    end

    -- WatchFrame (standard WoW objective tracker)
    if not watchHooked then
        if WatchFrame_Update then
            hooksecurefunc("WatchFrame_Update", refreshWatchBadges)
            watchHooked = true
        end
        if WatchFrame and not WatchFrame._sqoWatchHooked then
            WatchFrame:HookScript("OnShow", refreshWatchBadges)
            WatchFrame._sqoWatchHooked = true
        end
    end

    -- WorldMapFrame: badge the right-panel quest list and the bottom-left
    -- quest-detail panel used by HD clients (custom frame names unknown,
    -- so we scan the whole visible subtree on every relevant event).
    -- WorldMapFrame:OnShow badges the right-side tracker when the map opens.
    -- We intentionally do NOT hook OnMouseUp or QuestPOI_OnClick: those fire
    -- when the user clicks a quest POI, which causes the bottom-left detail
    -- panel to appear - badging after that click would stamp a badge on that
    -- panel too.  WORLD_MAP_UPDATE and QUEST_LOG_UPDATE cover tracker updates.
    if WorldMapFrame and not WorldMapFrame._sqoMapHooked then
        WorldMapFrame:HookScript("OnShow", refreshWorldMapBadges)
        WorldMapFrame._sqoMapHooked = true
    end

    refreshBadges()
    refreshWatchBadges()
    if WorldMapFrame and WorldMapFrame:IsShown() then
        refreshWorldMapBadges()
    end
end

-------------------------------------------------------------------------------
-- Server data (mod-sidequestsonly)
-------------------------------------------------------------------------------

local syncCount    = 0
local syncWaiting  = false
local syncElapsed  = 0
local SYNC_TIMEOUT = 15   -- seconds before saying the server didn't answer

local syncTimer = CreateFrame("Frame")
syncTimer:Hide()
syncTimer:SetScript("OnUpdate", function(self, elapsed)
    syncElapsed = syncElapsed + elapsed
    if syncElapsed < SYNC_TIMEOUT then return end
    self:Hide()
    if syncWaiting and not dataLoaded then
        print("|cFFFFD700[SQO]|r The server sent no quest categories. "
            .. "It needs mod-sidequestsonly; /sqo sync asks again.")
    end
    syncWaiting = false
end)

-- Runs .sqo sync over the core's addon command channel: no chat line, and
-- the answer comes back as SQO addon whispers.
local function requestSync()
    syncCount = syncCount % 9999 + 1
    SendAddonMessage("AzerothCore", ("i%04dsqo sync"):format(syncCount),
        "WHISPER", UnitName("player"))
    syncWaiting = true
    syncElapsed = 0
    syncTimer:Show()
end

local function refreshAll()
    refreshBadges()
    refreshWatchBadges()
    if WorldMapFrame and WorldMapFrame:IsShown() then
        refreshWorldMapBadges()
    end
    if QuestLogFrame and QuestLogFrame:IsShown() and GetQuestLogSelection then
        local index = GetQuestLogSelection()
        if index and index > 0 then
            updateQuestLogDetailBadge(classify(questIDFromIndex(index)))
        end
    end
end

-- One c row: comma list of base 36 gaps from the previous quest, "+n" for
-- the n quests that follow one by one.
local function decodeRow(target, category, ids)
    local previous = 0
    for token in ids:gmatch("[^,]+") do
        local gap, run = token:match("^(%w+)%+(%w+)$")
        gap = tonumber(gap or token, 36)
        run = run and tonumber(run, 36) or 0
        if gap then
            local first = previous + gap
            for questID = first, first + run do
                target[questID] = category
            end
            previous = first + run
        end
    end
end

local function split(message)
    local fields = {}
    for field in (message .. "\t"):gmatch("([^\t]*)\t") do
        fields[#fields + 1] = field
    end
    return fields
end

local function onServerMessage(message)
    local f = split(message)
    local kind = f[1]
    if kind == "begin" then
        incoming = {}
    elseif kind == "c" and incoming and f[2] and f[3] then
        decodeRow(incoming, f[2], f[3])
    elseif kind == "end" and incoming then
        categories = incoming
        incoming   = nil
        local n = 0
        for _ in pairs(categories) do n = n + 1 end
        if not dataLoaded then
            print(string.format("|cFFFFD700SideQuestsOnly|r v%s  |cFF00FF00%d|r quests classified by the server.",
                VERSION, n))
        end
        dataLoaded  = true
        syncWaiting = false
        refreshAll()
    elseif kind == "set" then
        local questID = tonumber(f[2])
        if questID and f[3] then
            categories[questID] = f[3]
            if incoming then incoming[questID] = f[3] end
            refreshAll()
        end
    elseif kind == "del" then
        local questID = tonumber(f[2])
        if questID then
            categories[questID] = nil
            if incoming then incoming[questID] = nil end
            refreshAll()
        end
    elseif kind == "q" then
        -- Sent just before an NPC quest dialog opens: its quest ID, category
        -- ("?" when unclassified) and title as the client shows it.
        local questID, category, title = tonumber(f[2]), f[3], f[4]
        if questID and title and title ~= "" then
            titleToID[title:lower()] = questID
            if category and category ~= "?" then
                categories[questID] = category
            end
        end
    end
end

-------------------------------------------------------------------------------
-- Event frame
-------------------------------------------------------------------------------

local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:RegisterEvent("QUEST_LOG_UPDATE")
frame:RegisterEvent("QUEST_GREETING")  -- NPC has multiple quests/options
frame:RegisterEvent("QUEST_DETAIL")    -- player views a quest at an NPC
frame:RegisterEvent("QUEST_ACCEPTED")  -- player clicks Accept
frame:RegisterEvent("QUEST_FINISHED")  -- quest dialog closed (accept OR decline)
frame:RegisterEvent("WORLD_MAP_UPDATE") -- world map opened / zone changed
frame:RegisterEvent("CHAT_MSG_ADDON")   -- the server's quest categories

frame:SetScript("OnEvent", function(self, event, arg1, arg2, arg3, arg4)
    -------------------------------------------------------------------------
    if event == "ADDON_LOADED" and arg1 == ADDON then
        if QuestLogFrame then
            QuestLogFrame:HookScript("OnShow", setupHooks)
        end
        if QuestLogFrame and QuestLogFrame:IsVisible() then setupHooks() end

    -------------------------------------------------------------------------
    elseif event == "PLAYER_ENTERING_WORLD" then
        setupHooks()
        -- Once a session (and after /reload); not on every loading screen.
        if not dataLoaded and not syncWaiting then
            requestSync()
        end

    -------------------------------------------------------------------------
    elseif event == "CHAT_MSG_ADDON" then
        -- arg1 = prefix, arg2 = message, arg3 = channel, arg4 = sender
        if arg1 == PREFIX and arg3 == "WHISPER" and arg4 == UnitName("player") then
            onServerMessage(arg2)
        end

    -------------------------------------------------------------------------
    elseif event == "QUEST_LOG_UPDATE" then
        refreshBadges()
        if WorldMapFrame and WorldMapFrame:IsShown() then
            refreshWorldMapBadges()
        end

    -------------------------------------------------------------------------
    elseif event == "WORLD_MAP_UPDATE" then
        -- Refresh both the standard WatchFrame tracker and the world-map panels
        refreshWatchBadges()
        refreshWorldMapBadges()

    -------------------------------------------------------------------------
    elseif event == "QUEST_GREETING" then
        -- NPC has multiple quests or options (greeting list shown first).
        -- Flag this so QUEST_DETAIL knows to delay its scan.
        _sqoFromGreeting = true

    -------------------------------------------------------------------------
    elseif event == "QUEST_DETAIL" then
        -- Player is viewing a quest at an NPC (hasn't accepted yet).
        -- The server named this dialog's quest just before it opened (the
        -- q message), so the title finds its ID even on first encounter.
        local title = GetTitleText and GetTitleText()
        if not title then return end
        local qid   = titleToID[title:lower()]
        local qtype = classify(qid)

        -- Use the UIParent overlay scan.  QuestFrame exists in the HD client
        -- but is not the visible quest dialog, so the direct QuestFrame path
        -- is skipped here.  The scan finds the actual dialog regardless of
        -- frame name, anchors the overlay to it, and is reliable for both
        -- single-quest and multi-quest (post-greeting) NPCs.
        --
        -- For multi-quest NPCs: QUEST_GREETING fires first; we wait ~30 frames
        -- for the greeting→detail transition before the first scan attempt.
        -- For single-quest NPCs: no greeting screen, so 2-frame start.
        _npcPendingTitle     = title
        _npcPendingType      = qtype
        _npcScanFrame._count  = 0
        _npcScanFrame._startAt = _sqoFromGreeting and 30 or 2
        _sqoFromGreeting     = false   -- consume the flag
        _npcScanFrame:Show()

        -- Chat notification (mirrors NPC dialog: show [?] for unknown quests
        -- when the database is loaded; silent while the database is loading)
        do
            local dbLoaded = dataLoaded
            local badge = qtype and BADGE[qtype]
                       or (dbLoaded and BADGE.unknown)
            if badge then
                print(string.format("|cFFFFD700[SQO]|r %s -- %s", title, badge))
            end
        end

    -------------------------------------------------------------------------
    elseif event == "QUEST_ACCEPTED" then
        -- arg1 = quest log index, arg2 = quest ID  (3.3.5a QUEST_ACCEPTED)
        local questID = tonumber(arg2)
        if not questID then
            -- Fallback: derive from quest link using log index
            questID = questIDFromIndex(tonumber(arg1))
        end
        if not questID then return end

        -- Update title->ID cache for future QUEST_DETAIL lookups
        local title = arg1 and GetQuestLogTitle(tonumber(arg1))
        if title then titleToID[title:lower()] = questID end

        local qtype = classify(questID)
        if not qtype then return end

        -- Chat notification
        print(string.format("|cFFFFD700[SQO]|r Accepted: %s  %s",
            title or ("Quest #" .. questID), BADGE[qtype]))

    -------------------------------------------------------------------------
    elseif event == "QUEST_FINISHED" then
        -- Quest dialog closed (player accepted, declined, or pressed Escape).
        -- Clear both the QuestFrame direct badge and the UIParent overlay.
        if questFrameBadge then questFrameBadge:SetText("") end
        if _npcBadgeOverlay then _npcBadgeOverlay:Hide() end
        _npcScanFrame:Hide()
        _npcScanFrame._count  = 0
        _npcScanFrame._startAt = nil
        _npcPendingTitle = nil
        _npcPendingType  = nil
        _sqoFromGreeting = false
    end
end)

-------------------------------------------------------------------------------
-- Slash commands
-------------------------------------------------------------------------------

SLASH_SIDEQUESTSONLY1 = "/sqo"

SlashCmdList["SIDEQUESTSONLY"] = function(input)
    input = strtrim(input or "")

    local id = tonumber(input)
    if id then
        local qtype = classify(id)
        if qtype then
            local desc = {
                intro          = "Starting zone quest (older server data)",
                story          = "Drives zone/expansion narrative",
                lore           = "World-building; optional but meaningful",
                attune         = "Required for raid/dungeon access",
                class          = "Class-specific quest chain",
                profession     = "Profession / tradeskill quest or reward",
                daily          = "Repeatable daily quest",
                pvp            = "Battleground / Wintergrasp / PvP quest",
                holiday        = "Seasonal event (Hallow's End, Brewfest, etc.)",
                event          = "World/patch event (Scourge Invasion, AQ War Effort)",
                dungeon_story  = "Dungeon quest with real narrative",
                dungeon_lore   = "Dungeon quest with lore content",
                dungeon_side   = "Dungeon quest - filler content",
                raid_story     = "Raid quest with real narrative",
                raid_lore      = "Raid quest with lore content",
                raid_side      = "Raid quest - filler content",
                side           = "Filler - no narrative value",
            }
            print(string.format("|cFFFFD700[SQO]|r Quest %d: %s  %s",
                id, BADGE[qtype], desc[qtype] or qtype))
        else
            local dbLoaded = dataLoaded
            if dbLoaded then
                print(string.format("|cFFFFD700[SQO]|r Quest %d: %s never classified.",
                    id, BADGE.unknown))
            else
                print(string.format("|cFFFFD700[SQO]|r Quest %d: not classified (data not loaded?).", id))
            end
        end
        return
    end

    if input:lower() == "frames" then
        -- Dump child frames and FontStrings of quest-related globals.
        -- Run this while the quest accept dialog or quest log detail is open
        -- to find the correct frame/FontString names in this HD client.
        local function dumpFrame(name, f)
            if not f then print("  " .. name .. " = nil"); return end
            print("|cFFFFD700" .. name .. "|r")
            -- Child frames
            local ok, children = pcall(function() return {f:GetChildren()} end)
            if ok then
                for _, c in ipairs(children) do
                    print("  child frame: " .. (c:GetName() or "(unnamed)"))
                end
            end
            -- Regions (FontStrings/Textures)
            local ok2, regions = pcall(function() return {f:GetRegions()} end)
            if ok2 then
                for _, r in ipairs(regions) do
                    local rname = (r.GetName and r:GetName()) or "(unnamed)"
                    if r.GetText then
                        print("  FontString: " .. rname .. ' = "' .. tostring(r:GetText()) .. '"')
                    end
                end
            end
        end
        dumpFrame("QuestFrame",              QuestFrame)
        dumpFrame("QuestLogFrame",           QuestLogFrame)
        dumpFrame("QuestDetailScrollChildFrame", QuestDetailScrollChildFrame)
        dumpFrame("QuestLogDetailScrollChildFrame", QuestLogDetailScrollChildFrame)
        -- If world map is open, dump its named children (helps identify HD panels)
        if WorldMapFrame and WorldMapFrame:IsShown() then
            print("|cFFFFD700WorldMapFrame children:|r")
            local ok, ch = pcall(function() return {WorldMapFrame:GetChildren()} end)
            if ok then
                for _, c in ipairs(ch) do
                    local n = c:GetName() or "(unnamed)"
                    local shown = c:IsShown() and "shown" or "hidden"
                    print("  " .. n .. " [" .. shown .. "]")
                    -- one level deeper for unnamed containers
                    local ok2, ch2 = pcall(function() return {c:GetChildren()} end)
                    if ok2 then
                        for _, c2 in ipairs(ch2) do
                            local n2 = c2:GetName() or "(unnamed)"
                            local s2 = c2:IsShown() and "shown" or "hidden"
                            -- print text regions if they have quest-looking text
                            for _, r in ipairs({c2:GetRegions()}) do
                                if r.GetText then
                                    local t = r:GetText()
                                    if t and t ~= "" then
                                        print("    " .. n2 .. " [" .. s2 .. "] text=\"" .. t .. "\"")
                                        break
                                    end
                                end
                            end
                        end
                    end
                end
            end
        end
        return
    end

    if input:lower() == "npc" then
        -- Enable NPC debug trace: the NEXT quest-accept dialog scan will print
        -- every text region it finds, so you can see what text the dialog contains
        -- and whether the title matches what GetTitleText() returned.
        _npcDebug = true
        print("|cFFFFD700[SQO]|r NPC trace ON - talk to an NPC and click a quest.")
        print("  Every text region found will be printed to chat.")
        print("  (Trace auto-disables after one scan.)")
        return
    end

    if input:lower() == "debug" then
        local watchCount, mapCount = 0, 0
        for _ in pairs(watchBadges) do watchCount = watchCount + 1 end
        for _ in pairs(mapBadges)   do mapCount   = mapCount   + 1 end
        print("|cFFFFD700[SQO]|r Hook status:")
        print("  QuestLog_Update hooked:     " .. tostring(hooked))
        print("  detail hooks installed:     " .. tostring(hooked_detail))
        print("  WatchFrame_Update hooked:   " .. tostring(watchHooked))
        print("  WatchFrame badge frames:    " .. watchCount)
        print("  WorldMap badge frames:      " .. mapCount)
        print("  WorldMapFrame hooked:       " .. tostring(WorldMapFrame and WorldMapFrame._sqoMapHooked == true))
        print("  QuestLog_SetSelection exists:" .. tostring(QuestLog_SetSelection ~= nil))
        print("  QuestLogQuestTitle exists:  " .. tostring(QuestLogQuestTitle ~= nil))
        print("  QuestTitleText exists:      " .. tostring(QuestTitleText ~= nil))
        print("  questFrameBadge created:    " .. tostring(questFrameBadge ~= nil))
        print("  questLogDetailBadge created:" .. tostring(questLogDetailBadge ~= nil))
        print("  titleToID entries:          " .. (function() local n=0 for _ in pairs(titleToID) do n=n+1 end return n end)())
        return
    end

    if input:lower() == "check" then
        local dbLoaded = dataLoaded
        local count = GetNumQuestLogEntries()
        print("|cFFFFD700[SQO]|r Quests in your log:")
        for i = 1, count do
            local title, _, _, isHeader = GetQuestLogTitle(i)
            if title and isHeader ~= true and isHeader ~= 1 then
                local qid   = questIDFromIndex(i)
                local qtype = classify(qid)
                local tag   = qtype and BADGE[qtype]
                           or (dbLoaded and BADGE.unknown or "|cFF444444[?]|r")
                print(string.format("  %s %s (id:%s)", tag, title, tostring(qid)))
            end
        end
        return
    end

    if input:lower() == "sync" then
        print("|cFFFFD700[SQO]|r Asking the server for the quest categories.")
        requestSync()
        return
    end

    local total, titles = 0, 0
    for _ in pairs(categories) do total = total + 1 end
    for _ in pairs(titleToID)  do titles = titles + 1 end
    print("|cFFFFD700SideQuestsOnly|r v" .. VERSION)
    if dataLoaded then
        print(string.format("  |cFF00FF00%d|r quests classified by the server.", total))
    elseif syncWaiting then
        print("  Waiting for the server's quest categories.")
    else
        print("  No quest categories from the server (it needs mod-sidequestsonly).")
    end
    print("  Categories: " .. BADGE.story .. " " .. BADGE.lore .. " " ..
          BADGE.attune .. " " .. BADGE.class .. " " .. BADGE.profession .. " " ..
          BADGE.daily  .. " " .. BADGE.pvp .. " " .. BADGE.holiday .. " " .. BADGE.event .. " " .. BADGE.side)
    print("  Dungeon: " .. BADGE.dungeon_story .. " " ..
          BADGE.dungeon_lore .. " " .. BADGE.dungeon_side)
    print("  Raid: " .. BADGE.raid_story .. " " ..
          BADGE.raid_lore .. " " .. BADGE.raid_side)
    print("  " .. BADGE.unknown .. " = seen but unclassified (NPC dialog only)")
    print(string.format("  |cFF00FF00%d|r quest titles seen.", titles))
    print("  /sqo check  - classify quests in your log")
    print("  /sqo sync   - ask the server for the categories again")
    print("  /sqo <id>   - look up a quest ID")
    print("  /sqo debug  - show hook status for troubleshooting")
    print("  /sqo npc    - trace next NPC quest scan (open dialog to see results)")
    print("  /sqo frames - dump quest frame/FontString names (for diagnosis)")
end
