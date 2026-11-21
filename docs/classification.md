# Classification

How the shipped quest categories were made, and how to run the classifier yourself.

The categories come from `tools/classify_quests.py`, which combines rules with an LLM
pass over quest chains for the story / lore / side split, using warcraft.wiki.gg zone
storyline and campaign pages and pre-Cataclysm WoWWiki pages as references.

- Quests linked by previous / next quest form a chain, and a chain shares one story /
  lore / side decision.
- Rules tag single quests, whatever their chain: attunement and key quests (a fixed list),
  holidays, world events, PvP, class quests (the class quest sorts) and professions.
- Dungeon and raid quests get the `[Dungeon]` or `[Raid]` prefix on their chain's story /
  lore / side. Raid quests are the ones the game types as raid quests, plus every quest
  filed under a raid.
- Open-world daily quests are `[Daily]`.
- A few story / lore / side calls are made by hand (`NARRATIVE_*_OVERRIDES`): the Death
  Knight start is story, the Argent Tournament's Black Knight chain and the dungeon set 2
  upgrade chain are lore, and the rest of the Tournament is side.

The shipped SQL is the classifier's 2026-10-28 run with these rules, covering all
9,536 quests.

## Running the classifier

1. Export the quests with `tools/export_quests.py`, where it can reach `acore_world` and
   the server's DBC files (usually on the server). It writes `wow_quests.json` with each
   quest's text, links, sort and zone name (from `AreaTable.dbc` and `QuestSort.dbc`).
   It uses PyMySQL when installed, otherwise the `mysql` client.

   ```sh
   python3 export_quests.py --conf /path/to/worldserver.conf
   # or: MYSQL_PWD=... python3 export_quests.py --host ... --user ... --dbc-dir /path/to/dbc
   ```

2. Copy `wow_quests.json` into `tools/` and run the classifier from there. The LLM pass
   calls an authenticated `claude` CLI (`claude --print`, model `MODEL` in the script),
   five chains per call. `--skip-llm` runs only the rules; `--max-batches N` stops after
   N calls, and `--resume` keeps the answers so far.

   ```sh
   cd tools
   python3 classify_quests.py --max-batches 50
   python3 classify_quests.py --resume --max-batches 50   # until no chain is left
   ```

   It writes `category_assignments.json`, the per-chain and per-quest result to review.

3. Turn it into the module's SQL:

   ```sh
   python3 tools/questdata_to_sql.py tools/category_assignments.json
   ```

   It stops if the classifier left chains without an LLM answer (run the classifier
   again with `--resume`); `--allow-incomplete` writes the SQL anyway and leaves those
   quests out. It also reads `QC_QuestCategory` from an old v1 `QuestData.lua`.

`wow_quests.json` and `category_assignments.json` are large and kept out of the
repository. `tools/expand_chains.py` is the older main/side chain expander, kept for
reference.
