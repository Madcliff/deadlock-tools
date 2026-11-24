"""Extend the game's indexed repair-bay action shortcuts and their key mappings."""
import copy
import os


MAX_REPAIR_ACTIONS = 16
FIRST_FUNCTION = 60
FIRST_DESCRIPTION = 53
FIRST_KEYCODE = 286  # F5; later actions use successive function keys where available.

LABELS = {
    "keymappings": lambda n: f"Repair Bay Action {n}",
    "keymappings_french": lambda n: f"Action baie de réparation {n}",
    "keymappings_german": lambda n: f"Reparaturbucht Aktion {n}",
    "keymappings_spanish": lambda n: f"Acción de bahía de reparación {n}",
    "keymappings_russian": lambda n: f"Действие ремонтного отсека {n}",
}


def _object_sizes(data):
    from deadlock_tools.shipdb import read_objects
    return {pid: size for pid, _start, size, _cid, _meta in read_objects(data)}


def _file_key(env, path):
    """the env.files key of the file loaded from path (dependencies such as globalgamemanagers.assets
    can be pulled into the same environment, so 'the first file' is not reliable)"""
    name = os.path.basename(path)
    matches = [key for key in env.files if os.path.basename(str(key)) == name]
    if len(matches) != 1:
        raise RuntimeError("could not identify %s among the loaded asset files: %s" % (name, list(env.files)))
    return matches[0]


def save_asset(serialized_file, source_path, target_path):
    """Serialise a UnityPy file and write it to target_path without ever damaging the source.

    UnityPy reads object data lazily from source_path, so the output bytes must be complete before
    any file is opened for writing (source and target may be the same file). The result must keep
    every object of the source, and no object may lose its data; otherwise nothing is written."""
    with open(source_path, "rb") as stream:
        before = _object_sizes(stream.read())
    data = serialized_file.save()
    after = _object_sizes(data)
    lost = [pid for pid, size in before.items() if size and not after.get(pid)]
    if set(before) != set(after) or lost:
        raise RuntimeError("refusing to write %s: the rewritten asset lost %d of %d objects (source %s)"
                           % (os.path.basename(target_path), len(lost) or len(set(before) ^ set(after)), len(before), source_path))
    directory = os.path.dirname(os.path.abspath(target_path))
    temporary = os.path.join(directory, ".%s.tmp-%d" % (os.path.basename(target_path), os.getpid()))
    try:
        with open(temporary, "wb") as stream:
            stream.write(data)
        os.replace(temporary, target_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return len(data)


def update_key_assets(levels, output_root, action_count, managed=None):
    """Stage level3 key mappings and their localized labels for repair actions.

    managed: the game's Managed/ folder (type trees for the KeyManager MonoBehaviour); defaults to
    <levels>/Managed, which is missing when the level files are kept apart from the game install."""
    if not 0 <= action_count <= MAX_REPAIR_ACTIONS:
        raise ValueError(f"repair action count must be between 0 and {MAX_REPAIR_ACTIONS}")
    try:
        from deadlock_tools.bundled_assets import load_asset_tools
        UnityPy, TypeTreeGenerator = load_asset_tools()
    except ImportError as exc:
        raise RuntimeError("repair-bay shortcuts need the optional UnityPy asset support") from exc

    managed = managed or os.path.join(levels, "Managed")
    if not os.path.isdir(levels):
        raise ValueError("configured levels path must be the game's BSG_Data folder")
    if not os.path.isfile(os.path.join(managed, "Assembly-CSharp.dll")):
        raise ValueError("could not find the game's Managed folder (needed to read level3 key mappings): " + managed)
    os.makedirs(output_root, exist_ok=True)

    def source_path(name):
        staged = os.path.join(output_root, name)
        return staged if os.path.isfile(staged) else os.path.join(levels, name)

    key_source = source_path("level3")
    if not os.path.isfile(key_source):
        raise ValueError(f"missing key mapping asset: {key_source}")
    env = UnityPy.load(key_source)
    env.path = levels
    version = next(iter(env.files.values())).unity_version
    generator = TypeTreeGenerator(version)
    generator.load_local_dll_folder(managed)
    env.typetree_generator = generator
    candidates = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        fields = obj.read_typetree(check_read=False)
        if isinstance(fields, dict) and "Mappings" in fields and "MappingsAZERTY" in fields:
            candidates.append((obj, fields))
    if len(candidates) != 1:
        raise ValueError(f"expected one KeyManager in level3, found {len(candidates)}")

    obj, fields = candidates[0]
    generated_range = range(FIRST_FUNCTION, FIRST_FUNCTION + MAX_REPAIR_ACTIONS)
    for layout in ("Mappings", "MappingsAZERTY"):
        mappings = fields[layout]
        by_function = {int(item["function"]): item for item in mappings}
        foreign = [item for fn, item in by_function.items() if fn >= FIRST_FUNCTION and
                   not (fn in generated_range and int(item["group"]) == 2001 and
                        FIRST_DESCRIPTION <= int(item["functionDescriptionID"]) < FIRST_DESCRIPTION + MAX_REPAIR_ACTIONS)]
        if foreign:
            raise ValueError(f"{layout} already uses reserved repair shortcut IDs")
        mappings[:] = [item for item in mappings if int(item["function"]) < FIRST_FUNCTION]
        template = by_function.get(47)
        if template is None:
            raise ValueError(f"{layout} has no SpecialAttack3 mapping template")
        for index in range(action_count):
            existing = by_function.get(FIRST_FUNCTION + index)
            entry = copy.deepcopy(existing if existing is not None else template)
            entry["function"] = FIRST_FUNCTION + index
            entry["group"] = 2001  # BattleCommandCapitalOnly
            entry["shortcutKey"] = (existing["shortcutKey"] if existing is not None
                                    else FIRST_KEYCODE + index if index < 8 else 0)
            entry["functionDescriptionID"] = FIRST_DESCRIPTION + index
            mappings.append(entry)
    obj.save_typetree(fields)
    save_asset(env.files[_file_key(env, key_source)], key_source, os.path.join(output_root, "level3"))

    resources_source = source_path("resources.assets")
    if not os.path.isfile(resources_source):
        raise ValueError(f"missing key description asset: {resources_source}")
    resources = UnityPy.load(resources_source)
    descriptions = {}
    for obj in resources.objects:
        if obj.type.name != "TextAsset":
            continue
        fields = obj.read_typetree()
        name = fields.get("m_Name", "")
        if name not in LABELS:
            continue
        lines = fields.get("m_Script", "").splitlines()
        kept = []
        found = {}
        for line in lines:
            key, sep, value = line.partition("|")
            if sep and key.isdigit() and FIRST_DESCRIPTION <= int(key) < FIRST_DESCRIPTION + MAX_REPAIR_ACTIONS:
                description_index = int(key) - FIRST_DESCRIPTION
                if description_index in found:
                    raise ValueError(f"{name} has duplicate description ID {key}")
                found[description_index] = line
                if description_index < action_count:
                    kept.append(line)
            else:
                kept.append(line)
        for index in range(action_count):
            if index not in found:
                kept.append(f"{FIRST_DESCRIPTION + index}|{LABELS[name](index + 1)}|")
        fields["m_Script"] = "\r\n".join(kept) + "\r\n"
        obj.save_typetree(fields)
        descriptions[name] = obj
    if set(descriptions) != set(LABELS):
        missing = sorted(set(LABELS) - set(descriptions))
        raise ValueError("missing localized keymapping text assets: " + ", ".join(missing))
    save_asset(resources.files[_file_key(resources, resources_source)], resources_source, os.path.join(output_root, "resources.assets"))
    print(f"staged {action_count} indexed repair-bay shortcut(s) in level3 and localized key mappings")


def key_action_count(levels, overlay=None):
    """Read the highest indexed repair shortcut already represented in level3 assets."""
    try:
        from deadlock_tools.bundled_assets import load_asset_tools
        UnityPy, TypeTreeGenerator = load_asset_tools()
    except ImportError as exc:
        raise RuntimeError("repair-bay shortcut inspection needs the optional UnityPy asset support") from exc
    managed = os.path.join(levels, "Managed")
    source = os.path.join(overlay, "level3") if overlay else ""
    if not source or not os.path.isfile(source):
        source = os.path.join(levels, "level3")
    if not os.path.isfile(source):
        return 0
    env = UnityPy.load(source)
    env.path = levels
    version = next(iter(env.files.values())).unity_version
    generator = TypeTreeGenerator(version)
    generator.load_local_dll_folder(managed)
    env.typetree_generator = generator
    candidates = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        fields = obj.read_typetree(check_read=False)
        if isinstance(fields, dict) and "Mappings" in fields and "MappingsAZERTY" in fields:
            candidates.append(fields)
    if len(candidates) != 1:
        raise ValueError(f"expected one KeyManager in level3, found {len(candidates)}")
    counts = []
    for layout in ("Mappings", "MappingsAZERTY"):
        counts.append(sum(1 for item in candidates[0][layout]
                          if FIRST_FUNCTION <= int(item["function"]) < FIRST_FUNCTION + MAX_REPAIR_ACTIONS
                          and int(item["group"]) == 2001
                          and FIRST_DESCRIPTION <= int(item["functionDescriptionID"]) < FIRST_DESCRIPTION + MAX_REPAIR_ACTIONS))
    return max(counts, default=0)
