#!/usr/bin/env python3
"""
classify_quests_codex.py  –  Classify WoW 3.3.5a quests into addon quest categories.

Uses Codex CLI as the primary classifier for story / lore / side decisions,
with rule-based fast-paths for attune, class, profession, daily, and dungeon quests.

Chains are classified as units: every quest in a chain inherits the chain's
category, so opening kill/collect quests in story chains get [Story] too.

Prerequisites:
    codex CLI must be installed and authenticated.

Run from the SideQuestsOnly addon directory:
    python3 classify_quests_codex.py

Output files:
    category_assignments.json   – per-quest category map (review before applying)
    QuestData.lua               – regenerated with QC_QuestCategory (after review)
    QuestData.lua.bak           – backup of the original
    quest_chain_cache.json      – updated cache (additive: adds text fields)
"""

import re, json, time, os, sys, shutil, argparse, subprocess, tempfile
from collections import defaultdict

try:
    import requests
except ImportError:
    requests = None


def _make_client():
    """
    Verify Codex CLI is available. The caller uses the CLI directly rather than
    a Python SDK client, so this returns the executable path for convenience.
    """
    from shutil import which

    exe = which("codex")
    if not exe:
        sys.exit("Cannot find `codex` on PATH.")
    return exe


# ── Configuration ─────────────────────────────────────────────────────────────

WOWHEAD_BASE      = "https://wotlk.wowhead.com"
RATE_LIMIT        = 0.5          # seconds between WoWHead HTTP requests
CACHE_FILE        = "quest_chain_cache.json"
DB_EXPORT_FILE    = "wow_quests.json"    # exported from acore_world via ssh
LUA_FILE          = "QuestData.lua"
ASSIGN_FILE       = "category_assignments.json"

MODEL             = "gpt-5.4"
CHAINS_PER_BATCH  = 10           # quest chains sent per LLM API call (smaller = avoids rate limits)
DESC_MAX_CHARS    = 180          # max chars of description / completion per quest
OBJ_MAX_CHARS     = 120          # max chars of objectives per quest
LARGE_CHAIN_WARN  = 20           # chains this long are flagged for manual review
# All quests in a chain are sent to the LLM — no sampling cap.

COMPARE_BASELINE_FILE = "QuestData.lua"

# PvP sort IDs: Alterac Valley, Strand of the Ancients, Arathi Basin, Wintergrasp
PVP_SORT_IDS = frozenset({2597, 3277, 2677, 4197})

# Holiday / seasonal event sort IDs (negative QuestSortID values used for world events).
# These cover all non-dungeon negative sort_id quests that are NOT starting zones or PvP.
# DK starting area (-372) is excluded here — those become "intro" via STARTING_ZONE_SORT_IDS.
HOLIDAY_SORT_IDS = frozenset({
    -22,   # Hallow's End / seasonal PvP events
    -241,  # Argent Tournament (Icecrown)
    -262,  # Hallow's End scripted dailies
    -304,  # Brewfest
    -364,  # Darkmoon Faire
    -365,  # AQ War Effort
    -366,  # Lunar Festival (Elders)
    -370,  # Hallow's End (additional)
    -375,  # Pilgrim's Bounty
    -376,  # Love is in the Air
})

# Starting zone sort IDs — story chains here become "intro" instead of "story".
# These are the level 1-10 starting areas for each race.
STARTING_ZONE_SORT_IDS = frozenset({
    9,    # Northshire (Human)
    85,   # Tirisfal Glades / Deathknell (Undead)
    132,  # Coldridge Valley (Dwarf / Gnome)
    188,  # Shadowglen / Teldrassil (Night Elf)
    215,  # Camp Narache (Tauren)
    220,  # Camp Narache / Mulgore hunt area (Tauren)
    363,  # Valley of Trials (Orc / Troll)
    3431, # Sunstrider Isle (Blood Elf)
    3526, # Ammen Vale / Azuremyst Isle crash site (Draenei)
    -372, # Ebon Hold (Death Knight)
})

# WoW class names — zone labels that exactly match one of these trigger the
# rule-based "class" fast-path without needing LLM classification.
CLASS_NAMES = frozenset({
    "warrior", "paladin", "hunter", "rogue", "priest",
    "death knight", "shaman", "mage", "warlock", "druid",
})

PROFESSION_SORT_IDS = frozenset({
    -264,  # Tailoring
    -201,  # Engineering
    -182,  # Leatherworking
    -181,  # Alchemy
    -121,  # Blacksmithing
    -101,  # Fishing
})

PROFESSION_PATTERNS = (
    r"\balchemy\b",
    r"\balchemist\b",
    r"\bblacksmith(?:ing)?\b",
    r"\bsmith(?:ing)?\b",
    r"\benchant(?:ing|ment|er)?\b",
    r"\bengineering\b",
    r"\bengineer\b",
    r"\bfirst aid\b",
    r"\bfishing\b",
    r"\bcooking\b",
    r"\bcook(?:ery)?\b",
    r"\bherbalism\b",
    r"\bherbalist\b",
    r"\bmining\b",
    r"\bminer(?:'s)?\b",
    r"\bsmelt(?:ing)?\b",
    r"\bskinning\b",
    r"\bleatherworking\b",
    r"\bleatherworker\b",
    r"\btailor(?:ing|ed|-made)?\b",
    r"\bjewelcraft(?:ing|er)?\b",
    r"\binscription\b",
    r"\bdisenchant(?:ing)?\b",
    r"\bprospecting\b",
    r"\b(recipe|pattern|formula|schematic|design|manual|plans):\b",
)

# WoW 3.3.5a zone area IDs (WoWHead uses these for zone filtering).
# This list covers every major leveling zone from starting areas through Northrend.
# If a zone scrape returns 0 quests the zone ID may be wrong — check the
# WoWHead URL for that zone and update accordingly.
ZONE_IDS = [
    # ── Eastern Kingdoms ──────────────────────────────────────────────────────
    1,    # Dun Morogh
    2,    # Coldridge Valley (dwarf/gnome start)
    3,    # Badlands
    4,    # Blasted Lands
    8,    # Swamp of Sorrows
    9,    # Northshire (human starting valley)
    10,   # Duskwood
    11,   # Wetlands
    12,   # Elwynn Forest
    28,   # Western Plaguelands
    33,   # Stranglethorn Vale
    36,   # Alterac Mountains
    40,   # Westfall
    41,   # Deadwind Pass
    44,   # Redridge Mountains
    45,   # Arathi Highlands
    46,   # Burning Steppes
    48,   # Loch Modan
    51,   # Searing Gorge
    85,   # Tirisfal Glades
    130,  # Silverpine Forest
    139,  # Eastern Plaguelands
    267,  # Hillsbrad Foothills
    1497, # Undercity (city quests)
    1537, # Ironforge (city quests)
    3430, # Eversong Woods (blood elf starting zone)
    3433, # Ghostlands
    4080, # Isle of Quel'Danas
    # ── Kalimdor ──────────────────────────────────────────────────────────────
    5,    # Valley of Trials (orc / troll start)
    14,   # Durotar
    15,   # Dustwallow Marsh
    16,   # Azshara
    17,   # The Barrens
    141,  # Teldrassil
    148,  # Darkshore
    214,  # Camp Narache (tauren start)
    215,  # Mulgore
    270,  # Shadowglen (night elf start)
    331,  # Ashenvale
    357,  # Feralas
    361,  # Felwood
    400,  # Thousand Needles
    405,  # Desolace
    406,  # Stonetalon Mountains
    440,  # Tanaris
    490,  # Un'Goro Crater
    493,  # Moonglade
    618,  # Winterspring
    1377, # Silithus
    1637, # Orgrimmar (city quests)
    1638, # Thunder Bluff (city quests)
    3524, # Azuremyst Isle (draenei start)
    3949, # Bloodmyst Isle
    # ── Outland ───────────────────────────────────────────────────────────────
    3483, # Hellfire Peninsula
    3518, # Nagrand
    3519, # Terokkar Forest
    3520, # Shadowmoon Valley
    3521, # Zangarmarsh
    3522, # Blade's Edge Mountains
    3523, # Netherstorm
    # ── Northrend ─────────────────────────────────────────────────────────────
    65,   # Dragonblight
    66,   # Zul'Drak
    67,   # Storm Peaks
    210,  # Icecrown
    394,  # Grizzly Hills
    495,  # Howling Fjord
    2817, # Crystalsong Forest
    3537, # Borean Tundra
    3711, # Sholazar Basin
    4197, # Wintergrasp
    # ── Death Knight starting area ────────────────────────────────────────────
    4298, # Ebon Hold / Death Knight starting experience
]


# ── Lua parser ────────────────────────────────────────────────────────────────

def load_lua(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def parse_main_quests(lua):
    """Return {quest_id: zone_label} from existing QC_MainQuests."""
    result = {}
    for m in re.finditer(r'_m\[(\d+)\]\s*=\s*"([^"]+)"', lua):
        result[int(m.group(1))] = m.group(2)
    return result

def parse_title_map(lua):
    """Return {lowercase_title: quest_id} from existing QC_QuestTitles."""
    result = {}
    for m in re.finditer(r'_t\["([^"]+)"\]\s*=\s*(\d+)', lua):
        result[m.group(1)] = int(m.group(2))
    return result

def parse_category_map(lua):
    """Return {quest_id: category} from QC_QuestCategory."""
    result = {}
    for m in re.finditer(r'_c\[(\d+)\]\s*=\s*"([^"]+)"', lua):
        result[int(m.group(1))] = m.group(2)
    return result


# ── Cache helpers ─────────────────────────────────────────────────────────────

def load_cache(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {int(k): v for k, v in raw.items()}
    return {}

def save_cache(path, cache):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in cache.items()}, f, indent=2)


# ── DB export loader ──────────────────────────────────────────────────────────

def load_db_export(path, cache):
    """
    Load quest data from a wow_quests.json export (from acore_world MySQL).
    Populates / updates cache with text fields and chain links.

    DB field → cache field mapping:
        title             ← LogTitle
        description       ← LogDescription   (quest log story text)
        details           ← QuestDescription (NPC dialog on accept)
        completion        ← QuestCompletionLog
        area_description  ← AreaDescription
        objectives        ← ObjectiveText1
        objectives2-4     ← ObjectiveText2-4
        is_daily          ← Flags & 0x1000
        is_dungeon        ← QuestSortID < 0  (negative sort = instance zone)
        quest_info_id     ← QuestInfoID  (41=PvP, 81=Dungeon, 85=Heroic)
        prev_quest        ← PrevQuestID
        next_quest        ← NextQuestID
        allowable_classes ← AllowableClasses (>0 means class-specific)
        sort_id           ← QuestSortID
        starter_npc       ← creature_queststarter JOIN creature_template
    """
    if not os.path.exists(path):
        print(f"  DB export not found: {path}")
        return 0

    with open(path, "r", encoding="utf-8") as f:
        db_quests = json.load(f)

    added = updated = 0
    for q in db_quests:
        qid = q["id"]
        flags        = q.get("flags", 0) or 0
        sort_id      = q.get("sort_id", 0) or 0
        quest_info_id = q.get("quest_info_id", 0) or 0
        is_daily   = bool(flags & 0x1000)
        is_dungeon = quest_info_id in (81, 85)  # 81=Dungeon, 85=Heroic; sort_id<0 is NOT reliable

        entry = cache.get(qid, {"id": qid, "series": []})
        is_new = qid not in cache

        entry["title"]             = q.get("title")  or entry.get("title") or ""
        entry["description"]       = (q.get("description")      or "")[:DESC_MAX_CHARS]
        entry["details"]           = (q.get("details")          or "")[:DESC_MAX_CHARS]
        entry["completion"]        = (q.get("completion")       or "")[:DESC_MAX_CHARS]
        entry["area_description"]  = (q.get("area_description") or "")[:120]
        entry["objectives"]        = (q.get("objectives")       or "")[:OBJ_MAX_CHARS]
        entry["objectives2"]       = (q.get("objectives2")      or "")[:OBJ_MAX_CHARS]
        entry["objectives3"]       = (q.get("objectives3")      or "")[:OBJ_MAX_CHARS]
        entry["objectives4"]       = (q.get("objectives4")      or "")[:OBJ_MAX_CHARS]
        entry["is_daily"]          = is_daily
        entry["is_dungeon"]        = is_dungeon
        entry["quest_info_id"]     = q.get("quest_info_id", 0) or 0
        entry["sort_id"]           = sort_id
        entry["prev_quest"]        = q.get("prev_quest", 0) or 0
        entry["next_quest"]        = q.get("next_quest", 0) or 0
        entry["allowable_classes"] = q.get("allowable_classes", 0) or 0
        entry["starter_npc"]       = (q.get("starter_npc") or "")[:80]
        cache[qid] = entry

        if is_new:
            added += 1
        else:
            updated += 1

    return added, updated


# ── WoWHead scraper ───────────────────────────────────────────────────────────

def _extract_text_from_html(text, pattern_list):
    """Try each regex pattern in order; return first non-empty stripped match."""
    for pat in pattern_list:
        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            raw = m.group(1)
            clean = re.sub(r'<[^>]+>', ' ', raw)
            clean = re.sub(r'\s+', ' ', clean).strip()
            if clean:
                return clean
    return ""


def scrape_quest_extended(quest_id, session):
    """
    Fetch the WoWHead WotLK page for quest_id and return an extended dict:
        {
            "id":          int,
            "title":       str | None,
            "series":      [int, ...],
            "description": str,      # NPC intro text (up to DESC_MAX_CHARS)
            "objectives":  str,      # objectives text (up to OBJ_MAX_CHARS)
            "completion":  str,      # turn-in text (up to DESC_MAX_CHARS)
            "is_daily":    bool,
            "is_dungeon":  bool,
        }
    Returns None on network / non-200 error.
    """
    if requests is None:
        sys.exit("requests is required for WoWHead scraping features.")

    url = f"{WOWHEAD_BASE}/quest={quest_id}"
    try:
        r = session.get(url, timeout=20)
    except requests.RequestException as e:
        print(f"    network error for {quest_id}: {e}")
        return None

    if r.status_code == 404:
        return {"id": quest_id, "title": None, "series": [],
                "description": "", "objectives": "", "completion": "",
                "is_daily": False, "is_dungeon": False}
    if r.status_code != 200:
        print(f"    HTTP {r.status_code} for quest {quest_id}")
        return None

    text = r.text

    # ── Title ────────────────────────────────────────────────────────────────
    title = None
    tm = re.search(r'<title>([^<]+)</title>', text)
    if tm:
        raw = tm.group(1)
        title = re.sub(r'\s*-\s*Quest\s*-.*$', '', raw, flags=re.IGNORECASE).strip()

    # ── Series table ─────────────────────────────────────────────────────────
    series = []
    sm = re.search(r'<table class="series">(.*?)</table>', text, re.DOTALL)
    if sm:
        series = [int(x) for x in re.findall(r'/wotlk/quest=(\d+)/', sm.group(1))]

    # ── Quest text from embedded JavaScript ──────────────────────────────────
    # WoWHead embeds quest data as JSON in script tags. Multiple formats exist
    # across their codebase versions; we try each in order.
    description = ""
    objectives  = ""
    completion  = ""
    is_daily    = False
    is_dungeon  = False

    # Pattern 1: WH.Gatherer.addData(1, N, {"QUESTID": {...}})
    for pat in [
        r'WH\.Gatherer\.addData\s*\(\s*1\s*,\s*\d+\s*,\s*(\{.*?\})\s*\)',
        r'g_questData\s*=\s*(\{.*?\})\s*;',
    ]:
        m = re.search(pat, text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1))
                qd  = obj.get(str(quest_id), {})
                if not qd and isinstance(obj, dict):
                    # Sometimes only one entry — take it if IDs match
                    for v in obj.values():
                        if isinstance(v, dict) and v.get("id") == quest_id:
                            qd = v
                            break
                description = str(qd.get("Description", "") or "")[:DESC_MAX_CHARS]
                objectives  = str(qd.get("Objectives", "")  or "")[:OBJ_MAX_CHARS]
                completion  = str(qd.get("Completion", "")  or "")[:DESC_MAX_CHARS]
                flags       = int(qd.get("flags", 0) or 0)
                category    = int(qd.get("category", 0) or 0)
                is_daily    = bool(flags & 0x1000)     # QUEST_FLAGS_DAILY
                is_dungeon  = (category < 0)           # negative = instance zone
                break
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

    # Pattern 2: HTML fallback for description text
    if not description:
        description = _extract_text_from_html(text, [
            r'id=["\']quest-description["\'][^>]*>(.*?)</div>',
            r'class=["\']quest-detail-text["\'][^>]*>\s*<p>(.*?)</p>',
        ])[:DESC_MAX_CHARS]

    if not objectives:
        objectives = _extract_text_from_html(text, [
            r'id=["\']quest-objectives["\'][^>]*>(.*?)</div>',
        ])[:OBJ_MAX_CHARS]

    # Detect daily from page text if not caught via flags
    if not is_daily:
        is_daily = bool(re.search(r'daily quest|repeatable', text, re.IGNORECASE))

    # Detect dungeon from page text if not caught via category
    if not is_dungeon:
        is_dungeon = bool(re.search(
            r'class=["\'][^"\']*icon-dungeon|dungeon quest|instance quest', text,
            re.IGNORECASE))

    return {
        "id":          quest_id,
        "title":       title,
        "series":      series,
        "description": description,
        "objectives":  objectives,
        "completion":  completion,
        "is_daily":    is_daily,
        "is_dungeon":  is_dungeon,
    }


def scrape_zone_quest_ids(zone_id, session):
    """
    Scrape the quest list for a zone from WoWHead WotLK.
    Returns a list of quest IDs found in the zone.
    Returns [] on error or no results.
    """
    if requests is None:
        sys.exit("requests is required for WoWHead scraping features.")

    # WoWHead zone pages embed the quest list as a JavaScript Listview.
    url = f"{WOWHEAD_BASE}/quests/zone:{zone_id}"
    try:
        r = session.get(url, timeout=20)
    except requests.RequestException as e:
        print(f"    network error for zone {zone_id}: {e}")
        return []

    if r.status_code != 200:
        return []

    text = r.text

    # Extract quest IDs from Listview JS data
    # Format: new Listview({template: 'quest', id: 'quests', ..., data: [{...}]})
    ids = set()

    # Method 1: extract from Listview data JSON
    for m in re.finditer(
            r"new Listview\s*\(\s*\{[^}]*?id\s*:\s*['\"]quests['\"][^}]*?data\s*:\s*(\[.*?\])\s*\}",
            text, re.DOTALL):
        try:
            entries = json.loads(m.group(1))
            for e in entries:
                if isinstance(e, dict) and "id" in e:
                    ids.add(int(e["id"]))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Method 2: extract quest IDs from all quest links on the page
    for qid_str in re.findall(r'/wotlk/quest=(\d+)/', text):
        ids.add(int(qid_str))

    return sorted(ids)


# ── Union-Find for chain grouping ─────────────────────────────────────────────

class UnionFind:
    def __init__(self):
        self._parent = {}

    def find(self, x):
        if x not in self._parent:
            self._parent[x] = x
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def components(self):
        """Return {representative: sorted list of members}."""
        groups = defaultdict(list)
        for x in self._parent:
            groups[self.find(x)].append(x)
        return {k: sorted(v) for k, v in groups.items()}


def build_chains(cache):
    """
    Use union-find to group quest IDs into chains.
    Links come from WoWHead series arrays AND DB prev/next quest fields.
    Returns {chain_root_id: [sorted list of quest IDs in chain]}.
    """
    uf = UnionFind()
    for qid, info in cache.items():
        uf.find(qid)  # ensure every cached quest is registered
        # WoWHead series links
        for sibling in info.get("series", []):
            if sibling > 0 and sibling in cache:
                uf.union(qid, sibling)
        # DB prev/next quest links (more reliable)
        # Do NOT bridge the dungeon ↔ open-world boundary: dungeon breadcrumb
        # notes store prev_quest pointing at open-world quests, which would
        # incorrectly drag the open-world chain into a dungeon classification.
        # Use quest_info_id (81/85) for dungeon detection — sort_id<0 is not reliable
        # (it also covers holiday events, Argent Tournament, AQ War Effort, etc.).
        for link_field in ("prev_quest", "next_quest"):
            linked = info.get(link_field, 0) or 0
            if linked > 0 and linked in cache:
                this_dungeon  = (info.get("quest_info_id", 0) or 0) in (81, 85)
                other_dungeon = (cache[linked].get("quest_info_id", 0) or 0) in (81, 85)
                if this_dungeon == other_dungeon:
                    uf.union(qid, linked)
    return uf.components()


def is_profession_related(qid, cache, zone_labels):
    """Return True when the quest text or sort ID clearly points to a profession."""
    info = cache.get(qid, {})
    sort_id = info.get("sort_id", 0) or 0
    if sort_id in PROFESSION_SORT_IDS:
        return True

    fields = [
        zone_labels.get(qid, "") or "",
        info.get("title", "") or "",
        info.get("description", "") or "",
        info.get("details", "") or "",
        info.get("completion", "") or "",
        info.get("area_description", "") or "",
        info.get("objectives", "") or "",
        info.get("objectives2", "") or "",
        info.get("objectives3", "") or "",
        info.get("objectives4", "") or "",
    ]
    for src in fields:
        text = src.lower()
        for pat in PROFESSION_PATTERNS:
            if re.search(pat, text):
                return True
    return False


# ── Pass 1: rule-based classifier ─────────────────────────────────────────────

def rule_based_category(chain_ids, cache, zone_labels):
    """
    Apply rule-based classification for attune, class, profession, pvp, and
    holiday quests. Returns one of those categories or None (→ needs LLM
    classification).

    Priority: attune > class > pvp > holiday > profession.
    Dungeon quests are NOT bypassed here — they go through LLM classification
    and receive a compound category (dungeon_story/lore/side/profession) in
    Pass 3.
    Daily is also not applied here so story/lore chains with daily turn-ins
    keep their narrative category.
    """
    for qid in chain_ids:
        zone = zone_labels.get(qid, "").lower()
        title = (cache.get(qid, {}).get("title") or "").lower()
        desc  = (cache.get(qid, {}).get("description") or "").lower()

        # Attunement: check zone label and quest text
        for src in (zone, title, desc):
            if "attunement" in src or "attune" in src:
                return "attune"

    for qid in chain_ids:
        # DB AllowableClasses > 0 means restricted to specific class(es)
        if cache.get(qid, {}).get("allowable_classes", 0) > 0:
            return "class"
        zone = zone_labels.get(qid, "").lower()
        # Class quest: zone label exactly matches a class name (fallback)
        for cls in CLASS_NAMES:
            if zone == cls or zone.endswith(f" {cls}") or f" {cls} " in zone:
                return "class"

    # PvP: QuestInfoID=41 or known BG sort IDs
    for qid in chain_ids:
        info    = cache.get(qid, {})
        qii     = info.get("quest_info_id", 0) or 0
        sort_id = info.get("sort_id", 0) or 0
        if qii == 41 or sort_id in PVP_SORT_IDS:
            return "pvp"

    # Holiday: seasonal event quests (Hallow's End, Brewfest, Darkmoon Faire, etc.)
    for qid in chain_ids:
        sort_id = cache.get(qid, {}).get("sort_id", 0) or 0
        if sort_id in HOLIDAY_SORT_IDS:
            return "holiday"

    for qid in chain_ids:
        if is_profession_related(qid, cache, zone_labels):
            return "profession"

    return None


# ── Pass 2: LLM classification ────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a World of Warcraft (Wrath of the Lich King 3.3.5a) quest analyst.
Your task is to classify quest chains into one of four categories:

  story  – The chain drives the zone or expansion narrative. It has named characters,
            involves the zone's central conflict or major lore events, and produces a
            meaningful in-world outcome (a villain defeated, a mystery resolved, etc.).
            Even if individual quests involve kill/collect tasks, the chain as a whole
            tells a recognisable story with setup → conflict → resolution.

  lore   – The chain enriches world-building without being progression-critical.
            It has genuine narrative content (backstory, character depth, world history)
            but is self-contained — the zone's main story arc would be unchanged without
            these quests. A local tragedy, a ghost's history, a minor faction's backstory.

  side   – Pure filler. No narrative contribution at any point in the chain.
            Repeated kill/collect with no escalation, no named characters of significance,
            no connection to zone lore, and no meaningful outcome beyond XP/gold.

  profession – The chain is primarily about a profession / tradeskill. It trains,
            unlocks, specializes, or rewards a profession, or the quest text is
            centered on profession work such as crafting, fishing, cooking,
            engineering, alchemy, tailoring, blacksmithing, leatherworking,
            enchanting, mining, herbalism, skinning, jewelcrafting, inscription,
            or first aid. Use this even when the quest has some story flavor.

Use these six qualitative metrics to decide:

1. NARRATIVE ARC: Does the chain have recognisable story structure (setup → conflict
   → resolution)? Even a simple arc counts. "Kill scouts → report to chief → drive them
   out" is a story. "Kill 10 wolves, collect 5 pelts, bring 3 fangs" with no escalation
   is not.

2. ZONE/EXPANSION LORE CONNECTIVITY: Does the chain reference, involve, or deepen
   understanding of the zone's central conflict (Defias Brotherhood, Scourge advance,
   Old God corruption) or major WoW factions/historical events (Third War, Arthas's
   betrayal, founding of the Horde)?
   → Strongly tied to zone's central conflict: story
   → Touches lore but not the central conflict: lore
   → No lore connection: side

3. CHARACTER SIGNIFICANCE: Are NPCs named and recurring within the zone, or generic
   one-off characters?
   → Named characters seen repeatedly across the zone: story
   → Named but self-contained minor characters: lore
   → Generic unnamed NPCs: side

4. CONSEQUENCE AND WORLD CHANGE: Does completing the chain produce a meaningful outcome?
   → Real narrative outcomes (villain defeated, mystery resolved, faction aided): story/lore
   → No outcome beyond XP/gold: side

5. CHAIN ARC QUALITY AS A UNIT: Evaluate the full sequence as a short story. Does each
   quest advance toward a narrative payoff, or is it just repeated task variation?
   A kill quest that builds toward confronting a named enemy is story. Five kill quests in
   a row with no escalation is side regardless of chain length.

6. LORE DEPTH WITHOUT ZONE PROGRESSION: Lore applies when there IS genuine narrative
   content but it is self-contained — the player learns something meaningful about the
   world, but the zone's arc would be unchanged without these quests.

Important nuances:
- Starting zone chains (Camp Narache → Bloodhoof Village, Valley of Trials → Durotar,
  Northshire → Elwynn Forest) are almost always STORY — they drive the new player
  narrative and establish the zone's main conflict.
- Profession training, specialization, and recipe/pattern/formula/schematic
  quests should be PROFESSION even if they have named NPCs or light narrative text.
- A chain whose early quests look like filler but whose later quests resolve a major
  conflict should be classified as STORY for the entire chain.
- Classify the chain as a UNIT, not quest by quest.
- When uncertain between story and lore, prefer story for chains with named major NPCs
  or zone-defining events. Prefer lore for self-contained character vignettes.

Return ONLY valid JSON in exactly this format (no markdown, no explanation, just JSON):
{"results": [{"chain_id": "<id>", "category": "story|lore|side|profession", "reasoning": "<one sentence>"}]}
"""


def _build_chain_prompt_entry(chain_id, chain_ids, cache, zone_labels):
    """Format one chain's data for inclusion in the LLM prompt.
    All quests in the chain are included — no sampling cap."""
    lines = []

    # Zone context (use zone of first quest in chain that has one)
    zones = [zone_labels.get(q) for q in chain_ids if zone_labels.get(q)]
    zone_str = zones[0] if zones else "Unknown Zone"

    # Quest-giver NPC (first non-empty starter_npc in chain)
    starter = next(
        (cache.get(q, {}).get("starter_npc") or "" for q in chain_ids
         if (cache.get(q, {}).get("starter_npc") or "").strip()),
        ""
    )

    lines.append(f"CHAIN_ID: {chain_id}")
    lines.append(f"Zone: {zone_str}")
    if starter:
        lines.append(f"Quest giver: {starter}")
    lines.append(f"Chain length: {len(chain_ids)} quest(s)")

    for i, qid in enumerate(chain_ids, 1):
        info    = cache.get(qid, {})
        t       = info.get("title") or f"Quest #{qid}"
        desc    = (info.get("description")      or "").strip()
        details = (info.get("details")          or "").strip()
        area    = (info.get("area_description") or "").strip()
        comp    = (info.get("completion")       or "").strip()
        objs    = [
            (info.get("objectives")  or "").strip(),
            (info.get("objectives2") or "").strip(),
            (info.get("objectives3") or "").strip(),
            (info.get("objectives4") or "").strip(),
        ]
        obj_str = " / ".join(o for o in objs if o)

        lines.append(f"  Quest {i}: \"{t}\"")
        if area:
            lines.append(f"    Area:       {area}")
        if desc:
            lines.append(f"    Log:        {desc[:DESC_MAX_CHARS]}")
        if details and details != desc:
            lines.append(f"    NPC:        {details[:DESC_MAX_CHARS]}")
        if obj_str:
            lines.append(f"    Objectives: {obj_str[:OBJ_MAX_CHARS]}")
        if comp:
            lines.append(f"    Completion: {comp[:DESC_MAX_CHARS]}")

    return "\n".join(lines)


def _codex_schema():
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "chain_id": {"type": "string"},
                        "category": {"type": "string", "enum": ["story", "lore", "side", "profession"]},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["chain_id", "category", "reasoning"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }


def _codex_prompt(user_content):
    return (
        "You are being used as a pure classification engine. "
        "Do not inspect the filesystem and do not run commands. "
        "Use only the quest-chain data in this prompt.\n\n"
        "SYSTEM INSTRUCTION:\n"
        f"{SYSTEM_PROMPT}\n\n"
        "USER DATA:\n"
        f"{user_content}\n"
    )


def classify_chains_llm(batch, cache, zone_labels, client=None):
    """
    Classify a batch of chains using Codex CLI.
    batch: list of (chain_id, chain_ids) tuples
    Returns {chain_id: "story"|"lore"|"side"|"profession"} dict.
    Falls back to "side" for any chain that fails to classify.
    """
    if not batch:
        return {}

    if client is None:
        client = _make_client()

    # Build the user message listing all chains in this batch
    entries = []
    for chain_id, chain_ids in batch:
        entries.append(_build_chain_prompt_entry(chain_id, chain_ids, cache, zone_labels))

    user_content = (
        f"Classify these {len(batch)} quest chain(s). "
        "Return JSON exactly as specified in the system prompt.\n\n"
        + "\n\n---\n\n".join(entries)
    )

    for attempt in range(3):
        try:
            with tempfile.TemporaryDirectory(prefix="codex_quest_batch_") as tmpdir:
                schema_path = os.path.join(tmpdir, "schema.json")
                out_path = os.path.join(tmpdir, "out.json")
                with open(schema_path, "w", encoding="utf-8") as f:
                    json.dump(_codex_schema(), f)

                cmd = [
                    client, "exec",
                    "--skip-git-repo-check",
                    "--sandbox", "read-only",
                    "--cd", os.getcwd(),
                    "--ephemeral",
                    "-c", 'reasoning_effort="low"',
                    "--model", MODEL,
                    "--output-schema", schema_path,
                    "-o", out_path,
                    _codex_prompt(user_content),
                ]
                proc = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or
                                       f"codex exec exited {proc.returncode}")

                with open(out_path, "r", encoding="utf-8") as f:
                    raw_text = f.read().strip()

            # Extract JSON object — scan for first { … last }
            json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if json_match:
                raw_text = json_match.group(0)

            parsed = json.loads(raw_text)
            results = {}
            for item in parsed.get("results", []):
                cid  = str(item.get("chain_id", ""))
                cat  = item.get("category", "side").lower()
                if cat not in ("story", "lore", "side", "profession"):
                    cat = "side"  # compound dungeon_ categories are applied in Pass 3
                results[cid] = cat
            return results

        except (json.JSONDecodeError, KeyError) as e:
            print(f"    LLM parse error (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
        except subprocess.TimeoutExpired:
            wait = 15 * (attempt + 1)
            print(f"    Codex timeout (attempt {attempt+1}); waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            print(f"    Error (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)

    # All retries failed — fallback to "side" for every chain in this batch
    print("    All retries failed; marking batch as 'side'.")
    return {str(chain_id): "side" for chain_id, _ in batch}


# ── Pass 3: post-LLM mechanical adjustments ───────────────────────────────────

def apply_dungeon_tag(base_category, chain_ids, cache):
    """
    After LLM classification, apply dungeon compound tag and daily upgrade.

    Dungeon chains get a compound category: dungeon_story / dungeon_lore /
    dungeon_side. Profession stays as profession, even for dungeon-linked
    profession quests, so the trade-skill tag is preserved.
    A chain is dungeon if any quest has sort_id < 0 or quest_info_id in (81, 85).

    Daily: only upgrade side→daily; story/lore/dungeon_* keep their narrative value.
    """
    any_dungeon = any(
        (cache.get(q, {}).get("quest_info_id", 0) or 0) in (81, 85)
        for q in chain_ids
    )

    if any_dungeon and base_category in ("story", "lore", "side"):
        # DK starting zone is instanced (sort_id=-372) but still an "intro" zone.
        # Apply intro AFTER dungeon tag is resolved below.
        result = f"dungeon_{base_category}"
    else:
        result = base_category

    # Upgrade story → intro for starting zone chains (before or after dungeon tag).
    # intro also overrides dungeon_story for DK starting zone (Ebon Hold).
    if base_category == "story":
        chain_sort_ids = {cache.get(q, {}).get("sort_id", 0) or 0 for q in chain_ids}
        if chain_sort_ids <= STARTING_ZONE_SORT_IDS:
            return "intro"

    # For open-world quests: upgrade side→daily if any quest has daily flag
    if result == "side":
        any_daily = any(cache.get(q, {}).get("is_daily", False) for q in chain_ids)
        if any_daily:
            return "daily"

    return result


# ── Lua writer ────────────────────────────────────────────────────────────────

HEADER_TEMPLATE = """\
-- QuestClassification Quest Data
-- Generated by classify_quests.py
--
-- QC_QuestCategory[questID] = category string
-- Categories: intro | story | lore | attune | class | profession | daily | pvp | holiday
--             dungeon_story | dungeon_lore | dungeon_side | side
--
-- A quest is absent from this table only when it was never classified —
-- the addon shows [?] in NPC dialogs for those quests.

-- QC_QuestCategory[questID] = "category"
QC_QuestCategory = {}
local _c = QC_QuestCategory
"""

def write_lua(path, quest_categories, title_map):
    """Regenerate QuestData.lua with QC_QuestCategory."""
    by_cat = defaultdict(list)
    for qid, cat in quest_categories.items():
        by_cat[cat].append(qid)

    cat_order = [
        "intro", "story", "lore", "attune", "class", "profession", "daily", "pvp", "holiday",
        "dungeon_story", "dungeon_lore", "dungeon_side", "side",
    ]
    lines = [HEADER_TEMPLATE.rstrip()]
    for cat in cat_order:
        if cat not in by_cat:
            continue
        lines.append(f"-- {cat.upper()}")
        for qid in sorted(by_cat[cat]):
            lines.append(f'_c[{qid}] = "{cat}"')
        lines.append("")

    lines.append("-- Quest title → ID lookup (lower-case keys)")
    lines.append("QC_QuestTitles = {}")
    lines.append("local _t = QC_QuestTitles")
    for title, qid in sorted(title_map.items(), key=lambda kv: kv[0]):
        safe = title.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'_t["{safe}"] = {qid}')
    lines.append("")  # trailing newline

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


# ── Incremental save helper ───────────────────────────────────────────────────

def _save_incremental(chains, rule_assignments, llm_assignments,
                      cache, zone_labels, path):
    """Write current assignments to JSON so --resume can pick up from here."""
    combined = {**llm_assignments, **rule_assignments}
    out = {}
    for root, members in chains.items():
        cat = combined.get(root, "side")
        out[str(root)] = {
            "category": cat,
            "chain_length": len(members),
            "quest_ids": members,
            "titles": [cache.get(q, {}).get("title") or f"#{q}" for q in members[:5]],
            "zone": next((zone_labels.get(q) for q in members if zone_labels.get(q)), None),
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


def build_chain_category_map(chains, quest_categories):
    """Collapse quest-level categories into {chain_root: category}."""
    result = {}
    for root, members in chains.items():
        cat = next((quest_categories.get(q) for q in members if quest_categories.get(q)), None)
        result[root] = cat or "side"
    return result


def compare_first_claude_batches(batch_count, chains, rule_assignments, cache, zone_labels,
                                 prev_assignments, baseline_chain_categories):
    """
    Replay the original first N Claude batches using the pre-resume QuestData.lua
    categories as the baseline for which chains were still "side" at run start.
    Compare Codex results against the saved Claude results in category_assignments.json.
    """
    if batch_count <= 0:
        return []

    original_needs_llm = [
        (root, members) for root, members in chains.items()
        if root not in rule_assignments and baseline_chain_categories.get(root, "side") == "side"
    ]
    compare_count = min(len(original_needs_llm), batch_count * CHAINS_PER_BATCH)
    subset = original_needs_llm[:compare_count]

    print("=== Dry Run: Codex vs Saved Claude Batches ===")
    print(f"  Replaying first {batch_count} batch(es) = {compare_count} chain(s)")
    print(f"  Baseline side-set source: {COMPARE_BASELINE_FILE}")
    print(f"  Claude result source: {ASSIGN_FILE}\n")

    differences = []
    for batch_start in range(0, compare_count, CHAINS_PER_BATCH):
        batch = subset[batch_start:batch_start + CHAINS_PER_BATCH]
        batch_num = batch_start // CHAINS_PER_BATCH + 1
        print(f"  Dry batch {batch_num}/{batch_count}...", end=" ", flush=True)
        results = classify_chains_llm(batch, cache, zone_labels)
        batch_diffs = 0
        for root, members in batch:
            codex_cat = results.get(str(root), "side")
            claude_cat = prev_assignments.get(root, "side")
            if codex_cat != claude_cat:
                differences.append({
                    "chain_id": root,
                    "codex_category": codex_cat,
                    "claude_category": claude_cat,
                    "titles": [cache.get(q, {}).get("title") or f"#{q}" for q in members[:5]],
                    "zone": next((zone_labels.get(q) for q in members if zone_labels.get(q)), None),
                    "chain_length": len(members),
                })
                batch_diffs += 1
        print(f"done ({batch_diffs} diff)")

    print()
    print(f"  Differences found: {len(differences)}")
    return differences


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Classify WoW quests into 7 narrative categories using Codex.")
    parser.add_argument(
        "--apply", action="store_true",
        help="Write QuestData.lua after classification (default: only write JSON).")
    parser.add_argument(
        "--discover-zones", action="store_true",
        help="Scrape zone quest lists to discover new quest IDs (slow).")
    parser.add_argument(
        "--skip-text-fetch", "--skip-db", action="store_true",
        help="Skip loading quest text from the DB export (wow_quests.json).")
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="Skip LLM classification (useful for testing the scraping pipeline).")
    parser.add_argument(
        "--resume", action="store_true",
        help="Load existing category_assignments.json and only re-run LLM on chains "
             "still marked 'side' from a previous incomplete run. Use with --max-batches "
             "to make incremental progress when the OAuth token expires mid-run.")
    parser.add_argument(
        "--max-batches", type=int, default=0,
        help="Stop LLM pass after this many batches (0 = unlimited). "
             "Use with --resume to make incremental progress across multiple runs.")
    parser.add_argument(
        "--compare-first-batches", type=int, default=0,
        help="Dry-run Codex on the original first N resumed Claude batches and report "
             "only the chains where Codex disagrees with the saved Claude result. "
             "Does not write QuestData.lua or category_assignments.json.")
    args = parser.parse_args()

    if not os.path.exists(LUA_FILE):
        sys.exit(f"Cannot find {LUA_FILE} – run from the SideQuestsOnly directory.")

    # ── Load existing data ────────────────────────────────────────────────────
    lua        = load_lua(LUA_FILE)
    main_q     = parse_main_quests(lua)   # {id: zone_label} – existing classifications
    title_map  = parse_title_map(lua)     # {title: id}
    quest_cat0 = parse_category_map(lua)  # quest-level categories before this Codex copy runs
    cache      = load_cache(CACHE_FILE)

    print(f"Loaded  {len(main_q):,} main quests | {len(title_map):,} title entries")
    print(f"Cache   {len(cache):,} entries pre-loaded from {CACHE_FILE}")

    # Seed cache with every quest in QC_MainQuests that isn't cached yet.
    # Also backfill titles into existing cache entries that still have empty titles —
    # this happens when a previous run seeded entries before titles were available.
    # Reverse-map title_map so we can pre-populate titles even without a WoWHead fetch.
    # Using title="" (not None) so the text-fetch step won't skip them as 404s.
    rev_title = {v: k for k, v in title_map.items()}  # {quest_id: lowercase_title}
    seeded = 0
    title_filled = 0
    for qid in main_q:
        if qid not in cache:
            cache[qid] = {"id": qid, "title": rev_title.get(qid, ""), "series": []}
            seeded += 1
        elif not cache[qid].get("title") and qid in rev_title:
            cache[qid]["title"] = rev_title[qid]
            title_filled += 1
    if seeded or title_filled:
        save_cache(CACHE_FILE, cache)
        if seeded:
            print(f"Seeded  {seeded:,} quests from QC_MainQuests into cache.")
        if title_filled:
            print(f"Filled  {title_filled:,} missing titles from QC_QuestTitles.")
    print()

    # ── Zone discovery (optional) ─────────────────────────────────────────────
    if args.discover_zones:
        print("=== Zone Discovery ===")
        new_from_zones = 0
        for zone_id in ZONE_IDS:
            print(f"  Zone {zone_id}...", end=" ", flush=True)
            ids = scrape_zone_quest_ids(zone_id, session)
            before = len(cache)
            for qid in ids:
                if qid not in cache:
                    cache[qid] = {"id": qid, "title": None, "series": []}
            added = len(cache) - before
            new_from_zones += added
            print(f"{len(ids)} quests ({added} new)")
            time.sleep(RATE_LIMIT)
        save_cache(CACHE_FILE, cache)
        print(f"Zone discovery: {new_from_zones:,} new quest IDs added to cache.\n")

    # ── Load quest text from DB export (replaces WoWHead scraping) ───────────
    print("=== Loading Quest Data from DB Export ===")
    if not args.skip_text_fetch and os.path.exists(DB_EXPORT_FILE):
        added, updated_count = load_db_export(DB_EXPORT_FILE, cache)
        save_cache(CACHE_FILE, cache)
        print(f"  DB export: {added:,} new quests added, {updated_count:,} updated.")
        print(f"  Cache now has {len(cache):,} quests.\n")
    elif args.skip_text_fetch:
        print("  (--skip-db: skipping DB export load)\n")
    else:
        print(f"  {DB_EXPORT_FILE} not found — using existing cache only.")
        print(f"  To regenerate: ssh nbtdwow and run the Python export script.\n")

    # ── Build chains ──────────────────────────────────────────────────────────
    print("=== Building Quest Chains ===")
    chains = build_chains(cache)
    print(f"  {len(chains):,} chains (components) across {len(cache):,} quests.")

    # Build zone_labels: {quest_id: zone_label} from original QC_MainQuests
    zone_labels = dict(main_q)

    # Warn about large chains
    large = [(root, members) for root, members in chains.items()
             if len(members) >= LARGE_CHAIN_WARN]
    if large:
        print(f"  WARNING: {len(large)} chains have {LARGE_CHAIN_WARN}+ quests:")
        for root, members in sorted(large, key=lambda x: -len(x[1]))[:10]:
            titles = [cache.get(q, {}).get("title") or f"#{q}" for q in members[:3]]
            print(f"    chain {root}: {len(members)} quests — {', '.join(titles)}...")
    print()

    # ── Pass 1: rule-based ────────────────────────────────────────────────────
    print("=== Pass 1: Rule-Based Classification ===")
    rule_assignments = {}  # {chain_root: category}
    rule_counts = defaultdict(int)

    for root, members in chains.items():
        cat = rule_based_category(members, cache, zone_labels)
        if cat:
            rule_assignments[root] = cat
            rule_counts[cat] += 1

    for cat, n in sorted(rule_counts.items()):
        print(f"  {cat:10s}: {n:,} chains")
    needs_llm = [(root, members) for root, members in chains.items()
                 if root not in rule_assignments]
    print(f"  → {len(needs_llm):,} chains need LLM classification.\n")

    # ── Pass 2: LLM classification ────────────────────────────────────────────
    llm_assignments = {}  # {chain_root: category}

    # --resume: load previous assignments so already-classified chains are kept
    prev_assignments = {}
    if args.resume and os.path.exists(ASSIGN_FILE):
        with open(ASSIGN_FILE, "r", encoding="utf-8") as f:
            prev_data = json.load(f)
        for root_str, info in prev_data.items():
            prev_assignments[int(root_str)] = info.get("category", "side")
        # Restore non-side rule-based and previously-good LLM results
        for root, cat in prev_assignments.items():
            if cat != "side":
                llm_assignments[root] = cat
        # In resume mode, only re-run chains that are currently "side"
        needs_llm = [(root, members) for root, members in chains.items()
                     if root not in rule_assignments
                     and prev_assignments.get(root, "side") == "side"]
        print(f"  --resume: {len(needs_llm):,} 'side' chains still need classification "
              f"({len(prev_assignments) - len(needs_llm) - len(rule_assignments):,} already done).\n")
    elif args.resume:
        print(f"  --resume: no existing {ASSIGN_FILE} found; running full pass.\n")

    if args.compare_first_batches:
        baseline_chain_categories = build_chain_category_map(chains, quest_cat0)
        differences = compare_first_claude_batches(
            args.compare_first_batches,
            chains,
            rule_assignments,
            cache,
            zone_labels,
            prev_assignments,
            baseline_chain_categories,
        )
        if differences:
            print(json.dumps(differences, indent=2))
        else:
            print("[]")
        return

    if args.skip_llm:
        print("(--skip-llm: skipping LLM classification; defaulting to 'side')\n")
        for root, _ in needs_llm:
            if root not in llm_assignments:
                llm_assignments[root] = "side"
    else:
        print("=== Pass 2: LLM Classification ===")
        print(f"  Model: {MODEL}  |  batch size: {CHAINS_PER_BATCH}")
        remaining = len(needs_llm)
        max_b = args.max_batches if args.max_batches > 0 else 999999
        cap = min(remaining, max_b * CHAINS_PER_BATCH)
        print(f"  Classifying {remaining:,} chains "
              f"(this run: up to {cap:,})...\n")

        # Validate Codex CLI availability once for the entire run
        llm_client = _make_client()
        llm_counts = defaultdict(int)
        batches_done = 0

        # Process in batches
        for batch_start in range(0, remaining, CHAINS_PER_BATCH):
            if args.max_batches > 0 and batches_done >= args.max_batches:
                print(f"  --max-batches {args.max_batches} reached; stopping LLM pass.")
                # Mark remaining as side so they're re-run next time
                for root, _ in needs_llm[batch_start:]:
                    if root not in llm_assignments:
                        llm_assignments[root] = "side"
                break

            batch = needs_llm[batch_start:batch_start + CHAINS_PER_BATCH]
            batch_num = batch_start // CHAINS_PER_BATCH + 1
            total_batches = (remaining + CHAINS_PER_BATCH - 1) // CHAINS_PER_BATCH

            print(f"  Batch {batch_num}/{total_batches} "
                  f"({batch_start+1}–{min(batch_start+CHAINS_PER_BATCH, remaining)}"
                  f" of {remaining})...", end=" ", flush=True)

            results = classify_chains_llm(batch, cache, zone_labels, client=llm_client)
            for root, _ in batch:
                cat = results.get(str(root), "side")
                llm_assignments[root] = cat
                llm_counts[cat] += 1

            print(f"done ({', '.join(f'{c}:{n}' for c,n in sorted(results.items() if hasattr(results,'items') else [])[:4])})")
            batches_done += 1

            # Save incremental progress every 10 batches so --resume can pick up
            if batches_done % 10 == 0:
                _save_incremental(chains, rule_assignments, llm_assignments,
                                  cache, zone_labels, ASSIGN_FILE)

            # Pause between batches — avoids haiku rate limits (tokens/min)
            time.sleep(4)

        print()
        for cat in ["story", "lore", "profession", "side"]:
            print(f"  {cat:10s}: {llm_counts[cat]:,} chains")
        print()
        # Final incremental save before Pass 3
        _save_incremental(chains, rule_assignments, llm_assignments,
                          cache, zone_labels, ASSIGN_FILE)

    # ── Pass 3: post-LLM mechanical adjustments ───────────────────────────────
    print("=== Pass 3: Dungeon Tagging & Daily Upgrades ===")
    combined = {**llm_assignments, **rule_assignments}  # rule overrides llm
    tag_counts = defaultdict(int)

    for root, members in chains.items():
        base = combined.get(root, "side")
        adjusted = apply_dungeon_tag(base, members, cache)
        if adjusted != base:
            combined[root] = adjusted
            tag_counts[adjusted] += 1

    for tag, n in sorted(tag_counts.items()):
        print(f"  +{n} chains → {tag}")
    print()

    # ── Expand chain assignments to individual quest IDs ──────────────────────
    quest_categories = {}  # {quest_id: category}
    for root, members in chains.items():
        cat = combined.get(root, "side")
        for qid in members:
            quest_categories[qid] = cat

    # ── Build extended title map ──────────────────────────────────────────────
    # Include all classified quest titles (not just formerly-main quests)
    extended_titles = dict(title_map)
    for qid, info in cache.items():
        t = info.get("title")
        if t and qid in quest_categories:
            key = t.lower()
            if key not in extended_titles:
                extended_titles[key] = qid

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=== Classification Summary ===")
    final_counts = defaultdict(int)
    for cat in quest_categories.values():
        final_counts[cat] += 1

    total_quests = len(quest_categories)
    for cat in ["intro", "story", "lore", "attune", "class", "profession", "daily", "pvp",
                "dungeon_story", "dungeon_lore", "dungeon_side", "side"]:
        n = final_counts.get(cat, 0)
        pct = 100.0 * n / total_quests if total_quests else 0
        print(f"  {cat:14s}: {n:>6,} quests ({pct:.1f}%)")
    print(f"  {'TOTAL':10s}: {total_quests:>6,} quests")
    print()

    # ── Write category_assignments.json ───────────────────────────────────────
    assignments_out = {}
    for root, members in chains.items():
        cat = combined.get(root, "side")
        assignments_out[str(root)] = {
            "category": cat,
            "chain_length": len(members),
            "quest_ids": members,
            "titles": [cache.get(q, {}).get("title") or f"#{q}" for q in members[:5]],
            "zone": next((zone_labels.get(q) for q in members if zone_labels.get(q)), None),
        }

    with open(ASSIGN_FILE, "w", encoding="utf-8") as f:
        json.dump(assignments_out, f, indent=2)
    print(f"✓  Written {ASSIGN_FILE}  ({len(assignments_out):,} chains)")
    print( "   Review this file before applying to QuestData.lua.\n")

    # ── Optionally write QuestData.lua ────────────────────────────────────────
    if args.apply:
        shutil.copy2(LUA_FILE, LUA_FILE + ".bak")
        print(f"Backup → {LUA_FILE}.bak")

        write_lua(LUA_FILE, quest_categories, extended_titles)
        print(f"✓  Written {LUA_FILE}:")
        print(f"   QC_QuestCategory: {len(quest_categories):,} quests")
        print(f"   QC_QuestTitles  : {len(extended_titles):,} titles")
        print()
        print("Done!  /reload in-game to apply the new quest categories.")
    else:
        print("Run with --apply to write QuestData.lua from these results.")


if __name__ == "__main__":
    main()
