"""Read and edit the game's commander skill tree in level DataTables."""
import math
import struct

from deadlock_tools import shipdb as sd

CUSTOM_ID_BASE = 1000
CUSTOM_SLOT_COUNT = 10
TYLIUM_TARGET = 30
TYLIUM_EFFECT = "Tylium per turn"
DISPLAY_TYPES = {"flat": 0, "percentage": 1}


def _custom_id(target, mode, flagship, slot):
    return CUSTOM_ID_BASE + int(target) * 100 + (20 if mode == "percentage" else 0) + (10 if flagship else 0) + int(slot)


def _decode_custom_id(ability_id):
    offset = int(ability_id) - CUSTOM_ID_BASE
    if TYLIUM_TARGET * 100 <= offset < TYLIUM_TARGET * 100 + CUSTOM_SLOT_COUNT:
        return {"target": TYLIUM_TARGET, "mode": "flat", "flagship": False, "slot": offset % 100}
    if not 0 <= offset <= 2039:
        return None
    target, suffix = divmod(offset, 100)
    if target < 3 or target > 20:
        return None
    mode = "percentage" if suffix >= 20 else "flat"
    suffix %= 20
    flagship = suffix >= 10
    slot = suffix % 10
    return {"target": target, "mode": mode, "flagship": flagship, "slot": slot}


def _align4(value):
    return (value + 3) & ~3


def _enum_for_field(schema, class_name, field_name):
    type_index = schema.typedef_index(class_name)
    for name, signature, _ in schema.fields(type_index):
        if name == field_name:
            tag, index, _ = sd.Reader(schema, b"").token(signature, 2)
            if tag != 0:
                raise ValueError("%s.%s is not a local enum" % (class_name, field_name))
            return sd.enum_names(schema, index - 1)
    raise ValueError("could not find %s.%s in the configured DLL" % (class_name, field_name))


def _tables(schema, data):
    hit = sd.find_tables(schema, data, schema.typedef_index("DataTables"))
    if not hit:
        raise LookupError("no commander DataTables object found")
    return hit


def read(data, schema):
    _, _, _, tables = _tables(schema, data)
    subsystem_names = _enum_for_field(schema, "CommanderSpecialisationBySubSystem", "SubSystem")
    # Nested typedef names are not consistently indexed by Mono.Cecil; use the
    # field's actual local type from the containing DataTables nested class.
    if not subsystem_names:
        subsystem_names = {i: "Subsystem %d" % i for i in range(7)}
    skill_names = _enum_for_field(schema, "CaptainSkill", "Skill")
    abilities = {}
    for item in tables["OfficerSkills"]["CaptainSkills"]:
        ability_id = int(item["Skill"])
        custom = _decode_custom_id(ability_id)
        abilities[ability_id] = {
            "id": ability_id,
            "name": item["name"] or skill_names.get(ability_id, "Ability %d" % ability_id),
            "effect": ((TYLIUM_EFFECT if custom["target"] == TYLIUM_TARGET else skill_names.get(custom["target"], "Unknown") + " · " + custom["mode"])
                       if custom else skill_names.get(ability_id, "Unknown (%d)" % ability_id)),
            "custom": custom is not None,
            "target": custom["target"] if custom else ability_id,
            "mode": custom["mode"] if custom else ("percentage" if int(item["valueDisplayType"]) == 1 else "flat"),
            "titleId": int(item["TitleID"]),
            "descriptionId": int(item["DescriptionID"]),
            "displayType": int(item["valueDisplayType"]),
            "flagship": bool(item["AppliesToFlagship"]),
        }
    bonuses = {int(item["Specialisation"]): float(item["BonusAmount"])
               for item in tables["CommanderSpecialisationBonusAmount"]}
    for ability_id, ability in abilities.items():
        ability["bonus"] = bonuses.get(ability_id, 0.0)
    allocations = {}
    for item in tables["CommanderSpecialisations"]:
        subsystem = int(item["SubSystem"])
        allocations[str(subsystem)] = {
            "id": subsystem,
            "name": subsystem_names.get(subsystem, "Subsystem %d" % subsystem),
            "choices": [int(value) for value in item["Specialisations"]],
        }
    return {
        "subsystems": [allocations[key] for key in sorted(allocations, key=int)],
        "abilities": [abilities[key] for key in sorted(abilities)],
        "rankCount": len(tables["RankXPTable"]),
        "supportedAbilityCount": len(abilities),
        "builtInEffectCount": len(skill_names),
    }


def edit(data, schema, assignments, ability_edits, new_abilities=None):
    """Apply supported-tree choice arrays and existing ability values to one level file."""
    pid, offset, size, tables = _tables(schema, data)
    existing = read(data, schema)
    ability_ids = {int(item["id"]) for item in existing["abilities"]}
    existing_abilities = {int(item["id"]): item for item in existing["abilities"]}
    new_abilities = list(new_abilities or [])
    clean_new = {}
    skills = _tables(schema, data)[3]["OfficerSkills"]["CaptainSkills"]
    skill_names = _enum_for_field(schema, "CaptainSkill", "Skill")
    slots_used = {(item["target"], item["mode"], item["flagship"], _decode_custom_id(item["id"])["slot"])
                  for item in existing["abilities"] if item["custom"]}
    for spec in new_abilities:
        if not isinstance(spec, dict):
            raise ValueError("each new ability must be an object")
        target = int(spec.get("target", -1))
        mode = str(spec.get("mode", "flat"))
        flagship = spec.get("flagship", False)
        name = str(spec.get("name", "")).strip()
        bonus = float(spec.get("bonus", 0))
        title_id, description_id = int(spec.get("titleId", -1)), int(spec.get("descriptionId", -1))
        if target == TYLIUM_TARGET:
            if mode != "flat" or flagship is not False or not 0 <= bonus <= 1_000_000 or not bonus.is_integer():
                raise ValueError("Tylium per turn requires a whole number from 0 to 1000000 and applies to the officer's fleet")
        elif target not in skill_names or not 3 <= target <= 20:
            raise ValueError("choose a supported ship stat or Tylium per turn")
        if mode not in DISPLAY_TYPES or not isinstance(flagship, bool):
            raise ValueError("new effect mode must be flat or percentage and flagship must be true or false")
        if not name or len(name) > 80:
            raise ValueError("new ability name must contain 1 to 80 characters")
        if not math.isfinite(bonus) or abs(bonus) > 1_000_000:
            raise ValueError("ability bonus must be finite and between -1000000 and 1000000")
        if not 0 <= title_id <= 2**31 - 1 or not 0 <= description_id <= 2**31 - 1:
            raise ValueError("localisation IDs must be between 0 and 2147483647")
        slot = next((i for i in range(CUSTOM_SLOT_COUNT) if (target, mode, flagship, i) not in slots_used), None)
        if slot is None:
            raise ValueError("that stat/mode/flagship combination already has 10 custom ability entries")
        ability_id = _custom_id(target, mode, flagship, slot)
        if ability_id in ability_ids or ability_id in clean_new:
            raise ValueError("custom ability ID collision; reload the commander data before saving")
        slots_used.add((target, mode, flagship, slot))
        clean_new[ability_id] = {"id": ability_id, "target": target, "mode": mode, "flagship": flagship,
                                 "name": name, "bonus": bonus, "titleId": title_id,
                                 "descriptionId": description_id, "displayType": DISPLAY_TYPES[mode]}
    ability_ids.update(clean_new)
    subsystem_ids = {int(item["id"]) for item in existing["subsystems"]}
    if not isinstance(assignments, dict) or {int(key) for key in assignments} != subsystem_ids:
        raise ValueError("provide a choice list for every commander subsystem")

    clean_assignments = {}
    for key, choices in assignments.items():
        subsystem = int(key)
        if not isinstance(choices, list) or not choices:
            raise ValueError("each subsystem must have at least one ability choice")
        clean = [int(value) for value in choices]
        if len(clean) > len(ability_ids):
            raise ValueError("a subsystem cannot offer more choices than the game has supported abilities")
        if len(set(clean)) != len(clean) or any(value not in ability_ids for value in clean):
            raise ValueError("subsystem %d has a duplicate or unsupported ability ID" % subsystem)
        clean_assignments[subsystem] = clean

    if not isinstance(ability_edits, dict):
        raise ValueError("ability edits must be an object")
    clean_edits = {}
    for key, values in ability_edits.items():
        ability_id = int(key)
        if ability_id not in ability_ids or not isinstance(values, dict):
            raise ValueError("unknown commander ability ID %s" % key)
        base_values = clean_new.get(ability_id, existing_abilities.get(ability_id))
        if base_values is None:
            raise ValueError("unknown commander ability ID %s" % key)
        bonus = float(values.get("bonus", base_values["bonus"]))
        display_type = int(values.get("displayType", base_values["displayType"]))
        flagship = values.get("flagship", base_values["flagship"])
        title_id = int(values.get("titleId", base_values["titleId"]))
        description_id = int(values.get("descriptionId", base_values["descriptionId"]))
        if not math.isfinite(bonus) or abs(bonus) > 1_000_000:
            raise ValueError("ability bonus must be finite and between -1000000 and 1000000")
        if base_values["target"] == TYLIUM_TARGET and (
            not 0 <= bonus <= 1_000_000 or not bonus.is_integer() or display_type != 0 or flagship is not False
        ):
            raise ValueError("Tylium per turn requires a whole number from 0 to 1000000 and applies to the officer's fleet")
        if display_type not in (0, 1, 2):
            raise ValueError("display type must be Regular, Percentage, or Per tick")
        if not isinstance(flagship, bool):
            raise ValueError("flagship must be true or false")
        if not 0 <= title_id <= 2**31 - 1 or not 0 <= description_id <= 2**31 - 1:
            raise ValueError("localisation IDs must be between 0 and 2147483647")
        clean_edits[ability_id] = {"bonus": bonus, "displayType": display_type, "flagship": flagship,
                                   "titleId": title_id, "descriptionId": description_id}

    mono = bytearray(data[offset:offset + size])
    # Fixed-size fields can be updated before replacing variable-size choice arrays.
    bonus_offsets = {int(item["Specialisation"]): item["_off"]["BonusAmount"][0]
                     for item in tables["CommanderSpecialisationBonusAmount"]}
    captain_offsets = {int(item["Skill"]): item["_off"]
                       for item in tables["OfficerSkills"]["CaptainSkills"]}
    for ability_id, values in clean_edits.items():
        if ability_id in clean_new:
            clean_new[ability_id].update(values)
            continue
        if ability_id not in bonus_offsets or ability_id not in captain_offsets:
            raise ValueError("commander ability %d has no editable bonus/definition record" % ability_id)
        struct.pack_into("<f", mono, bonus_offsets[ability_id], values["bonus"])
        fields = captain_offsets[ability_id]
        struct.pack_into("<i", mono, fields["valueDisplayType"][0], values["displayType"])
        struct.pack_into("<B", mono, fields["AppliesToFlagship"][0], 1 if values["flagship"] else 0)
        struct.pack_into("<i", mono, fields["TitleID"][0], values["titleId"])
        struct.pack_into("<i", mono, fields["DescriptionID"][0], values["descriptionId"])

    # Add custom ability definition and bonus rows, retaining a built-in icon as the template.
    captain_array_start = tables["OfficerSkills"]["_off"]["CaptainSkills"][0]
    captain_array_end = _align4(skills[-1]["_off"]["AppliesToFlagship"][0] + 1)
    captain_rows = []
    for ability_id, values in clean_new.items():
        donor_id = values["target"]
        donor = next((item for item in skills if int(item["Skill"]) == donor_id), skills[0])
        row_start = donor["_off"]["name"][0]
        row_end = next((item["_off"]["name"][0] for item in skills
                        if item["_off"]["name"][0] > row_start),
                       _align4(skills[-1]["_off"]["AppliesToFlagship"][0] + 1))
        raw = bytearray(data[offset + row_start:offset + row_end])
        icon_start = donor["_off"]["Icon"][0] - row_start
        icon = bytes(raw[icon_start:icon_start + 12])
        name_bytes = values["name"].encode("utf-8")
        row = bytearray(struct.pack("<i", len(name_bytes)) + name_bytes)
        row.extend(b"\0" * (_align4(len(row)) - len(row)))
        row.extend(struct.pack("<i", ability_id)); row.extend(icon)
        row.extend(struct.pack("<iiiB", values["titleId"], values["descriptionId"], values["displayType"],
                               1 if values["flagship"] else 0))
        row.extend(b"\0" * (_align4(len(row)) - len(row)))
        captain_rows.append(bytes(row))
    bonus_table = tables["CommanderSpecialisationBonusAmount"]
    bonus_array_start = tables["_off"]["CommanderSpecialisationBonusAmount"][0]
    bonus_array_end = bonus_array_start + 4 + 8 * len(bonus_table)
    bonus_rows = [struct.pack("<if", ability_id, values["bonus"]) for ability_id, values in clean_new.items()]
    if clean_new:
        struct.pack_into("<i", mono, captain_array_start, len(skills) + len(captain_rows))
        struct.pack_into("<i", mono, bonus_array_start, len(bonus_table) + len(bonus_rows))

    replacements = []
    if captain_rows:
        replacements.append((captain_array_end, captain_array_end, b"".join(captain_rows)))
    if bonus_rows:
        replacements.append((bonus_array_end, bonus_array_end, b"".join(bonus_rows)))
    for item in tables["CommanderSpecialisations"]:
        subsystem = int(item["SubSystem"])
        start = item["_off"]["Specialisations"][0]
        end = start + 4 + 4 * len(item["Specialisations"])
        blob = struct.pack("<i", len(clean_assignments[subsystem])) + b"".join(
            struct.pack("<i", value) for value in clean_assignments[subsystem])
        replacements.append((start, end, blob))
    for start, end, blob in sorted(replacements, reverse=True):
        mono[start:end] = blob

    updated = sd.resize_object(bytes(data), pid, bytes(mono))
    checked = read(updated, schema)
    got = {str(item["id"]): item["choices"] for item in checked["subsystems"]}
    want = {str(key): value for key, value in clean_assignments.items()}
    if got != want:
        raise ValueError("commander tree allocation did not verify after writing")
    for ability in checked["abilities"]:
        values = clean_edits.get(ability["id"])
        if values and (abs(ability["bonus"] - values["bonus"]) > 1e-5 or
                       ability["displayType"] != values["displayType"] or
                       ability["flagship"] != values["flagship"] or
                       ability["titleId"] != values["titleId"] or
                       ability["descriptionId"] != values["descriptionId"]):
            raise ValueError("commander ability %d did not verify after writing" % ability["id"])
    return updated, checked
