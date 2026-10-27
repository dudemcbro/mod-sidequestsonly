#!/usr/bin/env python3
"""
export_quests.py - Export quest data from acore_world for classify_quests.py.

Writes wow_quests.json: one object per quest_template row with its text, sort,
type, chain links, class and skill limits, the first quest-giver NPC and the
zone name. Zone names come from the server's DBC files: a QuestSortID above 0
is an AreaTable.dbc ID, below 0 a QuestSort.dbc ID, negated.

Run it where it can reach the database and the DBC files, usually on the
server itself. --conf reads the connection (WorldDatabaseInfo) and the DBC
folder (DataDir) from worldserver.conf:

    python3 export_quests.py --conf /path/to/worldserver.conf

or give them separately (the password comes from MYSQL_PWD):

    MYSQL_PWD=... python3 export_quests.py --host 127.0.0.1 --user acore \\
        --database acore_world --dbc-dir /path/to/data/dbc

It uses PyMySQL when installed, otherwise the mysql command-line client.
"""

import argparse
import json
import os
import re
import struct
import subprocess
import sys

try:
    import pymysql
except ImportError:
    pymysql = None

# DBC layouts, from AzerothCore's DBCfmt.h and DBCStructure.h. Names are 16
# locale strings (field 11 onward in AreaTable, field 1 onward in QuestSort).
AREA_TABLE_FIELDS = 36
AREA_TABLE_NAME = 11
QUEST_SORT_FIELDS = 18
QUEST_SORT_NAME = 1
LOCALES = 16

# One JSON object per quest, built by the server so text needs no escaping
# on the mysql client path. Keys are what classify_quests.load_db_export reads.
QUERY = """
SELECT JSON_OBJECT(
    'id', qt.ID,
    'title', qt.LogTitle,
    'description', qt.LogDescription,
    'details', qt.QuestDescription,
    'completion', qt.QuestCompletionLog,
    'area_description', qt.AreaDescription,
    'objectives', qt.ObjectiveText1,
    'objectives2', qt.ObjectiveText2,
    'objectives3', qt.ObjectiveText3,
    'objectives4', qt.ObjectiveText4,
    'flags', qt.Flags,
    'sort_id', qt.QuestSortID,
    'quest_info_id', qt.QuestInfoID,
    'prev_quest', IFNULL(qa.PrevQuestID, 0),
    'next_quest', IFNULL(qa.NextQuestID, 0),
    'allowable_classes', IFNULL(qa.AllowableClasses, 0),
    'required_skill_id', IFNULL(qa.RequiredSkillID, 0),
    'starter_npc', (SELECT ct.name
                    FROM creature_queststarter cq
                    JOIN creature_template ct ON ct.entry = cq.id
                    WHERE cq.quest = qt.ID
                    ORDER BY cq.id LIMIT 1)
)
FROM quest_template qt
LEFT JOIN quest_template_addon qa ON qa.ID = qt.ID
ORDER BY qt.ID
"""


def read_dbc_names(path, field_count, name_field):
    """Return {id: name} from a WDBC file, taking the first non-empty locale."""
    with open(path, "rb") as f:
        data = f.read()
    magic, records, fields, record_size, string_size = struct.unpack_from("<4s4I", data)
    if magic != b"WDBC":
        sys.exit(f"{path}: not a WDBC file")
    if fields != field_count or record_size != fields * 4:
        sys.exit(f"{path}: {fields} fields of {record_size // max(fields, 1)} bytes, "
                 f"expected {field_count} fields of 4 bytes")
    strings_at = 20 + records * record_size
    strings = data[strings_at:strings_at + string_size]

    def string(offset):
        return strings[offset:strings.index(b"\0", offset)].decode("utf-8", "replace")

    names = {}
    for i in range(records):
        row = struct.unpack_from(f"<{fields}I", data, 20 + i * record_size)
        offsets = row[name_field:name_field + LOCALES]
        names[row[0]] = next((string(o) for o in offsets if o and string(o)), "")
    return names


def parse_conf(path):
    """Return (connection, DataDir) from a worldserver.conf."""
    values = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'\s*(WorldDatabaseInfo|DataDir)\s*=\s*"?([^"\n]*)"?', line)
            if m:
                values[m.group(1)] = m.group(2).strip()
    if "WorldDatabaseInfo" not in values:
        sys.exit(f"{path}: no WorldDatabaseInfo")
    # host;port;user;password;database (port may be "." for a socket path in host)
    host, port, user, password, database = values["WorldDatabaseInfo"].split(";", 4)
    conn = {"host": host, "port": int(port) if port.isdigit() else 3306,
            "user": user, "password": password, "database": database}
    return conn, values.get("DataDir")


def fetch_rows(conn):
    """Run QUERY and return the JSON strings, one per quest."""
    if pymysql is not None:
        db = pymysql.connect(host=conn["host"], port=conn["port"], user=conn["user"],
                             password=conn["password"], database=conn["database"],
                             charset="utf8mb4")
        try:
            with db.cursor() as cur:
                cur.execute(QUERY)
                return [row[0] for row in cur.fetchall()]
        finally:
            db.close()
    env = dict(os.environ, MYSQL_PWD=conn["password"])
    cmd = ["mysql", "--batch", "--raw", "--skip-column-names",
           "--default-character-set=utf8mb4",
           "-h", conn["host"], "-P", str(conn["port"]), "-u", conn["user"],
           conn["database"], "-e", QUERY]
    try:
        out = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("Neither PyMySQL nor the mysql client is installed.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"mysql failed: {e.stderr.strip()}")
    return [line for line in out.stdout.splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--conf", help="worldserver.conf to read the database and DataDir from")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="acore")
    parser.add_argument("--database", default="acore_world")
    parser.add_argument("--dbc-dir", help="folder with AreaTable.dbc and QuestSort.dbc "
                                          "(default: <DataDir>/dbc from --conf)")
    parser.add_argument("-o", "--output", default="wow_quests.json")
    args = parser.parse_args()

    if args.conf:
        conn, data_dir = parse_conf(args.conf)
        dbc_dir = args.dbc_dir or (os.path.join(data_dir, "dbc") if data_dir else None)
    else:
        conn = {"host": args.host, "port": args.port, "user": args.user,
                "password": os.environ.get("MYSQL_PWD", ""), "database": args.database}
        dbc_dir = args.dbc_dir
    if not dbc_dir:
        sys.exit("Give --dbc-dir, or --conf with a DataDir.")

    areas = read_dbc_names(os.path.join(dbc_dir, "AreaTable.dbc"),
                           AREA_TABLE_FIELDS, AREA_TABLE_NAME)
    sorts = read_dbc_names(os.path.join(dbc_dir, "QuestSort.dbc"),
                           QUEST_SORT_FIELDS, QUEST_SORT_NAME)

    quests = [json.loads(row) for row in fetch_rows(conn)]
    no_zone = 0
    for q in quests:
        sort_id = q.get("sort_id") or 0
        q["zone"] = areas.get(sort_id) if sort_id > 0 else sorts.get(-sort_id) if sort_id < 0 else None
        if sort_id and not q["zone"]:
            no_zone += 1

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(quests, f, ensure_ascii=False, indent=1)
    print(f"Wrote {len(quests)} quests to {args.output} "
          f"({len(areas)} areas, {len(sorts)} quest sorts; "
          f"{no_zone} quests with a sort ID not in the DBCs)")


if __name__ == "__main__":
    main()
