"""Subsystem crew modifiers: let a ship subsystem add to a crew stat it does not drive stock.

A ship bundle gets one ``CustomSubsystemModifiers`` MonoBehaviour on the GameObject that holds
``ShipInfo``. Its entries are parallel arrays (``stats``, ``sources``, ``bonusMin``, ``bonusMax``).
In battle, the patched ``ShipDataTable.GetCrewVeterancyBonus`` adds, for every entry whose stat
matches, ``bonusMin + (bonusMax - bonusMin) * source.HealthPC`` to the stock crew-veterancy bonus.
The DLL side is ``dll_modules/CustomSubsystemModifiers.cs`` merged by ``scripts/build_custom_modules.sh``;
see ``docs/game/subsystems.md`` for which stats the game actually reads and when.

Function API (no printing, no argv):
    read(path) -> {"present": bool, "entries": [entry, ...]}
    apply(src, dst, entries) -> same shape, read back from dst
    parse_spec("STAT:SOURCE:MIN:MAX") -> entry; describe(entry) -> str
An entry is ``{"stat": int, "source": int, "min": float, "max": float}``. ``apply`` with an empty
list removes the component. Encrypted input gives encrypted output; plain UnityFS stays plain.
"""
import copy
import math
import os
import struct
import tempfile

from deadlock_tools.weapons import (
    Node, SerFile, _md4, parse_bundle, write_bundle, xor_bytes,
)

CLASS_NAME = "CustomSubsystemModifiers"
FIELDS = (("stats", "int"), ("sources", "int"), ("bonusMin", "float"), ("bonusMax", "float"))

# Source subsystem codes, matching CustomSubsystemModifiers.Source* and the stat enum's hundreds.
SOURCES = {
    0: ("FireControl", "Fire Control"),
    1: ("Engineering", "Engineering"),
    2: ("Armory", "Armoury"),
    3: ("CIC", "CIC"),
    4: ("Hangar", "Hangar"),
    5: ("Navigation", "Navigation"),
    6: ("TechLab", "Tech Bay"),
}

# FEDNAJLFKEF (crew stat) values. "mode" is how the game applies GetCrewVeterancyBonus:
#   "percent": value * (1 + bonus)  -> bonus 0.25 means +25%
#   "fixed":   value + bonus        -> bonus is an absolute amount
#   None:      nothing in the stock game reads this stat (only this toolkit's custom modules can)
# "when": "live" = re-read during battle (health-scaled bonus tracks damage);
#         "setup" = read once at spawn / Start / resume (bonus uses the source's health then).
STATS = {
    0: {"name": "Turret range", "mode": None},
    1: {"name": "Turret accuracy", "mode": "fixed", "when": "setup"},
    2: {"name": "Munition salvo size", "mode": "percent", "when": "live"},
    100: {"name": "Damage reduction", "mode": "percent", "when": "setup"},
    101: {"name": "Subsystem repair", "mode": None},
    102: {"name": "Squadron repair", "mode": "percent", "when": "live"},
    103: {"name": "Hull regeneration", "mode": None},
    200: {"name": "Marine armour", "mode": None},
    201: {"name": "Marine strength", "mode": "fixed", "when": "setup"},
    202: {"name": "Flak damage", "mode": "percent", "when": "setup"},
    203: {"name": "Flak range", "mode": None},
    204: {"name": "Boarding strength", "mode": None},
    300: {"name": "Posture effect", "mode": None},
    301: {"name": "Processing power", "mode": None},
    302: {"name": "System overclock", "mode": None},
    400: {"name": "Squadron launch", "mode": None},
    401: {"name": "Squadron evade", "mode": "fixed", "when": "setup"},
    500: {"name": "Pitch control", "mode": None},
    501: {"name": "Yaw control", "mode": "percent", "when": "setup"},
    502: {"name": "Maximum distance", "mode": "percent", "when": "setup"},
    600: {"name": "DRADIS range", "mode": None},
    601: {"name": "Firewall regeneration", "mode": None},
    602: {"name": "Hacking strength", "mode": None},
    603: {"name": "Micro-FTL range", "mode": None},
    604: {"name": "Munition resupply rate", "mode": None},
    605: {"name": "Firewall strength", "mode": "fixed", "when": "setup"},
    606: {"name": "Stealth drain rate", "mode": None},
    607: {"name": "Stealth recharge rate", "mode": None},
}
# Stats that change gameplay in the stock game when given a bonus.
EFFECTIVE_STATS = {stat for stat, info in STATS.items() if info["mode"]}


def stock_source(stat):
    """The subsystem whose crew drives this stat stock (the stat enum groups stats by hundreds)."""
    return min(stat // 100, 6)



def _key(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _lookup(text, names, what):
    text = str(text).strip()
    try:
        number = int(text)
    except ValueError:
        number = next((code for code, labels in names.items() if _key(text) in {_key(label) for label in labels}), None)
    if number not in names:
        raise ValueError("unknown %s %r" % (what, text))
    return number


def parse_spec(text):
    """Parse 'STAT:SOURCE:MIN:MAX' (the weapons --subsystem-modifier value) into an entry.

    STAT is a crew-stat number or name (e.g. 102 or squadron_repair); SOURCE is 0-6 or a subsystem
    name (engineering, Tech Bay, TechLab); an empty SOURCE means the stat's stock subsystem.
    """
    parts = str(text).split(":")
    if len(parts) != 4:
        raise ValueError("--subsystem-modifier needs STAT:SOURCE:MIN:MAX, got %r" % text)
    stat = _lookup(parts[0], {code: (info["name"],) for code, info in STATS.items()}, "crew stat")
    source = stock_source(stat) if not parts[1].strip() else _lookup(parts[1], SOURCES, "source subsystem")
    return _validate([{"stat": stat, "source": source, "min": parts[2], "max": parts[3]}])[0]


def describe(entry):
    """One readable line for an entry, with whether the stock game reads the stat."""
    info = STATS.get(entry["stat"], {"name": "stat %s" % entry["stat"], "mode": None})
    effect = ("%s, %s" % (info["mode"], info["when"])) if info.get("mode") else "no stock effect"
    return "%s (%d) from %s: %g at 0 health -> %g at full health [%s]" % (
        info["name"], entry["stat"], SOURCES.get(entry["source"], ("?", "source %s" % entry["source"]))[1],
        entry["min"], entry["max"], effect)

# ----- bundle plumbing ------------------------------------------------------------------

def _load(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    encrypted = not raw.startswith(b"UnityFS\0")
    bundle = parse_bundle(raw)
    index = next((i for i, node in enumerate(bundle["nodes"])
                  if not node[3].endswith((".resS", ".resource"))), None)
    if index is None:
        raise ValueError("bundle has no serialized file")
    offset, size, _flags, _name = bundle["nodes"][index]
    return bundle, index, SerFile(bundle["data"][offset:offset + size]), encrypted


def _scripts(F):
    return {o.pid: F.read(o)[0].get("m_ClassName") for o in F.objs if o.classid == 115}


def _ship_object(F):
    """The GameObject that holds ShipInfo (where this toolkit's custom modules live)."""
    scripts = _scripts(F)
    for o in F.objs:
        if o.classid != 114:
            continue
        value = F.read(o)[0]
        if scripts.get(value.get("m_Script", {}).get("m_PathID")) == "ShipInfo":
            return value["m_GameObject"]["m_PathID"]
    raise ValueError("bundle has no ShipInfo component (not a ship prefab)")


def _components(F, go_pid):
    scripts = _scripts(F)
    found = []
    for o in F.objs:
        if o.classid != 114:
            continue
        value = F.read(o)[0]
        if (scripts.get(value.get("m_Script", {}).get("m_PathID")) == CLASS_NAME
                and value.get("m_GameObject", {}).get("m_PathID") == go_pid):
            found.append(o)
    return found


def _entries(value):
    columns = [value.get(name) for name, _ in FIELDS]
    if any(not isinstance(column, list) for column in columns):
        raise ValueError(f"unrecognised {CLASS_NAME} layout")
    count = min(len(column) for column in columns)
    stats, sources, low, high = columns
    return [{"stat": stats[i], "source": sources[i], "min": low[i], "max": high[i]} for i in range(count)]


def _f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _validate(entries):
    clean = []
    for entry in entries:
        stat, source = int(entry["stat"]), int(entry["source"])
        low, high = float(entry["min"]), float(entry["max"])
        if stat not in STATS:
            raise ValueError(f"unknown crew stat {stat}")
        if source not in SOURCES:
            raise ValueError(f"unknown source subsystem {source}")
        if not (math.isfinite(low) and math.isfinite(high)):
            raise ValueError("bonus endpoints must be finite numbers")
        clean.append({"stat": stat, "source": source, "min": _f32(low), "max": _f32(high)})
    return clean


def _type_raw(template, script_index):
    """Unity 2018.4 MonoBehaviour type record for CustomSubsystemModifiers (four plain vectors)."""
    name_index = next((i for i, n in enumerate(template.nodes) if n.level == 1 and n.name == "m_Name"), None)
    if name_index is None:
        raise ValueError("bundle has no usable MonoBehaviour type-tree header")
    end = name_index + 1
    while end < len(template.nodes) and template.nodes[end].level > 1:
        end += 1
    nodes = []

    def add(level, type_name, name, size, meta, tflags=0, ver=1):
        node = Node()
        node.ver, node.level, node.tflags = ver, level, tflags
        node.type, node.name, node.size, node.index, node.meta = type_name, name, size, len(nodes), meta
        nodes.append(node)

    for source in template.nodes[:end]:
        add(source.level, source.type, source.name, source.size, source.meta, source.tflags, source.ver)
    # Same flags as the game's own serialized vectors (e.g. Hangar LaunchCompatibility).
    for name, element in FIELDS:
        add(1, "vector", name, -1, 0x8000)
        add(2, "Array", "Array", -1, 0x4000, tflags=1)
        add(3, "int", "size", 4, 0)
        add(3, element, "data", 4, 0)

    hashed = bytearray()
    for node in nodes:
        hashed += node.type.encode() + node.name.encode()
        hashed += struct.pack("<iiii", node.size, node.tflags, node.ver, node.meta & 0x4000)
    type_hash = _md4(hashed)
    script_hash = _md4(b"Assembly-CSharp.dll" + CLASS_NAME.encode())
    strings, offsets = bytearray(), {}

    def offset(text):
        if text not in offsets:
            offsets[text] = len(strings)
            strings.extend(text.encode() + b"\0")
        return offsets[text]

    node_data = bytearray()
    for node in nodes:
        node_data += struct.pack("<HBBIIiiI", node.ver, node.level, node.tflags, offset(node.type),
                                 offset(node.name), node.size, node.index, node.meta)
    raw = struct.pack("<iBh", 114, 0, script_index) + script_hash + type_hash
    raw += struct.pack("<ii", len(nodes), len(strings)) + node_data + strings
    return raw, nodes, type_hash


def _ensure_script(F, appended, newpid):
    """Return (script_pid, type_index, nodes), adding the type record and MonoScript when missing."""
    script_pid = next((pid for pid, cls in _scripts(F).items() if cls == CLASS_NAME), None)
    script_count = struct.unpack_from("<i", F.suffix, 0)[0]
    if script_pid is not None:
        script_index = next(i for i in range(script_count)
                            if struct.unpack_from("<iq", F.suffix, 4 + 12 * i)[1] == script_pid)
        type_index = next(i for i, t in enumerate(F.types) if t.classid == 114 and t.script_idx == script_index)
        nodes = F.types[type_index].nodes
        names = [(n.name, n.type) for n in nodes if n.level == 1 and not n.name.startswith("m_")]
        if names != [(name, "vector") for name, _ in FIELDS]:
            raise ValueError(f"unrecognised {CLASS_NAME} type tree")
        return script_pid, type_index, nodes

    template = next((t for t in F.types if t.classid == 114 and
                     any(n.level == 1 and n.name == "m_Name" for n in t.nodes)), None)
    mono = next((o for o in F.objs if o.classid == 115), None)
    if template is None or mono is None:
        raise ValueError("bundle has no MonoBehaviour/MonoScript to use as a template")
    raw, nodes, type_hash = _type_raw(template, script_count)
    prefix = bytearray(F.prefix)
    count_at = 4 + len(F.unity) + 1 + 4 + 1             # endian+reserved, unity version, platform, has-type-tree
    struct.pack_into("<i", prefix, count_at, struct.unpack_from("<i", prefix, count_at)[0] + 1)
    F.prefix = bytes(prefix + raw)
    type_index = len(F.types)

    script_pid = newpid()
    fields = copy.deepcopy(F.read(mono)[0])
    fields.update(m_Name=CLASS_NAME, m_ExecutionOrder=0, m_ClassName=CLASS_NAME,
                  m_Namespace="", m_AssemblyName="Assembly-CSharp.dll")
    fields["m_PropertiesHash"] = {f"bytes[{i}]": value for i, value in enumerate(type_hash)}
    appended.append((script_pid, mono.tidx, F.write(mono, fields)))
    suffix = bytearray(F.suffix)
    struct.pack_into("<i", suffix, 0, script_count + 1)
    suffix[4 + 12 * script_count:4 + 12 * script_count] = struct.pack("<iq", 0, script_pid)
    F.suffix_len = (getattr(F, "suffix_len", None) or F._suffix_len()) + 12
    F.suffix = bytes(suffix)
    return script_pid, type_index, nodes


def _preload_edit(F, replaced, added=(), removed=()):
    bundle_obj = next(o for o in F.objs if o.classid == 142)
    value = copy.deepcopy(F.read(bundle_obj)[0])
    preload, containers = value["m_PreloadTable"], value["m_Container"]
    for pid in removed:
        for index in [i for i, ref in enumerate(preload) if ref["m_FileID"] == 0 and ref["m_PathID"] == pid][::-1]:
            del preload[index]
            for entry in containers:
                info = entry["second"]
                if index < info["preloadIndex"]:
                    info["preloadIndex"] -= 1
                elif index < info["preloadIndex"] + info["preloadSize"]:
                    info["preloadSize"] -= 1
    if added:
        if len(containers) != 1 or containers[0]["second"]["preloadIndex"] + containers[0]["second"]["preloadSize"] != len(preload):
            raise ValueError("unsupported ship prefab preload layout")
        preload.extend({"m_FileID": 0, "m_PathID": pid} for pid in added)
        containers[0]["second"]["preloadSize"] += len(added)
    replaced[bundle_obj.pid] = F.write(bundle_obj, value)


def _save(bundle, index, F, cab, dst, encrypted):
    blobs = []
    for i, (offset, size, flags, name) in enumerate(bundle["nodes"]):
        blobs.append((flags, name, cab if i == index else bundle["data"][offset:offset + size]))
    folder = os.path.dirname(os.path.abspath(dst))
    fd, temp = tempfile.mkstemp(dir=folder, prefix=".subsystem-modifiers-")
    os.close(fd)
    try:
        write_bundle(temp, bundle, blobs)
        if encrypted:
            with open(temp, "rb") as handle:
                data = xor_bytes(handle.read())
            with open(temp, "wb") as handle:
                handle.write(data)
        os.replace(temp, dst)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


# ----- public API -----------------------------------------------------------------------

def read(path):
    """Return {"present": bool, "entries": [...]} for the ship bundle at path."""
    _bundle, _index, F, _encrypted = _load(path)
    found = _components(F, _ship_object(F))
    if len(found) > 1:
        raise ValueError(f"ship has multiple {CLASS_NAME} components")
    if not found:
        return {"present": False, "entries": []}
    return {"present": True, "entries": _entries(F.read(found[0])[0])}


def apply(src, dst, entries):
    """Write src to dst with exactly these entries (empty list removes the component).

    Returns the read-back state of dst; raises ValueError if it does not match the request.
    """
    wanted = _validate(entries)
    bundle, index, F, encrypted = _load(src)
    go_pid = _ship_object(F)
    found = _components(F, go_pid)
    if len(found) > 1:
        raise ValueError(f"ship has multiple {CLASS_NAME} components")
    replaced, appended, removed = {}, [], set()
    used = set(F.by_pid)
    state = {"next": max(abs(p) for p in used) + 1000}

    def newpid():
        while state["next"] in used:
            state["next"] += 1
        used.add(state["next"])
        return state["next"]

    columns = {"stats": [e["stat"] for e in wanted], "sources": [e["source"] for e in wanted],
               "bonusMin": [e["min"] for e in wanted], "bonusMax": [e["max"] for e in wanted]}
    if found and wanted:
        obj = found[0]
        value = F.read(obj)[0]
        _entries(value)                                   # layout check
        value.update(columns)
        replaced[obj.pid] = F.write(obj, value)
    elif found:
        obj = found[0]
        removed.add(obj.pid)
        go = F.by_pid[go_pid]
        go_value = copy.deepcopy(F.read(go)[0])
        go_value["m_Component"] = [c for c in go_value["m_Component"] if c["component"]["m_PathID"] != obj.pid]
        replaced[go_pid] = F.write(go, go_value)
        _preload_edit(F, replaced, removed=[obj.pid])
    elif wanted:
        new_pids = []
        script_pid, type_index, nodes = _ensure_script(F, appended, newpid)
        if appended:
            new_pids.append(script_pid)
        component = {"m_GameObject": {"m_FileID": 0, "m_PathID": go_pid}, "m_Enabled": 1,
                     "m_Script": {"m_FileID": 0, "m_PathID": script_pid}, "m_Name": "", **columns}
        data = bytearray()
        F._wr(nodes, 0, component, data)
        pid = newpid()
        appended.append((pid, type_index, bytes(data)))
        new_pids.append(pid)
        go = F.by_pid[go_pid]
        go_value = copy.deepcopy(F.read(go)[0])
        go_value["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": pid}})
        replaced[go_pid] = F.write(go, go_value)
        _preload_edit(F, replaced, added=new_pids)

    cab = F.rebuild(replaced=replaced, appended=appended, removed=removed)
    _save(bundle, index, F, cab, dst, encrypted)
    result = read(dst)
    if result["entries"] != wanted or result["present"] != bool(wanted):
        raise ValueError("read-back of the written bundle does not match the requested entries")
    return result
