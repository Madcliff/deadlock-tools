"""Repair the game's fixed row sizing for expanded ship slot lists."""
import argparse
import copy
import os
import sys

from deadlock_tools import config


def _unity_modules():
    try:
        import UnityPy
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
    except ImportError as exc:
        raise SystemExit(
            "UI asset editing needs the optional Unity asset package. Install it with: "
            "python -m pip install 'deadlock-tools[ui-assets]'"
        ) from exc
    return UnityPy, TypeTreeGenerator


def _game_objects(env):
    objects = {obj.path_id: obj for obj in env.objects}
    names = {}
    transforms = {}
    for obj in env.objects:
        if obj.type.name == "GameObject":
            names[obj.path_id] = obj.read_typetree().get("m_Name", "")
        elif obj.type.name in ("Transform", "RectTransform"):
            fields = obj.read_typetree()
            transforms[fields["m_GameObject"]["m_PathID"]] = obj
    return objects, names, transforms


def _ancestors(go_id, objects, names, transforms):
    result = []
    seen = set()
    while go_id and go_id not in seen:
        seen.add(go_id)
        result.append(names.get(go_id, ""))
        transform = transforms.get(go_id)
        if transform is None:
            break
        father = transform.read_typetree().get("m_Father", {}).get("m_PathID", 0)
        if not father or father not in objects:
            break
        go_id = objects[father].read_typetree().get("m_GameObject", {}).get("m_PathID", 0)
    return result


def _patch_layout_asset(path, generator, kind, dependency_root):
    UnityPy, _ = _unity_modules()
    env = UnityPy.load(path)
    # Resolve Unity's external script/type references against the configured game data,
    # even when the editable source is already in the separate work tree.
    env.path = dependency_root
    env.typetree_generator = generator
    asset = next(iter(env.files.values()))
    objects, names, transforms = _game_objects(env)
    changed = []

    if kind in ("campaign", "galactica"):
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            go_id = obj.read_typetree(check_read=False).get("m_GameObject", {}).get("m_PathID", 0)
            name = names.get(go_id, "")
            if name not in ("MunitionSlotWidgetList", "SquadronSlotWidgetList"):
                continue
            ancestry = _ancestors(go_id, objects, names, transforms)
            if not any(n in ancestry for n in ("BuildShipsPanel", "BlueprintsPanel")):
                continue
            fields = obj.read_typetree()
            if "listScaleMode" not in fields:
                continue
            if fields["listScaleMode"] != 1:  # UIWidgetList.TopToBottom
                fields["listScaleMode"] = 1
                obj.save_typetree(fields)
                changed.append("/".join(reversed(ancestry)))

    if kind == "battle":
        panel_ids = [pid for pid, name in names.items() if name == "ShipInfoPanel"]
        for panel_id in panel_ids:
            for go_id, name in names.items():
                if name != "ShipSquadronlMultiImageWidgetList":
                    continue
                if "ShipInfoPanel" not in _ancestors(go_id, objects, names, transforms):
                    continue
                transform = transforms.get(go_id)
                if transform is None or transform.type.name != "RectTransform":
                    continue
                rect = transform.read_typetree()
                if rect["m_SizeDelta"]["y"] < 75.0:
                    rect["m_SizeDelta"]["y"] = 75.0
                    transform.save_typetree(rect)
                    changed.append("ShipInfoPanel/ShipSquadronlMultiImageWidgetList height 75")

    if not changed:
        return None
    return asset.save(), changed


def _patch_ship_cards(path, generator, dependency_root):
    """Wrap the ship-card slot icons into rows and let the fleet list scroll vertically."""
    UnityPy, _ = _unity_modules()
    env = UnityPy.load(path)
    env.path = dependency_root
    env.typetree_generator = generator
    asset = next(iter(env.files.values()))
    objects, names, transforms = _game_objects(env)
    changed = []
    game_objects = {
        obj.path_id: obj for obj in env.objects if obj.type.name == "GameObject"
    }

    grid_source = None
    slot_lists = {"SlotWidgetListTopRight", "SlotWidgetListBottomRight"}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        fields = obj.read_typetree(check_read=False)
        if "m_Constraint" in fields and "m_CellSize" in fields:
            grid_source = (obj, fields)
        go_id = fields.get("m_GameObject", {}).get("m_PathID", 0)
        name = names.get(go_id, "")
        if name == "ShipWidgetList" and "listScaleMode" in fields:
            if fields["listScaleMode"] != 1:
                fields["listScaleMode"] = 1
                obj.save_typetree(fields)
                changed.append("ShipWidgetList vertical content sizing")
        if name not in slot_lists or "listScaleMode" not in fields:
            continue
        if fields["listScaleMode"] != 1:
            fields["listScaleMode"] = 1
            obj.save_typetree(fields)
            changed.append(name + " vertical content sizing")

    if grid_source is None:
        raise ValueError("could not find a GridLayoutGroup template in sharedassets5.assets")

    targets = {}
    for go_id, name in names.items():
        if name not in slot_lists:
            continue
        components = []
        for entry in game_objects[go_id].read_typetree()["m_Component"]:
            comp = objects.get(entry["component"]["m_PathID"])
            if comp is None or comp.type.name != "MonoBehaviour":
                continue
            data = comp.read_typetree(check_read=False)
            if "m_Constraint" in data and "m_CellSize" in data:
                components.append((comp, data))
        grid = components[0] if components else None
        horizontal = None
        for entry in game_objects[go_id].read_typetree()["m_Component"]:
            comp = objects.get(entry["component"]["m_PathID"])
            if comp is None or comp.type.name != "MonoBehaviour":
                continue
            data = comp.read_typetree(check_read=False)
            if "m_ChildForceExpandWidth" in data and "m_Spacing" in data:
                horizontal = (comp, data)
        if name == "SlotWidgetListTopRight" and grid is None:
            targets[go_id] = (name, None, horizontal)
        else:
            targets[go_id] = (name, grid, horizontal)

    for go_id, (name, grid, horizontal) in targets.items():
        columns = 8 if name == "SlotWidgetListTopRight" else 4
        if grid is None:
            template_obj, template_fields = grid_source
            clone = copy.copy(template_obj)
            clone.path_id = max(asset.objects) + 1
            clone.data = None
            clone._read_until = None
            asset.objects[clone.path_id] = clone
            grid_obj = clone
            grid_fields = dict(template_fields)
            grid_fields["m_GameObject"] = {"m_FileID": 0, "m_PathID": go_id}
            grid_fields["m_Constraint"] = 1  # GridLayoutGroup.Constraint.FixedColumnCount
            grid_fields["m_ConstraintCount"] = columns
            grid_fields["m_Spacing"] = {"x": 2.0, "y": 2.0}
            grid_obj.save_typetree(grid_fields)
            go_fields = game_objects[go_id].read_typetree()
            go_fields["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": clone.path_id}})
            game_objects[go_id].save_typetree(go_fields)
            changed.append(f"{name} grid ({columns} columns)")
        else:
            grid_obj, grid_fields = grid
            if grid_fields["m_Constraint"] != 1 or grid_fields["m_ConstraintCount"] != columns:
                grid_fields["m_Constraint"] = 1
                grid_fields["m_ConstraintCount"] = columns
                grid_fields["m_Spacing"] = {"x": 2.0, "y": 2.0}
                grid_obj.save_typetree(grid_fields)
                changed.append(f"{name} grid ({columns} columns)")
        if horizontal:
            layout_obj, layout_fields = horizontal
            if layout_fields.get("m_Enabled", 1):
                layout_fields["m_Enabled"] = 0
                layout_obj.save_typetree(layout_fields)
                changed.append(f"{name} horizontal layout disabled")

    if not changed:
        return None
    return asset.save(), changed


def fix_overflows(levels, dll, work):
    _, TypeTreeGenerator = _unity_modules()
    if not os.path.isdir(levels):
        raise SystemExit("--levels must be the game's BSG_Data folder")
    managed = os.path.dirname(dll)
    if not os.path.isdir(managed):
        raise SystemExit("could not locate the game's Managed folder beside the selected DLL")
    required = (("level5", "campaign"), ("level85", "galactica"), ("level9", "battle"),
                ("sharedassets5.assets", "ship-cards"))
    for filename, _ in required:
        if not os.path.isfile(os.path.join(levels, filename)):
            raise SystemExit(f"UI layout asset is missing: {os.path.join(levels, filename)}")

    probe, _ = _unity_modules()
    first = probe.load(os.path.join(levels, "level5"))
    unity_version = next(iter(first.files.values())).unity_version
    generator = TypeTreeGenerator(unity_version)
    generator.load_local_dll_folder(managed)
    output_root = os.path.join(work, "BSG_Data")
    for filename, kind in required:
        source = os.path.join(levels, filename)
        target = os.path.join(output_root, filename)
        read_path = target if os.path.isfile(target) else source
        result = (_patch_ship_cards(read_path, generator, levels) if kind == "ship-cards"
                  else _patch_layout_asset(read_path, generator, kind, levels))
        if result is None:
            print(f"{filename}: no layout changes needed")
            continue
        data, changed = result
        config.write(target, data)
        print(f"{filename}: updated {len(changed)} layout setting(s) -> {target}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="deadlock ui",
        description="Fix slot list overflow in construction, research and battle ship panels.",
    )
    sub = ap.add_subparsers(dest="command", required=True)
    fix = sub.add_parser("fix-overflows", help="stage row-sizing fixes for expanded ship slots")
    fix.add_argument("--levels", help="game BSG_Data folder (default: deadlock.json)")
    fix.add_argument("--dll", help="game Assembly-CSharp.dll (default: deadlock.json)")
    fix.add_argument("--work", help="editable work folder (default: deadlock.json)")
    args = ap.parse_args(argv)
    if args.command == "fix-overflows":
        levels = config.setting("levels", args.levels)
        dll = config.setting("dll", args.dll)
        work = config.setting("work", args.work)
        fix_overflows(levels, dll, work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
