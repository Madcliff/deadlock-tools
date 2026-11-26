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

# list fields: label in the log.  AbilityIDs are text IDs of the ability names listed in the squadron's info panel
# (the abilities themselves are components on the prefab, see squadron_types.py)
LIST_FIELDS = {"shipExclusionList": "excluded carriers", "AbilityIDs": "info-panel abilities",
               "MunitionSlots": "munition slots"}
# text IDs: descriptionID in the "default" localisation table (the name), the other two in "blueprint_descriptions"
TEXT_FIELDS = ("descriptionID", "descriptionHeaderID", "descriptionBodyID")
# other plain ints the toolkit sets itself (not offered in the allocation grid): ShipType = the fighter's ship-database id
HIDDEN_INT_FIELDS = TEXT_FIELDS + ("ShipType",)


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
    """Apply {type id or name: {field: value, 'shipExclusionList' / 'AbilityIDs' / 'MunitionSlots': [ints]}} to the
    table in one level file.
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
            if field in LIST_FIELDS:
                # the exclusion list is a set (kept sorted); ability text IDs and munition slots keep their order
                ids = [int(x) for x in (value or [])]
                if field == "shipExclusionList":
                    ids = sorted(set(ids))
                pos = e["_off"][field][0]
                old = e[field]
                splices.append((pos, 4 + 4 * len(old), struct.pack("<i", len(ids)) + b"".join(struct.pack("<i", i) for i in ids)))
                want[field] = ids
                if list(old) != ids:
                    log.append("%s: %s %s -> %s" % (e["name"], LIST_FIELDS[field], list(old), ids))
                continue
            if field not in FIELDS and field not in HIDDEN_INT_FIELDS:
                raise ValueError("cannot edit %r. Editable: %s, %s, %s" % (field, ", ".join(FIELDS), ", ".join(LIST_FIELDS), ", ".join(TEXT_FIELDS)))
            kind = FIELDS[field][0] if field in FIELDS else "int"
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
            ok = list(got) == value if field in LIST_FIELDS else \
                (abs(got - value) <= 1e-5 * max(1.0, abs(value)) if isinstance(value, float) else got == value)
            if not ok:
                raise ValueError("verification failed for %s.%s: wrote %s, read back %s" % (by_id[type_id]["name"], field, value, got))
    untouched = [e for e in res["objects"] if e["id"] not in expected]
    for e in untouched:
        a = {k: v for k, v in e.items() if k != "_off"}; b = {k: v for k, v in by_id[e["id"]].items() if k != "_off"}
        if a != b:
            raise ValueError("verification failed: %s changed although it was not edited" % e["name"])
    return result, log


def add_type(S, data, donor, type_id, name, changes=None):
    """Append squadron type `type_id` named `name` to the table in one level file: a copy of the donor's entry,
    then `changes` applied (as in apply). Returns (new bytes, log). An existing entry with that id is left alone."""
    found = find(S, data)
    if not found:
        raise ValueError("no %s in this file" % TABLE)
    pid, off, size, res = found
    if any(e["id"] == int(type_id) for e in res["objects"]):
        return bytes(data), []
    d = entry(res, donor)
    items = res["objects"]
    index = items.index(d)
    array_end = min(position for field, (position, _code) in res["_off"].items() if field != "objects")
    row_start = d["_off"]["name"][0]
    row_end = items[index + 1]["_off"]["name"][0] if index + 1 < len(items) else array_end
    mono = bytes(data[off:off + size])
    encoded = str(name).encode("utf-8")
    if not encoded or b"\0" in encoded:
        raise ValueError("invalid squadron type name %r" % name)
    name_field = struct.pack("<i", len(encoded)) + encoded
    name_field += b"\0" * ((-len(name_field)) % 4)
    id_pos = d["_off"]["id"][0]
    row = name_field + struct.pack("<i", int(type_id)) + mono[id_pos + 4:row_end]
    last_end = array_end
    new_mono = bytearray(mono[:last_end] + row + mono[last_end:])
    array_start = res["_off"]["objects"][0]
    struct.pack_into("<i", new_mono, array_start, len(items) + 1)
    result = sd.resize_object(bytes(data), pid, bytes(new_mono))
    after = find(S, result)
    added = [e for e in after[3]["objects"] if e["id"] == int(type_id)] if after else []
    if len(added) != 1 or added[0]["name"] != str(name):
        raise ValueError("verification failed: the new squadron type did not read back")
    plain = lambda e: {k: v for k, v in e.items() if k not in ("_off", "name", "id")}
    if plain(added[0]) != plain(d):
        raise ValueError("verification failed: the new squadron type is not a copy of %s" % d["name"])
    log = ["added squadron type %s (%d), a copy of %s (%d)" % (name, int(type_id), d["name"], d["id"])]
    if changes:
        result, more = apply(S, result, {int(type_id): changes})
        log += more
    return result, log
