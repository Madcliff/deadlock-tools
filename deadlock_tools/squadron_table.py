"""Read and edit the level-data SquadronDataTable: squadron types and which capital ships may carry them.

The table is in level5, level6, level7, level8, level85 and level9 (identical copies; edit them all).
One entry per squadron type (id = the game's squadron enum, the same number as FighterSquadron.SquadronShipType
on the prefab in sharedassets9.assets).

Allocation rule (the game's JNLHLGGFCGH.NMGCJCCKOIC, which builds the squadron choices for a carrier slot):
a squadron type is offered when its DLC is owned, it is unlocked in the campaign, its Faction equals the fleet's,
the carrier's ship type is NOT in its shipExclusionList, and - for a utility slot only - IsUtility is set.
Fighter slots accept every squadron type that passes the other checks. See docs/game/squadrons.md.
"""
import struct

from deadlock_tools import shipdb as sd

TABLE = "SquadronDataTable"
# field: (kind, label, help)
FIELDS = {
    "IsUtility": ("bool", "Utility squadron", "Can fill a carrier's utility slots (fighter slots accept every type)"),
    "Faction": ("int", "Faction", "Only fleets of this faction are offered the squadron"),
    "DLCRequired": ("int", "DLC required", "-1 = base game; otherwise the DLC that must be owned"),
    "PointValue": ("int", "Point value", "Fleet points (skirmish / multiplayer)"),
    "SupplyCostPerFighter": ("float", "Supply cost per fighter", "Resupply cost of each fighter"),
    "SurvivalCostPerFighter": ("float", "Survival cost per fighter", "Cost of each fighter lost"),
    "XP": ("int", "XP", "Experience awarded"),
    "DefaultGroup": ("int", "Default group", "Squadron group it is assigned to at launch"),
    "MoveOnLaunch": ("bool", "Move on launch", "Moves away from the carrier right after launching"),
    "includedInMultiplayer": ("bool", "In multiplayer", "Available in multiplayer / skirmish lists"),
    "IncludedInCodex": ("bool", "In codex", "Listed in the codex"),
    "MaxShipCount": ("int", "Fighters (level copy)", "Fighter count shown by the UI; keep equal to the prefab's"),
    "MaxHullPoints": ("float", "Hull pool (level copy)", "Hull shown by the UI; keep equal to the prefab's"),
}


def find(S, data):
    """(pid, offset, size, parsed) of the SquadronDataTable object in a level file, or None"""
    ti = S.typedef_index(TABLE)
    try:
        objects = sd.read_objects(data)
    except BaseException:                                      # not a stripped serialized file
        return None
    for pid, off, size, cid, _ in objects:
        if cid != 114 or size < 200:
            continue
        reader = sd.Reader(S, data[off:off + size], 32)
        try:
            res = reader.obj(ti)
        except Exception:
            continue
        if reader.p == size and isinstance(res.get("objects"), list) and res["objects"]:
            return pid, off, size, res
    return None


def entries(res):
    """the table's squadron types as plain dicts (no offsets)"""
    return [{k: v for k, v in e.items() if k != "_off"} for e in res["objects"]]


def entry(res, key):
    """an entry by id (int / digit string) or by (part of) its name"""
    items = res["objects"]
    if isinstance(key, int) or str(key).lstrip("-").isdigit():
        hits = [e for e in items if e["id"] == int(key)]
    else:
        k = str(key).lower().replace(" ", "")
        hits = [e for e in items if e["name"].lower().replace(" ", "") == k] or \
               [e for e in items if k in e["name"].lower().replace(" ", "")]
    if len(hits) != 1:
        raise ValueError("%s squadron type matches %r. Types: %s" % ("no" if not hits else "more than one", key,
                         ", ".join("%s (%d)" % (e["name"], e["id"]) for e in items)))
    return hits[0]


def allowed(res, ship_id, faction, utility_slot=False):
    """squadron-type ids the game offers a carrier (ignoring DLC ownership and campaign unlocks)"""
    return [e["id"] for e in res["objects"] if e["Faction"] == faction and ship_id not in e["shipExclusionList"]
            and (e["IsUtility"] or not utility_slot)]


def apply(S, data, edits):
    """Apply {type id or name: {field: value, 'shipExclusionList': [ship ids]}} to the table in one level file.
    Returns (new bytes, log). Raises ValueError if the file has no table or a check fails."""
    found = find(S, data)
    if not found:
        raise ValueError("no %s in this file" % TABLE)
    pid, off, size, res = found
    mono = bytearray(data[off:off + size])
    log, expected, splices = [], {}, []
    for key, changes in edits.items():
        e = entry(res, key)
        want = expected.setdefault(e["id"], {})
        for field, value in changes.items():
            if field == "shipExclusionList":
                ids = sorted({int(x) for x in value}) if value is not None else []
                pos = e["_off"]["shipExclusionList"][0]
                old = e["shipExclusionList"]
                splices.append((pos, 4 + 4 * len(old), struct.pack("<i", len(ids)) + b"".join(struct.pack("<i", i) for i in ids)))
                want[field] = ids
                if sorted(old) != ids:
                    log.append("%s: excluded carriers %s -> %s" % (e["name"], sorted(old), ids))
                continue
            if field not in FIELDS:
                raise ValueError("cannot edit %r. Editable: %s, shipExclusionList" % (field, ", ".join(FIELDS)))
            kind = FIELDS[field][0]
            pos = e["_off"][field][0]
            if kind == "bool":
                value = value in (True, 1, "1", "true", "True", "yes")
                struct.pack_into("<B", mono, pos, 1 if value else 0)
            elif kind == "int":
                number = float(value)
                if not number.is_integer():
                    raise ValueError("%s needs a whole number" % field)
                value = int(number); struct.pack_into("<i", mono, pos, value)
            else:
                value = float(value); struct.pack_into("<f", mono, pos, value)
            want[field] = value
            if str(e[field]) != str(value):
                log.append("%s: %s %s -> %s" % (e["name"], field, e[field], value))
    for pos, length, new in sorted(splices, reverse=True):       # back to front keeps earlier offsets valid
        mono[pos:pos + length] = new
    result = bytes(data[:off]) + bytes(mono) + bytes(data[off + size:]) if len(mono) == size else sd.resize_object(bytes(data), pid, bytes(mono))
    # verify
    after = find(S, result)
    if not after or len(after[3]["objects"]) != len(res["objects"]):
        raise ValueError("verification failed: the squadron table did not read back")
    by_id = {e["id"]: e for e in after[3]["objects"]}
    for type_id, fields in expected.items():
        for field, value in fields.items():
            got = by_id[type_id][field]
            ok = sorted(got) == value if field == "shipExclusionList" else \
                (abs(got - value) <= 1e-5 * max(1.0, abs(value)) if isinstance(value, float) else got == value)
            if not ok:
                raise ValueError("verification failed for %s.%s: wrote %s, read back %s" % (by_id[type_id]["name"], field, value, got))
    untouched = [e for e in res["objects"] if e["id"] not in expected]
    for e in untouched:
        a = {k: v for k, v in e.items() if k != "_off"}; b = {k: v for k, v in by_id[e["id"]].items() if k != "_off"}
        if a != b:
            raise ValueError("verification failed: %s changed although it was not edited" % e["name"])
    return result, log
