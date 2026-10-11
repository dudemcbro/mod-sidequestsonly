#include "SideQuestsOnly.h"

#include "Chat.h"
#include "Config.h"
#include "DatabaseEnv.h"
#include "Log.h"
#include "ObjectAccessor.h"
#include "ObjectMgr.h"
#include "Opcodes.h"
#include "Player.h"
#include "QuestDef.h"
#include "ScriptMgr.h"
#include "WorldPacket.h"
#include "WorldSession.h"

#include <algorithm>
#include <array>
#include <map>
#include <mutex>
#include <shared_mutex>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace
{
    constexpr char const* ADDON_PREFIX = "SQO\t";
    // The client takes no more than 255 bytes of prefix, tab and text.
    constexpr size_t ADDON_MESSAGE_LIMIT = 255;
    // Characters of ids in one c row, so the row stays under the limit.
    constexpr size_t ROW_LIMIT = 220;

    // "intro" is retired; it stays valid until no shipped data uses it.
    constexpr std::array<std::string_view, 17> CATEGORIES = {
        "intro", "story", "lore", "attune", "class", "profession", "daily",
        "pvp", "holiday", "event", "dungeon_story", "dungeon_lore",
        "dungeon_side", "raid_story", "raid_lore", "raid_side", "side"
    };

    bool _enabled = true;

    std::shared_mutex _lock;
    std::unordered_map<uint32, std::string> _categories;
    std::unordered_set<ObjectGuid> _synced;

    std::string Base36(uint32 value)
    {
        static constexpr char digits[] = "0123456789abcdefghijklmnopqrstuvwxyz";
        std::string out;
        do
        {
            out.insert(out.begin(), digits[value % 36]);
            value /= 36;
        } while (value);
        return out;
    }

    // Cuts a message to the client's limit without splitting a UTF-8 character.
    void Truncate(std::string& text, size_t limit)
    {
        if (text.size() <= limit)
            return;
        size_t end = limit;
        while (end > 0 && (static_cast<unsigned char>(text[end]) & 0xC0) == 0x80)
            --end;
        text.resize(end);
    }

    void SendAddon(Player* player, std::string const& text)
    {
        if (!player || !player->GetSession() || player->GetSession()->IsSocketClosed())
            return;

        std::string message = ADDON_PREFIX + text;
        Truncate(message, ADDON_MESSAGE_LIMIT);
        WorldPacket data;
        ChatHandler::BuildChatPacket(data, CHAT_MSG_WHISPER, LANG_ADDON, player->GetGUID(), player->GetGUID(), message, 0);
        player->SendDirectMessage(&data);
    }

    // Every category as c rows, ids in gaps and runs (see SideQuestsOnly.h).
    std::vector<std::string> BuildRows()
    {
        std::map<std::string, std::vector<uint32>> byCategory;
        {
            std::shared_lock lock(_lock);
            for (auto const& [questId, category] : _categories)
                byCategory[category].push_back(questId);
        }

        std::vector<std::string> rows;
        for (auto& [category, ids] : byCategory)
        {
            std::sort(ids.begin(), ids.end());
            std::string row;
            uint32 previous = 0;
            for (size_t i = 0; i < ids.size();)
            {
                size_t j = i;
                while (j + 1 < ids.size() && ids[j + 1] == ids[j] + 1)
                    ++j;

                if (row.size() >= ROW_LIMIT)
                {
                    rows.push_back("c\t" + category + "\t" + row);
                    row.clear();
                    previous = 0;
                }

                std::string token = Base36(ids[i] - previous);
                if (j > i)
                    token += "+" + Base36(static_cast<uint32>(j - i));
                row += (row.empty() ? "" : ",") + token;
                previous = ids[j];
                i = j + 1;
            }
            if (!row.empty())
                rows.push_back("c\t" + category + "\t" + row);
        }
        return rows;
    }

    void SendAll(Player* player, std::vector<std::string> const& rows, size_t count)
    {
        SendAddon(player, "begin\t" + std::to_string(count));
        for (std::string const& row : rows)
            SendAddon(player, row);
        SendAddon(player, "end");
    }

    std::vector<Player*> SyncedPlayers()
    {
        std::vector<ObjectGuid> guids;
        {
            std::shared_lock lock(_lock);
            guids.assign(_synced.begin(), _synced.end());
        }
        std::vector<Player*> players;
        for (ObjectGuid const& guid : guids)
            if (Player* player = ObjectAccessor::FindConnectedPlayer(guid))
                players.push_back(player);
        return players;
    }

    std::string QuestTitle(Player* player, Quest const* quest)
    {
        std::string title = quest->GetTitle();
        int32 locale = player->GetSession()->GetSessionDbLocaleIndex();
        if (QuestLocale const* localeData = sObjectMgr->GetQuestLocale(quest->GetQuestId()))
            ObjectMgr::GetLocaleString(localeData->Title, locale, title);
        return title;
    }
}

namespace SideQuestsOnly
{
    bool IsEnabled()
    {
        return _enabled;
    }

    bool IsCategory(std::string_view category)
    {
        return std::find(CATEGORIES.begin(), CATEGORIES.end(), category) != CATEGORIES.end();
    }

    std::string const& CategoryList()
    {
        static std::string const list = []
        {
            std::string out;
            for (std::string_view category : CATEGORIES)
                out += (out.empty() ? "" : ", ") + std::string(category);
            return out;
        }();
        return list;
    }

    uint32 Load()
    {
        std::unordered_map<uint32, std::string> loaded;
        if (QueryResult result = WorldDatabase.Query("SELECT `QuestId`, `Category` FROM `mod_sidequestsonly_quest`"))
        {
            do
            {
                Field* fields = result->Fetch();
                uint32 questId = fields[0].Get<uint32>();
                std::string category = fields[1].Get<std::string>();
                if (!IsCategory(category))
                {
                    LOG_ERROR("module", "mod-sidequestsonly: quest {} has unknown category '{}', skipped.", questId, category);
                    continue;
                }
                loaded.emplace(questId, std::move(category));
            } while (result->NextRow());
        }

        uint32 count = static_cast<uint32>(loaded.size());
        {
            std::unique_lock lock(_lock);
            _categories = std::move(loaded);
        }
        LOG_INFO("server.loading", ">> mod-sidequestsonly: loaded {} quest categories.", count);
        return count;
    }

    std::optional<std::string> Get(uint32 questId)
    {
        std::shared_lock lock(_lock);
        auto itr = _categories.find(questId);
        if (itr == _categories.end())
            return std::nullopt;
        return itr->second;
    }

    void Set(uint32 questId, std::string const& category)
    {
        WorldDatabase.Execute("REPLACE INTO `mod_sidequestsonly_quest` (`QuestId`, `Category`) VALUES ({}, '{}')", questId, category);
        {
            std::unique_lock lock(_lock);
            _categories[questId] = category;
        }
        SendToSynced("set\t" + std::to_string(questId) + "\t" + category);
    }

    bool Remove(uint32 questId)
    {
        {
            std::unique_lock lock(_lock);
            if (!_categories.erase(questId))
                return false;
        }
        WorldDatabase.Execute("DELETE FROM `mod_sidequestsonly_quest` WHERE `QuestId` = {}", questId);
        SendToSynced("del\t" + std::to_string(questId));
        return true;
    }

    void Sync(Player* player)
    {
        if (!player)
            return;
        size_t count;
        {
            std::unique_lock lock(_lock);
            _synced.insert(player->GetGUID());
            count = _categories.size();
        }
        SendAll(player, BuildRows(), count);
    }

    void Forget(ObjectGuid guid)
    {
        std::unique_lock lock(_lock);
        _synced.erase(guid);
    }

    bool IsSynced(ObjectGuid guid)
    {
        std::shared_lock lock(_lock);
        return _synced.count(guid) > 0;
    }

    void SyncAll()
    {
        std::vector<std::string> rows = BuildRows();
        size_t count;
        {
            std::shared_lock lock(_lock);
            count = _categories.size();
        }
        for (Player* player : SyncedPlayers())
            SendAll(player, rows, count);
    }

    void SendToSynced(std::string const& text)
    {
        for (Player* player : SyncedPlayers())
            SendAddon(player, text);
    }

    void SendQuest(Player* player, Quest const* quest)
    {
        if (!player || !quest)
            return;
        std::string category = Get(quest->GetQuestId()).value_or("?");
        std::string title = QuestTitle(player, quest);
        std::replace(title.begin(), title.end(), '\t', ' ');
        SendAddon(player, "q\t" + std::to_string(quest->GetQuestId()) + "\t" + category + "\t" + title);
    }
}

class SideQuestsOnly_WorldScript : public WorldScript
{
public:
    SideQuestsOnly_WorldScript() : WorldScript("SideQuestsOnly_WorldScript", {
        WORLDHOOK_ON_AFTER_CONFIG_LOAD,
        WORLDHOOK_ON_STARTUP
    }) { }

    void OnAfterConfigLoad(bool /*reload*/) override
    {
        _enabled = sConfigMgr->GetOption<bool>("SideQuestsOnly.Enable", true);
    }

    void OnStartup() override
    {
        SideQuestsOnly::Load();
    }
};

class SideQuestsOnly_PlayerScript : public PlayerScript
{
public:
    SideQuestsOnly_PlayerScript() : PlayerScript("SideQuestsOnly_PlayerScript", {
        PLAYERHOOK_ON_LOGOUT
    }) { }

    void OnPlayerLogout(Player* player) override
    {
        SideQuestsOnly::Forget(player->GetGUID());
    }
};

// Tells the addon which quest a quest dialog is about just before the client
// opens it, so the addon need not guess from the title.
class SideQuestsOnly_ServerScript : public ServerScript
{
public:
    SideQuestsOnly_ServerScript() : ServerScript("SideQuestsOnly_ServerScript", {
        SERVERHOOK_CAN_PACKET_SEND
    }) { }

    // Cores before mid-2026 pass the packet as WorldPacket&, newer ones as
    // WorldPacket const&; each core overrides the one it has (no override
    // keyword, so the other compiles too).
#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Winconsistent-missing-override"
#endif
    bool CanPacketSend(WorldSession* session, WorldPacket& packet)
    {
        return OnPacketSend(session, packet);
    }

    bool CanPacketSend(WorldSession* session, WorldPacket const& packet)
    {
        return OnPacketSend(session, packet);
    }
#if defined(__clang__)
#pragma clang diagnostic pop
#endif

private:
    bool OnPacketSend(WorldSession* session, WorldPacket const& packet)
    {
        if (!_enabled)
            return true;

        // Where the quest id sits: after the giver's guid, and for details
        // after the guid of the player who shared the quest too.
        size_t questIdPos;
        switch (packet.GetOpcode())
        {
            case SMSG_QUESTGIVER_QUEST_DETAILS:
                questIdPos = 16;
                break;
            case SMSG_QUESTGIVER_REQUEST_ITEMS:
            case SMSG_QUESTGIVER_OFFER_REWARD:
                questIdPos = 8;
                break;
            default:
                return true;
        }

        Player* player = session ? session->GetPlayer() : nullptr;
        if (!player || packet.size() < questIdPos + sizeof(uint32) || !SideQuestsOnly::IsSynced(player->GetGUID()))
            return true;

        SideQuestsOnly::SendQuest(player, sObjectMgr->GetQuestTemplate(packet.read<uint32>(questIdPos)));
        return true;
    }
};

void AddSC_mod_sidequestsonly_commandscript();

void Addmod_sidequestsonlyScripts()
{
    new SideQuestsOnly_WorldScript();
    new SideQuestsOnly_PlayerScript();
    new SideQuestsOnly_ServerScript();
    AddSC_mod_sidequestsonly_commandscript();
}
