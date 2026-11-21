# Installation

Requirements, installing the server module and the client addon, the database setup and rollback.

## Requirements

- AzerothCore WotLK (3.3.5a). Built on the
  [mod-playerbots AzerothCore fork](https://github.com/mod-playerbots/azerothcore-wotlk)
  and uses only hooks that stock
  [azerothcore-wotlk](https://github.com/azerothcore/azerothcore-wotlk) has too. No core
  patches.
- Players need the SideQuestsOnly addon from `client-addon/`.

## Server

1. Clone the module into the core's `modules` directory:

   ```sh
   cd <azerothcore>/modules
   git clone https://github.com/dudemcbro/mod-sidequestsonly.git
   ```

2. Re-run CMake and rebuild the core.
3. Copy `conf/mod_sidequestsonly.conf.dist` to your config directory's `modules/` folder
   as `mod_sidequestsonly.conf` (optional: the default is enabled). See
   [Configuration](configuration.md).
4. Restart `worldserver`. It installs the module's SQL on start (see
   [Database setup](#database-setup)).

## Client

Copy `client-addon/SideQuestsOnly/` into the client's `Interface/AddOns/` folder so the
`.toc` sits at `Interface/AddOns/SideQuestsOnly/SideQuestsOnly.toc`, then restart the
client. Remove any older SideQuestsOnly first: version 2 has no `QuestData.lua`, it gets
everything from the server. Raid categories need addon 2.1.0 or later.

On login the addon prints `SideQuestsOnly v2.1.0  9536 quests classified by the server.`
If the server does not answer within 15 seconds it says so; `/sqo sync` asks again.

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
