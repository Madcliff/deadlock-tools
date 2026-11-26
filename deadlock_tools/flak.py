"""Flak field: read and edit a ship bundle's FlakField component (weapons --flak FIELD=VALUE).

How the game uses each field is in docs/game/flak.md (read from the stock Assembly-CSharp.dll). In short: while flak
is on, every battle tick each ship inside an ellipsoid beside the ship (FlakRange.Min from the centre line, to port or
starboard; FlakAreaLength long, FlakAreaRadius wide) takes BaseDamagePerTick x crew flak bonus (stat 202) x the
Armory's FlakDamageRange evaluated on its health; each missile inside has a 30% chance per tick to be shot down.
"""
import math

# field -> (kind, minimum, help); kind float / int / bool. Order = display order.
FIELDS = {
    "BaseDamagePerTick.HullDamage": ("float", 0, "Hull damage each battle tick to every ship inside the flak zone (10 ticks a second, 120 a turn)."),
    "BaseDamagePerTick.ArmourDamage": ("float", 0, "Armour damage each tick to every ship inside the zone."),
    "BaseDamagePerTick.SubSystemDamage": ("float", 0, "Subsystem damage each tick (0 on every stock ship). Also the per-subsystem damage of the spread below."),
    "BaseDamagePerTick.SubsystemSpreadCount": ("int", 0, "Number of extra subsystems the subsystem damage spreads to."),
    "BaseDamagePerTick.SubsystemSpreadDamage": ("float", 0, "Ignored for flak: the game's damage scaling replaces it with Subsystem damage before the hit."),
    "BaseDamagePerTick.AppliesIrradiatedStatus": ("bool", None, "Hits also irradiate the target (as nuclear munitions do)."),
    "BaseDamagePerTick.IrradiatedTurns": ("int", 0, "How many turns the irradiation lasts."),
    "BaseDamagePerTick.IrradiatedPercentage": ("float", 0, "Irradiation strength."),
    "BaseDamagePerTick.Range": ("float", 0, "Not used by flak."),
    "FlakRange.Min": ("float", 0, "Distance from the ship's centre line to the middle of the flak zone, to port or starboard (the side picked in battle)."),
    "FlakRange.Max": ("float", 0, "Not used: the game always places flak at Min. Kept at least Min."),
    "FlakAreaLength": ("float", 0, "Length of the flak zone along the ship (fore-aft). The zone is an ellipsoid reaching Length/2 + Radius from its middle."),
    "FlakAreaRadius": ("float", 0, "Width and height of the flak zone (radius across the ship's sideways and vertical axes)."),
    "FlakExplosionsPerTick": ("float", 0, "Flak bursts drawn each tick at full Armory health (visual and sound only; damage does not depend on it)."),
}
POSITIVE = {"FlakAreaLength", "FlakAreaRadius"}
LABELS = {
    "BaseDamagePerTick.HullDamage": "Hull damage per tick",
    "BaseDamagePerTick.ArmourDamage": "Armour damage per tick",
    "BaseDamagePerTick.SubSystemDamage": "Subsystem damage per tick",
    "BaseDamagePerTick.SubsystemSpreadCount": "Subsystems the damage spreads to",
    "BaseDamagePerTick.SubsystemSpreadDamage": "Spread damage (ignored)",
    "BaseDamagePerTick.AppliesIrradiatedStatus": "Irradiates targets",
    "BaseDamagePerTick.IrradiatedTurns": "Irradiation turns",
    "BaseDamagePerTick.IrradiatedPercentage": "Irradiation strength",
    "BaseDamagePerTick.Range": "Damage range (unused)",
    "FlakRange.Min": "Distance from the ship",
    "FlakRange.Max": "Distance maximum (unused)",
    "FlakAreaLength": "Zone length",
    "FlakAreaRadius": "Zone radius",
    "FlakExplosionsPerTick": "Bursts per tick (visual)",
}

# FlakField of every stock ship that has one (Steam 1.5.113 bundles; no Cylon or Sagittaron ship has flak).
# hull, armour per tick; distance (FlakRange.Min); length; radius; bursts per tick; Armory FlakDamageRange %.
STOCK = [
    ("Artemis", 1.0, 0.02, 100, 110, 45, 1.33, (20, 100)),
    ("Battlestar (Galactica)", 1.0, 0.02, 120, 155, 70, 1.4, (20, 100)),
    ("Defender", 1.0, 0.02, 70, 70, 30, 1.33, (20, 100)),
    ("Heracles", 1.0, 0.02, 60, 90, 45, 1.4, (20, 100)),
    ("Jupiter Mk II", 1.0, 0.02, 120, 160, 75, 1.4, (20, 100)),
    ("Mercury", 1.0, 0.02, 100, 110, 45, 1.33, (20, 100)),
    ("Minerva", 1.0, 0.02, 100, 90, 35, 1.33, (20, 90)),
    ("Valkyrie", 1.0, 0.02, 60, 75, 35, 1.33, (20, 100)),
]


def _scripts(prefab):
    return {obj.pid: prefab.read(obj)[0]["m_ClassName"] for obj in prefab.objs if obj.classid == 115}


def _find(prefab, wanted):
    scripts = _scripts(prefab)
    found = []
    for obj in prefab.objs:
        if obj.classid != 114:
            continue
        fields = prefab.read(obj)[0]
        if scripts.get(fields.get("m_Script", {}).get("m_PathID")) == wanted:
            found.append((obj, fields))
    return found


def _get(fields, key):
    for part in key.split("."):
        fields = fields[part]
    return fields


def _value(kind, raw):
    return bool(raw) if kind == "bool" else int(raw) if kind == "int" else float(raw)


def read(prefab):
    """{"values": {field: value}, "armory": {"Min", "Max"} or None} or None when the ship has no FlakField"""
    flak = _find(prefab, "FlakField")
    if not flak:
        return None
    if len(flak) > 1:
        raise ValueError("this bundle has more than one FlakField")
    fields = flak[0][1]
    armory = _find(prefab, "Armory")
    damage = armory[0][1].get("FlakDamageRange") if armory else None
    return {"values": {key: _value(kind, _get(fields, key)) for key, (kind, _m, _h) in FIELDS.items()},
            "armory": {"Min": damage["Min"], "Max": damage["Max"]} if isinstance(damage, dict) else None}


def hull(prefab):
    """rough hull box for the Flak field diagram, in the ship root's local units: the largest mesh drawn under the
    ShipInfo root (MeshFilter -> Mesh.m_LocalAABB), scaled by the transforms between it and the root (rotations are
    ignored). {"center": [x, y, z], "extent": [x, y, z], "scale": root scale, "mesh": name} or None"""
    ships = _find(prefab, "ShipInfo")
    if not ships:
        return None
    transforms = {obj.pid: prefab.read(obj)[0] for obj in prefab.objs if obj.classid == 4}
    by_object = {fields["m_GameObject"]["m_PathID"]: pid for pid, fields in transforms.items()}
    root = by_object.get(ships[0][1]["m_GameObject"]["m_PathID"])
    if root is None:
        return None
    meshes = {obj.pid: obj for obj in prefab.objs if obj.classid == 43}
    best = None
    for obj in prefab.objs:
        if obj.classid != 33:
            continue
        fields = prefab.read(obj)[0]
        mesh = meshes.get(fields.get("m_Mesh", {}).get("m_PathID"))
        node = by_object.get(fields["m_GameObject"]["m_PathID"])
        if mesh is None or node is None:
            continue
        scale, offset, chain = [1.0, 1.0, 1.0], [0.0, 0.0, 0.0], []
        while node is not None and node != root:
            chain.append(transforms[node])
            node = transforms[node]["m_Father"]["m_PathID"] or None
        if node != root:
            continue
        box = prefab.read(mesh)[0].get("m_LocalAABB")
        if not box:
            continue
        center = [box["m_Center"][k] for k in "xyz"]
        extent = [abs(box["m_Extent"][k]) for k in "xyz"]
        for step in chain:                       # innermost first: scale, then move into the parent
            factor = [step["m_LocalScale"][k] for k in "xyz"]
            center = [c * f + step["m_LocalPosition"][k] for c, f, k in zip(center, factor, "xyz")]
            extent = [e * abs(f) for e, f in zip(extent, factor)]
        volume = extent[0] * extent[1] * extent[2]
        if best is None or volume > best[0]:
            best = (volume, {"center": center, "extent": extent, "mesh": prefab.read(mesh)[0].get("m_Name", "")})
    if best is None:
        return None
    out = best[1]
    out["scale"] = transforms[root]["m_LocalScale"]["x"]
    return out


def parse(items):
    """['FIELD=VALUE', ...] -> {field: value}, refusing unknown fields and bad values"""
    out = {}
    for item in items:
        key, sep, raw = str(item).partition("=")
        key = key.strip()
        if not sep:
            raise ValueError("--flak expects FIELD=VALUE, got %r" % item)
        if key not in FIELDS:
            raise ValueError("unknown flak field %r; fields: %s" % (key, ", ".join(FIELDS)))
        kind, minimum, _h = FIELDS[key]
        raw = raw.strip()
        try:
            if kind == "bool":
                if raw.lower() not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
                    raise ValueError
                value = raw.lower() in ("1", "true", "yes", "on")
            elif kind == "int":
                value = int(raw)
            else:
                value = float(raw)
                if not math.isfinite(value):
                    raise ValueError
        except ValueError:
            raise ValueError("flak field %s needs a %s, got %r" % (key, {"bool": "true/false", "int": "whole number"}.get(kind, "number"), raw))
        if minimum is not None and value < minimum:
            raise ValueError("flak field %s cannot be negative" % key)
        if key in POSITIVE and value <= 0:
            raise ValueError("flak field %s must be greater than 0" % key)
        out[key] = value
    return out


def apply(prefab, changes, replaced):
    """write {field: value} into the ship's FlakField (into `replaced`, as apply_prefab_fields does). Returns log lines."""
    if not changes:
        return []
    flak = _find(prefab, "FlakField")
    if not flak:
        raise ValueError("this ship has no flak field (FlakField component); only ships with flak can be edited")
    if len(flak) > 1:
        raise ValueError("this bundle has more than one FlakField")
    obj = flak[0][0]
    fields = (prefab._rd(prefab.types[obj.tidx].nodes, 0, replaced[obj.pid], [0])[0]
              if obj.pid in replaced else prefab.read(obj)[0])
    log = []
    for key, value in changes.items():
        kind = FIELDS[key][0]
        parts = key.split(".")
        target = fields
        for part in parts[:-1]:
            target = target[part]
        old = target[parts[-1]]
        stored = int(value) if kind == "bool" else value
        if _value(kind, old) != _value(kind, stored):
            log.append("flak %s %s -> %s" % (key, _value(kind, old), _value(kind, stored)))
        target[parts[-1]] = stored
    ranges = fields["FlakRange"]
    if ranges["Max"] < ranges["Min"]:
        if "FlakRange.Max" in changes:
            raise ValueError("flak FlakRange.Max (%g) must be at least FlakRange.Min (%g)" % (ranges["Max"], ranges["Min"]))
        ranges["Max"] = ranges["Min"] + 1
        log.append("flak FlakRange.Max raised to %g (stays at least Min)" % ranges["Max"])
    replaced[obj.pid] = prefab.write(obj, fields)
    return log


def describe(info):
    """lines for weapons --inspect"""
    if not info:
        return ["flak field: none"]
    lines = ["flak field:"]
    for key, value in info["values"].items():
        lines.append("  %-42s %s" % (key, value))
    if info["armory"]:
        lines.append("  Armory FlakDamageRange (%% of base damage, by Armory health): %g - %g" % (info["armory"]["Min"], info["armory"]["Max"]))
    return lines
