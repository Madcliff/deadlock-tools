"""Module subsystems: which ship subsystems drive the modules this toolkit adds, and how strongly.

A ship bundle gets one ``CustomModuleSubsystems`` MonoBehaviour on the GameObject that holds ``ShipInfo``. Its
entries are parallel arrays (``modules``, ``sources``, ``rateMin``, ``rateMax``). In battle, a module that has at
least one entry uses

    factor % = sum over its entries of  rateMin + (rateMax - rateMin) * source.HealthPC

in place of its stock subsystem factor:

* Regenerative Hull Resin: stock ``Engineering.HullRepairRateRange`` (hull per tick = hullPerTick x factor / 100)
* Munition Factory: stock ``Armory.MunitionResupplyRange`` (work per tick = factor / 100)
* Custom armour repair, drone resupply, squadron replacement, magazine capacity: stock no subsystem (100 %);
  the factor multiplies their rate (or capacity) on top of the crew-veterancy weighting

A module without entries behaves as before. Several entries for one module add up, so e.g. Engineering 50-100 %
plus Tech Bay 0-25 % gives 50 % with both subsystems destroyed and 125 % with both at full health.
The DLL side is ``dll_modules/CustomModuleSubsystems.cs`` (hooks in ``scripts/merge_custom_modules.cs``).

Function API (no printing, no argv):
    read(path) -> {"present": bool, "entries": [entry, ...]}
    apply(src, dst, entries) -> same shape, read back from dst
    parse_spec("MODULE:SOURCE:MIN:MAX") -> entry; describe(entry) -> str
An entry is ``{"module": int, "source": int, "min": float, "max": float}`` with percentages for min/max.
"""
import math

from deadlock_tools import subsystem_modifiers as sm

CLASS_NAME = "CustomModuleSubsystems"

# Module codes, matching CustomModuleSubsystems.Module* in the DLL. "script" is the module's MonoBehaviour,
# "stock" what drives it without entries.
MODULES = {
    0: {"name": "Hull regeneration", "script": "RegenerativeHullResinModule", "stock": "Engineering hull repair range"},
    1: {"name": "Munition Factory", "script": "MunitionFactoryModule", "stock": "Armoury munition resupply range"},
    2: {"name": "Custom armour repair", "script": "CustomArmourRepairModule", "stock": "no subsystem (100 %)"},
    3: {"name": "Custom drone resupply", "script": "CustomDroneResupplyModule", "stock": "no subsystem (100 %)"},
    4: {"name": "Custom squadron replacement", "script": "CustomSquadronReplacementModule", "stock": "no subsystem (100 %)"},
    5: {"name": "Custom magazine capacity", "script": "CustomMagazineCapacityModule", "stock": "no subsystem (100 %)"},
}
SOURCES = sm.SOURCES


def _validate(entries):
    clean = []
    for entry in entries:
        module, source = int(entry["module"]), int(entry["source"])
        low, high = float(entry["min"]), float(entry["max"])
        if module not in MODULES:
            raise ValueError(f"unknown module {module}")
        if source not in SOURCES:
            raise ValueError(f"unknown source subsystem {source}")
        if not (math.isfinite(low) and math.isfinite(high)):
            raise ValueError("rate endpoints must be finite numbers")
        clean.append({"module": module, "source": source, "min": sm._f32(low), "max": sm._f32(high)})
    return clean


SPEC = sm.ComponentSpec(CLASS_NAME, (("modules", "int"), ("sources", "int"), ("rateMin", "float"), ("rateMax", "float")),
                        ("module", "source", "min", "max"), _validate)


def read(path):
    """Return {"present": bool, "entries": [...]} for the ship bundle at path."""
    return sm.read(path, SPEC)


def apply(src, dst, entries):
    """Write src to dst with exactly these entries (empty list removes the component)."""
    return sm.apply(src, dst, entries, SPEC)


def parse_spec(text):
    """Parse 'MODULE:SOURCE:MIN:MAX' (weapons --module-subsystem). MODULE is 0-5 or a name (hull_regeneration,
    munition factory, CustomArmourRepairModule); SOURCE is 0-6 or a subsystem name; MIN/MAX are percentages at 0 and
    full health of that subsystem."""
    parts = str(text).split(":")
    if len(parts) != 4:
        raise ValueError("--module-subsystem needs MODULE:SOURCE:MIN:MAX, got %r" % text)
    module = sm._lookup(parts[0], {code: (info["name"], info["script"]) for code, info in MODULES.items()}, "module")
    source = sm._lookup(parts[1], SOURCES, "source subsystem")
    return _validate([{"module": module, "source": source, "min": parts[2], "max": parts[3]}])[0]


def describe(entry):
    info = MODULES.get(entry["module"], {"name": "module %s" % entry["module"]})
    return "%s from %s: %g %% at 0 health -> %g %% at full health" % (
        info["name"], SOURCES.get(entry["source"], ("?", "source %s" % entry["source"]))[1], entry["min"], entry["max"])
