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


def update_key_assets(levels, output_root, action_count):
    """Stage level3 key mappings and their localized labels for repair actions."""
    if not 0 <= action_count <= MAX_REPAIR_ACTIONS:
        raise ValueError(f"repair action count must be between 0 and {MAX_REPAIR_ACTIONS}")
    try:
        import UnityPy
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
    except ImportError as exc:
        raise RuntimeError("repair-bay shortcuts need the optional UnityPy asset support") from exc

    managed = os.path.join(levels, "Managed")
    if not os.path.isdir(levels) or not os.path.isdir(managed):
        raise ValueError("configured levels path must be the game's BSG_Data folder")
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
    staged_level = os.path.join(output_root, "level3")
    with open(staged_level, "wb") as stream:
        stream.write(next(iter(env.files.values())).save())

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
    staged_resources = os.path.join(output_root, "resources.assets")
    with open(staged_resources, "wb") as stream:
        stream.write(next(iter(resources.files.values())).save())
    print(f"staged {action_count} indexed repair-bay shortcut(s) in level3 and localized key mappings")


def key_action_count(levels, overlay=None):
    """Read the highest indexed repair shortcut already represented in level3 assets."""
    try:
        import UnityPy
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
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
