# How it works

How the server and the addon exchange quest categories.

The addon runs `.sqo sync` once it has loaded, over AzerothCore's addon command channel
(an addon whisper to yourself with the prefix `AzerothCore`), so nothing shows in chat.
The server answers with addon whispers, prefix `SQO`: the categories in about 55
messages of compact id lists (see [`src/SideQuestsOnly.h`](../src/SideQuestsOnly.h) for
the format), then later only changes. Just before it sends a quest dialog (quest details,
items needed, reward), the server sends that quest's id, category and title, which is how
the addon knows which quest an NPC is offering.
