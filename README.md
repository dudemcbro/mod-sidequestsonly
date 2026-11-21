# mod-sidequestsonly

<img src="icon.png" alt="" width="64" align="right">

An [AzerothCore](https://www.azerothcore.org/) (WotLK 3.3.5a) module and client addon
that badge every quest with what kind of quest it is, so you can tell the story apart
from the filler: `[Story]`, `[Lore]`, `[Side]`, `[Class]`, `[Dungeon][Story]`, `[Raid][Lore]`
and so on. The server keeps the categories in the world database and sends them to the
SideQuestsOnly addon. GMs can change a quest's category in game and every player with
the addon sees it at once.

- **Author:** [dudemcbro](https://github.com/dudemcbro)
- **License:** [GNU GPL v3.0](LICENSE)
- **Works with:** AzerothCore WotLK (3.3.5a). Built on the
  [mod-playerbots AzerothCore fork](https://github.com/mod-playerbots/azerothcore-wotlk)
  and uses only hooks that stock
  [azerothcore-wotlk](https://github.com/azerothcore/azerothcore-wotlk) has too. No core
  patches. Players need the SideQuestsOnly addon from `client-addon/`.

## Features

- **Badges everywhere quests show up.** In the quest log list, the quest log detail
  panel, the objective tracker, the world map quest list and NPC quest dialogs, plus a
  chat line when an NPC offers you a quest and when you accept one. The badges are
  listed in [Categories](docs/categories.md).
- **Classified out of the box.** 9,536 quests classified out of the box (see
  [Classification](docs/classification.md)).
- **Right badge in every NPC dialog.** The server tells the addon which quest each NPC
  dialog is about, so the dialog badge is right even for a quest you have never seen.
- **Live GM edits.** `.sqo set` / `.sqo remove` change a category for the whole realm
  without a restart.

## Quick start

1. Clone into your AzerothCore `modules/` folder (keep the name `mod-sidequestsonly`):
   ```sh
   cd <azerothcore>/modules
   git clone https://github.com/dudemcbro/mod-sidequestsonly.git
   ```
2. Re-run CMake, build and install the core.
3. Optional: copy `conf/mod_sidequestsonly.conf.dist` to your config directory's
   `modules/` folder as `mod_sidequestsonly.conf` (the default is enabled).
4. Restart `worldserver`. It installs the module's SQL on start.
5. Give your players the addon: copy `client-addon/SideQuestsOnly/` into the client's
   `Interface/AddOns/` folder and restart the client.

The full steps, the database setup and rollback are in
[docs/installation.md](docs/installation.md).

## Documentation

| Page | What's in it |
| --- | --- |
| [Installation](docs/installation.md) | Requirements, server and client install, database setup, rollback |
| [Categories](docs/categories.md) | Every badge and what it means |
| [Configuration](docs/configuration.md) | The option in `mod_sidequestsonly.conf` |
| [Commands](docs/commands.md) | `.sqo` server commands and `/sqo` addon commands |
| [How it works](docs/how-it-works.md) | How the server and addon talk to each other |
| [Classification](docs/classification.md) | How quests are classified, and running the classifier |
| [Contributing](docs/contributing.md) | Testing the module in a throwaway container |

## Contributing

How to test changes is in [docs/contributing.md](docs/contributing.md).

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
