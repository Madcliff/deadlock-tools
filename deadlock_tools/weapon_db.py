"""Edit the combat prefabs and scene tables that define Deadlock's weapon types."""

from hashlib import sha256
from pathlib import Path
import struct

from deadlock_tools import shipdb as sd


SCENES = ("level5", "level6", "level7", "level8", "level9", "level85")
SHARED = "sharedassets5.assets"
SCRIPTS = "globalgamemanagers.assets"
FIELDS = {
    "MaxDamage": "Maximum damage", "Accuracy": "Accuracy",
    "WeaponMinRange": "Minimum range", "WeaponMaxRange": "Maximum range",
    "ArcAngle": "Firing arc", "full360Mode": "Full 360° traverse",
    "fireMin": "Minimum fire delay", "fireMax": "Maximum fire delay",
    "AccuracyPenaltyUnidentified": "Unidentified target accuracy modifier",
    "accuracyBonusAgainstMunitions": "Accuracy bonus against munitions",
}


def align(value, multiple):
    return (value + multiple - 1) & -multiple


def unity_string(value):
    raw = value.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw + bytes((-len(raw)) % 4)


def script_fields(asset, pid, schema, name, reader=sd.Reader):
    data = asset.raw(pid)
    start = align(32 + struct.unpack_from("<I", data, 28)[0], 4)
    r = reader(schema, data, start)
    result = r.obj(schema.typedef_index(name))
    if r.p != len(data):
        raise ValueError(f"Unsupported {name} object layout in {asset.name}.")
    return result, r


class Asset:
    """Stripped Unity v17 scene or shared asset. Preserve type and external metadata."""
    def __init__(self, name, data):
        self.name, self.data = name, data
        meta_size, size, version, self.data_start = struct.unpack_from(">IIII", data)
        if version != 17 or size != len(data) or data[16] != 0:
            raise ValueError(f"Unsupported Unity asset format in {name}.")
        self.meta_end = 20 + meta_size
        rows, self.count_at = sd.read_objects(data, with_count_offset=True)
        self.rows = {row[0]:row for row in rows}
        self.suffix_at = rows[-1][4] + 20
        position = self.suffix_at
        count = struct.unpack_from("<I", data, position)[0]
        for _ in range(count):
            position = align(position + 4, 4) + 8
        position += 4
        count = struct.unpack_from("<I", data, position)[0]
        position += 4
        self.externals = [name]
        for _ in range(count):
            position = data.index(b"\0", position) + 21
            end = data.index(b"\0", position)
            self.externals.append(data[position:end].decode("utf-8"))
            position = end + 1

    def raw(self, pid):
        _, offset, size, _, _ = self.rows[pid]
        return self.data[offset:offset + size]

    def rebuild(self, replacements, additions=None):
        additions = additions or {}
        data = bytearray()
        metadata = bytearray(self.data[:self.count_at])
        metadata += struct.pack("<I", len(self.rows) + len(additions))
        for pid in sorted(self.rows.keys() | additions.keys()):
            if pid in additions:
                type_index, raw = additions[pid]
            else:
                type_index = struct.unpack_from("<i", self.data, self.rows[pid][4] + 16)[0]
                raw = replacements[pid] if pid in replacements else self.raw(pid)
            data.extend(bytes((-len(data)) % 8))
            metadata.extend(bytes((-len(metadata)) % 4))
            metadata.extend(struct.pack("<qIIi", pid, len(data), len(raw), type_index))
            data.extend(raw)
        metadata.extend(self.data[self.suffix_at:self.meta_end])
        data_start = max(self.data_start, align(len(metadata), 16))
        struct.pack_into(">IIII", metadata, 0, len(metadata)-20, data_start+len(data), 17, data_start)
        return b"".join((metadata, bytes(data_start-len(metadata)), data))


class ReferenceReader(sd.Reader):
    def __init__(self, schema, data, start):
        super().__init__(schema, data, start)
        self.references = []

    def pptr(self):
        offset = self.p
        result = super().pptr()
        self.references.append((offset, result))
        return result


class WeaponDatabase:
    def __init__(self, sources, schema):
        self.schema = schema
        self.assets = {name: Asset(name, value if isinstance(value, bytes) else value.read_bytes())
                       for name, value in sources.items()}
        digest = sha256()
        for name, asset in self.assets.items():
            digest.update(name.encode())
            digest.update(asset.data)
        self.revision = digest.hexdigest()
        scripts = self.assets[SCRIPTS]
        self.scripts = {}
        for pid, _, _, class_id, _ in scripts.rows.values():
            if class_id == 115:
                raw = scripts.raw(pid)
                length = struct.unpack_from("<I", raw)[0]
                self.scripts[pid] = raw[4:4+length].decode("utf-8")
        self.factories, self.tables = {}, {}
        for scene in SCENES:
            asset = self.assets[scene]
            for name, registry in (("WeaponFactory", self.factories), ("WeaponDataTable", self.tables)):
                matches = [pid for pid, _, size, class_id, _ in asset.rows.values()
                           if class_id == 114 and size >= 32 and self.script(asset, pid) == name]
                if not matches and name == "WeaponFactory":
                    continue
                if len(matches) != 1:
                    raise ValueError(f"Expected one {name} in {scene}.")
                pid = matches[0]
                fields, _ = script_fields(asset, pid, schema, name)
                registry[scene] = (pid, fields)
        self.weapons = {}
        for scene, (_, table) in self.factories.items():
            for row in table["objects"]:
                file_id, root = row["prefab"][1]
                if self.assets[scene].externals[file_id] != SHARED:
                    raise ValueError(f"Unsupported prefab location for weapon #{row['id']}.")
                previous = self.weapons.setdefault(row["id"], (root,row["name"]))
                if previous[0] != root:
                    raise ValueError(f"Weapon #{row['id']} points to different prefabs across scenes.")
        if not self.weapons:
            raise ValueError("No supported weapon factories were found.")

    def script(self, asset, pid):
        file_id, script_id = struct.unpack_from("<iq", asset.raw(pid), 16)
        if 0 <= file_id < len(asset.externals) and asset.externals[file_id] == SCRIPTS:
            return self.scripts.get(script_id)
        return None

    def components(self, pid):
        data = self.assets[SHARED].raw(pid)
        count = struct.unpack_from("<I", data)[0]
        result = [struct.unpack_from("<iq", data, 4+12*i) for i in range(count)]
        if any(file_id for file_id, _ in result):
            raise ValueError("The weapon prefab has an external component.")
        return [ref for _,ref in result]

    def info(self, root):
        asset = self.assets[SHARED]
        names = {"WeaponInfo", "SquadronWeaponInfo"}
        matches = [(pid, self.script(asset, pid)) for pid in self.components(root) if asset.rows[pid][3] == 114]
        choices = [(pid,name) for pid,name in matches if name in names]
        if len(choices) != 1:
            raise ValueError(f"Weapon prefab #{root} has no unique WeaponInfo.")
        pid,name = choices[0]
        values,_ = script_fields(asset, pid, self.schema, name)
        fire_min,fire_max = struct.unpack_from("<ii", asset.raw(pid), values["_off"]["FireDelay"][0])
        return pid, {field:values[field] for field in FIELDS if field in values}|{"fireMin":fire_min,"fireMax":fire_max}, values["_off"]

    def rows(self):
        default = {row["id"]:row for row in self.tables["level5"][1]["objects"]}
        return [{"id":key,"name":name,"descriptionID":default[key]["descriptionID"],
                 "fields":self.info(root)[1]}
                for key,(root,name) in sorted(self.weapons.items()) if key in default]

    def prefab_clone(self, root, name, info_pid, edited):
        asset = self.assets[SHARED]
        objects = {}
        references = {}
        pending = [root]
        while pending:
            owner = pending.pop()
            if owner in objects:
                raise ValueError("The weapon prefab hierarchy has a cycle.")
            objects[owner] = asset.raw(owner)
            comps = self.components(owner)
            references[owner] = [4+12*i for i in range(len(comps))]
            transforms = [pid for pid in comps if asset.rows[pid][3] == 4]
            if len(transforms) != 1:
                raise ValueError("The weapon prefab has no unique transform.")
            for pid in comps:
                data = asset.raw(pid)
                kind = asset.rows[pid][3]
                objects[pid] = data
                refs = [0]
                if kind == 4:
                    count = struct.unpack_from("<I", data, 52)[0]
                    refs += [56+12*i for i in range(count+1)]
                    for offset in refs[1:-1]:
                        file_id,child = struct.unpack_from("<iq",data,offset)
                        if file_id or asset.rows[child][3] != 4:
                            raise ValueError("The weapon prefab has an external transform.")
                        pending.append(struct.unpack_from("<q",asset.raw(child),4)[0])
                elif kind == 114:
                    script = self.script(asset, pid)
                    if not script:
                        raise ValueError("The weapon prefab has an unidentified script.")
                    # Its only custom fields are Vector2 and AnimationCurve, which contain no object references.
                    if script != "LowPassFilterDistanceModifier":
                        _, reader = script_fields(asset,pid,self.schema,script,ReferenceReader)
                        refs += [offset for offset,_ in reader.references]
                elif kind not in {23,33,82,169,198,199}:
                    raise ValueError(f"Unsupported weapon component class {kind}.")
                references[pid] = refs
        first_id = max(asset.rows) + 1
        ids = {pid:first_id+index for index,pid in enumerate(objects)}
        additions = {}
        for pid, data in objects.items():
            updated = bytearray(edited if pid == info_pid else data)
            known = set(references[pid])
            if any(offset not in known and file_id == 0 and target in ids
                   for offset in range(0,len(data)-11,4)
                   for file_id,target in [struct.unpack_from("<iq",data,offset)]):
                raise ValueError(f"Unrecognised internal reference on weapon prefab object #{pid}.")
            for offset in references[pid]:
                file_id,target = struct.unpack_from("<iq",updated,offset)
                if file_id == 0 and target in ids:
                    struct.pack_into("<q",updated,offset+4,ids[target])
            if pid == root:
                name_start = 8+12*len(self.components(pid))
                size = struct.unpack_from("<I",updated,name_start)[0]
                name_end = align(name_start+4+size,4)
                updated[name_start:name_end] = unity_string(name)
            entry = asset.rows[pid][4]
            additions[ids[pid]] = (struct.unpack_from("<i",asset.data,entry+16)[0],bytes(updated))
        return ids[root], additions

    def update(self, edit):
        if edit.get("revision") != self.revision:
            raise ValueError("Weapon data changed. Reload the editor before saving.")
        key = edit.get("id")
        values = edit.get("fields")
        clone = edit.get("clone")
        if type(key) is not int or key not in self.weapons or not isinstance(values,dict) or type(clone) is not bool:
            raise ValueError("Invalid weapon edit.")
        if set(values) != FIELDS.keys():
            raise ValueError("Weapon edit has missing or unsupported stats.")
        for field,value in values.items():
            if field == "full360Mode":
                valid = type(value) is bool
            elif field in {"fireMin","fireMax"}:
                valid = type(value) is int and 0 <= value <= 1000000
            else:
                valid = type(value) in (int,float) and -1e8 <= value <= 1e8
            if not valid:
                raise ValueError(f"Invalid {FIELDS[field]}.")
        if (values["WeaponMinRange"] < 0 or values["WeaponMinRange"] > values["WeaponMaxRange"]
                or values["fireMin"] > values["fireMax"] or values["MaxDamage"] < 0
                or not 0 <= values["ArcAngle"] <= 360):
            raise ValueError("Check damage, arc, and minimum/maximum ranges and fire delays.")
        name = edit.get("name")
        if not isinstance(name, str):
            raise ValueError("Enter a weapon name.")
        name = name.strip()
        description = edit.get("descriptionID")
        if not name or len(name.encode("utf-8")) > 256 or any(c in name for c in "\r\n\0"):
            raise ValueError("Enter a weapon name of up to 256 UTF-8 bytes.")
        if type(description) is not int or not -1 <= description <= 2147483647:
            raise ValueError("Invalid localisation string ID.")
        occupied = {row["id"] for registry in (self.factories,self.tables) for _,table in registry.values() for row in table["objects"]}
        occupied.update(sd.enum_names(self.schema, self.schema.typedef_index("EGMNIMDCPGL")))
        result_key = max(occupied)+1 if clone else key
        if result_key > 2147483647:
            raise ValueError("No free weapon ID remains.")
        root,_ = self.weapons[key]
        info_pid,_,offsets = self.info(root)
        shared = self.assets[SHARED]
        weapon = bytearray(shared.raw(info_pid))
        for field,value in values.items():
            if field not in {"fireMin","fireMax"}:
                struct.pack_into("<?" if field=="full360Mode" else "<f",weapon,offsets[field][0],value)
        struct.pack_into("<ii",weapon,offsets["FireDelay"][0],values["fireMin"],values["fireMax"])
        additions = {}
        if clone:
            root,additions = self.prefab_clone(root,name,info_pid,weapon)
        changes = {SHARED:shared.rebuild({} if clone else {info_pid:weapon},additions)}
        for scene in SCENES:
            asset = self.assets[scene]
            replacements = {}
            for registry in (self.factories,self.tables):
                if scene not in registry:
                    continue
                pid,fields = registry[scene]
                rows = fields["objects"]
                items = [i for i,row in enumerate(rows) if row["id"] == key]
                if len(items) != 1:
                    raise ValueError(f"Weapon #{key} does not have one entry in {scene}.")
                index = items[0]
                row = rows[index]
                data = asset.raw(pid)
                start = row["_off"]["name"][0]
                end = rows[index+1]["_off"]["name"][0] if index+1<len(rows) else len(data)
                id_at = row["_off"]["id"][0]
                tail = bytearray(data[id_at:end])
                struct.pack_into("<i",tail,0,result_key)
                if registry is self.factories:
                    file_id,_ = row["prefab"][1]
                    struct.pack_into("<iq",tail,row["_off"]["prefab"][0]-id_at,file_id,root)
                else:
                    struct.pack_into("<i",tail,row["_off"]["descriptionID"][0]-id_at,description)
                    struct.pack_into("<f",tail,row["_off"]["MaxDamage"][0]-id_at,values["MaxDamage"])
                entry = unity_string(name)+tail
                if clone:
                    changed = bytearray(data+entry)
                    struct.pack_into("<I",changed,fields["_off"]["objects"][0],len(rows)+1)
                else:
                    changed = data[:start]+entry+data[end:]
                replacements[pid] = changed
            changes[scene] = asset.rebuild(replacements)
        return result_key, changes


MUNITION_FIELDS = {
    "StandardSalvoSize": "Salvo size", "MaxMissiles": "Maximum carried",
    "HasUnlimitedMunitions": "Unlimited ammunition",
    "fireMin": "Minimum first-launch delay (ticks)", "fireMax": "Maximum first-launch delay (ticks)",
    "DelayBetweenMissilesInTicks": "Delay between missiles (ticks)",
    "SupplyCostPerMunition": "Supply cost per munition",
    "SurvivalCostPerMunition": "Survival cost per munition", "PointValue": "Point value",
    "IncludedInCodex": "Show in codex", "includedInMultiplayer": "Available in multiplayer",
}


def source_files(game, working, levels=None):
    files = {}
    for name in (SCRIPTS, SHARED, *SCENES):
        candidates = [working/name, (levels or game)/name, game/name]
        files[name] = next((path for path in candidates if path.is_file()), candidates[-1])
    return files


def munition_fields(data, offset, row):
    start = offset + row["_off"]["FirstLaunchDelayInTicks"][0]
    low, high = struct.unpack_from("<ii", data, start)
    return {key:row[key] for key in MUNITION_FIELDS if key not in {"fireMin","fireMax"}}|{"fireMin":low,"fireMax":high}


def munition_rows(data, schema):
    offset, table = sd.load_missiles(schema, data)
    return [{"id":row["id"], "name":row["name"], "fields":munition_fields(data, offset, row),
             "details":{"Listed range":row["Range"], "Listed maximum velocity":row["MaxVelocity"],
                        "Listed hull damage":row["DisplayDamage"]["HullDamage"],
                        "Listed armour damage":row["DisplayDamage"]["ArmourDamage"]}}
            for row in table["objects"]]


def update_munition(data, schema, key, values):
    offset, table = sd.load_missiles(schema, data)
    matching = [row for row in table["objects"] if row["id"] == key]
    if len(matching) != 1:
        raise ValueError(f"Expected a single munition #{key}.")
    row = matching[0]
    if not isinstance(values, dict) or set(values) != MUNITION_FIELDS.keys():
        raise ValueError("Munition edit has missing or unsupported stats.")
    out = bytearray(data)
    for field,value in values.items():
        if field in {"fireMin","fireMax"}:
            start, code = row["_off"]["FirstLaunchDelayInTicks"][0] + (4 if field=="fireMax" else 0), 8
        else:
            start, code = row["_off"][field]
        if code == 2 and type(value) is bool:
            fmt = "<?"
        elif code == 8 and type(value) is int and 0 <= value <= 2147483647:
            fmt = "<i"
        elif code == 12 and type(value) in (float,int) and 0 <= value <= 1e8:
            fmt = "<f"
        else:
            raise ValueError(f"Invalid {MUNITION_FIELDS[field]}.")
        struct.pack_into(fmt,out,offset+start,value)
    if values["fireMin"] > values["fireMax"]:
        raise ValueError("Minimum launch delay must be at most the maximum.")
    return bytes(out)
