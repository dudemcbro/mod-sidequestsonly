#include "SideQuestsOnly.h"

#include "Chat.h"
#include "CommandScript.h"
#include "ObjectMgr.h"
#include "Player.h"
#include "QuestDef.h"

using namespace Acore::ChatCommands;

class mod_sidequestsonly_commandscript : public CommandScript
{
public:
    mod_sidequestsonly_commandscript() : CommandScript("mod_sidequestsonly_commandscript") { }

    ChatCommandTable GetCommands() const override
    {
        static ChatCommandTable sqoTable =
        {
            { "sync",   HandleSync,   SEC_PLAYER,        Console::No  },
            { "get",    HandleGet,    SEC_PLAYER,        Console::Yes },
            { "set",    HandleSet,    SEC_GAMEMASTER,    Console::Yes },
            { "remove", HandleRemove, SEC_GAMEMASTER,    Console::Yes },
            { "reload", HandleReload, SEC_ADMINISTRATOR, Console::Yes }
        };
        static ChatCommandTable rootTable =
        {
            { "sqo", sqoTable }
        };
        return rootTable;
    }

    // The addon sends this over the AzerothCore addon channel once it has loaded.
    static bool HandleSync(ChatHandler* handler)
    {
        if (!SideQuestsOnly::IsEnabled())
        {
            handler->SendErrorMessage("SideQuestsOnly is turned off on this server.");
            return false;
        }
        SideQuestsOnly::Sync(handler->GetSession()->GetPlayer());
        return true;
    }

    static bool HandleGet(ChatHandler* handler, uint32 questId)
    {
        Quest const* quest = sObjectMgr->GetQuestTemplate(questId);
        if (!quest)
        {
            handler->SendErrorMessage("Quest {} does not exist.", questId);
            return false;
        }
        std::optional<std::string> category = SideQuestsOnly::Get(questId);
        handler->PSendSysMessage("Quest {} \"{}\": {}.", questId, quest->GetTitle(), category ? *category : "not classified");
        return true;
    }

    static bool HandleSet(ChatHandler* handler, uint32 questId, std::string category)
    {
        if (!sObjectMgr->GetQuestTemplate(questId))
        {
            handler->SendErrorMessage("Quest {} does not exist.", questId);
            return false;
        }
        if (!SideQuestsOnly::IsCategory(category))
        {
            handler->SendErrorMessage("Unknown category '{}'. Use one of: {}.", category, SideQuestsOnly::CategoryList());
            return false;
        }
        SideQuestsOnly::Set(questId, category);
        handler->PSendSysMessage("Quest {} is now {}.", questId, category);
        return true;
    }

    static bool HandleRemove(ChatHandler* handler, uint32 questId)
    {
        if (!SideQuestsOnly::Remove(questId))
        {
            handler->SendErrorMessage("Quest {} is not classified.", questId);
            return false;
        }
        handler->PSendSysMessage("Quest {} is no longer classified.", questId);
        return true;
    }

    static bool HandleReload(ChatHandler* handler)
    {
        uint32 count = SideQuestsOnly::Load();
        SideQuestsOnly::SyncAll();
        handler->PSendSysMessage("Reloaded {} quest categories and sent them to every player with the addon.", count);
        return true;
    }
};

void AddSC_mod_sidequestsonly_commandscript()
{
    new mod_sidequestsonly_commandscript();
}
