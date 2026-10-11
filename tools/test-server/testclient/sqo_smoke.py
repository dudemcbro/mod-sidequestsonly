#!/usr/bin/env python3
"""End-to-end test for mod-sidequestsonly with the headless client (wowclient.py).

Expects a server with the module and two accounts: admin (GM level 3) and player
(level 0). Run by tools/test-server/podman-test.sh inside the test server image.

Checks: the sync over the AzerothCore addon channel brings exactly the world table;
an NPC quest dialog is preceded by the quest's id, category and title (and only for
players who synced); .sqo set / remove / reload reach synced players and the database;
players cannot use the GM commands.
"""

import argparse
import struct
import subprocess
import sys
import time

from wowclient import TYPEID_UNIT, WorldClient, auth_login

CMSG_QUESTGIVER_QUERY_QUEST = 0x186
CMSG_QUESTLOG_REMOVE_QUEST = 0x194
SMSG_MESSAGECHAT = 0x096
SMSG_QUESTGIVER_QUEST_DETAILS = 0x188
ADDON_LIMIT = 255

DEPUTY_WILLEM = 823          # Northshire, next to where humans start
A_THREAT_WITHIN = 783        # his first quest, no prerequisites

results = []


def log(msg):
    print(msg, flush=True)


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    log("%s %s%s" % ("PASS" if ok else "FAIL", name, (" :: " + str(detail)) if detail and not ok else ""))
    return ok


def db(sql, database="acore_world"):
    out = subprocess.run(["mysql", "-uacore", "-pacore", "-N", "-B", database, "-e", sql],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr)
    return [line.split("\t") for line in out.stdout.splitlines() if line]


def wait_for(predicate, wc, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        wc.pump(0.25)
    return predicate()


def connect(args, spec, char_name, verbose):
    account, password = spec.split(":")
    key, realms = auth_login(args.host, args.auth_port, account, password)
    realm = realms[0]
    host, port = realm["address"].split(":")
    wc = WorldClient(args.host, int(port), account, key, realm_id=realm["id"],
                     log=(lambda m: log("[%s]%s" % (account, m))) if verbose else None)

    # Keep the order packets arrive in: the quest's q whisper must come before its dialog.
    wc.order = []
    dispatch = wc._dispatch

    def recording(opcode, data):
        wc.order.append((opcode, len(wc.addon_messages)))
        dispatch(opcode, data)

    wc._dispatch = recording
    wc.connect()

    chars = {c["name"]: c for c in wc.enum_chars()}
    if char_name not in chars:
        code = wc.create_char(char_name, race=1, cls=1)  # Human Warrior: starts by Deputy Willem
        if code != 0x2F:
            raise RuntimeError("character create for %s failed: 0x%x" % (char_name, code))
        chars = {c["name"]: c for c in wc.enum_chars()}
    wc.login(chars[char_name]["guid"])
    wc.char_name = char_name
    return wc


def sqo_since(wc, mark):
    return [m[len("SQO\t"):] for m in wc.addon_messages[mark:] if m.startswith("SQO\t")]


def decode(messages):
    """The begin count and the categories of the last complete list in messages."""
    count, current, done = None, None, None
    for m in messages:
        f = m.split("\t")
        if f[0] == "begin":
            count, current = int(f[1]), {}
        elif f[0] == "c" and current is not None:
            previous = 0
            for token in f[2].split(","):
                gap, _, run = token.partition("+")
                first = previous + int(gap, 36)
                last = first + (int(run, 36) if run else 0)
                for quest in range(first, last + 1):
                    current[quest] = f[1]
                previous = last
        elif f[0] == "end" and current is not None:
            done = (count, current)
    return done


def table():
    return {int(q): c for q, c in db("SELECT QuestId, Category FROM mod_sidequestsonly_quest")}


def abandon_first_quest(wc):
    """Abandons the quest in log slot 0. A Threat Within accepts itself when its dialog opens,
    and a quest in the log gets no new dialog, so each dialog below starts from an empty log."""
    wc.send(CMSG_QUESTLOG_REMOVE_QUEST, struct.pack("<B", 0))
    wc.pump(0.5)


def query_quest(wc, giver, quest_id):
    """What the client sends on clicking a quest in an NPC's list: the server answers with
    the quest's details dialog."""
    abandon_first_quest(wc)
    wc.quest_offered = None
    wc.send(CMSG_QUESTGIVER_QUERY_QUEST, struct.pack("<QIB", giver.guid, quest_id, 1))
    wait_for(lambda: wc.quest_offered == quest_id, wc)
    wc.pump(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--auth-port", type=int, default=3724)
    ap.add_argument("--admin", default="admin:admin")
    ap.add_argument("--player", default="player:player")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    expected = table()
    check("world table seeded", len(expected) > 9000, len(expected))
    original = expected.get(A_THREAT_WITHIN)

    gm = connect(args, args.admin, "Sqoadmin", args.verbose)
    player = connect(args, args.player, "Sqoplayer", args.verbose)

    # --- quest dialog before syncing: nothing for this player
    willem = player.nearest(DEPUTY_WILLEM, TYPEID_UNIT)
    check("Deputy Willem in sight", willem is not None)
    mark = len(player.addon_messages)
    query_quest(player, willem, A_THREAT_WITHIN)
    check("dialog opens", player.quest_offered == A_THREAT_WITHIN)
    check("no q whisper without sync", not sqo_since(player, mark), sqo_since(player, mark))

    # --- sync over the AzerothCore addon channel
    mark = len(player.addon_messages)
    player.addon_command("sqo sync", to=player.char_name)
    wait_for(lambda: "end" in sqo_since(player, mark), player, timeout=15)
    messages = sqo_since(player, mark)
    got = decode(messages)
    check("sync answers begin .. end", got is not None, messages[:3])
    if got:
        count, categories = got
        check("begin count matches the table", count == len(expected), (count, len(expected)))
        check("decoded list equals the table", categories == expected,
              "%d decoded, %d differ" % (len(categories), sum(1 for q in expected if categories.get(q) != expected[q])))
    longest = max(len(("SQO\t" + m).encode()) for m in messages) if messages else 0
    check("every message within the client's 255 bytes",
          all(len(("SQO\t" + m).encode()) <= ADDON_LIMIT for m in messages), longest)
    log("     sync: %d messages, %d bytes" % (len(messages), sum(len(m) for m in messages)))

    # --- quest dialog after syncing: q whisper first, then the dialog
    mark = len(player.addon_messages)
    order_mark = len(player.order)
    query_quest(player, willem, A_THREAT_WITHIN)
    q = [m for m in sqo_since(player, mark) if m.startswith("q\t")]
    want = "q\t%d\t%s\tA Threat Within" % (A_THREAT_WITHIN, original)
    check("q whisper names the dialog's quest", q == [want], q)
    seq = [op for op, _ in player.order[order_mark:] if op in (SMSG_MESSAGECHAT, SMSG_QUESTGIVER_QUEST_DETAILS)]
    check("q whisper arrives before the dialog",
          SMSG_QUESTGIVER_QUEST_DETAILS in seq and seq.index(SMSG_MESSAGECHAT) < seq.index(SMSG_QUESTGIVER_QUEST_DETAILS), seq)

    # --- GM changes reach synced players and the database
    mark = len(player.addon_messages)
    replies = gm.command(".sqo set %d holiday" % A_THREAT_WITHIN)
    wait_for(lambda: sqo_since(player, mark), player)
    check(".sqo set answers", any("is now holiday" in r for r in replies), replies)
    check(".sqo set reaches synced players", sqo_since(player, mark) == ["set\t%d\tholiday" % A_THREAT_WITHIN],
          sqo_since(player, mark))
    check(".sqo set saved", table().get(A_THREAT_WITHIN) == "holiday")
    replies = gm.command(".sqo get %d" % A_THREAT_WITHIN)
    check(".sqo get", any("A Threat Within" in r and "holiday" in r for r in replies), replies)
    replies = gm.command(".sqo set %d nonsense" % A_THREAT_WITHIN)
    check(".sqo set refuses unknown categories", any("Unknown category" in r for r in replies), replies)

    mark = len(player.addon_messages)
    replies = gm.command(".sqo remove %d" % A_THREAT_WITHIN)
    wait_for(lambda: sqo_since(player, mark), player)
    check(".sqo remove reaches synced players", sqo_since(player, mark) == ["del\t%d" % A_THREAT_WITHIN],
          sqo_since(player, mark))
    check(".sqo remove deleted the row", A_THREAT_WITHIN not in table())

    mark = len(player.addon_messages)
    query_quest(player, willem, A_THREAT_WITHIN)
    q = [m for m in sqo_since(player, mark) if m.startswith("q\t")]
    check("unclassified quest shows ?", q == ["q\t%d\t?\tA Threat Within" % A_THREAT_WITHIN], q)

    # put it back by hand, then .sqo reload picks it up and resends everything
    db("INSERT INTO mod_sidequestsonly_quest (QuestId, Category) VALUES (%d, '%s')" % (A_THREAT_WITHIN, original))
    mark = len(player.addon_messages)
    replies = gm.command(".sqo reload")
    wait_for(lambda: "end" in sqo_since(player, mark), player, timeout=15)
    got = decode(sqo_since(player, mark))
    check(".sqo reload resends the list", got is not None and got[1] == table(), replies)

    # --- players can't change categories
    replies = player.command(".sqo set %d side" % A_THREAT_WITHIN)
    check("players cannot .sqo set", table().get(A_THREAT_WITHIN) == original, replies)

    # --- a GM change after the player left (the server forgets players on logout)
    player.logout()
    gm.command(".sqo set %d %s" % (A_THREAT_WITHIN, original))
    check("set after the player left", table().get(A_THREAT_WITHIN) == original)
    gm.logout()

    failed = [name for name, ok in results if not ok]
    log("\n%d passed, %d failed" % (len(results) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
