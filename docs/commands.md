# Commands

The `.sqo` server commands and the `/sqo` addon commands.

## Commands

| Command | Level | What it does |
|---|---|---|
| `.sqo get <questId>` | player | Shows a quest's category. |
| `.sqo set <questId> <category>` | GM (2) | Sets a category and sends it to every player with the addon. |
| `.sqo remove <questId>` | GM (2) | Removes a quest's category (it shows as `[?]`). |
| `.sqo reload` | admin (3) | Reloads the table and resends it, after editing the database by hand. |
| `.sqo sync` | player | What the addon sends on login; you don't need to type it. |

The category names for `.sqo set` are listed in [Categories](categories.md).

## Addon

| Command | What it does |
|---|---|
| `/sqo` | Status and help |
| `/sqo check` | Lists the quests in your log with their categories |
| `/sqo <id>` | Looks up a quest ID |
| `/sqo sync` | Asks the server for the categories again |
| `/sqo debug`, `/sqo npc`, `/sqo frames` | Troubleshooting for clients with custom quest frames (HD clients) |
