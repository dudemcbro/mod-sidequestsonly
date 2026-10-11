# mod-sidequestsonly

An [AzerothCore](https://www.azerothcore.org/) (WotLK 3.3.5a) module and client addon
that badge every quest with what kind of quest it is, so you can tell the story apart
from the filler: `[Story]`, `[Lore]`, `[Side]`, `[Class]`, `[Dungeon][Story]`, `[Raid][Lore]`
and so on.

The server keeps the categories in the world database and sends them to the
SideQuestsOnly addon. GMs can change a quest's category in game and every player with
the addon sees it at once.

## Features

- Badges in the quest log list, the quest log detail panel, the objective tracker, the
  world map quest list and NPC quest dialogs, plus a chat line when an NPC offers you a
  quest and when you accept one.
- 9,528 quests classified out of the box (see [Classification](#classification)).
- The server tells the addon which quest each NPC dialog is about, so the dialog badge is
  right even for a quest you have never seen.
- `.sqo set` / `.sqo remove` change a category for the whole realm without a restart.

### Categories

| Badge | Category | Meaning |
|---|---|---|
| `[Story]` | `story` | Drives the zone or expansion story |
| `[Lore]` | `lore` | World-building; optional but meaningful |
| `[Attune]` | `attune` | Needed to enter a raid or dungeon |
| `[Class]` | `class` | Class quest chain |
| `[Profession]` | `profession` | Profession quest or reward |
| `[Daily]` | `daily` | Repeatable daily |
| `[PvP]` | `pvp` | Battleground, Wintergrasp or other PvP quest |
| `[Holiday]` | `holiday` | Seasonal event (Hallow's End, Brewfest, ...) |
| `[Event]` | `event` | World or patch event (Scourge Invasion, AQ War Effort, ...) |
| `[Dungeon][Story]` | `dungeon_story` | Dungeon quest with a real story |
| `[Dungeon][Lore]` | `dungeon_lore` | Dungeon quest with lore content |
| `[Dungeon][Side]` | `dungeon_side` | Dungeon quest, filler |
| `[Raid][Story]` | `raid_story` | Raid quest with a real story |
| `[Raid][Lore]` | `raid_lore` | Raid quest with lore content |
| `[Raid][Side]` | `raid_side` | Raid quest, filler |
| `[Side]` | `side` | Filler with no story value |
| `[?]` | | Not classified (NPC dialogs only) |

`[Intro]` (`intro`, starting zone quests) is retired: the classifier no longer assigns it
and starting zone quests get story, lore or side like everywhere else. The server and
addon still accept it for data from older classifier runs.

## Requirements

- AzerothCore WotLK (3.3.5a). Built on the
  [mod-playerbots AzerothCore fork](https://github.com/mod-playerbots/azerothcore-wotlk)
  and uses only hooks that stock
  [azerothcore-wotlk](https://github.com/azerothcore/azerothcore-wotlk) has too. No core
  patches.
- Players need the SideQuestsOnly addon from `client-addon/`.

## Installation

### Server

1. Clone the module into the core's `modules` directory:

   ```sh
   cd <azerothcore>/modules
   git clone https://github.com/dudemcbro/mod-sidequestsonly.git
   ```

2. Re-run CMake and rebuild the core.
3. Copy `conf/mod_sidequestsonly.conf.dist` to your config directory's `modules/` folder
   as `mod_sidequestsonly.conf` (optional: the default is enabled).
4. Restart `worldserver`. It installs the module's SQL on start (see
   [Database setup](#database-setup)).

### Client

Copy `client-addon/SideQuestsOnly/` into the client's `Interface/AddOns/` folder so the
`.toc` sits at `Interface/AddOns/SideQuestsOnly/SideQuestsOnly.toc`, then restart the
client. Remove any older SideQuestsOnly first: version 2 has no `QuestData.lua`, it gets
everything from the server. Raid categories need addon 2.1.0 or later.

On login the addon prints `SideQuestsOnly v2.1.0  9528 quests classified by the server.`
If the server does not answer within 15 seconds it says so; `/sqo sync` asks again.

## Configuration

`mod_sidequestsonly.conf`:

| Option | Default | Meaning |
|---|---|---|
| `SideQuestsOnly.Enable` | `1` | `0` stops answering the addon and naming quests in NPC dialogs. |

## Database setup

The module's SQL is in `data/sql/world/` and creates one table in the world database,
`mod_sidequestsonly_quest` (`QuestId`, `Category`). `worldserver` imports it on start when
`Updates.EnableDatabases` includes the world database (flag `4`, part of the default
`7`), and again whenever the file changes.

The data rows use `INSERT IGNORE`, so a category a GM changed with `.sqo set` survives a
re-import. To reset everything to the file's values, run the rollback below and restart.

To apply the SQL by hand instead:

```sh
mysql -u <user> -p acore_world < modules/mod-sidequestsonly/data/sql/world/mod_sidequestsonly_quest.sql
```

### Rollback

`data/sql/rollback/world/mod_sidequestsonly_rollback.sql` drops the table. The worldserver
never imports `data/sql/rollback/`; run it by hand after removing the module.

## Usage

### Commands

| Command | Level | What it does |
|---|---|---|
| `.sqo get <questId>` | player | Shows a quest's category. |
| `.sqo set <questId> <category>` | GM (2) | Sets a category and sends it to every player with the addon. |
| `.sqo remove <questId>` | GM (2) | Removes a quest's category (it shows as `[?]`). |
| `.sqo reload` | admin (3) | Reloads the table and resends it, after editing the database by hand. |
| `.sqo sync` | player | What the addon sends on login; you don't need to type it. |

### Addon

| Command | What it does |
|---|---|
| `/sqo` | Status and help |
| `/sqo check` | Lists the quests in your log with their categories |
| `/sqo <id>` | Looks up a quest ID |
| `/sqo sync` | Asks the server for the categories again |
| `/sqo debug`, `/sqo npc`, `/sqo frames` | Troubleshooting for clients with custom quest frames (HD clients) |

## How it works

The addon runs `.sqo sync` once it has loaded, over AzerothCore's addon command channel
(an addon whisper to yourself with the prefix `AzerothCore`), so nothing shows in chat.
The server answers with addon whispers, prefix `SQO`: the categories in about 55
messages of compact id lists (see `src/SideQuestsOnly.h` for the format), then later only
changes. Just before it sends a quest dialog (quest details, items needed, reward), the
server sends that quest's id, category and title, which is how the addon knows which
quest an NPC is offering.

## Classification

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

The shipped SQL is still the classifier's 2026-03-27 run (`category_assignments.json`),
from before these rules.

To regenerate the SQL after a new classifier run:

```sh
python3 tools/questdata_to_sql.py path/to/QuestData.lua
# or straight from the classifier's review file:
python3 tools/questdata_to_sql.py path/to/category_assignments.json
```

It stops if the classifier left chains without an LLM answer (run the classifier again
with `--resume`); `--allow-incomplete` writes the SQL anyway and leaves those quests out.

`tools/classify_quests.py` (Claude) and `tools/classify_quests_codex.py` (Codex CLI) need
`requests` and either the `anthropic` package with `ANTHROPIC_API_KEY` set or an
authenticated Codex CLI. They read and write their caches (`wow_quests.json`,
`quest_chain_cache.json`, `category_assignments.json`) in the folder they run in; those
are large and kept out of the repository. `tools/expand_chains.py` is the older
main/side chain expander, kept for reference.

## Testing

`tools/test-server/podman-test.sh` builds the module in a throwaway container from the
prebuilt test server image (`ghcr.io/dudemcbro/acore-test-server`, from
`apps/test-server-image` in azerothcore-wotlk-playerbots-custom), starts the servers and
runs `tools/test-server/testclient/sqo_smoke.py` with a headless 3.3.5a client: the sync
against the table, the quest id before an NPC dialog, and the GM commands. `--verbose`
prints every chat line.

## License

GNU GPL v3, see [LICENSE](LICENSE).
