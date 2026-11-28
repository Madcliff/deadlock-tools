"""Runtime mechanics options used by the custom-module DLL."""
import math
import os
import tempfile
from pathlib import Path

FILE = "deadlock-mechanics.txt"
DEFAULTS = {"campaign_refit": False, "drone_self_targeting": True, "magazine_loadout_display": True}


def read(path):
    settings = DEFAULTS.copy()
    if Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            words = line.split()
            if len(words) == 2 and words[0] in settings and words[1] in ("on", "off"):
                settings[words[0]] = words[1] == "on"
    return settings


def write(path, settings):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                     dir=path.parent, delete=False) as stream:
        name = stream.name
        try:
            for key in DEFAULTS:
                stream.write(key + (" on\n" if settings[key] else " off\n"))
            stream.close()
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def magazine_preview(bundle, *, allow_nonship=False):
    """Read the loadout preview multiplier from a ship's actual battle prefab."""
    from deadlock_tools import weapons
    data = weapons.read_bundle(bundle)
    offset, size, *_ = data["nodes"][0]
    prefab = weapons.SerFile(data["data"][offset:offset + size])
    scripts = {obj.pid: prefab.read(obj)[0].get("m_ClassName")
               for obj in prefab.objs if obj.classid == 115}
    ship_types = set()
    multipliers = set()
    for obj in prefab.objs:
        if obj.classid != 114:
            continue
        fields = prefab.read(obj)[0]
        script = scripts.get(fields.get("m_Script", {}).get("m_PathID"))
        if script == "ShipInfo" and "ShipType" in fields:
            ship_types.add(int(fields["ShipType"]))
        if script == "CustomMagazineCapacityModule" and "capacityMultiplier" in fields:
            multipliers.add(float(fields["capacityMultiplier"]))
    if not ship_types and not multipliers and allow_nonship:
        return None
    if len(ship_types) != 1 or len(multipliers) > 1:
        raise ValueError("need one ship type and one consistent magazine multiplier in the bundle")
    multiplier = next(iter(multipliers), 1.0)
    if not math.isfinite(multiplier) or multiplier <= 0:
        multiplier = 1.0
    return "deadlock-magazine-%d.txt" % ship_types.pop(), format(multiplier, ".9g") + "\n"


def magazine_output(bundle, folder, fallback=None):
    """Return output bytes and destination when a magazine preview needs synchronization."""
    preview = magazine_preview(bundle, allow_nonship=True)
    if preview is None:
        return None
    filename, value = preview
    target = Path(folder, filename)
    if value == "1\n" and not target.is_file() and not (fallback and Path(fallback, filename).is_file()):
        return None
    return value.encode("ascii"), str(target)
