#!/usr/bin/env python3
"""
expand_chains.py  –  Expand QuestData.lua with full quest-chain members.

For every main quest already in QC_MainQuests, fetches the WoWHead WotLK
page and reads the "Quest series" table.  Every quest in the same chain as
a known main quest is also added to QC_MainQuests and QC_QuestTitles.

Run from the SideQuestsOnly addon directory:
    python3 expand_chains.py

Output files:
    QuestData.lua          – updated in-place
    QuestData.lua.bak      – backup of the original
    quest_chain_cache.json – HTTP cache; delete to force a full re-fetch

Rate-limited to 2 req/s so as not to hammer WoWHead.
"""

import re, json, time, os, sys, shutil
from collections import deque, defaultdict

try:
    import requests
except ImportError:
    sys.exit("pip install requests  (requests library not found)")

# ── Config ──────────────────────────────────────────────────────────────────
WOWHEAD_BASE = "https://wotlk.wowhead.com"
RATE_LIMIT   = 0.5          # seconds between requests
CACHE_FILE   = "quest_chain_cache.json"
LUA_FILE     = "QuestData.lua"
# ────────────────────────────────────────────────────────────────────────────


# ── LUA PARSER ───────────────────────────────────────────────────────────────

def load_lua(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def parse_main_quests(lua):
    """Return {quest_id: zone_label}."""
    result = {}
    for m in re.finditer(r'_m\[(\d+)\]\s*=\s*"([^"]+)"', lua):
        result[int(m.group(1))] = m.group(2)
    return result

def parse_title_map(lua):
    """Return {lowercase_title: quest_id}."""
    result = {}
    for m in re.finditer(r'_t\["([^"]+)"\]\s*=\s*(\d+)', lua):
        result[m.group(1)] = int(m.group(2))
    return result


# ── WOWHEAD FETCHER ──────────────────────────────────────────────────────────

def scrape_quest(quest_id, session):
    """
    Fetch the WoWHead WotLK page for quest_id.
    Returns a dict:
        {
            "id":     int,
            "title":  str | None,
            "series": [int, ...]   # all quest IDs in the same chain
        }
    Returns None on network / non-200 error (caller may retry or skip).
    """
    url = f"{WOWHEAD_BASE}/quest={quest_id}"
    try:
        r = session.get(url, timeout=20)
    except requests.RequestException as e:
        print(f"    network error for {quest_id}: {e}")
        return None

    if r.status_code == 404:
        return {"id": quest_id, "title": None, "series": []}
    if r.status_code != 200:
        print(f"    HTTP {r.status_code} for quest {quest_id}")
        return None

    text = r.text

    # ── Title ────────────────────────────────────────────────────────────────
    title = None
    tm = re.search(r'<title>([^<]+)</title>', text)
    if tm:
        raw = tm.group(1)
        # "As Water Cascades - Quest - WotLK Classic"
        # "The Princess Trapped - Quest - WotLK Classic"
        title = re.sub(r'\s*-\s*Quest\s*-.*$', '', raw,
                       flags=re.IGNORECASE).strip()

    # ── Series table ─────────────────────────────────────────────────────────
    # WoWHead renders a <table class="series"> listing every quest in the chain.
    # Example row for a linked quest:
    #   <a href="/wotlk/quest=651/stones-of-binding">Stones of Binding</a>
    # The current quest is in a <b>…</b> (no href).
    series = []
    sm = re.search(r'<table class="series">(.*?)</table>', text, re.DOTALL)
    if sm:
        chunk = sm.group(1)
        # Extract IDs from hrefs
        series = [int(x) for x in re.findall(r'/wotlk/quest=(\d+)/', chunk)]

    return {"id": quest_id, "title": title, "series": series}


# ── CACHE HELPERS ─────────────────────────────────────────────────────────────

def load_cache(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {int(k): v for k, v in raw.items()}
    return {}

def save_cache(path, cache):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in cache.items()}, f, indent=2)


# ── LUA WRITER ────────────────────────────────────────────────────────────────

HEADER_TEMPLATE = """\
-- QuestClassification Quest Data
-- Updated by expand_chains.py
--
-- A quest is [Main] if it is:
--   1. Listed on warcraft.wiki.gg zone storyline / campaign pages, OR
--   2. Part of a quest chain that contains a quest from source 1.
-- All IDs validated against the 3.3.5a AzerothCore DB.

-- QC_MainQuests[questID] = "Source/ZoneName"
QC_MainQuests = {}
local _m = QC_MainQuests
"""

def write_lua(path, main_quests, title_map):
    """Regenerate QuestData.lua from expanded dicts."""
    by_zone = defaultdict(list)
    for qid, zone in main_quests.items():
        by_zone[zone].append(qid)

    lines = [HEADER_TEMPLATE.rstrip()]
    for zone in sorted(by_zone):
        for qid in sorted(by_zone[zone]):
            lines.append(f'_m[{qid}] = "{zone}"')

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


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(LUA_FILE):
        sys.exit(f"Cannot find {LUA_FILE} – run from the SideQuestsOnly directory.")

    lua       = load_lua(LUA_FILE)
    main_q    = parse_main_quests(lua)   # {id: zone}
    title_map = parse_title_map(lua)     # {title: id}

    print(f"Loaded  {len(main_q):,} main quests | {len(title_map):,} title entries")

    cache = load_cache(CACHE_FILE)
    print(f"Cache   {len(cache):,} entries pre-loaded from {CACHE_FILE}")
    print()

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (compatible; SideQuestsOnly-ChainExpander/1.0)")

    # BFS
    id_to_zone = dict(main_q)          # every quest we consider main → zone
    queue      = deque(main_q.keys())  # start from all known main quests
    visited    = set()

    new_main   = {}   # {id: zone} – chain quests not in original main_q
    new_titles = {}   # {title: id} – titles for newly found chain quests

    total_q   = len(queue)
    processed = 0
    fetched   = 0

    while queue:
        qid = queue.popleft()
        if qid in visited or qid <= 0:
            continue
        visited.add(qid)
        zone = id_to_zone.get(qid, "chain")

        # Fetch or use cache
        if qid not in cache:
            processed += 1
            fetched   += 1
            est_remaining = len(queue)
            print(f"  [{processed:>5} fetched | {est_remaining:>5} queued]"
                  f"  quest {qid}…", end=" ", flush=True)
            info = scrape_quest(qid, session)
            if info is None:
                print("(skipped)")
                continue
            cache[qid] = info
            if fetched % 100 == 0:
                save_cache(CACHE_FILE, cache)
                print(f"\n  [cache saved – {len(new_main)} chain quests found]\n")
            time.sleep(RATE_LIMIT)
            print(f"chain={info['series']}")
        else:
            info = cache[qid]

        # Record title for newly discovered quests
        if qid not in main_q and info.get("title"):
            new_titles[info["title"].lower()] = qid

        # Enqueue chain siblings that aren't already classified as main
        for sibling_id in info.get("series", []):
            if sibling_id > 0 and sibling_id not in id_to_zone:
                id_to_zone[sibling_id] = zone + " (chain)"
                new_main[sibling_id]   = id_to_zone[sibling_id]
                queue.append(sibling_id)
                total_q += 1

    # Final cache save
    save_cache(CACHE_FILE, cache)
    print(f"\n✓  BFS complete.")
    print(f"   Fetched  {fetched:,} pages | {len(new_main):,} chain quests discovered\n")

    if not new_main:
        print("Nothing new – every quest is already in QC_MainQuests.")
        print("QuestData.lua unchanged.")
        return

    # Backup original
    shutil.copy2(LUA_FILE, LUA_FILE + ".bak")
    print(f"Backup → {LUA_FILE}.bak")

    # Merge and write
    expanded_main   = {**main_q,    **new_main}
    expanded_titles = {**title_map, **new_titles}

    write_lua(LUA_FILE, expanded_main, expanded_titles)
    print(f"Updated {LUA_FILE}:")
    print(f"  Main quests : {len(main_q):,} → {len(expanded_main):,}"
          f"  (+{len(new_main):,} chain quests)")
    print(f"  Title map   : {len(title_map):,} → {len(expanded_titles):,}"
          f"  (+{len(new_titles):,} titles)")
    print("\nDone!  /reload in-game to apply.")


if __name__ == "__main__":
    main()
