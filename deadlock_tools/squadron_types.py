"""Create new squadron types and add or remove squadron abilities.

A squadron type is spread over four places (see docs/game/squadrons.md):
    sharedassets9.assets   the prefab: a GameObject tree with FighterSquadron, ShipInfo, the action menus ...
    level9                 SquadronFactory: squadron id -> prefab pointer (into sharedassets9.assets)
    level5/6/7/8/85/9      SquadronDataTable: one entry per type (faction, fighter ship type, abilities text ...)
    Assembly-CSharp.dll    the squadron enum; campaign stock data iterates it, so every id needs a member

Abilities are action components on the prefab, usually with a helper component on the prefab root:
the Raptor has an "Abilities" submenu (a child GameObject of "Actions") holding BoardAction, DisarmAction and
BoostFirewallsAction, and a DisarmAgent on its root. The SquadronDataTable's AbilityIDs only list the ability
names shown in the squadron's info panel (text IDs).

The prefab file has no type trees; MonoBehaviours are read and written with type trees generated from the game's
DLLs (UnityPy + TypeTreeGeneratorAPI, the optional asset support). Every squadron prefab object round-trips
byte for byte through those trees. Their MonoBehaviour header misreads m_Script (an alignment the generated tree
leaves out), so script names are taken from the raw bytes instead and m_Script is never rewritten.
"""
import copy
import os
import struct

ASSET_FILE = "sharedassets9.assets"
ABILITIES_TEXT = 21          # "Abilities": the label of the abilities submenu
SUBMENU_ICON = 8             # icon index the stock abilities / assign-group submenus use
MENU_SLOTS = range(8)        # radial menu positions 0-7

# key: (label, action script, helper scripts on the prefab root, info-panel text IDs (first = default))
ABILITIES = {
    "board": ("Board target", "BoardAction", (), (34,)),
    "disarm": ("Disarm", "DisarmAction", ("DisarmAgent",), (833,)),
    "boost_firewalls": ("Boost firewalls", "BoostFirewallsAction", (), (967,)),
    "hack": ("Hack target", "HackTargetAction", ("HackModule",), (441,)),
    "jamming": ("Jamming mode", "JammingAction", ("Jammer",), (995,)),
    "chaff": ("Deploy chaff", "ChaffCloudAction", ("ChaffCloudAgent",), (33,)),
    "paint_target": ("Paint target", "PaintTargetAction", ("PaintTargetModule",), (971,)),
    "bomb": ("Bomb target", "BombTargetAction", ("BombTargetModule",), ()),
    "fire_missile": ("Fire munitions", "FireMissileAction", (), (232, 25)),
    # built from scratch (no squadron has it to copy): StealthModule + StealthAction, see add_stealth
    "stealth": ("Stealth", "StealthAction", ("StealthModule",), (1534,)),
}
BUILT_IN = {"stealth"}
# Editable settings of each ability: (component on the prefab root, [(field, kind, label, minimum, maximum)]).
# Stock values in docs/game/squadrons.md. Chaff's maxStock is full at battle start and refills when the squadron lands
# (ChaffCloudAgent resets it on FightersDocked).
ABILITY_SETTINGS = {
    "chaff": ("ChaffCloudAgent", (("maxStock", "int", "Chaff supply (clouds; refills on landing)", 0, 1000),
                                  ("releaseInterval", "float", "Ticks between clouds", 1, 10000),
                                  ("minRangeFromPreviousClouds", "float", "Minimum distance from earlier clouds", 0, 100000))),
    "disarm": ("DisarmAgent", (("disarmRange", "float", "Disarm range", 0, 100000),)),
    "hack": ("HackModule", (("HackStrength", "float", "Hack strength (0-1)", 0, 1),
                            ("HackRange", "float", "Hack range", 0, 100000))),
    "jamming": ("Jammer", (("jamRange", "float", "Jam range", 0, 100000),
                           ("jamMissileChance", "float", "Chance to jam each missile (0-1)", 0, 1))),
    "paint_target": ("PaintTargetModule", (("accuracyBuff", "float", "Accuracy bonus", 0, 1000),
                                           ("range", "float", "Paint range", 0, 100000))),
    "bomb": ("BombTargetModule", (("bombRange", "float", "Bomb release range", 0, 100000),
                                  ("bombVerticalOffset", "float", "Bomb vertical offset", -10000, 10000),
                                  ("moveOutRange", "float", "Move-out range after the run", 0, 100000),
                                  ("missileType", "int", "Bomb munition id", 0, 100000))),
    "stealth": ("StealthModule", (("batteryMax", "float", "Stealth battery", 0.001, 100000),
                                  ("destealthCooldownTurns", "int", "Cooldown (turns)", 0, 1000),
                                  ("activeByDefault", "bool", "Active at battle start", None, None))),
}
STEALTH_DEFAULTS = {"battery": 100.0, "cooldown": 1, "active": False}     # the Orion's battery and cooldown
STEALTH_EFFECTS = ("StealthEffect", "ChangeStealthEffect")
# Stock squadrons carry a DisarmAgent without a DisarmAction (Taipan, the story raptors), so it is left in place when
# Disarm is removed. Other helpers are removed with the last action that uses them.
KEEP_HELPERS = {"DisarmAgent"}
TRANSFORMS = ("Transform", "RectTransform")


def ability_key(text):
    """an ability key from a key, label or action script name ("Board target", "BoardAction", "board")"""
    k = str(text).lower().replace(" ", "").replace("_", "").replace("-", "")
    for key, (label, action, _helpers, _ids) in ABILITIES.items():
        if k in (key.replace("_", ""), label.lower().replace(" ", ""), action.lower(), action.lower()[:-6]):
            return key
    raise ValueError("unknown squadron ability %r. Abilities: %s" % (text, ", ".join(ABILITIES)))


def _unity():
    try:
        from deadlock_tools.bundled_assets import load_asset_tools
        return load_asset_tools()
    except ImportError as exc:
        raise RuntimeError("squadron prefab editing needs the optional UnityPy asset support "
                           "(python -m pip install 'deadlock-tools[ui-assets]', or run the deadlock.pyz build)") from exc


def _ptr(value):
    return isinstance(value, dict) and set(value) == {"m_FileID", "m_PathID"}


def _pointers(value, out=None):
    """every file-local PPtr path ID inside a parsed object (m_Script is skipped: see the module notes)"""
    out = [] if out is None else out
    if _ptr(value):
        if value["m_FileID"] == 0 and value["m_PathID"]:
            out.append(value["m_PathID"])
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != "m_Script":
                _pointers(item, out)
    elif isinstance(value, list):
        for item in value:
            _pointers(item, out)
    return out


def _remap(value, mapping):
    if _ptr(value):
        if value["m_FileID"] == 0 and value["m_PathID"] in mapping:
            return {"m_FileID": 0, "m_PathID": mapping[value["m_PathID"]]}
        return value
    if isinstance(value, dict):
        return {key: (item if key == "m_Script" else _remap(item, mapping)) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, mapping) for item in value]
    return value


class Prefabs:
    """The squadron prefabs of one sharedassets9.assets, opened for editing.

    source: path or bytes;  managed: the game's Managed folder (type trees);  dependency_root: the folder holding
    globalgamemanagers.assets (the game's BSG_Data), where the MonoScripts live."""

    def __init__(self, source, managed, dependency_root):
        UnityPy, TypeTreeGenerator = _unity()
        data = open(source, "rb").read() if isinstance(source, str) else bytes(source)
        self.before = data
        self.env = UnityPy.load(data)
        self.env.path = dependency_root
        self.asset = next(iter(self.env.files.values()))
        generator = TypeTreeGenerator(self.asset.unity_version)
        if not os.path.isfile(os.path.join(managed or "", "Assembly-CSharp.dll")):
            raise ValueError("could not find the game's Managed folder (needed for squadron prefabs): %s" % managed)
        generator.load_local_dll_folder(managed)
        self.env.typetree_generator = generator
        self.objects = self.asset.objects
        self.script_props, self.script_refs = {}, {}
        self.scripts = self._script_names(UnityPy, dependency_root)
        self.added, self.removed = set(), set()
        self.added_types = []
        self.menu_notes = []
        self._fields = {}
        self.roots = {}
        for obj in list(self.objects.values()):
            if obj.type.name == "GameObject":
                name = self.read(obj.path_id).get("m_Name", "")
                if name.endswith("Squadron") or name == "CylonScorpionSentry":
                    if self.find_component(obj.path_id, "FighterSquadron") is not None:
                        self.roots[name] = obj.path_id

    # ---- reading ----
    def _script_names(self, UnityPy, dependency_root):
        names = {}
        for index, external in enumerate(self.asset.externals, 1):
            path = os.path.join(dependency_root, os.path.basename(external.path))
            if not os.path.isfile(path):
                continue
            env = UnityPy.load(path)
            for obj in env.objects:
                if obj.type.name == "MonoScript":
                    fields = obj.read_typetree()
                    names[(index, obj.path_id)] = fields.get("m_ClassName", "")
                    props = fields.get("m_PropertiesHash")
                    if isinstance(props, dict) and "bytes[0]" in props:
                        props = bytes(props["bytes[%d]" % i] for i in range(16))
                    self.script_props[(index, obj.path_id)] = props
                    if fields.get("m_AssemblyName") == "Assembly-CSharp.dll" and not fields.get("m_Namespace"):
                        self.script_refs.setdefault(fields.get("m_ClassName", ""), (index, obj.path_id))
        if not names:
            raise ValueError("no MonoScripts found: %s needs globalgamemanagers.assets" % dependency_root)
        return names

    def read(self, pid):
        if pid not in self._fields:
            self._fields[pid] = self.objects[pid].read_typetree()
        return copy.deepcopy(self._fields[pid])

    def write(self, pid, fields):
        self.objects[pid].save_typetree(fields)
        self._own_reader(self.objects[pid])
        self._fields[pid] = copy.deepcopy(fields)

    @staticmethod
    def _own_reader(obj):
        """UnityPy reads an object from its place in the source file and only saves obj.data; point the object at
        its new bytes so get_raw_data / read_typetree in this session see the edit (and a clone not its template)"""
        from UnityPy.streams import EndianBinaryReader
        obj.reader = EndianBinaryReader(bytes(obj.data), obj.reader.endian)
        obj.byte_start, obj.byte_size = 0, len(obj.data)

    def kind(self, pid):
        """class name of an object; the script's class name for a MonoBehaviour"""
        obj = self.objects[pid]
        if obj.type.name != "MonoBehaviour":
            return obj.type.name
        raw = obj.get_raw_data()
        file_id, path_id = struct.unpack_from("<iq", raw, 16)
        return self.scripts.get((file_id, path_id), "MonoBehaviour")

    def components(self, go):
        return [c["component"]["m_PathID"] for c in self.read(go)["m_Component"]]

    def find_component(self, go, kind):
        return next((c for c in self.components(go) if c in self.objects and self.kind(c) == kind), None)

    def transform(self, go):
        return next(c for c in self.components(go) if self.kind(c) in TRANSFORMS)

    def children(self, go):
        return [self.read(t)["m_GameObject"]["m_PathID"] for t in
                [c["m_PathID"] for c in self.read(self.transform(go))["m_Children"]]]

    def child(self, go, name):
        return next((c for c in self.children(go) if self.read(c)["m_Name"] == name), None)

    def parent(self, go):
        father = self.read(self.transform(go))["m_Father"]["m_PathID"]
        return self.read(father)["m_GameObject"]["m_PathID"] if father else None

    def tree(self, go, out=None):
        """every object of a GameObject tree: the GameObjects and all their components"""
        out = [] if out is None else out
        out.append(go)
        out.extend(self.components(go))
        for child in self.children(go):
            self.tree(child, out)
        return out

    def root(self, name):
        key = lambda t: t.lower().replace(" ", "").replace("_", "")
        short = lambda t: key(t).replace("squadron", "").replace("colonial", "").replace("cylon", "").replace("sagittaron", "")
        hits = [n for n in self.roots if key(n) == key(name)] or \
               [n for n in self.roots if short(n) == short(name)] or \
               [n for n in self.roots if key(name) in key(n)]
        if len(hits) != 1:
            raise ValueError("%s squadron prefab matches %r. Prefabs: %s" % ("no" if not hits else "more than one",
                             name, ", ".join(sorted(self.roots))))
        return hits[0], self.roots[hits[0]]

    def squadron_type(self, name):
        _name, go = self.root(name)
        return self.read(self.find_component(go, "FighterSquadron"))["SquadronShipType"]

    def by_type(self, type_id):
        return next((n for n, go in self.roots.items()
                     if self.read(self.find_component(go, "FighterSquadron"))["SquadronShipType"] == type_id), None)

    # ---- menus ----
    def menus(self, name):
        """(actions GameObject, abilities submenu GameObject or None, the SubMenuAction that opens it or None)"""
        _name, go = self.root(name)
        actions = self.child(go, "Actions")
        if actions is None:
            raise ValueError("%s has no Actions menu" % name)
        for comp in self.components(actions):
            if self.kind(comp) == "SubMenuAction":
                fields = self.read(comp)
                if fields.get("descriptionID") == ABILITIES_TEXT:
                    return actions, self._submenu_go(fields["SubMenu"]["m_PathID"]), comp
        return actions, None, None

    def _submenu_go(self, pid):
        """SubMenu points at a GameObject (or, defensively, one of its components)"""
        return pid if self.objects[pid].type.name == "GameObject" else self.read(pid)["m_GameObject"]["m_PathID"]

    def used_slots(self, menu_go):
        slots = set()
        for comp in self.components(menu_go):
            fields = self.read(comp) if self.objects[comp].type.name == "MonoBehaviour" else {}
            if "radialMenuPosition" in fields and not fields.get("hideInMenu"):
                slots.add(fields["radialMenuPosition"])
        return slots

    def abilities(self, name):
        """[{key, label, action pid, menu ('main' / 'abilities'), slot}] of a squadron"""
        actions, sub, _opener = self.menus(name)
        out = []
        for menu_go, where in ((actions, "main"), (sub, "abilities")):
            if menu_go is None:
                continue
            for comp in self.components(menu_go):
                kind = self.kind(comp)
                for key, (label, action, _h, _i) in ABILITIES.items():
                    if kind == action:
                        out.append({"key": key, "label": label, "action": comp, "menu": where,
                                    "slot": self.read(comp).get("radialMenuPosition")})
        return out

    def catalogue(self):
        """{ability key: [squadron prefabs that have it]} (possible donors)"""
        out = {key: [] for key in ABILITIES}
        for name in sorted(self.roots):
            try:
                for item in self.abilities(name):
                    if name not in out[item["key"]]:
                        out[item["key"]].append(name)
            except ValueError:
                continue
        return out

    # ---- objects of a script no squadron uses yet (stealth) ----
    def _script_type(self, class_name):
        """index of the file's type entry for MonoBehaviour script `class_name`, added when missing. The script
        (in globalgamemanagers.assets) gets a script-type entry; the type's script ID is MD4(class name +
        "Assembly-CSharp.dll") and its type hash the MonoScript's m_PropertiesHash, as Unity writes them."""
        from deadlock_tools.weapons import _md4
        ref = self.script_refs.get(class_name)
        if ref is None:
            raise ValueError("no MonoScript for %s in globalgamemanagers.assets" % class_name)
        types, scripts = self.asset.types, self.asset.script_types
        for index, typ in enumerate(types):
            if typ.class_id == 114 and 0 <= typ.script_type_index < len(scripts):
                item = scripts[typ.script_type_index]
                if (item.local_serialized_file_index, item.local_identifier_in_file) == ref:
                    return index
        template_type = next(t for t in types if t.class_id == 114 and t.script_type_index >= 0)
        item = copy.copy(scripts[template_type.script_type_index])
        item.local_serialized_file_index, item.local_identifier_in_file = ref
        scripts.append(item)
        typ = copy.copy(template_type)
        typ.script_type_index = len(scripts) - 1
        typ.script_id = _md4(class_name.encode("utf-8") + b"Assembly-CSharp.dll")
        typ.old_type_hash = self.script_props[ref]
        types.append(typ)
        self.added_types.append(class_name)
        return len(types) - 1

    def _new_monobehaviour(self, class_name, go, tail):
        """a new MonoBehaviour of script `class_name` on `go`: header (m_GameObject, m_Enabled, m_Script, empty
        m_Name) plus the serialized fields `tail`; returns its pid"""
        type_index = self._script_type(class_name)
        file_id, path_id = self.script_refs[class_name]
        raw = struct.pack("<iq", 0, go) + b"\x01\0\0\0" + struct.pack("<iq", file_id, path_id) + struct.pack("<i", 0) + tail
        template = next(o for o in self.objects.values() if o.type.name == "MonoBehaviour")
        clone = copy.copy(template)
        clone.path_id = self._new_pid()
        clone.type_id = type_index
        clone.serialized_type = self.asset.types[type_index]
        clone.class_id = 114
        clone.data = raw
        clone._read_until = None
        self._own_reader(clone)
        self.objects[clone.path_id] = clone
        self.added.add(clone.path_id)
        self._fields.pop(clone.path_id, None)
        self._attach(go, clone.path_id)
        return clone.path_id

    def _new_child(self, parent, name, active=True, like=None):
        """an empty child GameObject (Transform only) of `parent`, modelled on GameObject `like`"""
        like = like or self.child(parent, "Actions") or parent
        fields = self.read(like)
        fields.update(m_Name=name, m_Component=[], m_IsActive=1 if active else 0)
        go = self._clone_object(like, fields)
        tr = self.read(self.transform(like))
        tr.update(m_GameObject={"m_FileID": 0, "m_PathID": go}, m_Children=[],
                  m_Father={"m_FileID": 0, "m_PathID": self.transform(parent)})
        for key, value in (("m_LocalPosition", {"x": 0.0, "y": 0.0, "z": 0.0}), ("m_LocalRotation", {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}),
                           ("m_LocalScale", {"x": 1.0, "y": 1.0, "z": 1.0})):
            if key in tr:
                tr[key] = value
        new_tr = self._clone_object(self.transform(like), tr)
        self._attach(go, new_tr)
        parent_tr = self.read(self.transform(parent))
        parent_tr["m_Children"].append({"m_FileID": 0, "m_PathID": new_tr})
        self.write(self.transform(parent), parent_tr)
        return go

    def add_stealth(self, name, battery=None, cooldown=None, active=None):
        """StealthModule on the squadron root (battery, de-stealth cooldown in turns, active at the start of a
        battle; two empty effect objects stand in for the Orion's, which live in its bundle) and a StealthAction in
        the Abilities submenu. Needs the custom-module DLL's CustomStealthFallback: squadrons have no Tech Bay."""
        battery = float(STEALTH_DEFAULTS["battery"] if battery is None else battery)
        cooldown = int(STEALTH_DEFAULTS["cooldown"] if cooldown is None else cooldown)
        active = bool(STEALTH_DEFAULTS["active"] if active is None else active)
        if not battery > 0 or cooldown < 0:
            raise ValueError("the stealth battery must be above 0 and the cooldown 0 or more turns")
        target_name, root = self.root(name)
        if self.find_component(root, "StealthModule") is not None:
            raise ValueError("%s already has a StealthModule" % target_name)
        menu = self._abilities_menu(target_name)
        free = [s for s in MENU_SLOTS if s and s not in self.used_slots(menu)]
        if not free:
            raise ValueError("the Abilities menu of %s is full (7 abilities)" % target_name)
        effect = self._new_child(root, STEALTH_EFFECTS[0], active=False)
        change = self._new_child(root, STEALTH_EFFECTS[1], active=False)
        module_tail = (struct.pack("<fi", battery, cooldown) + bytes([1 if active else 0]) + b"\0\0\0"
                       + struct.pack("<iq", 0, effect) + struct.pack("<iq", 0, change))
        module = self._new_monobehaviour("StealthModule", root, module_tail)
        # StealthAction serializes only ActionBase's fields: copy them from a stock action, then set the slot
        template = next((a["action"] for n in sorted(self.roots) for a in self.abilities(n) if a["key"] == "boost_firewalls"), None)
        if template is None:
            raise ValueError("no stock action to model the StealthAction on")
        action = self._new_monobehaviour("StealthAction", menu, bytes(self.objects[template].get_raw_data())[32:])
        fields = self.read(action)
        fields.update(radialMenuPosition=free[0], hideInMenu=0)
        self.write(action, fields)
        check = self.read(module)
        if (abs(check["batteryMax"] - battery) > 1e-4 or check["destealthCooldownTurns"] != cooldown
                or check["stealthEffect"]["m_PathID"] != effect or check["changeStealthEffect"]["m_PathID"] != change):
            raise ValueError("the new StealthModule did not read back")
        notes = self._take_menu_notes()
        return "%s: added Stealth (Abilities menu slot %d; battery %g, cooldown %d turn(s)%s%s)" % (
            target_name, free[0], battery, cooldown, ", active at battle start" if active else "",
            "; " + ", ".join(notes) if notes else "")

    def _take_menu_notes(self):
        notes, self.menu_notes = self.menu_notes, []
        return notes

    def ability_settings(self, name):
        """{ability key: {field: value}} for the squadron's abilities that have settings"""
        _target, root = self.root(name)
        have = {a["key"] for a in self.abilities(_target)}
        out = {}
        for key, (component, fields) in ABILITY_SETTINGS.items():
            comp = self.find_component(root, component)
            if key in have and comp is not None:
                values = self.read(comp)
                out[key] = {f: (bool(values[f]) if kind == "bool" else values[f]) for f, kind, _l, _lo, _hi in fields}
        return out

    def set_ability_settings(self, name, key, values):
        """change settings of one ability (see ABILITY_SETTINGS); returns a log line"""
        key = ability_key(key)
        if key not in ABILITY_SETTINGS:
            raise ValueError("%s has no settings" % ABILITIES[key][0])
        component, fields = ABILITY_SETTINGS[key]
        target, root = self.root(name)
        if key not in {a["key"] for a in self.abilities(target)}:
            raise ValueError("%s has no %s" % (target, ABILITIES[key][0]))
        comp = self.find_component(root, component)
        if comp is None:
            raise ValueError("%s has no %s component" % (target, component))
        spec = {f: (kind, lo, hi) for f, kind, _l, lo, hi in fields}
        current = self.read(comp)
        changes = []
        for field, value in values.items():
            if field not in spec:
                raise ValueError("%s settings are %s, not %r" % (ABILITIES[key][0], ", ".join(spec), field))
            kind, lo, hi = spec[field]
            if kind == "bool":
                value = 1 if value in (True, 1, "1", "true", "True", "yes", "on") else 0
            else:
                number = float(value)
                if kind == "int":
                    if not number.is_integer():
                        raise ValueError("%s needs a whole number" % field)
                    number = int(number)
                if not lo <= number <= hi:
                    raise ValueError("%s must be between %g and %g" % (field, lo, hi))
                value = number
            if current[field] != value and not (kind == "float" and abs(current[field] - value) < 1e-6):
                changes.append("%s %s -> %s" % (field, current[field], value))
                current[field] = value
        if not changes:
            return None
        self.write(comp, current)
        return "%s: %s %s" % (target, ABILITIES[key][0], ", ".join(changes))

    def stealth_settings(self, name):
        _name, root = self.root(name)
        module = self.find_component(root, "StealthModule")
        if module is None:
            return None
        fields = self.read(module)
        return {"battery": fields["batteryMax"], "cooldown": fields["destealthCooldownTurns"], "active": bool(fields["activeByDefault"])}

    # ---- object creation / removal ----
    def _new_pid(self):
        # never reuse an id this session has handed out or removed (a running high-water mark)
        self._high = max(getattr(self, "_high", 0), max(self.objects), max(self.removed, default=0)) + 1
        return self._high

    def _clone_object(self, template_pid, fields):
        template = self.objects[template_pid]
        clone = copy.copy(template)
        clone.path_id = self._new_pid()
        clone.data = None
        clone._read_until = None
        self.objects[clone.path_id] = clone
        self.added.add(clone.path_id)
        self.write(clone.path_id, fields)
        return clone.path_id

    def _remove_object(self, pid):
        self.objects.pop(pid)
        self._fields.pop(pid, None)
        if pid in self.added:
            self.added.discard(pid)
        else:
            self.removed.add(pid)

    def _attach(self, go, component):
        fields = self.read(go)
        fields["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": component}})
        self.write(go, fields)

    def _detach(self, go, component):
        fields = self.read(go)
        fields["m_Component"] = [c for c in fields["m_Component"] if c["component"]["m_PathID"] != component]
        self.write(go, fields)

    def references_to(self, pid, ignore=()):
        """objects (outside `ignore`) of the squadron trees that point at pid"""
        hits = []
        for go in self.roots.values():
            for obj in self.tree(go):
                if obj not in ignore and pid in _pointers(self.read(obj)):
                    hits.append(obj)
        return hits

    def _copy_component(self, donor_comp, target_go, donor_tree, mapping=None, **overrides):
        """copy a component from a donor squadron onto target_go. Pointers into the donor's own tree must be
        covered by `mapping` (its GameObject always is); pointers to shared assets are kept."""
        fields = self.read(donor_comp)
        mapping = dict(mapping or {})
        mapping[fields["m_GameObject"]["m_PathID"]] = target_go
        inside = [p for p in _pointers(fields) if p in donor_tree and p not in mapping]
        if inside:
            raise ValueError("%s refers to other parts of its donor squadron (%s); copying it is not supported"
                             % (self.kind(donor_comp), ", ".join(self.kind(p) for p in inside)))
        fields = _remap(fields, mapping)
        fields.update(overrides)
        pid = self._clone_object(donor_comp, fields)
        self._attach(target_go, pid)
        return pid

    def clone_tree(self, top, new_parent_transform, mapping=None, rename=None, forbid=()):
        """deep-copy a GameObject tree; returns {old pid: new pid}. Pointers between its objects are remapped,
        pointers out of it are kept, except into `forbid` (e.g. the rest of a donor squadron), which is refused.
        The new top transform's father is new_parent_transform (0 = none)."""
        objs = self.tree(top)
        mapping = dict(mapping or {})
        for pid in objs:
            mapping[pid] = self._new_pid()
        own_father = self.read(self.transform(top))["m_Father"]["m_PathID"]
        for pid in objs:
            bad = [p for p in _pointers(self.read(pid)) if p in forbid and p not in mapping and p != own_father]
            if bad:
                raise ValueError("%s refers to other parts of its donor squadron (%s); copying it is not supported"
                                 % (self.kind(pid), ", ".join(self.kind(p) for p in bad)))
        for pid in objs:
            fields = _remap(self.read(pid), mapping)
            if pid == top and rename:
                fields["m_Name"] = rename
            if self.kind(pid) in TRANSFORMS and self.read(pid)["m_GameObject"]["m_PathID"] == top:
                fields["m_Father"] = {"m_FileID": 0, "m_PathID": new_parent_transform}
            template = self.objects[pid]
            clone = copy.copy(template)
            clone.path_id = mapping[pid]
            clone.data = None
            clone._read_until = None
            self.objects[clone.path_id] = clone
            self.added.add(clone.path_id)
            self.write(clone.path_id, fields)
        if new_parent_transform:
            parent = self.read(new_parent_transform)
            parent["m_Children"].append({"m_FileID": 0, "m_PathID": mapping[self.transform(top)]})
            self.write(new_parent_transform, parent)
        return mapping

    def _remove_tree(self, top):
        parent = self.parent(top)
        mine = self.transform(top)
        for pid in self.tree(top):
            self._remove_object(pid)
        if parent is not None:
            fields = self.read(self.transform(parent))
            fields["m_Children"] = [c for c in fields["m_Children"] if c["m_PathID"] != mine]
            self.write(self.transform(parent), fields)

    # ---- squadron types ----
    def clone_squadron(self, donor, new_name, type_id):
        """copy a squadron prefab as `new_name` with FighterSquadron.SquadronShipType = type_id; returns its root pid"""
        if not new_name.endswith("Squadron"):
            raise ValueError("a squadron prefab name must end in 'Squadron' (the editor lists them by that)")
        if any(c in new_name for c in "\0\r\n/\\") or not new_name.strip():
            raise ValueError("invalid squadron prefab name %r" % new_name)
        if new_name in self.roots:
            raise ValueError("a squadron prefab named %s already exists" % new_name)
        if self.by_type(type_id) is not None:
            raise ValueError("squadron type %d is already used by %s" % (type_id, self.by_type(type_id)))
        _donor_name, go = self.root(donor)
        mapping = self.clone_tree(go, 0, rename=new_name)
        root = mapping[go]
        squadron = self.find_component(root, "FighterSquadron")
        fields = self.read(squadron)
        fields["SquadronShipType"] = int(type_id)
        self.write(squadron, fields)
        self.roots[new_name] = root
        return root

    def set_fighter(self, name, ship_id):
        """point a squadron prefab at fighter ship type `ship_id` (ShipInfo.ShipType and FighterSquadron.ShipType)"""
        _name, go = self.root(name)
        for kind in ("ShipInfo", "FighterSquadron"):
            comp = self.find_component(go, kind)
            fields = self.read(comp)
            fields["ShipType"] = int(ship_id)
            self.write(comp, fields)

    def fighter(self, name):
        _name, go = self.root(name)
        return self.read(self.find_component(go, "FighterSquadron"))["ShipType"]

    # ---- abilities ----
    def _abilities_menu(self, name, create=True):
        """the abilities submenu GameObject, created (with a Back button and a SubMenuAction) when missing"""
        actions, sub, _opener = self.menus(name)
        if sub is not None or not create:
            return sub
        _name, go = self.root(name)
        free = [s for s in (7, 6) if s not in self.used_slots(actions)]
        moving = None
        if not free:
            # main menu full (e.g. the Assault Raptor: Fire munitions 6, Paint target 7): move one of its abilities
            # into the new submenu and put the submenu button in its slot. Fire munitions stays if it can.
            movable = [a for a in self.abilities(name) if a["menu"] == "main" and a["slot"] in (7, 6)]
            if not movable:
                raise ValueError("%s has no Abilities submenu and all 8 slots of its main menu are taken by "
                                 "non-ability buttons" % name)
            moving = sorted(movable, key=lambda a: (a["key"] == "fire_missile", -a["slot"]))[0]
            free = [moving["slot"]]
        # templates: the squadron's own submenu (assign group) and its Back button
        opener = next((c for c in self.components(actions) if self.kind(c) == "SubMenuAction"), None)
        if opener is None:
            raise ValueError("%s has no submenu to model an Abilities menu on" % name)
        template_go = self._submenu_go(self.read(opener)["SubMenu"]["m_PathID"])
        back = self.find_component(template_go, "BackAction")
        template_transform = self.transform(template_go)
        go_fields = self.read(template_go)
        go_fields["m_Name"] = "Abilities"
        go_fields["m_Component"] = []
        new_go = self._clone_object(template_go, go_fields)
        tr = self.read(template_transform)
        tr["m_GameObject"] = {"m_FileID": 0, "m_PathID": new_go}
        tr["m_Children"] = []
        new_tr = self._clone_object(template_transform, tr)
        self._attach(new_go, new_tr)
        parent = self.read(self.transform(actions))
        parent["m_Children"].append({"m_FileID": 0, "m_PathID": new_tr})
        self.write(self.transform(actions), parent)
        if back is not None:
            fields = self.read(back)
            fields["m_GameObject"] = {"m_FileID": 0, "m_PathID": new_go}
            self._attach(new_go, self._clone_object(back, fields))
        if moving is not None:
            fields = self.read(moving["action"])
            fields.update(m_GameObject={"m_FileID": 0, "m_PathID": new_go}, radialMenuPosition=1)
            self.write(moving["action"], fields)
            self._detach(actions, moving["action"])
            self._attach(new_go, moving["action"])
            self.menu_notes.append("moved %s from main slot %d into the new Abilities menu (slot 1)" % (moving["label"], moving["slot"]))
        fields = self.read(opener)
        fields.update(m_GameObject={"m_FileID": 0, "m_PathID": actions}, radialMenuPosition=free[0], hideInMenu=0,
                      SubMenu={"m_FileID": 0, "m_PathID": new_go}, descriptionID=ABILITIES_TEXT, Icon=SUBMENU_ICON)
        self._attach(actions, self._clone_object(opener, fields))
        return new_go

    def add_ability(self, name, key, donor=None, settings=None):
        """copy ability `key` from a donor squadron (default: the first that has it) into the squadron's
        Abilities submenu (created if needed). Returns a log line."""
        key = ability_key(key)
        label, action, helpers, _ids = ABILITIES[key]
        target_name, target = self.root(name)
        if any(a["key"] == key for a in self.abilities(target_name)):
            raise ValueError("%s already has %s" % (target_name, label))
        if key == "stealth":
            return self.add_stealth(target_name, **(settings or {}))
        donors = self.catalogue()[key]
        if donor is None:
            donors = [d for d in donors if d != target_name]
            if not donors:
                raise ValueError("no squadron has %s to copy it from" % label)
            donor = donors[0]
        donor_name, donor_go = self.root(donor)
        donor_item = next((a for a in self.abilities(donor_name) if a["key"] == key), None)
        if donor_item is None:
            raise ValueError("%s has no %s. Squadrons with it: %s" % (donor_name, label, ", ".join(donors) or "none"))
        donor_tree = set(self.tree(donor_go))
        log = []
        menu = self._abilities_menu(target_name)          # first, so a full menu stops before anything is copied
        free = [s for s in MENU_SLOTS if s and s not in self.used_slots(menu)]
        if not free:
            raise ValueError("the Abilities menu of %s is full (7 abilities)" % target_name)
        for helper in helpers:
            if self.find_component(target, helper) is None:
                source = self.find_component(donor_go, helper)
                if source is None:
                    raise ValueError("%s has %s but no %s to copy" % (donor_name, label, helper))
                self._copy_component(source, target, donor_tree)
                log.append("copied %s" % helper)
        if key == "fire_missile":
            log += self._ensure_launcher(target_name, target, donor_go, donor_tree)
        self._copy_component(donor_item["action"], menu, donor_tree, radialMenuPosition=free[0], hideInMenu=0)
        log = self._take_menu_notes() + log
        return "%s: added %s from %s (Abilities menu slot %d%s)" % (target_name, label, donor_name, free[0],
                                                                  "; " + ", ".join(log) if log else "")

    def fire_buttons(self, name):
        """the squadron's FireMissileAction buttons in hierarchy order: at battle start button i fires munition i
        (MJDBEJECJMN.OBEGIIMCLHP pairs GetComponentsInChildren<FireMissileAction> with ShipInfo's munitions)"""
        _name, root = self.root(name)
        return [p for p in self.tree(root) if self.kind(p) == "FireMissileAction"]

    def sync_fire_buttons(self, name, count):
        """one Fire munitions button per munition: adds buttons (copies of the first, in the Abilities submenu, so they
        come after it in hierarchy order) or removes extra ones (last first); with munitions but no button at all,
        adds the Fire munitions ability (button + MissileLauncher). Returns log lines."""
        target_name, _root = self.root(name)
        buttons = self.fire_buttons(target_name)
        log = []
        if count <= 0 or len(buttons) == count:
            return log
        if not buttons:
            log.append(self.add_ability(target_name, "fire_missile"))
            buttons = self.fire_buttons(target_name)
            if len(buttons) == count:
                return log
        while len(buttons) > count:
            extra = buttons.pop()
            go = self.read(extra)["m_GameObject"]["m_PathID"]
            self._detach(go, extra)
            self._remove_object(extra)
            log.append("%s: removed a Fire munitions button (%d munition(s))" % (target_name, count))
        added = 0
        while len(buttons) < count:
            menu = self._abilities_menu(target_name)
            free = [slot for slot in MENU_SLOTS if slot and slot not in self.used_slots(menu)]
            if not free:
                raise ValueError("no room in the Abilities menu of %s for another Fire munitions button" % target_name)
            fields = self.read(buttons[0])
            fields.update(m_GameObject={"m_FileID": 0, "m_PathID": menu}, radialMenuPosition=free[0], hideInMenu=0)
            pid = self._clone_object(buttons[0], fields)
            self._attach(menu, pid)
            buttons = self.fire_buttons(target_name)
            added += 1
        if added:
            notes = self._take_menu_notes()
            log.append("%s: added %d Fire munitions button(s) in the Abilities menu, one per munition (%d)%s"
                       % (target_name, added, count, "; " + ", ".join(notes) if notes else ""))
        empty = self.menus(target_name)
        if empty[1] is not None and all(self.kind(c) in TRANSFORMS + ("BackAction",) for c in self.components(empty[1])):
            self._detach(empty[0], empty[2]); self._remove_object(empty[2]); self._remove_tree(empty[1])
            log.append("%s: removed the empty Abilities menu" % target_name)
        return log

    def _ensure_launcher(self, target_name, target, donor_go, donor_tree):
        """FireMissileAction fires through a MissileLauncher in the squadron's Weapon tree"""
        if any(self.kind(p) == "MissileLauncher" for p in self.tree(target)):
            return []
        source = next((p for p in donor_tree if self.kind(p) == "MissileLauncher"), None)
        if source is None:
            raise ValueError("the donor has no MissileLauncher to copy")
        launcher_go = self.read(source)["m_GameObject"]["m_PathID"]
        parent = self.child(target, "Weapon") or target
        self.clone_tree(launcher_go, self.transform(parent), forbid=donor_tree)
        return ["copied the MissileLauncher (%s)" % self.read(launcher_go)["m_Name"]]

    def remove_ability(self, name, key):
        """remove ability `key` (its action; its helper when nothing else uses it; an emptied Abilities menu)"""
        key = ability_key(key)
        label, _action, helpers, _ids = ABILITIES[key]
        target_name, target = self.root(name)
        items = [a for a in self.abilities(target_name) if a["key"] == key]
        if not items:
            raise ValueError("%s has no %s" % (target_name, label))
        log = []
        for item in items:
            go = self.read(item["action"])["m_GameObject"]["m_PathID"]
            refs = self.references_to(item["action"], ignore={go})
            if refs:
                raise ValueError("%s's %s is referenced by %s; not removed" % (target_name, label, ", ".join(self.kind(r) for r in refs)))
            self._detach(go, item["action"])
            self._remove_object(item["action"])
        remaining = {a["key"] for a in self.abilities(target_name)}
        still_needed = {h for k in remaining for h in ABILITIES[k][2]}
        for helper in helpers:
            comp = self.find_component(target, helper)
            if comp is None or helper in still_needed or helper in KEEP_HELPERS:
                continue
            if self.references_to(comp, ignore={target}):
                continue
            self._detach(target, comp)
            self._remove_object(comp)
            log.append("removed %s" % helper)
        if key == "stealth":
            for effect in STEALTH_EFFECTS:
                go = self.child(target, effect)
                if go is not None and not self.references_to(go, ignore=set(self.tree(go))):
                    self._remove_tree(go)
        actions, sub, opener = self.menus(target_name)
        if sub is not None and all(self.kind(c) in TRANSFORMS + ("BackAction",) for c in self.components(sub)):
            self._detach(actions, opener)
            self._remove_object(opener)
            self._remove_tree(sub)
            log.append("removed the empty Abilities menu")
        return "%s: removed %s%s" % (target_name, label, "; " + ", ".join(log) if log else "")

    # ---- output ----
    def save(self):
        """the edited file's bytes, after checking that only the intended objects were added or removed"""
        from deadlock_tools.repair_shortcuts import _object_sizes
        data = self.asset.save()
        before, after = _object_sizes(self.before), _object_sizes(data)
        expected = (set(before) - self.removed) | self.added
        lost = [pid for pid, size in before.items() if pid not in self.removed and size and not after.get(pid)]
        if set(after) != expected or lost:
            raise RuntimeError("refusing to write %s: %d object(s) unexpectedly lost or added"
                               % (ASSET_FILE, len(lost) or len(set(after) ^ expected)))
        return data


def info_ids(entry_ids, key, donor_ids=()):
    """AbilityIDs after adding ability `key`: its text ID (the donor's choice for Fire munitions) is appended"""
    ids = ABILITIES[key][3]
    if not ids:
        return list(entry_ids)
    pick = next((i for i in donor_ids if i in ids), ids[0])
    return list(entry_ids) + ([] if any(i in entry_ids for i in ids) else [pick])


def info_ids_without(entry_ids, key):
    return [i for i in entry_ids if i not in ABILITIES[key][3]]


# ---- whole workflows (level files + prefabs together); used by `shipdb` and the GUI ----
def _table_levels(S, levels):
    from deadlock_tools import squadron_table as st
    out = {}
    for name, data in levels.items():
        try:
            found = st.find(S, data)
        except Exception:
            continue
        if found:
            out[name] = found[3]
    if not out:
        raise ValueError("no level file holds the SquadronDataTable")
    return out


def next_type_id(S, prefabs, levels, enum_ids=()):
    """the next free squadron type id (above every table, prefab and enum id)"""
    used = set(enum_ids)
    for res in _table_levels(S, levels).values():
        used |= {e["id"] for e in res["objects"]}
    for go in prefabs.roots.values():
        used.add(prefabs.read(prefabs.find_component(go, "FighterSquadron"))["SquadronShipType"])
    return max(used) + 1


def create_type(S, prefabs, levels, donor, prefab_name, table_name, type_id, changes=None):
    """Copy squadron prefab `donor` as a new squadron type.

    levels: {level file name: bytes} (at least the SquadronDataTable copies and level9 with the SquadronFactory).
    Clones the prefab in `prefabs`, appends a copy of the donor's table entry (plus `changes`) to every table and
    registers the prefab in the SquadronFactory. Returns ({file name: new bytes} of the changed levels, log).
    The DLL enum member is the caller's job (ship_types.add(..., squadron=True))."""
    import types
    from deadlock_tools import shipdb as sd, squadron_table as st
    donor_name, _go = prefabs.root(donor)
    donor_type = prefabs.squadron_type(donor_name)
    tables = _table_levels(S, levels)
    if not any(any(e["id"] == donor_type for e in res["objects"]) for res in tables.values()):
        raise ValueError("%s (squadron type %d) has no SquadronDataTable entry to copy" % (donor_name, donor_type))
    factory_file = None
    db = types.SimpleNamespace(S=S)
    for name, data in levels.items():
        try:
            _b, _base, parsed = sd.parse_factory(db, data, "SquadronFactory")
        except LookupError:
            continue
        factory_file, factory = name, parsed
    if factory_file is None:
        raise ValueError("no level file holds the SquadronFactory (level9)")
    donor_entry = next((e for e in factory["objects"] if int(e["id"]) == donor_type), None)
    if donor_entry is None:
        raise ValueError("the SquadronFactory has no prefab for %s (type %d)" % (donor_name, donor_type))
    if donor_entry["prefab"][1][1] != prefabs.roots[donor_name]:
        raise ValueError("the SquadronFactory's prefab for type %d is not %s; the files do not belong together"
                         % (donor_type, donor_name))
    root = prefabs.clone_squadron(donor_name, prefab_name, type_id)
    out, log = {}, ["%s: new prefab %s (squadron type %d), a copy of %s" % (ASSET_FILE, prefab_name, type_id, donor_name)]
    for name in tables:
        new, lines = st.add_type(S, levels[name], donor_type, type_id, table_name, changes)
        out[name] = new
        if lines and not any(l.startswith("SquadronDataTable") for l in log):
            log.append("SquadronDataTable (%s): %s" % (", ".join(sorted(tables)), "; ".join(lines)))
    file_id = donor_entry["prefab"][1][0]
    data = out.get(factory_file, levels[factory_file])
    new, added = sd.append_factory_entry(db, data, "SquadronFactory", type_id, table_name,
                                         {"prefab": ("PPtr", (file_id, root))})
    if added:
        out[factory_file] = new
        log.append("%s: SquadronFactory %d -> %s" % (factory_file, type_id, prefab_name))
    return out, log


def edit_abilities(S, prefabs, levels, squadron, add=(), remove=(), fighter=None):
    """Add [(key, donor or None[, settings])] / remove [key] abilities on a squadron prefab and keep its SquadronDataTable entry's
    info-panel AbilityIDs in step (Fire munitions also gives an entry without munition slots the donor's).
    fighter: optional {"read_bundle": f, "enum_ids": ShipType enum ids}; with it a squadron whose fighter is shared
    gets its own fighter copy for those munitions, and fighter["bundles"] / fighter["enum"] receive the new bundle
    and ShipType member. Returns ({file name: new bytes} of the changed levels, log)."""
    from deadlock_tools import squadron_table as st
    name, _go = prefabs.root(squadron)
    type_id = prefabs.squadron_type(name)
    log, table_changes = [], {}
    tables = _table_levels(S, levels)
    out = {}
    any_res = next(iter(tables.values()))
    current = next((e for e in any_res["objects"] if e["id"] == type_id), None)
    ids = list(current["AbilityIDs"]) if current else []
    munitions = list(current["MunitionSlots"]) if current else []
    for key in remove:
        key = ability_key(key)
        log.append(prefabs.remove_ability(name, key))
        ids = info_ids_without(ids, key)
    for item in add:
        key, donor = item[0], item[1]
        settings = item[2] if len(item) > 2 else None
        key = ability_key(key)
        donors = [d for d in prefabs.catalogue()[key] if d != name]
        donor_name = None if key in BUILT_IN else (prefabs.root(donor)[0] if donor else (donors[0] if donors else None))
        log.append(prefabs.add_ability(name, key, donor_name, settings))
        donor_entry = next((e for e in any_res["objects"] if e["id"] == prefabs.squadron_type(donor_name)), None) if donor_name else None
        ids = info_ids(ids, key, donor_entry["AbilityIDs"] if donor_entry else ())
        if key == "fire_missile" and not munitions and donor_entry and donor_entry["MunitionSlots"]:
            munitions = list(donor_entry["MunitionSlots"])
            if current is not None and donor_entry["Faction"] != current["Faction"]:
                munitions = []              # foreign munitions would be swapped; choose some with set_munitions
                log.append("%s: no munitions copied (the donor is another faction); add some under Munitions" % name)
    if current is None:
        log.append("%s has no SquadronDataTable entry (story-only prefab); only the prefab was changed" % name)
        return out, log
    if ids != list(current["AbilityIDs"]):
        table_changes["AbilityIDs"] = ids
    if table_changes:
        for file_name, _res in tables.items():
            new, lines = st.apply(S, levels[file_name], {type_id: table_changes})
            out[file_name] = new
            if lines and not any(l.startswith("SquadronDataTable") for l in log):
                log.append("SquadronDataTable (%s): %s" % (", ".join(sorted(tables)), "; ".join(lines)))
    if any(ability_key(item[0]) == "fire_missile" for item in add) and munitions == list(current["MunitionSlots"]):
        log += prefabs.sync_fire_buttons(name, len(munitions))      # an existing list: one button per munition
    if munitions != list(current["MunitionSlots"]):
        merged = dict(levels); merged.update(out)
        if fighter is not None:
            changed, bundles, enum, lines = munitions_with_fighter(S, prefabs, merged, name, munitions,
                                                                   fighter["read_bundle"], fighter.get("enum_ids", ()))
            fighter.setdefault("bundles", {}).update(bundles); fighter.setdefault("enum", {}).update(enum)
        else:
            changed, lines = set_munitions(S, merged, type_id, munitions)
            lines += prefabs.sync_fire_buttons(name, len(munitions))
        out.update(changed); log += lines
    return out, log


def add_texts(resources, display_name=None, heading=None, description=None):
    """Add the new type's info-panel name ("default" table) and blueprint heading/description
    ("blueprint_descriptions") to resources.assets bytes. Returns (new bytes, {table field: text id})."""
    from deadlock_tools import localisation as loc
    catalogue = loc.Localisation(resources)
    ids = {}
    for field, table, text in (("descriptionID", "default", display_name),
                               ("descriptionHeaderID", "blueprint_descriptions", heading),
                               ("descriptionBodyID", "blueprint_descriptions", description)):
        if not text:
            continue
        info = catalogue.table(table)
        string_id = info["next_id"]
        catalogue = loc.Localisation(catalogue.update(table, catalogue.revision, string_id, True,
                                                      {language: text for language in info["languages"]}))
        ids[field] = string_id
    return catalogue.data, ids


def game_paths(levels, dll, managed=None, game_data=None):
    """(Managed folder, folder with globalgamemanagers.assets) for prefab editing"""
    levels = levels if os.path.isdir(levels) else os.path.dirname(levels)
    managed = managed or next((p for p in (os.path.join(levels, "Managed"), os.path.dirname(os.path.abspath(dll)))
                               if os.path.isfile(os.path.join(p, "UnityEngine.CoreModule.dll"))), None)
    game_data = game_data or next((p for p in (levels, os.path.dirname(managed or ""))
                                   if os.path.isfile(os.path.join(p, "globalgamemanagers.assets"))), None)
    if not managed:
        raise ValueError("could not find the game's Managed folder; pass --managed")
    if not game_data:
        raise ValueError("could not find globalgamemanagers.assets (the game's BSG_Data folder); pass --game-data")
    return managed, game_data


def cli(a):
    """shipdb squadronabilities [SQUADRON] | setsquadronability SQUADRON add|remove ABILITY [--donor SQ] |
    addsquadron DONOR PREFAB_NAME [--table-name N] [--display-name T] [--heading T] [--description T] [--type-id N]"""
    from deadlock_tools import shipdb as sd, ship_types as stypes
    S = sd.Schema(a.dll)
    folder = a.level0 if os.path.isdir(a.level0) else os.path.dirname(a.level0)
    managed, game_data = game_paths(folder, a.dll, a.managed, a.game_data)
    asset_source = sd.chain_source(a, os.path.join(folder, ASSET_FILE), a.out, quiet=a.cmd == "squadronabilities")
    if not os.path.isfile(asset_source):
        raise SystemExit("%s was not found in %s" % (ASSET_FILE, folder))
    prefabs = Prefabs(asset_source, managed, game_data)
    rest = list(a.args)
    if a.cmd == "squadronabilities":
        names = [prefabs.root(rest[0])[0]] if rest else sorted(prefabs.roots)
        for name in names:
            items = prefabs.abilities(name)
            print("%-38s type %-4d %s" % (name, prefabs.squadron_type(name), ", ".join(
                "%s (%s %s)" % (i["label"], i["menu"], i["slot"]) for i in items) or "-"))
            for key, values in prefabs.ability_settings(name).items():
                print("    %-14s %s" % (key, ", ".join("%s=%s" % kv for kv in values.items())))
        print("\nabilities and the squadrons they can be copied from:")
        for key, donors in prefabs.catalogue().items():
            print("  %-16s %-18s %s" % (key, ABILITIES[key][0], ", ".join(donors) or "-"))
        return
    level_sources = {}
    for f_ in sd.files_of(folder):
        base = os.path.basename(f_)
        if base.startswith("level") and not base.endswith(".resS"):
            level_sources[base] = sd.chain_source(a, f_, a.out, quiet=True)
    levels = {base: open(path, "rb").read() for base, path in level_sources.items()}
    os.makedirs(a.out, exist_ok=True)
    outputs = {}
    if a.cmd == "setsquadronbays":
        if len(rest) != 1:
            raise SystemExit('usage: shipdb setsquadronbays SQUADRON_PREFAB [--launch-like SQUADRON]   (default: the stock squadron its name contains)')
        name = prefabs.root(rest[0])[0]
        like = prefabs.squadron_type(prefabs.root(a.launch_like)[0]) if a.launch_like else guess_like(S, levels, prefabs, name)
        if like is None:
            raise SystemExit("could not tell which squadron %s launches like; pass --launch-like" % name)
        log = []
        _cli_launch_bays(a, S, levels, prefabs.squadron_type(name), like, folder, log)
        print("\n".join("[ok] " + line for line in log))
        return
    if a.cmd == "setsquadronability":
        if len(rest) >= 4 and rest[1].lower() == "set":
            values = {}
            for item in rest[3:]:
                field, sep, value = item.partition("=")
                if not sep:
                    raise SystemExit("expected FIELD=VALUE, got %r" % item)
                values[field] = value
            try:
                line = prefabs.set_ability_settings(rest[0], rest[2], values)
            except ValueError as e:
                raise SystemExit(str(e))
            data = prefabs.save()
            os.makedirs(a.out, exist_ok=True)
            with open(os.path.join(a.out, ASSET_FILE), "wb") as stream:
                stream.write(data)
            print("[ok] " + (line or "no change"))
            print("\nwrote %s to %s/" % (ASSET_FILE, a.out))
            return
        if len(rest) != 3 or rest[1].lower() not in ("add", "remove"):
            raise SystemExit('usage: shipdb setsquadronability "Viper Mk VII" add|remove ABILITY [--donor SQUADRON]\n'
                             '       shipdb setsquadronability "Sweeper" set chaff maxStock=20 releaseInterval=60\n'
                             "  abilities: " + ", ".join(ABILITIES) + "\n  settings: " + "; ".join(
                                 "%s: %s" % (k, ", ".join(f[0] for f in v[1])) for k, v in ABILITY_SETTINGS.items()))
        settings = {k: v for k, v in (("battery", a.battery), ("cooldown", a.cooldown),
                                      ("active", True if a.stealth_active else None)) if v is not None}
        add = [(rest[2], a.donor, settings)] if rest[1].lower() == "add" else []
        remove = [rest[2]] if rest[1].lower() == "remove" else []
        fighter = {"read_bundle": _cli_bundle_reader(a, folder), "enum_ids": stypes.members(_cli_dll_source(a))}
        try:
            changed, log = edit_abilities(S, prefabs, levels, rest[0], add, remove, fighter)
        except ValueError as e:
            raise SystemExit(str(e))
        outputs.update(changed)
        _cli_write_fighters(a, managed, fighter.get("bundles", {}), fighter.get("enum", {}), log)
        if any(ability_key(k[0]) == "stealth" for k in add):
            log.append(stealth_dll_note(_cli_dll_source(a)))
    else:  # addsquadron
        if len(rest) != 2:
            raise SystemExit('usage: shipdb addsquadron DONOR_PREFAB NewPrefabNameSquadron [--table-name "name"] '
                             '[--display-name T] [--heading T] [--description T] [--type-id N]')
        donor, prefab_name = rest
        enum_dll = os.path.join(os.path.dirname(a.out), "dll") if os.path.basename(a.out) == "levels" else os.path.join(a.out, "Managed")
        enum_source = os.path.join(enum_dll, os.path.basename(a.dll))
        enum_source = enum_source if not a.fresh and os.path.isfile(enum_source) else a.dll
        enum_ids = stypes.members(enum_source, squadron=True)
        type_id = a.type_id if a.type_id is not None else next_type_id(S, prefabs, levels, enum_ids)
        if type_id in enum_ids:
            raise SystemExit("squadron type %d is already in the DLL's squadron enum (%s)" % (type_id, enum_ids[type_id]))
        changes = {}
        if a.display_name or a.heading or a.description:
            loc_original = next((os.path.join(p, "resources.assets") for p in (folder, game_data)
                                 if os.path.isfile(os.path.join(p, "resources.assets"))), None)
            if not loc_original:
                raise SystemExit("resources.assets (the localisation) was not found in %s or %s" % (folder, game_data))
            loc_source = sd.chain_source(a, loc_original, a.out, quiet=True)
            resources, changes = add_texts(open(loc_source, "rb").read(), a.display_name, a.heading, a.description)
            outputs["resources.assets"] = resources
        try:
            changed, log = create_type(S, prefabs, levels, donor, prefab_name,
                                       a.table_name or prefab_name[:-len("Squadron")], type_id, changes)
        except ValueError as e:
            raise SystemExit(str(e))
        outputs.update(changed)
        os.makedirs(enum_dll, exist_ok=True)
        member = stypes.enum_name(prefab_name)
        target = os.path.join(enum_dll, os.path.basename(a.dll))
        result = stypes.add(managed, enum_source, target + ".tmp", {type_id: member}, config_stage(a), squadron=True)
        if result.returncode or not os.path.isfile(target + ".tmp"):
            raise SystemExit("could not add the squadron enum member; nothing was written:\n" + (result.stdout + result.stderr)[-4000:])
        os.replace(target + ".tmp", target)
        log.append("%s: squadron enum %s = %d" % (target, member, type_id))
        merged = dict(levels); merged.update(outputs)
        _cli_launch_bays(a, S, merged, type_id, prefabs.squadron_type(donor), folder, log)
    outputs[ASSET_FILE] = prefabs.save()
    for base, data in outputs.items():
        with open(os.path.join(a.out, base), "wb") as stream:
            stream.write(data)
    print("\n".join("[ok] " + line for line in log))
    print("\nwrote %s to %s/ (same file names). Install them with the rest of the work tree." % (", ".join(sorted(outputs)), a.out))


def _cli_out_dirs(a):
    """(bundle output folder, DLL output folder) beside the level output folder"""
    if os.path.basename(a.out) == "levels":
        root = os.path.dirname(a.out)
        return os.path.join(root, "bundles"), os.path.join(root, "dll")
    return os.path.join(a.out, "StreamingAssets"), os.path.join(a.out, "Managed")


def _cli_bundle_reader(a, folder):
    from deadlock_tools import config
    bundle_out, _dll = _cli_out_dirs(a)
    dirs = [d for d in ([] if a.fresh else [bundle_out]) + [config.setting("bundles", required=False),
            os.path.join(folder, "StreamingAssets")] if d]
    def read(name):
        for d in dirs:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                return open(path, "rb").read()
        raise ValueError("fighter bundle %s not found in %s" % (name, ", ".join(dirs)))
    return read


def _cli_dll_source(a):
    _bundles, dll_dir = _cli_out_dirs(a)
    staged = os.path.join(dll_dir, os.path.basename(a.dll))
    return staged if not a.fresh and os.path.isfile(staged) else a.dll


def _cli_launch_bays(a, S, levels, type_id, like_id, folder, log):
    """add a squadron type to its carriers' launch bays (like `like_id`); bundles go to the output bundle folder"""
    from deadlock_tools import bays, config
    bundle_dir, _dll = _cli_out_dirs(a)
    dirs = [d for d in ([] if a.fresh else [bundle_dir]) + [config.setting("bundles", required=False),
            os.path.join(folder, "StreamingAssets")] if d]
    find = lambda name: next((os.path.join(d, name) for d in dirs if os.path.isfile(os.path.join(d, name))), None)
    jobs, lines = plan_launch_bays(S, levels, type_id, like_id, find)
    os.makedirs(bundle_dir, exist_ok=True)
    for asset, src, spec in jobs:
        bays.apply(src, os.path.join(bundle_dir, asset), spec, encrypt=not src.endswith(".dec"))
    log += lines + (["launch bay bundles written to %s/" % bundle_dir] if jobs else [])


def _cli_write_fighters(a, managed, bundles, enum, log):
    """write fighter bundle copies and add their ShipType enum members (DLL output folder)"""
    from deadlock_tools import ship_types as stypes
    if not bundles and not enum:
        return
    bundle_dir, dll_dir = _cli_out_dirs(a)
    os.makedirs(bundle_dir, exist_ok=True)
    for name, data in bundles.items():
        with open(os.path.join(bundle_dir, name), "wb") as stream:
            stream.write(data)
        log.append("%s: fighter bundle written" % os.path.join(bundle_dir, name))
    if enum:
        os.makedirs(dll_dir, exist_ok=True)
        target = os.path.join(dll_dir, os.path.basename(a.dll))
        result = stypes.add(managed, _cli_dll_source(a), target + ".tmp", enum, config_stage(a))
        if result.returncode or not os.path.isfile(target + ".tmp"):
            raise SystemExit("could not add the fighter ShipType enum member:\n" + (result.stdout + result.stderr)[-4000:])
        os.replace(target + ".tmp", target)
        log.append("%s: ShipType enum %s" % (target, ", ".join("%s = %d" % (n, i) for i, n in enum.items())))


def stealth_dll_note(dll):
    """a reminder when the DLL the game will load lacks the stealth fallback (squadrons have no Tech Bay)"""
    try:
        has = b"CustomStealthFallback" in open(dll, "rb").read()
    except OSError:
        has = False
    return ("stealth: %s has the Tech Bay fallback" % os.path.basename(dll) if has else
            "NOTE: squadron stealth needs the custom-module DLL with CustomStealthFallback; %s does not have it yet. "
            "Rebuild it (Save ship on a ship with custom modules, or scripts/build_custom_modules.sh) before playing."
            % dll)


def config_stage(a):
    from deadlock_tools import config
    return config.work_dir("stage")


# ---- squadron munitions (level data only; no prefab change) ----
MAX_MUNITIONS = 8            # a sanity cap; stock squadrons carry one munition type


def munition_types(S, levels):
    """{munition id: entry} from the first level file with the MissileDataTable"""
    from deadlock_tools import shipdb as sd
    for data in levels.values():
        try:
            _off, res = sd.load_missiles(S, data)
        except LookupError:
            continue
        return {e["id"]: e for e in res["objects"]}
    raise ValueError("no level file holds the MissileDataTable (munition types)")


def _ship_table(S, data):
    """(pid, offset, parsed) of the ShipDataTable in one level file, or None"""
    from deadlock_tools import shipdb as sd
    ti = S.typedef_index("ShipDataTable")
    for pid, off, size, cid, _e in sd.read_objects(data):
        if cid != 114 or size <= 5000:
            continue
        reader = sd.Reader(S, data[off:off + size], 32)
        try:
            res = reader.obj(ti)
        except Exception:
            continue
        if reader.p == size and res.get("objects"):
            return pid, off, res
    return None


def set_munitions(S, levels, type_key, munitions):
    """Give squadron type `type_key` (id or table name) the munition types `munitions` (ids, in slot order).

    The squadron's SquadronDataTable.MunitionSlots is the list the game hands to ShipInfo.ConfigureMunitions when it
    spawns the squadron. That checks each munition's faction (a foreign one is swapped for the faction default, with
    an error in the log) and compares the count with MunitionSlots of the squadron's fighter in the ship database
    (logging a mismatch). The fighter count is kept in step when every squadron type using that fighter agrees.
    Returns ({file name: new bytes}, log)."""
    import struct
    from deadlock_tools import squadron_table as st
    tables = _table_levels(S, levels)
    any_res = next(iter(tables.values()))
    entry = st.entry(any_res, type_key)
    known = munition_types(S, levels)
    munitions = [int(m) for m in munitions]
    if len(munitions) > MAX_MUNITIONS:
        raise ValueError("at most %d munition types per squadron" % MAX_MUNITIONS)
    for m in munitions:
        if m not in known:
            raise ValueError("unknown munition type %d. Types: %s" % (m, ", ".join("%s (%d)" % (e["name"], i) for i, e in sorted(known.items()))))
        if known[m]["Faction"] != entry["Faction"]:
            raise ValueError("%s is a faction %d munition; %s is faction %d (the game would swap it for its faction default)"
                             % (known[m]["name"], known[m]["Faction"], entry["name"], entry["Faction"]))
    out, log = {}, []
    if munitions != list(entry["MunitionSlots"]):
        for name in tables:
            new, lines = st.apply(S, levels[name], {entry["id"]: {"MunitionSlots": munitions}})
            out[name] = new
        log.append("SquadronDataTable (%s): %s munitions %s -> %s" % (", ".join(sorted(tables)), entry["name"],
                   [known[m]["name"] for m in entry["MunitionSlots"] if m in known] or "none",
                   [known[m]["name"] for m in munitions] or "none"))
    # keep the fighter's ship-database slot count in step
    after = {e["id"]: (munitions if e["id"] == entry["id"] else e["MunitionSlots"]) for e in any_res["objects"]}
    sharing = [e for e in any_res["objects"] if e["ShipType"] == entry["ShipType"]]
    counts = {len(after[e["id"]]) for e in sharing}
    if len(counts) > 1:
        log.append("WARNING: squadron types sharing fighter ship %d carry different munition counts (%s); the game logs a "
                   "slot mismatch for some of them but still loads every munition"
                   % (entry["ShipType"], ", ".join("%s %d" % (e["name"], len(after[e["id"]])) for e in sharing)))
        return out, log
    count = counts.pop()
    for name, data in levels.items():
        data = out.get(name, data)
        found = _ship_table(S, data)
        if not found:
            continue
        _pid, off, res = found
        ship = next((s for s in res["objects"] if s["id"] == entry["ShipType"]), None)
        if ship is None or ship.get("MunitionSlots") == count:
            continue
        pos, _code = ship["_off"]["MunitionSlots"]
        buf = bytearray(data)
        struct.pack_into("<i", buf, off + pos, count)
        out[name] = bytes(buf)
        if not any(l.startswith("ship database") for l in log):
            log.append("ship database: fighter %s MunitionSlots %s -> %d" % (ship["name"], ship.get("MunitionSlots"), count))
    return out, log


def fighter_users(S, levels, ship_id):
    """squadron table entries whose fighter is ship `ship_id`"""
    res = next(iter(_table_levels(S, levels).values()))
    return [e for e in res["objects"] if e["ShipType"] == ship_id]


def needs_own_fighter(S, levels, type_key, munitions):
    """True when giving squadron type `type_key` len(munitions) munitions would leave it disagreeing with another
    squadron type on the same fighter (their fighter's ship-database MunitionSlots can only match one count)"""
    from deadlock_tools import squadron_table as st
    entry = st.entry(next(iter(_table_levels(S, levels).values())), type_key)
    others = [e for e in fighter_users(S, levels, entry["ShipType"]) if e["id"] != entry["id"]]
    return any(len(e["MunitionSlots"]) != len(munitions) for e in others)


def next_ship_id(S, levels, enum_ids=()):
    used = set(enum_ids)
    for data in levels.values():
        found = _ship_table(S, data)
        if found:
            used |= {int(s["id"]) for s in found[2]["objects"]}
    return max(used) + 1


def _copy_ship_row(S, data, donor_id, new_id, new_name):
    """append a copy of ship row `donor_id` as `new_id` / `new_name` to the ShipDataTable in one level file"""
    import struct
    from deadlock_tools import shipdb as sd
    found = _ship_table(S, data)
    if not found:
        return data, False
    pid, off, res = found
    ships = res["objects"]
    if any(int(s["id"]) == int(new_id) for s in ships):
        return data, False
    donor = next((s for s in ships if int(s["id"]) == int(donor_id)), None)
    if donor is None:
        raise ValueError("ship %d is missing from a ship table" % donor_id)
    index = ships.index(donor)
    size = next(sz for p, o, sz, _c, _e in sd.read_objects(data) if p == pid)
    mono = bytes(data[off:off + size])
    row_start = donor["_off"]["name"][0]
    array_end = res["_off"]["CrewVeterancyRankInfo"][0]
    row_end = ships[index + 1]["_off"]["name"][0] if index + 1 < len(ships) else array_end
    id_pos = donor["_off"]["id"][0]
    encoded = str(new_name).encode("utf-8")
    name_field = struct.pack("<i", len(encoded)) + encoded
    name_field += b"\0" * ((-len(name_field)) % 4)
    row = name_field + struct.pack("<i", int(new_id)) + mono[id_pos + 4:row_end]
    new_mono = bytearray(mono[:array_end] + row + mono[array_end:])
    struct.pack_into("<i", new_mono, res["_off"]["objects"][0], len(ships) + 1)
    updated = sd.resize_object(bytes(data), pid, bytes(new_mono))
    check = _ship_table(S, updated)
    copy_ = [s for s in check[2]["objects"] if int(s["id"]) == int(new_id)] if check else []
    if len(copy_) != 1 or copy_[0]["name"] != str(new_name):
        raise ValueError("the copied fighter row did not read back")
    def plain(value, top=True):                     # nested records carry their own offsets too
        if isinstance(value, dict):
            return {k: plain(v, False) for k, v in value.items() if k != "_off" and not (top and k in ("name", "id"))}
        if isinstance(value, list):
            return [plain(v, False) for v in value]
        return value
    if plain(copy_[0]) != plain(donor):
        raise ValueError("the copied fighter row is not a copy of %s" % donor["name"])
    return updated, True


def copy_fighter(S, prefabs, levels, squadron, ship_id, ship_name, bundle_raw, bundle_name):
    """Give a squadron type its own fighter: a copy of its current fighter as ship `ship_id`.

    Copies the fighter's ship-database row and its ShipFactory (-> the new bundle), ProjectionFactory,
    FleetModelFactory and ShipImageDataTable entries in every level file, copies the fighter bundle (`bundle_raw`,
    the old fighter's bundle) with the new ShipType and its own CAB names, and points the squadron prefab and its
    SquadronDataTable entry at the copy. Returns ({level: bytes}, new bundle bytes, log). The ShipType enum member is
    the caller's job (ship_types.add)."""
    import types
    from deadlock_tools import shipdb as sd, squadron_table as st, weapons as wmod
    name, _go = prefabs.root(squadron)
    type_id = prefabs.squadron_type(name)
    old = prefabs.fighter(name)
    entry = st.entry(next(iter(_table_levels(S, levels).values())), type_id)
    if entry["ShipType"] != old:
        raise ValueError("%s's prefab uses fighter %d but its table entry says %d" % (name, old, entry["ShipType"]))
    db = types.SimpleNamespace(S=S)
    out, log = {}, []
    for level_name, data in levels.items():
        new, copied = _copy_ship_row(S, data, old, ship_id, ship_name)
        for factory in ("ProjectionFactory", "FleetModelFactory"):
            try:
                _b, _o, parsed = sd.parse_factory(db, new, factory)
            except LookupError:
                continue
            donor = next((e for e in parsed["objects"] if int(e["id"]) == old), None)
            if donor is not None:
                new, _added = sd.append_factory_entry(db, new, factory, ship_id, ship_name, donor)
        try:
            new, _added = sd.append_ship_image_entry(db, new, ship_id, ship_name, old)
        except (LookupError, ValueError):
            pass                                    # a scene without art for this fighter
        new, _added = sd.append_ship_factory_entry(db, new, ship_id, ship_name, bundle_name)
        if new != data:
            out[level_name] = new
    if not out:
        raise ValueError("no level file holds the ship table for fighter %d" % old)
    log.append("ship database (%s): fighter %s (%d), a copy of ship %d, with its own bundle %s"
               % (", ".join(sorted(out)), ship_name, ship_id, old, bundle_name))
    merged = dict(levels); merged.update(out)
    for level_name in _table_levels(S, merged):
        merged[level_name], _lines = st.apply(S, merged[level_name], {type_id: {"ShipType": ship_id}})
        out[level_name] = merged[level_name]
    prefabs.set_fighter(name, ship_id)
    log.append("%s: squadron type %s now uses fighter %d" % (name, entry["name"], ship_id))
    bundle = wmod.rename_bundle_asset(bundle_raw, bundle_name, ship_id)
    return out, bundle, log


def fighter_bundle_name(S, levels, ship_id):
    """the battle bundle a fighter ship loads (ShipFactory assetName)"""
    import types
    from deadlock_tools import shipdb as sd
    for data in levels.values():
        try:
            _b, _o, parsed = sd.parse_factory(types.SimpleNamespace(S=S), data, "ShipFactory")
        except LookupError:
            continue
        hit = next((e for e in parsed["objects"] if int(e["id"]) == int(ship_id)), None)
        if hit:
            return hit["assetName"]
    raise ValueError("no ShipFactory entry for fighter ship %d" % ship_id)


def munitions_with_fighter(S, prefabs, levels, squadron, munitions, read_bundle, ship_enum_ids=()):
    """set_munitions for a squadron prefab; when its fighter is shared with a squadron type that would end up with
    a different munition count, the squadron first gets its own copy of the fighter (copy_fighter).
    read_bundle(name) returns a fighter bundle's bytes. Returns ({level: bytes}, {bundle name: bytes},
    {ship id: ShipType enum member to add}, log)."""
    from deadlock_tools import squadron_table as st
    name, _go = prefabs.root(squadron)
    type_id = prefabs.squadron_type(name)
    out, bundles, enum, log = {}, {}, {}, []
    if needs_own_fighter(S, levels, type_id, munitions):
        entry = st.entry(next(iter(_table_levels(S, levels).values())), type_id)
        ship_id = next_ship_id(S, levels, ship_enum_ids)
        ship_name, bundle_name = fighter_names(entry, ship_id)
        raw = read_bundle(fighter_bundle_name(S, levels, entry["ShipType"]))
        changed, bundle, lines = copy_fighter(S, prefabs, levels, name, ship_id, ship_name, raw, bundle_name)
        out.update(changed); bundles[bundle_name] = bundle; enum[ship_id] = "CustomShip%d" % ship_id; log += lines
    merged = dict(levels); merged.update(out)
    changed, lines = set_munitions(S, merged, type_id, munitions)
    out.update(changed); log += lines
    log += prefabs.sync_fire_buttons(name, len(munitions))
    return out, bundles, enum, log


def fighter_names(entry, ship_id):
    """(ship-database name, bundle file name) for a squadron type's own fighter copy"""
    stem = "".join(ch for ch in entry["name"].lower() if ch.isalnum())
    return "%s fighter" % entry["name"], "%sfighter%d" % (stem, ship_id)


def munitions_cli(a):
    """shipdb squadronmunitions [TYPE] | setsquadronmunitions TYPE ID[,ID...] (or 'none')"""
    from deadlock_tools import shipdb as sd, squadron_table as st
    S = sd.Schema(a.dll)
    folder = a.level0 if os.path.isdir(a.level0) else os.path.dirname(a.level0)
    sources = {}
    for f_ in sd.files_of(folder):
        base = os.path.basename(f_)
        if base.startswith("level") and not base.endswith(".resS"):
            sources[base] = sd.chain_source(a, f_, a.out, quiet=a.cmd == "squadronmunitions")
    levels = {base: open(path, "rb").read() for base, path in sources.items()}
    known = munition_types(S, levels)
    res = next(iter(_table_levels(S, levels).values()))
    rest = list(a.args)
    if a.cmd == "squadronmunitions":
        types = [st.entry(res, rest[0])] if rest else res["objects"]
        for e in types:
            print("%-28s %-4d faction %d  munitions: %s" % (e["name"], e["id"], e["Faction"],
                  ", ".join("%s (%d)" % (known[m]["name"], m) for m in e["MunitionSlots"] if m in known) or "-"))
        print("\nmunition types: " + ", ".join("%s (%d, faction %d)" % (e["name"], i, e["Faction"]) for i, e in sorted(known.items())))
        return
    if len(rest) != 2:
        raise SystemExit('usage: shipdb setsquadronmunitions "raptor" 12,20   (munition ids in slot order, or none)')
    ids = [] if rest[1].lower() in ("none", "-", "") else [int(x) for x in rest[1].split(",") if x.strip()]
    try:
        # the prefab changes too: one Fire munitions button per munition, and a fighter copy when the fighter is shared
        from deadlock_tools import ship_types as stypes
        managed, game_data = game_paths(folder, a.dll, a.managed, a.game_data)
        asset = sd.chain_source(a, os.path.join(folder, ASSET_FILE), a.out, quiet=True)
        prefabs = Prefabs(asset, managed, game_data)
        entry = st.entry(res, rest[0])
        prefab = prefabs.by_type(entry["id"])
        if prefab is None:
            raise ValueError("no squadron prefab has squadron type %d" % entry["id"])
        if needs_own_fighter(S, levels, rest[0], ids):
            changed, bundles, enum, log = munitions_with_fighter(S, prefabs, levels, prefab, ids, _cli_bundle_reader(a, folder),
                                                                 stypes.members(_cli_dll_source(a)))
            _cli_write_fighters(a, managed, bundles, enum, log)
        else:
            changed, log = set_munitions(S, levels, rest[0], ids)
            log += prefabs.sync_fire_buttons(prefab, len(ids))
        if prefabs.added or prefabs.removed:
            changed[ASSET_FILE] = prefabs.save()
    except ValueError as e:
        raise SystemExit(str(e))
    os.makedirs(a.out, exist_ok=True)
    for base, data in changed.items():
        with open(os.path.join(a.out, base), "wb") as stream:
            stream.write(data)
    print("\n".join("[ok] " + line for line in log) or "[ok] no change")
    if changed:
        print("\nwrote %s to %s/" % (", ".join(sorted(changed)), a.out))


# ---- launch bays ----
# A carrier launches a squadron only from a LaunchPoint whose LaunchCompatibility lists the squadron type
# (MJDBEJECJMN.FindLaunchPoint). Bays live in each carrier's ship bundle, so a new squadron type launches nowhere until
# it is added to them: here, to every bay that already takes a "launch like" type (normally the donor).
def launch_carriers(S, levels, type_id):
    """[(ship id, ship name, bundle name)] of the capital ships that may carry squadron type `type_id`: same faction,
    fighter or utility slots, not on its exclusion list (utility types also fit fighter slots)"""
    import types as _types
    from deadlock_tools import shipdb as sd, squadron_table as st
    entry_ = st.entry(next(iter(_table_levels(S, levels).values())), type_id)
    ships = next((t[2]["objects"] for t in (_ship_table(S, d) for d in levels.values()) if t), [])
    assets = {}
    db = _types.SimpleNamespace(S=S)
    for data in levels.values():
        try:
            _b, _o, parsed = sd.parse_factory(db, data, "ShipFactory")
        except LookupError:
            continue
        for e in parsed["objects"]:
            assets.setdefault(int(e["id"]), e["assetName"])
    out = []
    for ship in ships:
        slots = ship.get("FighterSlots", 0) or 0
        slots += ship.get("UtilityFighterSlots", 0) or 0
        if (slots and ship.get("Faction") == entry_["Faction"] and ship["id"] not in entry_["shipExclusionList"]
                and ship["id"] in assets):
            out.append((ship["id"], ship["name"], assets[ship["id"]]))
    return out


def bay_spec(bundle_path, like_ids, type_id):
    """bays.apply spec adding `type_id` to every bay of a bundle that accepts one of `like_ids` (None: nothing to do)"""
    from deadlock_tools import bays
    layout = bays.read_layout(bundle_path)
    like_ids = {int(i) for i in like_ids}
    rows = [{"id": b["id"], "compat": sorted(set(b["compat"]) | {int(type_id)})} for b in layout["bays"]
            if set(b["compat"]) & like_ids and int(type_id) not in b["compat"]]
    return {"revision": layout["revision"], "bays": rows} if rows else None


def plan_launch_bays(S, levels, type_id, like_id, find_bundle):
    """[(bundle name, source path, spec)] to let squadron type `type_id` launch from every bay of its carriers that
    takes `like_id`. find_bundle(name) returns the path of a carrier bundle (work copy first) or None. Returns
    (jobs, log)."""
    jobs, log, missing, already = [], [], [], []
    for _ship_id, ship_name, asset in launch_carriers(S, levels, type_id):
        path = find_bundle(asset)
        if not path:
            missing.append(asset)
            continue
        spec = bay_spec(path, [like_id], type_id)
        if spec:
            jobs.append((asset, path, spec))
            log.append("%s (%s): %d launch bay(s) now take squadron type %d" % (asset, ship_name, len(spec["bays"]), type_id))
        else:
            already.append(asset)
    if already:
        log.append("no change needed in: %s" % ", ".join(sorted(set(already))))
    if missing:
        log.append("bundle not found (skipped): %s" % ", ".join(sorted(set(missing))))
    return jobs, log


def guess_like(S, levels, prefabs, name):
    """the stock squadron type a copied prefab most likely came from (its prefab name contains the stock one's)"""
    short = lambda t: t.lower().replace("squadron", "").replace("colonial", "").replace("cylon", "").replace("sagittaron", "")
    target = short(name)
    own = prefabs.squadron_type(name)
    candidates = [(len(short(n)), prefabs.squadron_type(n)) for n in prefabs.roots
                  if n != name and short(n) and short(n) in target and prefabs.squadron_type(n) != own]
    return max(candidates)[1] if candidates else None
