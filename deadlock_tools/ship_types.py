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
SCRIPTS = ("scripts/add_ship_type_enum.sh", "scripts/add_ship_type_enum.cs")


def members(dll, schema=None):
    """Return {ship id: enum member name} for the ShipType enum in dll."""
    S = schema or sd.Schema(dll)
    owner = None
    for k, (_flags, name, _sig) in enumerate(S.fl):
        if S.m.s(name) == ANCHOR[0]:
            owner = S.owner[k]
            found = sd.enum_names(S, owner)
            if found.get(ANCHOR[1]) == ANCHOR[0]:
                return found
    raise ValueError("could not find the ShipType enum in " + dll)


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


def missing(current, previous=None, level_ids=()):
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
            wanted[ship_id] = "CustomShip%d" % ship_id
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


def add(managed, source, output, wanted, stage_dir, timeout=600):
    """Write `output` = `source` plus the {id: name} enum members in `wanted`."""
    root = script_root(stage_dir)
    script = os.path.join(root, "scripts", "add_ship_type_enum.sh")
    specs = ["%d=%s" % item for item in sorted(wanted.items())]
    return subprocess.run(["bash", script, managed, source, output] + specs,
                          capture_output=True, text=True, timeout=timeout)
