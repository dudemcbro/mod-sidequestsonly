#!/usr/bin/env python3
"""
classify_quests.py  –  Classify WoW 3.3.5a quests into addon quest categories.

Uses Claude (the claude CLI, model MODEL) as the primary classifier for story /
lore / side decisions, with rules for attune, holiday, event, pvp, class and
profession quests, and per-quest dungeon, raid and daily tags.

Chains share one story / lore / side decision, so opening kill/collect quests
in story chains get [Story] too. Rules look at single quests: a PvP daily or a
key quest in a chain does not change the rest of the chain.

Prerequisites:
    wow_quests.json from export_quests.py in the working folder.
    claude CLI installed and authenticated (uses claude --print for LLM calls).

Run from tools/:
    python3 classify_quests.py --max-batches 50
    python3 classify_quests.py --resume --max-batches 50   # until no chain is left

Output:
    category_assignments.json   – per-chain and per-quest categories; review it,
                                  then turn it into SQL with questdata_to_sql.py
"""

import re, json, time, os, sys, argparse
from collections import defaultdict


# ── Configuration ─────────────────────────────────────────────────────────────

DB_EXPORT_FILE    = "wow_quests.json"    # from export_quests.py
ASSIGN_FILE       = "category_assignments.json"

MODEL             = "opus"       # claude --print --model (opus or sonnet)
CHAINS_PER_BATCH  = 5            # quest chains per subprocess call (sonnet needs smaller batches)
DESC_MAX_CHARS    = 180          # max chars of description / completion per quest
OBJ_MAX_CHARS     = 120          # max chars of objectives per quest
LARGE_CHAIN_WARN  = 20           # chains this long are flagged for manual review
# All quests in a chain are sent to the LLM — no sampling cap.

# QuestInfoID values (QuestInfo.dbc).
QUEST_INFO_RAID    = frozenset({62, 88, 89})   # Raid, Raid (10), Raid (25)
QUEST_INFO_DUNGEON = frozenset({81, 85})       # Dungeon, Heroic
QUEST_INFO_PVP     = 41

# Every rule below tags single quests, never whole chains: a chain linked by
# prev/next can hold a PvP daily or a key quest next to a zone's main story.

# PvP sort IDs: battlegrounds and Wintergrasp.
PVP_SORT_IDS = frozenset({
    -25,   # Battlegrounds
    2597,  # Alterac Valley
    2839,  # Alterac Valley (subzone)
    3277,  # Warsong Gulch
    3358,  # Arathi Basin
    3820,  # Eye of the Storm
    4197,  # Wintergrasp
    4384,  # Strand of the Ancients
    4710,  # Isle of Conquest
})

# Holiday / seasonal event sort IDs (negative QuestSortID values, QuestSort.dbc).
HOLIDAY_SORT_IDS = frozenset({
    -22,   # Seasonal: Hallow's End, Winter Veil, Harvest Festival
    -41,   # Day of the Dead
    -364,  # Darkmoon Faire
    -366,  # Lunar Festival (Elders of the Alliance/Horde)
    -369,  # Midsummer Fire Festival
    -370,  # Brewfest
    -374,  # Noblegarden
    -375,  # Pilgrim's Bounty
    -376,  # Love is in the Air
})

# Children's Week shares the "Special" sort (-284) with unrelated quests.
CHILDRENS_WEEK_QUEST_IDS = frozenset({
    171, 172, 558, 910, 911, 915, 925, 1468, 1479, 1558, 1687, 1800, 4822,
    5502, 10942, 10943, 10945, 10950, 10951, 10952, 10953, 10954, 10956,
    10960, 10962, 10963, 10966, 10967, 10968, 11974, 11975, 13926, 13927,
    13929, 13930, 13933, 13934, 13937, 13938, 13950, 13951, 13954, 13955,
    13956, 13957, 13959, 13960, 14441,
})

# World / patch events: limited-time server events, not annual holidays.
# The Argent Tournament (-241) is permanent: see NARRATIVE_SORT_OVERRIDES.
EVENT_SORT_IDS = frozenset({
    -365,  # AQ War Effort (server gate-opening event)
    -368,  # Scourge Invasion world event
})

# Patch 3.0 Zombie Infestation, filed under the "Special" sort (-284).
EVENT_QUEST_IDS = frozenset({
    12752, 12753, 12771, 12772, 12773, 12774, 12775, 12776, 12777, 12782,
    12783, 12784, 12785, 12786, 12787, 12788, 12808, 12809, 12811, 12812,
})

# Class quest sort IDs (QuestSort.dbc). Tier set token exchanges are not class
# quests: they go to the LLM as gear swaps (side).
CLASS_SORT_IDS = frozenset({
    -61,   # Warlock
    -81,   # Warrior
    -82,   # Shaman
    -141,  # Paladin
    -161,  # Mage
    -162,  # Rogue
    -261,  # Hunter
    -262,  # Priest
    -263,  # Druid
})

# Quests that grant a key or an attunement for a dungeon or raid. Only the
# quest that grants it: the chain leading up to it keeps its own category.
ATTUNE_QUEST_IDS = frozenset({
    4742, 4743,         # Seal of Ascension (Upper Blackrock Spire)
    5505, 5511,         # The Key to Scholomance
    6502,               # Drakefire Amulet (Onyxia's Lair, Alliance)
    6602,               # Blood of the Black Dragon Champion (Onyxia's Lair, Horde)
    7487, 7848,         # Attunement to the Core (Molten Core)
    7761,               # Blackhand's Command (Blackwing Lair)
    9121, 9122, 9123, 9378,  # The Dread Citadel - Naxxramas
    9837,               # Return to Khadgar (The Master's Key, Karazhan)
    10277,              # The Caverns of Time (Old Hillsbrad Foothills)
    10297,              # The Opening of the Dark Portal (The Black Morass)
    10445, 13432,       # The Vials of Eternity (Hyjal Summit)
    10459, 10558, 10559, 10560, 10561,  # Revered Among ... (heroic keys)
    10704,              # How to Break Into the Arcatraz
    10758, 10764,       # Hotter than Hell (Shattered Halls key)
    10883, 10884, 10885, 10886,  # The Tempest Key, Trial of the Naaru (Tempest Keep)
    10888, 11116, 13430,  # Trial of the Naaru: Magtheridon
    10901, 13431,       # The Cudgel of Kar'desh (Serpentshrine Cavern)
    10985, 13429,       # A Distraction for Akama (Black Temple)
    13372, 13375,       # The Key to the Focusing Iris (The Eye of Eternity)
    13607, 13816,       # The Celestial Planetarium (Algalon, Ulduar)
})

# Raid instance sort IDs. Quests the game types as raid quests
# (QUEST_INFO_RAID) are raid quests wherever they are filed.
RAID_SORT_IDS = frozenset({
    19, 1977,    # Zul'Gurub
    2159,        # Onyxia's Lair
    2562,        # Karazhan
    2677,        # Blackwing Lair
    2717,        # Molten Core
    3428,        # Ahn'Qiraj Temple
    3429,        # Ruins of Ahn'Qiraj
    3456,        # Naxxramas
    3606,        # Hyjal Summit
    3607,        # Serpentshrine Cavern
    3805,        # Zul'Aman
    3836,        # Magtheridon's Lair
    3840, 3959,  # Black Temple
    3923,        # Gruul's Lair
    4273,        # Ulduar
    4493,        # The Obsidian Sanctum
    4500,        # The Eye of Eternity
    4603,        # Vault of Archavon
    4722,        # Trial of the Crusader
    4812,        # Icecrown Citadel
    4987,        # The Ruby Sanctum
})

# Story / lore / side decided by hand instead of by the LLM.
# By sort ID: every quest filed there.
NARRATIVE_SORT_OVERRIDES = {
    -372: "story",  # Death Knight starting experience: one story chain
    -241: "side",   # Argent Tournament rank-ups (its dailies become daily)
}
# By chain: the whole chain holding the listed quest. Wins over the sort IDs.
NARRATIVE_CHAIN_OVERRIDES = {
    13663: "lore",  # The Black Knight's Orders (Argent Tournament Black Knight chain)
    8905:  "lore",  # An Earnest Proposition (dungeon set 2 upgrade, Anthion and Valthalak)
}

PROFESSION_SORT_IDS = frozenset({
    -373,  # Jewelcrafting
    -371,  # Inscription
    -324,  # First Aid
    -304,  # Cooking
    -264,  # Tailoring
    -201,  # Engineering
    -182,  # Leatherworking
    -181,  # Alchemy
    -121,  # Blacksmithing
    -101,  # Fishing
    -24,   # Herbalism
})

# RequiredSkillID values that are professions (SkillLine.dbc). Riding (762)
# and other skills are not.
PROFESSION_SKILL_IDS = frozenset({
    129,  # First Aid
    164,  # Blacksmithing
    165,  # Leatherworking
    171,  # Alchemy
    182,  # Herbalism
    185,  # Cooking
    186,  # Mining
    197,  # Tailoring
    202,  # Engineering
    333,  # Enchanting
    356,  # Fishing
    393,  # Skinning
    755,  # Jewelcrafting
    773,  # Inscription
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


# ── DB export loader ──────────────────────────────────────────────────────────

def load_db_export(path):
    """
    Load quest data from a wow_quests.json export (export_quests.py).
    Returns ({quest_id: quest info}, {quest_id: zone name}).

    DB field → quest info field mapping:
        title             ← LogTitle
        description       ← LogDescription   (quest log story text)
        details           ← QuestDescription (NPC dialog on accept)
        completion        ← QuestCompletionLog
        area_description  ← AreaDescription
        objectives        ← ObjectiveText1
        objectives2-4     ← ObjectiveText2-4
        is_daily          ← Flags & 0x1000
        quest_info_id     ← QuestInfoID  (41=PvP, 62/88/89=Raid, 81=Dungeon, 85=Heroic)
        prev_quest        ← PrevQuestID
        next_quest        ← NextQuestID
        allowable_classes ← AllowableClasses (>0 means class-specific)
        required_skill_id ← quest_template_addon.RequiredSkillID
        sort_id           ← QuestSortID
        starter_npc       ← creature_queststarter JOIN creature_template
    The zone name is AreaTable.dbc's (QuestSortID > 0) or QuestSort.dbc's
    (QuestSortID < 0) name for the quest's sort.
    """
    with open(path, "r", encoding="utf-8") as f:
        db_quests = json.load(f)

    quests = {}
    zone_labels = {}
    for q in db_quests:
        qid = q["id"]
        flags = q.get("flags", 0) or 0
        quests[qid] = {
            "id":                qid,
            "title":             q.get("title") or "",
            "description":       (q.get("description")      or "")[:DESC_MAX_CHARS],
            "details":           (q.get("details")          or "")[:DESC_MAX_CHARS],
            "completion":        (q.get("completion")       or "")[:DESC_MAX_CHARS],
            "area_description":  (q.get("area_description") or "")[:120],
            "objectives":        (q.get("objectives")       or "")[:OBJ_MAX_CHARS],
            "objectives2":       (q.get("objectives2")      or "")[:OBJ_MAX_CHARS],
            "objectives3":       (q.get("objectives3")      or "")[:OBJ_MAX_CHARS],
            "objectives4":       (q.get("objectives4")      or "")[:OBJ_MAX_CHARS],
            "is_daily":          bool(flags & 0x1000),
            "quest_info_id":     q.get("quest_info_id", 0) or 0,
            "sort_id":           q.get("sort_id", 0) or 0,
            "prev_quest":        q.get("prev_quest", 0) or 0,
            "next_quest":        q.get("next_quest", 0) or 0,
            "allowable_classes": q.get("allowable_classes", 0) or 0,
            "required_skill_id": q.get("required_skill_id", 0) or 0,
            "starter_npc":       (q.get("starter_npc") or "")[:80],
        }
        if q.get("zone"):
            zone_labels[qid] = q["zone"]
    return quests, zone_labels


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


def build_chains(quests):
    """
    Use union-find to group quest IDs into chains by their prev/next quest
    links. Returns {chain_root_id: [sorted list of quest IDs in chain]}.
    """
    uf = UnionFind()
    for qid, info in quests.items():
        uf.find(qid)  # ensure every quest is registered
        # Dungeon and raid quests join the open-world chain they belong to:
        # the [Dungeon] / [Raid] tag is added per quest in Pass 3, so the
        # chain shares one story.
        for link_field in ("prev_quest", "next_quest"):
            linked = info.get(link_field, 0) or 0
            if linked > 0 and linked in quests:
                uf.union(qid, linked)
    return uf.components()


def is_profession_related(qid, quests):
    """Return True only on high-confidence profession signals.

    Uses sort_id (verified profession buckets) and required_skill_id (quest
    mechanically gated behind a profession skill).  Text-pattern matching was
    removed because profession words appear in NPC dialogue/flavor text and
    produce too many false positives.  Ambiguous quests go to the LLM.
    """
    info = quests.get(qid, {})
    if (info.get("sort_id", 0) or 0) in PROFESSION_SORT_IDS:
        return True
    if (info.get("required_skill_id", 0) or 0) in PROFESSION_SKILL_IDS:
        return True
    return False


# ── Pass 1: rule-based classifier ─────────────────────────────────────────────

def rule_category(qid, quests):
    """
    Category a quest gets from its own data, whatever chain it is in: attune,
    holiday, event, pvp, class or profession. None when the quest needs its
    chain's story / lore / side decision.

    Priority: attune > holiday > event > pvp > class > profession.
    Holiday comes before pvp so Hallow's End pranks (typed PvP) stay holiday.
    """
    info    = quests.get(qid, {})
    sort_id = info.get("sort_id", 0) or 0
    if qid in ATTUNE_QUEST_IDS:
        return "attune"
    if sort_id in HOLIDAY_SORT_IDS or qid in CHILDRENS_WEEK_QUEST_IDS:
        return "holiday"
    if sort_id in EVENT_SORT_IDS or qid in EVENT_QUEST_IDS:
        return "event"
    if (info.get("quest_info_id", 0) or 0) == QUEST_INFO_PVP or sort_id in PVP_SORT_IDS:
        return "pvp"
    if sort_id in CLASS_SORT_IDS:
        return "class"
    if is_profession_related(qid, quests):
        return "profession"
    return None


def narrative_overrides(chain_ids, quests):
    """
    Return {quest_id: "story"|"lore"|"side"} for the quests in a chain whose
    narrative was decided by hand (NARRATIVE_CHAIN_OVERRIDES, then
    NARRATIVE_SORT_OVERRIDES).
    """
    for key, category in NARRATIVE_CHAIN_OVERRIDES.items():
        if key in chain_ids:
            return {qid: category for qid in chain_ids}
    result = {}
    for qid in chain_ids:
        sort_id = quests.get(qid, {}).get("sort_id", 0) or 0
        if sort_id in NARRATIVE_SORT_OVERRIDES:
            result[qid] = NARRATIVE_SORT_OVERRIDES[sort_id]
    return result


def needs_llm(chain_ids, quests):
    """True when some quest in the chain has neither a rule category nor a
    hand-decided narrative, so the chain needs the LLM's story/lore/side."""
    decided = narrative_overrides(chain_ids, quests)
    return any(rule_category(q, quests) is None and q not in decided
               for q in chain_ids)


# ── Pass 2: LLM classification ────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a World of Warcraft (Wrath of the Lich King 3.3.5a) quest analyst.
Classify quest chains into exactly one of four categories:

  story      – The chain is a CORE part of the zone's primary narrative arc. It directly
               advances the zone's central conflict, involves the zone's key named NPCs,
               and produces a meaningful in-world outcome (villain defeated, siege broken,
               conspiracy exposed). Story should be RARE — only chains that a player would
               consider "the main questline" of that zone qualify. A chain with named NPCs
               and a narrative arc still does NOT qualify as story if it is a side-arc that
               the zone's main story could proceed without.

  lore       – The chain has genuine narrative content — backstory, world history, NPC
               character depth, or regional lore — but is self-contained. The zone's
               primary story arc is unchanged without it. Examples: a ghost revealing the
               history of a ruined village, a minor faction's internal conflict, an
               explorer's journal mystery, a relic's origin. Lore is the DEFAULT for any
               chain with real narrative but not the zone's central conflict.

  side       – No meaningful narrative contribution. Repeated kill/collect/delivery tasks
               with no escalation, no named NPCs of significance, no connection to zone
               lore, and no outcome beyond XP/gold. Side is the DEFAULT when there is any
               doubt about narrative value.

  profession – The chain primarily trains, unlocks, specialises, or rewards a
               tradeskill (alchemy, blacksmithing, cooking, enchanting, engineering,
               first aid, fishing, herbalism, inscription, jewelcrafting, leatherworking,
               mining, skinning, tailoring). Use this even if the quest has minor story
               flavor text.

CALIBRATION — expected frequency across all WoW quests:
  side      ≈ 60–70%   (most quests are pure task filler)
  lore      ≈ 15–25%   (genuine narrative but not the main arc)
  story     ≈  5–10%   (only the central zone questlines)
  profession ≈  5%     (tradeskill quests — mostly handled by rules)

If you are classifying more than ~10% as story, you are being too generous with story.
When uncertain between story and lore → choose LORE.
When uncertain between lore and side → choose SIDE.

CANONICAL ZONE STORYLINES — only chains that are part of these arcs qualify as STORY:

Eastern Kingdoms:
  Elwynn Forest      – Defias Brotherhood infiltration + murloc threat
  Tirisfal Glades    – Forsaken vs the Scarlet Crusade + the Apothecarium's new plague
  Silverpine Forest  – Arugal's worgen + Shadowfang Keep + Dalaran's Ambermill
  Dun Morogh         – Frostmane troll assault + Leper Gnome incursion (Gnomeregan fallout)
  Loch Modan         – Trogg invasion + Stonewrought Dam sabotage plot
  Westfall           – Defias Brotherhood investigation + Deadmines assault
  Redridge Mountains – Blackrock orc occupation + Stonewatch Keep
  Duskwood           – Stalvan Mistmantle murder mystery + Stitches + Night Watch plague
  Wetlands           – Dragonmaw orc remnants + Dark Iron dwarf threat
  Hillsbrad Foothills– Syndicate operations + The Battle for Hillsbrad
  Arathi Highlands   – Stromgarde ruins + Tower of Arathor defense + Trol'kalar sword
  The Hinterlands    – Revantusk/Witherbark troll conflict + Quel'Danil Lodge defense
  Searing Gorge      – Dark Iron dwarves + Thorium Brotherhood resistance
  Burning Steppes    – Blackrock Mountain forces + Nefarian's black dragon brood
  Western Plaguelands– Scourge cauldrons + path to Scholomance
  Eastern Plaguelands– Scourge corruption + Scarlet Crusade conflict + Light's Hope
  Badlands           – Dark Iron dwarves + Uldaman titan ruins
  Swamp of Sorrows   – Atal'ai trolls + Sunken Temple investigation
  Ghostlands         – Blood elf reclamation from Scourge + Amani troll threat
  Eversong Woods     – Sin'dorei rebuilding + Scourge remnants + Burning Blade cult
  Isle of Quel'Danas – Sunwell Plateau assault + Kael'thas final confrontation

Kalimdor:
  Durotar            – Burning Blade demon cult + Zalazane's Echo Isles corruption
  Teldrassil         – Gnarlpine furbolg corruption + the Shadowglen moonwell
  Azuremyst Isle     – Exodar crash + blood elf saboteurs + Stillpine furbolgs
  Bloodmyst Isle     – Sun King's blood elves + the Vector Coil + Bloodmyst corruption
  Mulgore            – Venture Company threat + tauren coming-of-age rites
  The Barrens        – Quilboar Razorfen threat + centaur conflict + Wailing Caverns corruption
  Darkshore          – Twilight's Hammer introduction + communication relay repair
  Ashenvale          – Scythe of Elune mystery + satyr/Felwood corruption + faction conflict
  Stonetalon Mtns    – Venture Company logging operation + Grimtotem treachery
  Desolace           – Burning Blade cult + centaur clan wars (Magram vs Gelkis)
  Dustwallow Marsh   – The Missing Diplomat (King Varian Wrynn) + Onyxia's brood
  Thousand Needles   – Galak centaur + Razorfen Kraul quilboar
  Feralas            – Gordunni ogre operations + twin Colossals
  Tanaris            – Pirate conflict + Zul'Farrak troll threat
  Un'Goro Crater     – Silithid research experiments + Pylon mystery + A-Me 01
  Felwood            – Jaedenar Shadow Council corruption + Emerald Dream corruption
  Winterspring       – Kel'Theril night elf spirit curse + corrupted furbolg purification
  Silithus           – Twilight's Hammer cultists + Silithid invasion buildup (AQ)
  Azshara            – Naga operations + Hydraxian Waterlords

Outland:
  Hellfire Peninsula – Fel orc uprising + Hand of Gul'dan + establishing beachheads
  Zangarmarsh        – Naga draining the swamp + Cenarion Expedition survival
  Terokkar Forest    – Auchindoun and Shadow Council + Ethereal operations
  Nagrand            – Mag'har orc plight + Garrosh Hellscream's redemption arc
  Blade's Edge Mtns  – Gronn/Ogri'la conflict + Nether Dragon liberation
  Shadowmoon Valley  – Illidan's dominion + Hand of Gul'dan + Black Temple approach
  Netherstorm        – Manaforge sabotage + Kael'thas forces + Eco-Dome survival

Northrend:
  Borean Tundra      – Scourge citadel assault + Nerubian threat + Gearmaster Mechazod
  Howling Fjord      – Vrykul clan awakening + Lich King's origins revealed
  Dragonblight       – Wrathgate / Forsaken betrayal + Wyrmrest Temple + Blue Dragon war
  Grizzly Hills      – Wolfcult of Arugal + Vordrassil failure + Drakkari incursion
  Zul'Drak           – Drakkari trolls sacrificing their loa gods + Scourge siege
  Sholazar Basin     – Titan facility Freya's corruption + Wolvar vs Oracle conflict
  Storm Peaks        – Loken's betrayal of the titans + Iron dwarf/Vrykul forces
  Icecrown           – Assault on Icecrown Citadel + Argent Crusade + Ebon Blade + Lich King

DECISION RULES:
1. Is this chain part of a canonical zone storyline listed above? → story
2. Does this chain have real narrative content but is self-contained? → lore
3. Everything else → side
4. Does the chain train/unlock/reward a tradeskill? → profession (overrides 1-3)

Classify the chain as a UNIT, not quest by quest. Starting zones (Northshire, Valley of
Trials, Deathknell, etc.) are judged like any other zone: their main questline is story,
the rest lore or side. Chains with narrative flavor in quest text but no arc and no
consequence → side. Quests that only trade tokens or materials for a piece of gear
(tier set exchanges such as Paragons of Power or Naxxramas tier tokens) → side.
Repeatable daily quests are tagged [Daily] separately; judge a chain by its other quests.

Return ONLY valid JSON in exactly this format (no markdown, no explanation, just JSON):
{"results": [{"chain_id": "<id>", "category": "story|lore|side|profession", "reasoning": "<one sentence>"}]}
"""


def _build_chain_prompt_entry(chain_id, chain_ids, quests, zone_labels):
    """Format one chain's data for inclusion in the LLM prompt.
    All quests in the chain are included — no sampling cap."""
    lines = []

    # Zone context (use zone of first quest in chain that has one)
    zones = [zone_labels.get(q) for q in chain_ids if zone_labels.get(q)]
    zone_str = zones[0] if zones else "Unknown Zone"

    # Quest-giver NPC (first non-empty starter_npc in chain)
    starter = next(
        (quests.get(q, {}).get("starter_npc") or "" for q in chain_ids
         if (quests.get(q, {}).get("starter_npc") or "").strip()),
        ""
    )

    lines.append(f"CHAIN_ID: {chain_id}")
    lines.append(f"Zone: {zone_str}")
    if starter:
        lines.append(f"Quest giver: {starter}")
    lines.append(f"Chain length: {len(chain_ids)} quest(s)")

    for i, qid in enumerate(chain_ids, 1):
        info    = quests.get(qid, {})
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


def classify_chains_llm(batch, quests, zone_labels):
    """
    Classify a batch of chains using `claude --print` subprocess.
    batch: list of (chain_id, chain_ids) tuples
    Returns {chain_id: "story"|"lore"|"side"|"profession"} dict.
    A chain the LLM did not answer for is left out, so it stays unclassified
    and --resume asks again.
    """
    import subprocess

    if not batch:
        return {}

    # Build the user message listing all chains in this batch
    entries = []
    for chain_id, chain_ids in batch:
        entries.append(_build_chain_prompt_entry(chain_id, chain_ids, quests, zone_labels))

    user_content = (
        f"Classify these {len(batch)} quest chain(s). "
        "Return JSON exactly as specified in the system prompt.\n\n"
        + "\n\n---\n\n".join(entries)
    )

    full_prompt = SYSTEM_PROMPT + "\n\n" + user_content

    for attempt in range(3):
        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            result = subprocess.run(
                ["claude", "--print", "--model", MODEL,
                 "--output-format", "text", "--no-session-persistence"],
                input=full_prompt,
                capture_output=True, text=True,
                timeout=180, env=env,
            )
            raw_text = result.stdout.strip()
            if not raw_text:
                raise ValueError(f"Empty response (stderr: {result.stderr[:200]})")

            # Extract JSON object — scan for first { … last }
            json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if json_match:
                raw_text = json_match.group(0)

            parsed = json.loads(raw_text)
            results = {}
            for item in parsed.get("results", []):
                cid = str(item.get("chain_id", ""))
                cat = str(item.get("category", "")).lower()
                if cat in ("story", "lore", "side", "profession"):
                    results[cid] = cat
            return results

        except subprocess.TimeoutExpired:
            print(f"    CLI timeout (attempt {attempt+1})")
            if attempt < 2:
                time.sleep(5)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"    LLM parse error (attempt {attempt+1}): {e}; "
                  f"reply: {result.stdout.strip()[:150]!r}")
            if attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as e:
            print(f"    Error (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)

    # All retries failed: leave the batch unclassified for --resume.
    print("    All retries failed; batch left unclassified.")
    return {}


# ── Pass 3: post-LLM mechanical adjustments ───────────────────────────────────

def is_raid(info):
    return ((info.get("quest_info_id", 0) or 0) in QUEST_INFO_RAID
            or (info.get("sort_id", 0) or 0) in RAID_SORT_IDS)


def quest_category(qid, chain_category, overrides, quests):
    """
    Final category of one quest. chain_category is the chain's story / lore /
    side / profession from the LLM (None if it has none); overrides is the
    chain's narrative_overrides().

    Rule categories win. Otherwise the quest's narrative (hand-decided, else
    the chain's) gets the raid_ or dungeon_ prefix for raid and dungeon quests,
    and an open-world daily becomes daily. Returns None when the quest has no
    category yet (its chain still needs the LLM).
    """
    rule = rule_category(qid, quests)
    if rule:
        return rule
    base = overrides.get(qid, chain_category)
    if base is None or base == "profession":
        return base
    info = quests.get(qid, {})
    if is_raid(info):
        return f"raid_{base}"
    if (info.get("quest_info_id", 0) or 0) in QUEST_INFO_DUNGEON:
        return f"dungeon_{base}"
    if info.get("is_daily", False):
        return "daily"
    return base


# ── Summary order ─────────────────────────────────────────────────────────────

CATEGORY_ORDER = [
    "story", "lore", "attune", "class", "profession", "daily", "pvp", "holiday", "event",
    "dungeon_story", "dungeon_lore", "dungeon_side", "raid_story", "raid_lore", "raid_side",
    "side",
]


# ── Assignments file ──────────────────────────────────────────────────────────

def write_assignments(path, chains, llm_assignments, llm_done, quests, zone_labels):
    """
    Write category_assignments.json and return {quest_id: category}.

    Per chain: "category" is the LLM's story / lore / side / profession (null
    when the chain did not need the LLM or has no answer yet), "needs_llm" and
    "llm_done" say whether it still needs one, and "quest_categories" holds the
    final category of every quest (null while its chain needs the LLM).
    --resume and questdata_to_sql.py read this file.
    """
    quest_categories = {}
    out = {}
    for root, members in chains.items():
        overrides = narrative_overrides(members, quests)
        chain_cat = llm_assignments.get(root)
        cats = {q: quest_category(q, chain_cat, overrides, quests) for q in members}
        quest_categories.update({q: c for q, c in cats.items() if c})
        out[str(root)] = {
            "category": chain_cat,
            "chain_length": len(members),
            "quest_ids": members,
            "quest_categories": {str(q): c for q, c in cats.items()},
            "titles": [quests.get(q, {}).get("title") or f"#{q}" for q in members[:5]],
            "zone": next((zone_labels.get(q) for q in members if zone_labels.get(q)), None),
            "needs_llm": needs_llm(members, quests),
            "llm_done": root in llm_done,
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return quest_categories


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Classify WoW quests into the addon's quest categories using Claude.")
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="Skip LLM classification; chains that need it stay unclassified "
             "(useful for testing the rules).")
    parser.add_argument(
        "--resume", action="store_true",
        help="Load existing category_assignments.json and only run the LLM on chains "
             "it has not classified yet. Use with --max-batches to make incremental "
             "progress across several runs.")
    parser.add_argument(
        "--max-batches", type=int, default=0,
        help="Stop LLM pass after this many batches (0 = unlimited). "
             "Use with --resume to make incremental progress across multiple runs.")
    args = parser.parse_args()

    if not os.path.exists(DB_EXPORT_FILE):
        sys.exit(f"Cannot find {DB_EXPORT_FILE}: write it with export_quests.py "
                 "and run from the folder that holds it.")

    print("=== Loading Quest Data from DB Export ===")
    quests, zone_labels = load_db_export(DB_EXPORT_FILE)
    print(f"  {len(quests):,} quests from {DB_EXPORT_FILE}, "
          f"{len(zone_labels):,} with a zone.\n")

    # ── Build chains ──────────────────────────────────────────────────────────
    print("=== Building Quest Chains ===")
    chains = build_chains(quests)
    print(f"  {len(chains):,} chains (components) across {len(quests):,} quests.")

    # Warn about large chains
    large = [(root, members) for root, members in chains.items()
             if len(members) >= LARGE_CHAIN_WARN]
    if large:
        print(f"  WARNING: {len(large)} chains have {LARGE_CHAIN_WARN}+ quests:")
        for root, members in sorted(large, key=lambda x: -len(x[1]))[:10]:
            titles = [quests.get(q, {}).get("title") or f"#{q}" for q in members[:3]]
            print(f"    chain {root}: {len(members)} quests — {', '.join(titles)}...")
    print()

    # ── Pass 1: rule-based ────────────────────────────────────────────────────
    print("=== Pass 1: Rule-Based Classification ===")
    rule_counts = defaultdict(int)
    overridden = 0
    for root, members in chains.items():
        overrides = narrative_overrides(members, quests)
        for qid in members:
            cat = rule_category(qid, quests)
            if cat:
                rule_counts[cat] += 1
            elif qid in overrides:
                overridden += 1

    for cat, n in sorted(rule_counts.items()):
        print(f"  {cat:10s}: {n:,} quests")
    print(f"  {'by hand':10s}: {overridden:,} quests (NARRATIVE_*_OVERRIDES)")
    llm_chains = [(root, members) for root, members in chains.items()
                  if needs_llm(members, quests)]
    print(f"  → {len(llm_chains):,} chains need LLM classification.\n")

    # ── Pass 2: LLM classification ────────────────────────────────────────────
    llm_assignments = {}  # {chain_root: "story"|"lore"|"side"|"profession"}
    llm_done = set()      # chain roots the LLM has answered for

    # --resume: keep the answers of a previous run
    if args.resume and os.path.exists(ASSIGN_FILE):
        with open(ASSIGN_FILE, "r", encoding="utf-8") as f:
            prev_data = json.load(f)
        for root_str, info in prev_data.items():
            root = int(root_str)
            # Files from before per-quest tagging hold final categories here.
            if (info.get("llm_done", False)
                    and info.get("category") in ("story", "lore", "side", "profession")):
                llm_assignments[root] = info["category"]
                llm_done.add(root)
        print(f"  --resume: {len(llm_done):,} chains already classified.\n")
    elif args.resume:
        print(f"  --resume: no existing {ASSIGN_FILE} found; running full pass.\n")
    todo = [(root, members) for root, members in llm_chains if root not in llm_done]

    if args.skip_llm:
        print(f"(--skip-llm: {len(todo):,} chains left unclassified)\n")
    else:
        print("=== Pass 2: LLM Classification ===")
        print(f"  Model: {MODEL}  |  batch size: {CHAINS_PER_BATCH}")
        remaining = len(todo)
        max_b = args.max_batches if args.max_batches > 0 else 999999
        cap = min(remaining, max_b * CHAINS_PER_BATCH)
        print(f"  Classifying {remaining:,} chains "
              f"(this run: up to {cap:,})...\n")

        llm_counts = defaultdict(int)
        batches_done = 0

        # Process in batches
        for batch_start in range(0, remaining, CHAINS_PER_BATCH):
            if args.max_batches > 0 and batches_done >= args.max_batches:
                print(f"  --max-batches {args.max_batches} reached; stopping LLM pass.")
                break

            batch = todo[batch_start:batch_start + CHAINS_PER_BATCH]
            batch_num = batch_start // CHAINS_PER_BATCH + 1
            total_batches = (remaining + CHAINS_PER_BATCH - 1) // CHAINS_PER_BATCH

            print(f"  Batch {batch_num}/{total_batches} "
                  f"({batch_start+1}–{min(batch_start+CHAINS_PER_BATCH, remaining)}"
                  f" of {remaining})...", end=" ", flush=True)

            results = classify_chains_llm(batch, quests, zone_labels)
            for root, _ in batch:
                cat = results.get(str(root))
                if cat:
                    llm_assignments[root] = cat
                    llm_done.add(root)
                    llm_counts[cat] += 1
                else:
                    llm_counts["unanswered"] += 1

            print(f"done ({', '.join(f'{c}:{n}' for c,n in sorted(results.items())[:4])})")
            batches_done += 1

            # Save incremental progress every 5 batches so --resume can pick up
            if batches_done % 5 == 0:
                write_assignments(ASSIGN_FILE, chains, llm_assignments, llm_done,
                                  quests, zone_labels)

            # Small pause between subprocess calls
            time.sleep(1)

        print()
        for cat in ["story", "lore", "profession", "side", "unanswered"]:
            print(f"  {cat:10s}: {llm_counts[cat]:,} chains")
        print()

    # ── Pass 3: per-quest categories, write category_assignments.json ─────────
    print("=== Pass 3: Quest Categories ===")
    quest_categories = write_assignments(ASSIGN_FILE, chains, llm_assignments, llm_done,
                                         quests, zone_labels)
    print(f"✓  Written {ASSIGN_FILE}  ({len(chains):,} chains)")
    print( "   Review this file before turning it into SQL (tools/questdata_to_sql.py).")
    unclassified = [(root, members) for root, members in llm_chains if root not in llm_done]
    if unclassified:
        n_quests = sum(len(members) for _, members in unclassified)
        print(f"  WARNING: {len(unclassified):,} chains ({n_quests:,} quests) still need "
              f"the LLM; run again with --resume.")
    print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=== Classification Summary ===")
    final_counts = defaultdict(int)
    for cat in quest_categories.values():
        final_counts[cat] += 1

    total_quests = len(quest_categories)
    for cat in CATEGORY_ORDER:
        n = final_counts.get(cat, 0)
        pct = 100.0 * n / total_quests if total_quests else 0
        print(f"  {cat:14s}: {n:>6,} quests ({pct:.1f}%)")
    print(f"  {'TOTAL':10s}: {total_quests:>6,} quests")
    print()


if __name__ == "__main__":
    main()
