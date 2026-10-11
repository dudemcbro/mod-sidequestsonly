#ifndef MOD_SIDEQUESTSONLY_H
#define MOD_SIDEQUESTSONLY_H

#include "ObjectGuid.h"

#include <optional>
#include <string>
#include <string_view>

class Player;
class Quest;

// Quest categories for the SideQuestsOnly client addon. The world table
// mod_sidequestsonly_quest holds them; the addon asks for them with
// .sqo sync over the core's AzerothCore addon channel and gets them back as
// addon whispers with the prefix SQO:
//   begin <count>
//   c <category> <ids>       one row per message, ids as below
//   end
//   set <questId> <category> (after .sqo set)
//   del <questId>            (after .sqo remove)
//   q <questId> <category> <title>  before every quest dialog an NPC opens
// <ids> is a comma list of base 36 numbers: each is the gap from the
// previous quest in the row (from 0 for the first), and "+n" after one
// means the next n quests follow it one by one.
namespace SideQuestsOnly
{
    bool IsEnabled();
    bool IsCategory(std::string_view category);
    std::string const& CategoryList();

    uint32 Load();
    std::optional<std::string> Get(uint32 questId);
    void Set(uint32 questId, std::string const& category);
    bool Remove(uint32 questId);

    // Players whose addon asked for the data: they get the updates.
    void Sync(Player* player);
    void Forget(ObjectGuid guid);
    bool IsSynced(ObjectGuid guid);
    void SyncAll();
    void SendToSynced(std::string const& text);

    void SendQuest(Player* player, Quest const* quest);
}

#endif
