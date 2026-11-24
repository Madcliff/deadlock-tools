"""Read and edit fighter squadron prefabs (they live in the game's sharedassets9.assets).

Each squadron is a GameObject (e.g. ColonialViperMkVIISquadron) with these components:
    FighterSquadron        fighter count, loss thresholds, formation, defend/retreat ranges, missile defence
    ShipInfo               hull pool, turn rate, pitch/yaw, DRADIS range, boarding/repair flags
    PropulsionInfo         minimum / maximum speed
    SquadronMovementAgent  launch speed, boarding / landing / collision ranges
    WeaponsTarget          miss-sphere radius (how far enemy shots that miss land from the squadron)

Scalar fields are rewritten in place. Changing the fighter count resizes the two lists that depend on it
(ShipLoss: count - 1 hull thresholds; FormationOffset: one slot per fighter). Every edit is read back and
verified before the bytes are returned. See docs/game/squadrons.md for what the game does with each field.
"""
import struct

from deadlock_tools import shipdb as sd

ASSET_FILE = "sharedassets9.assets"
MAX_FIGHTERS = 16           # the largest squadron the game ships with (Cylon raiders)

# (component, field, kind, label, help)   kind: float / int / bool
FIELDS = [
    ("ShipInfo", "MaxHullPoints", "float", "Hull pool", "Total hull of the whole squadron; fighters are lost as it drops past the loss thresholds"),
    ("ShipInfo", "MaxCombatantStrength", "float", "Combat strength", "Strength value used by the AI and auto-resolve (normally equal to the hull pool)"),
    ("PropulsionInfo", "MaxVelocity", "float", "Max speed", "Top speed"),
    ("PropulsionInfo", "MinVelocity", "float", "Min speed", "Slowest cruising speed"),
    ("ShipInfo", "MaxTurnRate", "float", "Turn rate", "How fast the squadron turns"),
    ("ShipInfo", "MaxPitch", "float", "Max pitch", "Steepest climb/dive angle"),
    ("ShipInfo", "MaxYaw", "float", "Max yaw", "Yaw limit per tick"),
    ("ShipInfo", "YAW_INTERTIAL_STRESS_THRESHOLD", "float", "Yaw stress threshold", "Inertial stress limit for yawing"),
    ("ShipInfo", "MaxDRADISRange", "float", "DRADIS range", "Sensor range"),
    ("ShipInfo", "CollisionExclusionZoneRadius", "float", "Exclusion radius", "Radius other ships keep clear of"),
    ("ShipInfo", "missileCooldownTurns", "int", "Missile cooldown (turns)", "Turns between missile launches"),
    ("ShipInfo", "AllowBoarding", "bool", "Can be boarded", ""),
    ("ShipInfo", "CanRepair", "bool", "Can repair", ""),
    ("FighterSquadron", "LaunchSpacingInTicks", "int", "Launch spacing (ticks)", "Delay between fighters leaving the bay"),
    ("FighterSquadron", "RetreatDistance", "float", "Retreat distance", "How far the squadron falls back when retreating"),
    ("FighterSquadron", "MaxDefendRange", "float", "Defend range", "Range at which a defending squadron engages attackers"),
    ("FighterSquadron", "MaxMissileDefendRange", "float", "Missile defend range", "Range at which it intercepts missiles"),
    ("FighterSquadron", "MaxUtilitySquadronDefendRange", "float", "Utility-squadron defend range", "Range at which it protects support squadrons"),
    ("FighterSquadron", "MaxSelfDefenseRange", "float", "Self-defence range", "Range at which it fights back for itself"),
    ("FighterSquadron", "AttackFormationSize", "float", "Attack formation size", "Spread of the formation when attacking"),
    ("FighterSquadron", "DefendFormationSize", "float", "Defend formation size", "Spread of the formation when defending"),
    ("FighterSquadron", "BoardFormationSize", "float", "Board formation size", "Spread of the formation when boarding"),
    ("FighterSquadron", "AttackFlakShipsPercentage", "float", "Attack flak ships (0-1)", "Share of attack effort directed at flak ships"),
    ("FighterSquadron", "missileRadius", "float", "Missile defence radius", "Radius within which it targets missiles"),
    ("FighterSquadron", "missileIgnoreTravelSeconds", "float", "Missile ignore time (s)", "Missiles arriving sooner than this are ignored"),
    ("FighterSquadron", "missileSquadronClosenessPriority", "float", "Missile closeness priority", "Weight given to missiles close to the squadron"),
    ("FighterSquadron", "boardRecallTicks", "int", "Board recall (ticks)", "Ticks to recall boarders"),
    ("FighterSquadron", "ignoreCasualty", "bool", "Ignore casualties", "Fighters are not lost as hull drops"),
    ("FighterSquadron", "preventBoostingFirewalls", "bool", "Prevent boosting firewalls", ""),
    ("SquadronMovementAgent", "launchVelocity", "float", "Launch speed", "Speed when leaving the launch bay"),
    ("SquadronMovementAgent", "BoardingRange", "float", "Boarding range", ""),
    ("SquadronMovementAgent", "LandingRange", "float", "Landing range", "Distance at which it lands in its carrier"),
    ("SquadronMovementAgent", "collisionRangeMin", "float", "Collision range min", ""),
    ("SquadronMovementAgent", "collisionRangeMax", "float", "Collision range max", ""),
    ("SquadronMovementAgent", "DetectCollisions", "bool", "Detect collisions", ""),
    ("WeaponsTarget", "missSphereRadius", "float", "Miss-sphere radius", "How far from the squadron enemy misses land"),
]
FIELD_KEYS = {"%s.%s" % (c, f): (c, f, k) for c, f, k, _l, _h in FIELDS}
BY_NAME = {f.lower(): "%s.%s" % (c, f) for c, f, _k, _l, _h in FIELDS}
COMPONENTS = ("FighterSquadron", "ShipInfo", "PropulsionInfo", "SquadronMovementAgent", "WeaponsTarget")


def field_key(name):
    """'MaxVelocity' or 'PropulsionInfo.MaxVelocity' -> 'PropulsionInfo.MaxVelocity'"""
    if name in FIELD_KEYS:
        return name
    key = BY_NAME.get(name.lower())
    if not key:
        raise ValueError("unknown squadron field %r. Editable: %s, MaxShipCount, ShipLoss" % (name, ", ".join(f for _c, f, _k, _l, _h in FIELDS)))
    return key


def _parse(S, buf, off, size, class_name):
    reader = sd.Reader(S, buf[off:off + size], 32)
    try:
        res = reader.obj(S.typedef_index(class_name))
    except Exception:
        return None
    return res if reader.p == size else None


def _valid(class_name, res):
    if res is None:
        return False
    if class_name == "FighterSquadron":
        count = res.get("MaxShipCount")
        return isinstance(count, int) and 1 <= count <= 60 and isinstance(res.get("ShipLoss"), list) and len(res["ShipLoss"]) == count - 1
    if class_name == "ShipInfo":
        return isinstance(res.get("MaxHullPoints"), float)
    if class_name == "PropulsionInfo":
        return isinstance(res.get("MaxVelocity"), float) and set(res) - {"_off"} == {"MinVelocity", "MaxVelocity"}
    if class_name == "SquadronMovementAgent":
        return isinstance(res.get("launchVelocity"), float) and "LandingRange" in res
    if class_name == "WeaponsTarget":
        return isinstance(res.get("missSphereRadius"), float) and "missPoints" in res
    return False


def load(S, buf):
    """every squadron in a serialized file: [{name, components: {class: (pid, offset, size, parsed)}}], sorted by name"""
    objects = {pid: (off, size, cid) for pid, off, size, cid, _ in sd.read_objects(buf)}
    squadrons = []
    for pid, (off, size, cid) in objects.items():
        if cid != 1:
            continue
        try:
            count = struct.unpack_from("<i", buf, off)[0]
            pos = off + 4 + 12 * count + 4
            length = struct.unpack_from("<i", buf, pos)[0]
            name = buf[pos + 4:pos + 4 + length].decode("latin1")
        except Exception:
            continue
        if not name.endswith("Squadron"):
            continue
        components = {}
        for index in range(count):
            _file, cpid = struct.unpack_from("<iq", buf, off + 4 + 12 * index)
            if cpid not in objects or objects[cpid][2] != 114:
                continue
            coff, csize, _ = objects[cpid]
            for class_name in COMPONENTS:
                if class_name in components:
                    continue
                res = _parse(S, buf, coff, csize, class_name)
                if _valid(class_name, res):
                    components[class_name] = (cpid, coff, csize, res)
                    break
        if "FighterSquadron" in components and "ShipInfo" in components:
            squadrons.append({"name": name, "components": components})
    return sorted(squadrons, key=lambda item: item["name"])


def find(squadrons, name):
    key = name.lower().replace(" ", "").replace("_", "")
    exact = [s for s in squadrons if s["name"].lower() == key or s["name"].lower() == key + "squadron"]
    hits = exact or [s for s in squadrons if key in s["name"].lower()]
    if len(hits) != 1:
        raise ValueError("%s squadron matches %r. Squadrons: %s" % ("no" if not hits else "more than one", name, ", ".join(s["name"] for s in squadrons)))
    return hits[0]


def describe(squadron):
    """JSON-friendly view of one squadron for the GUI / CLI"""
    comps = squadron["components"]
    fs = comps["FighterSquadron"][3]
    fields = []
    for class_name, field, kind, label, help_text in FIELDS:
        if class_name not in comps or field not in comps[class_name][3]:
            continue
        value = comps[class_name][3][field]
        fields.append({"key": "%s.%s" % (class_name, field), "component": class_name, "field": field, "kind": kind,
                       "label": label, "help": help_text, "value": round(value, 6) if isinstance(value, float) else value})
    return {"name": squadron["name"], "shipType": fs["ShipType"], "squadronShipType": fs["SquadronShipType"],
            "count": fs["MaxShipCount"], "shipLoss": [round(x, 4) for x in fs["ShipLoss"]],
            "hull": round(comps["ShipInfo"][3]["MaxHullPoints"], 4), "fields": fields,
            "components": sorted(comps)}


def default_thresholds(old, old_hull, count, hull):
    """loss thresholds for a new fighter count, following the shape of the old curve (scaled to the new hull)."""
    if count <= 1:
        return []
    n = len(old) + 1
    points = [(0.0, 0.0)] + [((i + 1) / n, t / old_hull) for i, t in enumerate(sorted(old))] + [(1.0, 1.0)]
    out = []
    for j in range(1, count):
        x = j / count
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= x <= x1:
                y = y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                break
        out.append(round(y * hull, 3))
    return out


def _formation(old, count):
    """keep the existing formation slots; extra fighters continue the V outwards and backwards"""
    slots = [tuple(v) for v in old[:count]]
    while len(slots) < count:
        if len(slots) >= 2:
            x, y, z = slots[-2]
            side = 1.0 if x >= 0 else -1.0
            slots.append((x + side, y, z + 0.8))
        else:
            slots.append((float(len(slots)), 0.0, 0.8 * len(slots)))
    return slots


def _check_thresholds(values, count, hull):
    """The game counts active fighters as 1 + the thresholds strictly below the current hull
    (FighterSquadron.CalculateActiveFighters), so order does not matter; values must lie in (0, hull]."""
    if len(values) != count - 1:
        raise ValueError("%d fighters need %d loss thresholds, got %d" % (count, count - 1, len(values)))
    for value in values:
        if not 0 < value <= hull:
            raise ValueError("loss thresholds must be above 0 and at most the hull pool (%g): %s" % (hull, values))


def apply(S, buf, name, changes, scale_thresholds=True):
    """Apply {field key or name: value, 'MaxShipCount': n, 'ShipLoss': [..]} to one squadron. Returns (new bytes, log lines).

    scale_thresholds: when the hull pool changes and no ShipLoss is given, scale the thresholds with it
    (the way the game's own squadrons are built)."""
    buf = bytearray(buf)
    every = load(S, bytes(buf)); names_before = [x["name"] for x in every]
    squadron = find(every, name)
    comps = squadron["components"]
    fs_pid, fs_off, fs_size, fs = comps["FighterSquadron"]
    hull_old = comps["ShipInfo"][3]["MaxHullPoints"]
    changes = dict(changes)
    count = int(changes.pop("MaxShipCount", fs["MaxShipCount"]))
    losses = changes.pop("ShipLoss", None)
    scalar = {field_key(k): v for k, v in changes.items()}
    hull = float(scalar.get("ShipInfo.MaxHullPoints", hull_old))
    if not 1 <= count <= MAX_FIGHTERS:
        raise ValueError("fighter count must be 1-%d" % MAX_FIGHTERS)
    if hull <= 0:
        raise ValueError("the hull pool must be positive")
    if losses is None:
        if count != fs["MaxShipCount"]:
            losses = default_thresholds(fs["ShipLoss"], hull_old, count, hull if scale_thresholds else hull_old)
        elif scale_thresholds and hull != hull_old:
            losses = [round(t * hull / hull_old, 4) for t in fs["ShipLoss"]]
        else:
            losses = list(fs["ShipLoss"])
    losses = [float(x) for x in losses]
    _check_thresholds(losses, count, hull)
    log = []

    # 1. scalar fields, written in place (sizes never change)
    for key, value in scalar.items():
        class_name, field, kind = FIELD_KEYS[key]
        if class_name not in comps or field not in comps[class_name][3]:
            raise ValueError("%s has no %s" % (squadron["name"], key))
        _pid, off, _size, res = comps[class_name]
        pos = off + res["_off"][field][0]
        old = res[field]
        if kind == "float":
            struct.pack_into("<f", buf, pos, float(value))
        elif kind == "int":
            number = float(value)
            if not number.is_integer() or abs(number) > 2 ** 31 - 1:
                raise ValueError("%s needs a whole number" % key)
            struct.pack_into("<i", buf, pos, int(number))
        else:
            struct.pack_into("<B", buf, pos, 1 if value in (True, 1, "1", "true", "True") else 0)
        if str(old) != str(value):
            log.append("%s: %s %s -> %s" % (squadron["name"], key, old, value))

    # 2. fighter count, loss thresholds and formation (FighterSquadron is re-serialised when its size changes)
    mono = bytearray(buf[fs_off:fs_off + fs_size])
    loss_at = fs["_off"]["ShipLoss"][0]
    form_at = fs["_off"]["FormationOffset"][0]
    count_at = fs["_off"]["MaxShipCount"][0]
    old_count, old_losses, old_form = fs["MaxShipCount"], fs["ShipLoss"], fs["FormationOffset"]
    formation = _formation(old_form, count) if count != old_count else [tuple(v) for v in old_form]
    regions = sorted([(loss_at, 4 + 4 * len(old_losses), struct.pack("<i", len(losses)) + b"".join(struct.pack("<f", x) for x in losses)),
                      (form_at, 4 + 12 * len(old_form), struct.pack("<i", len(formation)) + b"".join(struct.pack("<fff", *v) for v in formation))],
                     reverse=True)
    for start, length, data in regions:                    # back to front, so earlier offsets stay valid
        mono[start:start + length] = data
    struct.pack_into("<i", mono, count_at, count)
    if count != old_count:
        log.append("%s: fighters %d -> %d (formation %d -> %d slots)" % (squadron["name"], old_count, count, len(old_form), len(formation)))
    if [round(x, 4) for x in losses] != [round(x, 4) for x in old_losses]:
        log.append("%s: loss thresholds %s -> %s" % (squadron["name"], [round(x, 2) for x in old_losses], [round(x, 2) for x in losses]))
    if len(mono) == fs_size:
        buf[fs_off:fs_off + fs_size] = mono
        result = bytes(buf)
    else:
        result = sd.resize_object(bytes(buf), fs_pid, bytes(mono))

    # 3. verify by reading everything back
    after = find(load(S, result), squadron["name"])
    afs = after["components"]["FighterSquadron"][3]
    if afs["MaxShipCount"] != count or len(afs["FormationOffset"]) != len(formation) or \
            any(abs(a - b) > 1e-4 * max(1.0, abs(b)) for a, b in zip(afs["ShipLoss"], losses)) or len(afs["ShipLoss"]) != len(losses):
        raise ValueError("verification failed for the fighter count / loss thresholds of %s" % squadron["name"])
    for key, value in scalar.items():
        class_name, field, kind = FIELD_KEYS[key]
        got = after["components"][class_name][3][field]
        ok = (bool(got) == (value in (True, 1, "1", "true", "True"))) if kind == "bool" else abs(float(got) - float(value)) <= 1e-5 * max(1.0, abs(float(value)))
        if not ok:
            raise ValueError("verification failed for %s.%s: wrote %s, read back %s" % (squadron["name"], key, value, got))
    if [x["name"] for x in load(S, result)] != names_before:
        raise ValueError("verification failed: the squadron list changed after the edit")
    return result, log
