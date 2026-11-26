"""Keep the game's ShipType enum in step with the ship rows in the level tables.

Fleet placement (Skirmish and campaign) builds its collision-radius map by
iterating the ShipType enum. A ship row whose ID has no enum member throws
KeyNotFoundException when the ship is added to a fleet. Copied ships therefore
need an enum member in Assembly-CSharp.dll, and any rebuild of that DLL from a
clean base must put those members back.
"""
import os
import subprocess

from deadlock_tools import config
from deadlock_tools import shipdb as sd

ANCHOR = ("ColonialBattlestarMercury", 29)   # a vanilla member that identifies the obfuscated enum
# the squadron enum (FighterSquadron.SquadronShipType / SquadronDataTable ids): campaign stock data iterates it with
# Enum.GetValues, so copied squadron types get members too
SQUADRON_ANCHOR = ("ColonialRaptorSquadron", 2)
MUNITION_ANCHOR = ("ColonialRaptorTorpedo", 12)       # the munition enum (MissileDataTable ids), iterated the same way
SCRIPTS = ("scripts/add_ship_type_enum.sh", "scripts/add_ship_type_enum.cs")


def members(dll, schema=None, squadron=False, munition=False):
    """Return {id: enum member name} for the ShipType enum (squadron=True: the squadron enum, munition=True: the
    munition enum) in dll."""
    S = schema or sd.Schema(dll)
    anchor = MUNITION_ANCHOR if munition else SQUADRON_ANCHOR if squadron else ANCHOR
    for k, (_flags, name, _sig) in enumerate(S.fl):
        if S.m.s(name) == anchor[0]:
            owner = S.owner[k]
            found = sd.enum_names(S, owner)
            if found.get(anchor[1]) == anchor[0]:
                return found
    raise ValueError("could not find the %s enum in %s" % ("munition" if munition else "squadron" if squadron else "ShipType", dll))


def level_ship_ids(dll, level_paths, schema=None):
    """Return {ship id: ship name} across the given level files' ShipDataTables."""
    S = schema or sd.Schema(dll)
    ids = {}
    for path in level_paths:
        if not os.path.isfile(path):
            continue
        try:
            db = sd.Db(dll, path, S)
        except SystemExit:
            continue                        # this level file has no ship table
        for ship in db.ships:
            ids.setdefault(int(ship["id"]), ship["name"])
    return ids


def missing(current, previous=None, level_ids=(), prefix="CustomShip"):
    """Members to add so `current` keeps every custom ship type.

    Keeps members that `previous` (the DLL being replaced) had and `current`
    lacks, under their old names, and adds any level-table ship ID that still
    has no member as CustomShip<id>. Raises if an ID is taken by another name.
    """
    previous = previous or {}
    wanted = {}
    for ship_id, name in previous.items():
        if ship_id not in current:
            wanted[ship_id] = name
    for ship_id in level_ids:
        ship_id = int(ship_id)
        if ship_id > 0 and ship_id not in current and ship_id not in wanted:
            wanted[ship_id] = "%s%d" % (prefix, ship_id)
    taken = {name: ship_id for ship_id, name in current.items()}
    for ship_id, name in wanted.items():
        if name in taken:
            raise ValueError("ship type %s is already used for ID %d, cannot add it as %d" % (name, taken[name], ship_id))
    return dict(sorted(wanted.items()))


def script_root(stage_dir):
    """Folder holding scripts/add_ship_type_enum.*; extracted from the .pyz when needed."""
    if not config.ZIPAPP:
        return config.PROJECT_DIR
    from zipfile import ZipFile
    root = os.path.join(stage_dir, "ship-enum-builder")
    with ZipFile(config.ZIPAPP) as archive:
        for name in SCRIPTS:
            archive.extract(name, root)
    return root


def add(managed, source, output, wanted, stage_dir, timeout=600, squadron=False, munition=False):
    """Write `output` = `source` plus the {id: name} enum members in `wanted` (squadron=True / munition=True: that
    enum instead of ShipType)."""
    root = script_root(stage_dir)
    script = os.path.join(root, "scripts", "add_ship_type_enum.sh")
    specs = (["--munition"] if munition else ["--squadron"] if squadron else []) + ["%d=%s" % item for item in sorted(wanted.items())]
    return subprocess.run(["bash", script, managed, source, output] + specs,
                          capture_output=True, text=True, timeout=timeout)


def level_squadron_ids(dll, level_paths, schema=None):
    """Return {squadron type id: name} across the given level files' SquadronDataTables."""
    from deadlock_tools import squadron_table as st
    S = schema or sd.Schema(dll)
    ids = {}
    for path in level_paths:
        if not os.path.isfile(path):
            continue
        try:
            found = st.find(S, open(path, "rb").read())
        except Exception:
            continue
        if found:
            for e in found[3]["objects"]:
                ids.setdefault(int(e["id"]), e["name"])
    return ids


def enum_name(text):
    """an enum member name from a prefab or display name ("Colonial Raptor Mk2 Squadron" -> ColonialRaptorMk2Squadron)"""
    name = "".join(ch for ch in str(text).title() if ch.isalnum()) if " " in str(text) else \
        "".join(ch for ch in str(text) if ch.isalnum() or ch == "_")
    if not name or not (name[0].isalpha() or name[0] == "_"):
        name = "Custom" + name
    return name


def level_munition_ids(dll, level_paths, schema=None):
    """Return {munition id: name} across the given level files' MissileDataTables."""
    S = schema or sd.Schema(dll)
    ids = {}
    for path in level_paths:
        if not os.path.isfile(path):
            continue
        try:
            _off, res = sd.load_missiles(S, open(path, "rb").read())
        except Exception:
            continue
        for e in res["objects"]:
            ids.setdefault(int(e["id"]), e["name"])
    return ids
