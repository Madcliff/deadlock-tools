"""Create new munition types (copies of an existing munition that can then be edited like any other).

A munition type is spread over these places (see docs/game/munitions.md, "New munition types"):
    sharedassets9.assets   the missile prefab; its Missile component's missileType is the id the game looks up the
                           munition's stats with (nothing sets it at runtime), so a new type needs its own prefab copy
    level9                 MissileFactory: munition id -> missile prefab (PPtr into sharedassets9.assets)
    level5/6/7/8/85/9      MissileDataTable (stats, faction, costs, text IDs) and MissileImageDataTable (icons)
    level5 / level85       research: WarRoomDataTables.BlueprintInfo[campaign].MunitionUnlocks
    Assembly-CSharp.dll    the munition enum; campaign stock data (ShipStockData) iterates it with Enum.GetValues
"""
import os
import struct
import types

from deadlock_tools import shipdb as sd

ASSET_FILE = "sharedassets9.assets"
TEXT_FIELDS = ("descriptionID", "descriptionHeaderID", "descriptionBodyID")


def _object_size(data, pid):
    return next(size for p, _off, size, _cid, _e in sd.read_objects(data) if p == pid)


def missile_table(S, data):
    """(pid, offset, size, parsed) of the MissileDataTable in one level file, or None"""
    try:
        off, res = sd.load_missiles(S, data)
    except LookupError:
        return None
    pid = next(p for p, o, _s, _c, _e in sd.read_objects(data) if o == off)
    return pid, off, _object_size(data, pid), res


def image_table(S, data, munition_ids):
    """(pid, offset, size, parsed) of the munition MissileImageDataTable: several tables share its layout (weapon and
    other icons), so it is the one whose ids are the munition ids"""
    ti = S.typedef_index("MissileImageDataTable")
    for pid, off, size, cid, _e in sd.read_objects(data):
        if cid != 114:
            continue
        reader = sd.Reader(S, data[off:off + size], 32)
        try:
            res = reader.obj(ti)
        except Exception:
            continue
        if reader.p == size and res.get("objects") and {e["id"] for e in res["objects"]} == set(munition_ids):
            return pid, off, size, res
    return None


def _plain(value, top=True):
    if isinstance(value, dict):
        return {k: _plain(v, False) for k, v in value.items() if k != "_off" and not (top and k in ("name", "id"))}
    if isinstance(value, list):
        return [_plain(v, False) for v in value]
    return value


def _parse(S, data, pid, typedef):
    for p, off, size, _cid, _e in sd.read_objects(data):
        if p == pid:
            reader = sd.Reader(S, data[off:off + size], 32)
            res = reader.obj(S.typedef_index(typedef))
            return res if reader.p == size else None
    return None


def append_copy(S, data, finder, donor_id, new_id, new_name, typedef="MissileDataTable"):
    """append a copy of row `donor_id` (a DataTable entry starting with name and id) as `new_id` / `new_name` to the
    table `finder(S, data)` returns; verified by reading it back"""
    found = finder(S, data)
    if not found:
        return data, False
    pid, off, size, res = found
    items = res["objects"]
    if any(int(e["id"]) == int(new_id) for e in items):
        return data, False
    donor = next((e for e in items if int(e["id"]) == int(donor_id)), None)
    if donor is None:
        raise ValueError("munition %d is missing from a table" % donor_id)
    index = items.index(donor)
    array_start = res["_off"]["objects"][0]
    array_end = min((pos for field, (pos, _code) in res["_off"].items() if field != "objects" and pos > array_start),
                    default=size)
    row_end = items[index + 1]["_off"]["name"][0] if index + 1 < len(items) else array_end
    mono = bytes(data[off:off + size])
    encoded = str(new_name).encode("utf-8")
    if not encoded or b"\0" in encoded:
        raise ValueError("invalid munition name %r" % new_name)
    name_field = struct.pack("<i", len(encoded)) + encoded
    name_field += b"\0" * ((-len(name_field)) % 4)
    id_pos = donor["_off"]["id"][0]
    row = name_field + struct.pack("<i", int(new_id)) + mono[id_pos + 4:row_end]
    new_mono = bytearray(mono[:array_end] + row + mono[array_end:])
    struct.pack_into("<i", new_mono, array_start, len(items) + 1)
    result = sd.resize_object(bytes(data), pid, bytes(new_mono))
    after = _parse(S, result, pid, typedef)                 # re-read by object id (the image table is found by its ids)
    copy_ = [e for e in after["objects"] if int(e["id"]) == int(new_id)] if after else []
    if len(copy_) != 1 or copy_[0]["name"] != str(new_name) or _plain(copy_[0]) != _plain(donor):
        raise ValueError("the copied munition row did not read back as a copy of %s" % donor["name"])
    return result, True


def set_text_ids(S, data, munition_id, ids):
    """write descriptionID / descriptionHeaderID / descriptionBodyID of a munition in one level file"""
    found = missile_table(S, data)
    if not found or not ids:
        return data
    _pid, off, _size, res = found
    row = next(e for e in res["objects"] if int(e["id"]) == int(munition_id))
    out = bytearray(data)
    for field, value in ids.items():
        struct.pack_into("<i", out, off + row["_off"][field][0], int(value))
    return bytes(out)


def entry(S, levels, key):
    """a MissileDataTable entry by id or (part of) its name"""
    found = next((missile_table(S, d) for d in levels.values() if missile_table(S, d)), None)
    if not found:
        raise ValueError("no level file holds the MissileDataTable")
    items = found[3]["objects"]
    if isinstance(key, int) or str(key).lstrip("-").isdigit():
        hits = [e for e in items if e["id"] == int(key)]
    else:
        k = str(key).lower().replace(" ", "")
        hits = [e for e in items if e["name"].lower().replace(" ", "") == k] or \
               [e for e in items if k in e["name"].lower().replace(" ", "")]
    if len(hits) != 1:
        raise ValueError("%s munition matches %r. Munitions: %s" % ("no" if not hits else "more than one", key,
                         ", ".join("%s (%d)" % (e["name"], e["id"]) for e in items)))
    return hits[0]


def next_munition_id(S, levels, enum_ids=()):
    used = set(enum_ids)
    for data in levels.values():
        found = missile_table(S, data)
        if found:
            used |= {int(e["id"]) for e in found[3]["objects"]}
    return max(used) + 1


def prefab_name(name):
    """a GameObject name for the copied missile prefab"""
    return "".join(ch for ch in str(name).title() if ch.isalnum()) or "CustomMunition"


def create_munition(S, prefabs, levels, donor, name, munition_id, text_ids=None, copy_research=True):
    """Copy munition `donor` (id or name) as `munition_id` / `name`.

    prefabs: squadron_types.Prefabs over sharedassets9.assets (the missile prefab is cloned there);
    levels: {level file name: bytes}, at least the six scene levels with the munition tables (level9 has the
    MissileFactory). Returns ({file: new bytes}, log); the munition enum member is the caller's job."""
    from deadlock_tools import squadron_research as sr
    donor_entry = entry(S, levels, donor)
    donor_id = int(donor_entry["id"])
    db = types.SimpleNamespace(S=S)
    factory_file, factory = None, None
    for file_name, data in levels.items():
        try:
            _b, _o, parsed = sd.parse_factory(db, data, "MissileFactory")
        except LookupError:
            continue
        factory_file, factory = file_name, parsed
    if factory is None:
        raise ValueError("no level file holds the MissileFactory (level9)")
    donor_factory = next((e for e in factory["objects"] if int(e["id"]) == donor_id), None)
    if donor_factory is None:
        raise ValueError("the MissileFactory has no prefab for %s" % donor_entry["name"])
    file_id, donor_root = donor_factory["prefab"][1]
    if donor_root not in prefabs.objects or prefabs.objects[donor_root].type.name != "GameObject":
        raise ValueError("the MissileFactory's prefab for %s is not a GameObject in %s; the files do not belong together"
                         % (donor_entry["name"], ASSET_FILE))
    missiles = [p for p in prefabs.tree(donor_root) if prefabs.kind(p) == "Missile"]
    if len(missiles) != 1 or prefabs.read(missiles[0]).get("missileType") != donor_id:
        raise ValueError("%s's prefab does not have exactly one Missile component of type %d" % (donor_entry["name"], donor_id))
    new_prefab = prefab_name(name)
    mapping = prefabs.clone_tree(donor_root, 0, rename=new_prefab)
    root = mapping[donor_root]
    missile = mapping[missiles[0]]
    fields = prefabs.read(missile)
    fields["missileType"] = int(munition_id)
    prefabs.write(missile, fields)
    log = ["%s: new missile prefab %s (munition %d), a copy of %s's" % (ASSET_FILE, new_prefab, munition_id, donor_entry["name"])]
    out, stats_files, image_files = {}, [], []
    munition_ids = None
    for file_name, data in levels.items():
        table = missile_table(S, data)
        if not table:
            continue
        munition_ids = munition_ids or [e["id"] for e in table[3]["objects"]]
        ids = [e["id"] for e in table[3]["objects"]]
        new, added = append_copy(S, data, missile_table, donor_id, munition_id, name)
        if added:
            stats_files.append(file_name)
        new = set_text_ids(S, new, munition_id, text_ids or {})
        new, added = append_copy(S, new, lambda S_, d: image_table(S_, d, ids), donor_id, munition_id, name,
                                 typedef="MissileImageDataTable")
        if added:
            image_files.append(file_name)
        if new != data:
            out[file_name] = new
    if not stats_files:
        raise ValueError("no level file holds the MissileDataTable")
    log.append("MissileDataTable (%s): added %s (%d), a copy of %s (%d)%s" % (
        ", ".join(sorted(stats_files)), name, munition_id, donor_entry["name"], donor_id,
        "; text IDs %s" % ", ".join("%s %d" % kv for kv in text_ids.items()) if text_ids else ""))
    if image_files:
        log.append("MissileImageDataTable (%s): icons copied" % ", ".join(sorted(image_files)))
    data = out.get(factory_file, levels[factory_file])
    new, added = sd.append_factory_entry(db, data, "MissileFactory", munition_id, name, {"prefab": ("PPtr", (file_id, root))})
    if added:
        out[factory_file] = new
        log.append("%s: MissileFactory %d -> %s" % (factory_file, munition_id, new_prefab))
    if copy_research:
        merged = dict(levels); merged.update(out)
        more, lines = sr.copy_entries(S, merged, donor_id, munition_id, key=sr.MUNITION_KEY)
        out.update(more); log += lines
    return out, log


def cli(a):
    """shipdb addmunition DONOR "New name" [--display-name T] [--heading T] [--description T] [--munition-id N]
    [--no-research]"""
    from deadlock_tools import ship_types as stypes, squadron_types as sqt
    S = sd.Schema(a.dll)
    folder = a.level0 if os.path.isdir(a.level0) else os.path.dirname(a.level0)
    rest = list(a.args)
    if len(rest) != 2:
        raise SystemExit('usage: shipdb addmunition "Colonial Raptor Torpedo" "Heavy Raptor Torpedo" [--display-name T] '
                         '[--heading T] [--description T] [--munition-id N] [--no-research]')
    donor, name = rest
    managed, game_data = sqt.game_paths(folder, a.dll, a.managed, a.game_data)
    asset_source = sd.chain_source(a, os.path.join(folder, ASSET_FILE), a.out, quiet=True)
    prefabs = sqt.Prefabs(asset_source, managed, game_data)
    levels = {}
    for f_ in sd.files_of(folder):
        base = os.path.basename(f_)
        if base.startswith("level") and not base.endswith(".resS"):
            levels[base] = open(sd.chain_source(a, f_, a.out, quiet=True), "rb").read()
    dll_source = sqt._cli_dll_source(a)
    enum_ids = stypes.members(dll_source, munition=True)
    munition_id = a.munition_id if a.munition_id is not None else next_munition_id(S, levels, enum_ids)
    if munition_id in enum_ids:
        raise SystemExit("munition %d is already in the DLL's munition enum (%s)" % (munition_id, enum_ids[munition_id]))
    outputs, text_ids = {}, {}
    if a.display_name or a.heading or a.description:
        loc_original = next((os.path.join(p, "resources.assets") for p in (folder, game_data)
                             if os.path.isfile(os.path.join(p, "resources.assets"))), None)
        if not loc_original:
            raise SystemExit("resources.assets (the localisation) was not found in %s or %s" % (folder, game_data))
        outputs["resources.assets"], text_ids = sqt.add_texts(
            open(sd.chain_source(a, loc_original, a.out, quiet=True), "rb").read(), a.display_name, a.heading, a.description)
    try:
        changed, log = create_munition(S, prefabs, levels, donor, name, munition_id, text_ids, not a.no_research)
    except ValueError as e:
        raise SystemExit(str(e))
    outputs.update(changed)
    outputs[ASSET_FILE] = prefabs.save()
    _bundles, dll_dir = sqt._cli_out_dirs(a)
    os.makedirs(dll_dir, exist_ok=True)
    target = os.path.join(dll_dir, os.path.basename(a.dll))
    member = stypes.enum_name(name)
    result = stypes.add(managed, dll_source, target + ".tmp", {munition_id: member}, sqt.config_stage(a), munition=True)
    if result.returncode or not os.path.isfile(target + ".tmp"):
        raise SystemExit("could not add the munition enum member; nothing was written:\n" + (result.stdout + result.stderr)[-4000:])
    os.replace(target + ".tmp", target)
    log.append("%s: munition enum %s = %d" % (target, member, munition_id))
    os.makedirs(a.out, exist_ok=True)
    for base, data in outputs.items():
        with open(os.path.join(a.out, base), "wb") as stream:
            stream.write(data)
    print("\n".join("[ok] " + line for line in log))
    print("\nwrote %s to %s/. Edit its stats with 'shipdb setmunition' or on the Armaments tab; squadrons pick it in "
          "their Munitions list (same faction)." % (", ".join(sorted(outputs)), a.out))


# ---- combat stats (the missile prefab's Missile component; the tables only hold display copies) ----
# key: (where, field, kind, label, minimum, maximum).  where: "damage" = Missile.damage (what hits use; Missile.
# DisplayDamage and MissileDataTable.DisplayDamage are kept equal for the UI), "missile" = a Missile field,
# "mover" = the mover component (guided movers only have accuracyPC), "range" = MissileDataTable.Range (the firing
# range targeting uses) and Missile.range (how far the missile flies) together.
COMBAT_FIELDS = {
    "hull": ("damage", "HullDamage", "float", "Hull damage", 0, 100000),
    "armour": ("damage", "ArmourDamage", "float", "Armour damage", 0, 100000),
    "subsystem": ("damage", "SubSystemDamage", "float", "Subsystem damage", 0, 100000),
    "blast": ("damage", "Range", "float", "Blast radius (0 = direct hit)", 0, 100000),
    "spread_damage": ("damage", "SubsystemSpreadDamage", "float", "Subsystem spread damage", 0, 100000),
    "spread_count": ("damage", "SubsystemSpreadCount", "int", "Subsystems hit by the spread", 0, 100),
    "irradiate": ("damage", "AppliesIrradiatedStatus", "bool", "Irradiates", None, None),
    "irradiate_turns": ("damage", "IrradiatedTurns", "int", "Irradiated turns", 0, 1000),
    "irradiate_percent": ("damage", "IrradiatedPercentage", "float", "Irradiated percentage", 0, 1000),
    "speed": ("missile", "maxVelocity", "float", "Speed", 0.01, 100000),
    "range": ("range", "range", "float", "Range", 1, 1000000),
    "accuracy": ("mover", "accuracyPC", "float", "Guidance accuracy (0-1, guided only)", 0, 1),
    "turn_rate": ("mover", "maxTurnRate", "float", "Turn rate", 0, 100000),
}
MOVERS = ("MissileMoverTargetSeeking", "MissileMoverUnguided", "MissileMoverGuided", "MissileMover")


def _missile_prefab(S, prefabs, levels, munition_id):
    """(Missile component pid, mover pid or None) of a munition's prefab; refuses a prefab two munitions share"""
    db = types.SimpleNamespace(S=S)
    for data in levels.values():
        try:
            _b, _o, factory = sd.parse_factory(db, data, "MissileFactory")
        except LookupError:
            continue
        entry_ = next((e for e in factory["objects"] if int(e["id"]) == int(munition_id)), None)
        if entry_ is None:
            raise ValueError("the MissileFactory has no prefab for munition %d" % munition_id)
        root = entry_["prefab"][1][1]
        sharing = [e["name"] for e in factory["objects"] if e["prefab"][1][1] == root and int(e["id"]) != int(munition_id)]
        if sharing:
            raise ValueError("munition %d shares its missile prefab with %s; copy it as a new munition first"
                             % (munition_id, ", ".join(sharing)))
        tree = prefabs.tree(root)
        missiles = [p for p in tree if prefabs.kind(p) == "Missile"]
        movers = [p for p in tree if prefabs.kind(p) in MOVERS]
        if len(missiles) != 1:
            raise ValueError("munition %d's prefab has %d Missile components" % (munition_id, len(missiles)))
        return missiles[0], (movers[0] if movers else None)
    raise ValueError("no level file holds the MissileFactory (level9)")


def combat_stats(S, prefabs, levels, munition_id):
    """{key: value} of COMBAT_FIELDS the munition has (accuracy only on guided movers)"""
    missile, mover = _missile_prefab(S, prefabs, levels, munition_id)
    m = prefabs.read(missile)
    out = {}
    for key, (where, field, kind, _l, _lo, _hi) in COMBAT_FIELDS.items():
        if where == "damage":
            value = m["damage"][field]
        elif where in ("missile", "range"):
            value = m[field]
        else:
            fields = prefabs.read(mover) if mover is not None else {}
            if field not in fields:
                continue
            value = fields[field]
        out[key] = bool(value) if kind == "bool" else value
    table = entry(S, levels, munition_id)
    out["range"] = table["Range"]                       # the firing range targeting uses
    return out


def set_combat_stats(S, prefabs, levels, munition_id, values):
    """change combat stats; writes the prefab and the tables' display copies. Returns ({file: bytes}, log)"""
    missile, mover = _missile_prefab(S, prefabs, levels, munition_id)
    m = prefabs.read(missile)
    mv = prefabs.read(mover) if mover is not None else {}
    changes, clean = [], {}
    for key, value in values.items():
        if key not in COMBAT_FIELDS:
            raise ValueError("munition combat stats are %s, not %r" % (", ".join(COMBAT_FIELDS), key))
        where, field, kind, _label, lo, hi = COMBAT_FIELDS[key]
        if kind == "bool":
            value = 1 if value in (True, 1, "1", "true", "True", "yes", "on") else 0
        else:
            number = float(value)
            if kind == "int":
                if not number.is_integer():
                    raise ValueError("%s needs a whole number" % key)
                number = int(number)
            if not lo <= number <= hi:
                raise ValueError("%s must be between %g and %g" % (key, lo, hi))
            value = number
        if where == "mover" and field not in mv:
            raise ValueError("%s's mover has no %s (%s)" % (entry(S, levels, munition_id)["name"], field,
                             "unguided" if field == "accuracyPC" else "no mover"))
        clean[key] = value
    for key, value in clean.items():
        where, field, kind, label, _lo, _hi = COMBAT_FIELDS[key]
        if where == "damage":
            old = m["damage"][field]
            m["damage"][field] = value
            m["DisplayDamage"][field] = value
        elif where in ("missile", "range"):
            old = m[field]
            m[field] = value
        else:
            old = mv[field]
            mv[field] = value
        if old != value and not (kind == "float" and abs(old - value) < 1e-6):
            changes.append("%s %s -> %s" % (key, old, value))
    prefabs.write(missile, m)
    if mover is not None and mv:
        prefabs.write(mover, mv)
    # display copies (and the firing range) in the munition tables
    out = {}
    for name, data in levels.items():
        found = missile_table(S, data)
        if not found:
            continue
        _pid, off, _size, res = found
        row = next((e for e in res["objects"] if int(e["id"]) == int(munition_id)), None)
        if row is None:
            continue
        buf = bytearray(data)
        for key, value in clean.items():
            where, field, kind, _l, _lo, _hi = COMBAT_FIELDS[key]
            if where == "damage":
                pos = row["DisplayDamage"]["_off"][field][0]
            elif key == "speed":
                pos = row["_off"]["MaxVelocity"][0]
            elif key == "range":
                pos = row["_off"]["Range"][0]
            else:
                continue
            if kind == "float":
                struct.pack_into("<f", buf, off + pos, float(value))
            elif kind == "int":
                struct.pack_into("<i", buf, off + pos, int(value))
            else:
                struct.pack_into("<B", buf, off + pos, int(value))
        if bytes(buf) != data:
            out[name] = bytes(buf)
    log = ["%s (%d): %s" % (entry(S, levels, munition_id)["name"], munition_id, ", ".join(changes))] if changes else []
    return out, log


def stats_cli(a):
    """shipdb munitionstats MUNITION | setmunitionstats MUNITION hull=8 armour=6 blast=30 speed=70 range=5000 ..."""
    from deadlock_tools import squadron_types as sqt
    S = sd.Schema(a.dll)
    folder = a.level0 if os.path.isdir(a.level0) else os.path.dirname(a.level0)
    rest = list(a.args)
    if not rest:
        raise SystemExit(stats_cli.__doc__ + "\n  stats: " + ", ".join(COMBAT_FIELDS))
    managed, game_data = sqt.game_paths(folder, a.dll, a.managed, a.game_data)
    prefabs = sqt.Prefabs(sd.chain_source(a, os.path.join(folder, ASSET_FILE), a.out, quiet=True), managed, game_data)
    levels = {}
    for f_ in sd.files_of(folder):
        base = os.path.basename(f_)
        if base.startswith("level") and not base.endswith(".resS"):
            levels[base] = open(sd.chain_source(a, f_, a.out, quiet=True), "rb").read()
    try:
        munition = entry(S, levels, rest[0])
        if a.cmd == "munitionstats":
            stats = combat_stats(S, prefabs, levels, munition["id"])
            print("%s (%d):" % (munition["name"], munition["id"]))
            for key, value in stats.items():
                print("  %-18s %-10s %s" % (key, value, COMBAT_FIELDS[key][3]))
            return
        values = {}
        for item in rest[1:]:
            key, sep, value = item.partition("=")
            if not sep:
                raise ValueError("expected STAT=VALUE, got %r (stats: %s)" % (item, ", ".join(COMBAT_FIELDS)))
            values[key] = value
        changed, log = set_combat_stats(S, prefabs, levels, munition["id"], values)
    except ValueError as e:
        raise SystemExit(str(e))
    changed[ASSET_FILE] = prefabs.save()
    os.makedirs(a.out, exist_ok=True)
    for base, data in changed.items():
        with open(os.path.join(a.out, base), "wb") as stream:
            stream.write(data)
    print("\n".join("[ok] " + line for line in log) or "[ok] no change")
    print("\nwrote %s to %s/" % (", ".join(sorted(changed)), a.out))
