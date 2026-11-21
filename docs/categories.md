# Categories

Every badge the addon shows, the category name the server stores, and what it means.

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

How quests get their category is in [Classification](classification.md).
