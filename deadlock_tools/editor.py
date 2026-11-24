#!/usr/bin/env python3
"""
gui - graphical editor for BSG Deadlock (runs a small web page on your own computer).

  python -m deadlock_tools gui [LEVELS DLL] [--bundles DIR] [--work DIR] [--port 8765] [--no-browser]

The paths come from deadlock.json (the same settings file the command-line tools use) and can be changed in the page
(Settings tab, with a Browse button); changes are saved back to deadlock.json. On the command line:
    LEVELS         the folder with the game's level files
    DLL            Assembly-CSharp.dll
    --bundles DIR  folder with ship bundles (encrypted or .dec; encrypted ones are decrypted automatically) for the Ships page (optional)
  --work DIR     editable work folder (the work tree mirrors BSG_Data)

It opens http://127.0.0.1:8765 by itself. Nothing leaves your computer and the originals are never touched.

TABS
  Settings default folders for level data and ship bundles, the DLL file, and the work folder.
  Ships    pick a ship, edit its stats (hull, armour per side, speed, turn rate, DRADIS range, build cost/time/rush, fleet
           points, carrier slots, flags ...), press Apply: changed level files are written into the work tree.
           Bundle controls appear alongside the ship's stats, with a file picker and inspection controls above.
  DLL      the 'dll' patches (max rank, command nodes, munition load, repair shortcuts; revert of removed legacy patches).
  Commanders allocate built-in abilities across commander tree subsystems and edit their bonus/display settings.
  Database related 'shipdb' command as a form: commanders (ranks, fixspecs), ship list, XP per rank, copy veterancy,
           munitions, research lists, fleet limits, fleet map icons, squadron hull. Research for ONE ship is on its Ships page.

The work folder mirrors BSG_Data so its contents can be merged into the game installation.
"""
import argparse, json, os, subprocess, sys, struct, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

if __package__ in (None, ""): sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deadlock_tools import config
from deadlock_tools import commander_editor as cmod
from deadlock_tools import localisation as loc
from deadlock_tools import shipdb as sd
from deadlock_tools import weapon_db as wd
from deadlock_tools import weapons as wmod

SIMPLE = {0x02: "bool", 0x08: "int", 0x0c: "float", 0x11: "int"}      # field type codes that can be written in place
ENUM_FIELDS = {"Faction": "faction", "ShipClass": "sclass"}
READONLY = {"name", "id"}
HIDDEN = {"AttachmentInfo", "CrewVeterancyRankEffects", "ArmamentInfoList"}


def tool_cmd(tool):
    """argv prefix that runs one of the command-line tools (python -m deadlock_tools <tool>) as its own process"""
    if getattr(sys, "frozen", False):
        return [sys.executable, tool]
    return [sys.executable, config.ZIPAPP, tool] if config.ZIPAPP else [sys.executable, "-m", "deadlock_tools", tool]


def run_cmd(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=config.PROJECT_DIR, timeout=timeout)


def num(x):
    return float("%.7g" % x) if isinstance(x, float) else x


class Project:
    def __init__(self, levels, dll, work, bundles):
        ab = lambda x: os.path.abspath(x) if x else x                  # the helper tools run from the script's folder, so no relative paths
        self.levels, self.dll, self.work, self.bundles = ab(levels), ab(dll), ab(work), ab(bundles)
        self.stage_root = os.path.join(self.work, ".deadlock-tools", "stage")
        self.commander_levels_path = os.path.join(self.work, ".deadlock-tools", "commander_ability_levels.json")
        self.game_data = os.path.join(self.work, "BSG_Data")
        self.work_paths = {kind: os.path.join(self.game_data, *parts) for kind, parts in config.WORK_PATHS.items()}
        levels, dll = self.levels, self.dll
        self.S = sd.Schema(dll)
        self.lock = threading.Lock()
        self.table_files = []
        for f in sd.files_of(levels):
            try: sd.Db(dll, f, self.S); self.table_files.append(f)
            except BaseException: pass
        if not self.table_files: raise SystemExit("no level file with the ship table found in %s" % levels)
        self._view = None

    def src_for(self, f, fresh=False):
        """Prefer the same-named file in the merge-ready work tree, else the configured default."""
        cand = os.path.join(self.work_paths["levels"], os.path.basename(f))
        return cand if (not fresh and os.path.exists(cand) and os.path.abspath(cand) != os.path.abspath(f)) else f

    def stage_dir(self, kind):
        if kind not in config.WORK_PATHS:
            raise ValueError("unknown work-tree file type: %s" % kind)
        return os.path.join(self.stage_root, kind)

    def prepare_stage(self, kind):
        import shutil
        staged = self.stage_dir(kind)
        if os.path.isdir(staged): shutil.rmtree(staged)
        os.makedirs(staged, exist_ok=True)
        source = self.work_paths[kind]
        if os.path.isdir(source):
            for filename in os.listdir(source):
                src = os.path.join(source, filename)
                if os.path.isfile(src): shutil.copy2(src, os.path.join(staged, filename))
        return {filename: (os.path.getsize(os.path.join(staged, filename)), os.stat(os.path.join(staged, filename)).st_mtime_ns)
                for filename in os.listdir(staged) if os.path.isfile(os.path.join(staged, filename))}

    def promote_stage(self, kind, before=None):
        import shutil
        staged = self.stage_dir(kind)
        target_dir = self.work_paths[kind]
        os.makedirs(target_dir, exist_ok=True)
        copied = []
        for filename in os.listdir(staged) if os.path.isdir(staged) else []:
            src = os.path.join(staged, filename)
            if not os.path.isfile(src): continue
            stat = os.stat(src); prior = (before or {}).get(filename)
            if prior == (stat.st_size, stat.st_mtime_ns): continue
            target = os.path.join(target_dir, filename)
            shutil.copy2(src, target); copied.append(target)
        return copied

    def run_staged(self, kind, cmd, timeout):
        """Run a CLI edit in its expected output folder, then promote changed files to the work tree."""
        with self.lock:
            before = self.prepare_stage(kind)
            result = run_cmd(cmd, timeout)
            if result.returncode == 0:
                self.promote_stage(kind, before)
        return result

    def view(self, fresh=False):
        path = self.src_for(self.table_files[0], fresh); key = (path, os.path.getmtime(path))
        cached = self._view
        if cached and cached[0] == key:
            return cached[1]
        db = sd.Db(self.dll, path, self.S)
        self._view = (key, db)
        return db

    def ships(self):
        db = self.view()
        return [{"name": s["name"], "faction": db.faction.get(s["Faction"], str(s["Faction"])), "class": db.sclass.get(s["ShipClass"], str(s["ShipClass"])),
                 "buildable": s["Buildable"], "cost": s["BuildCost"], "hull": num(s["MaxHullPoints"]), "fleet": s["FleetPointValue"]} for s in db.ships]

    def ship(self, name, fresh=False):
        db = self.view(fresh); s = db.find(name); fields = []
        for k, (off, code) in s["_off"].items():
            if k in HIDDEN: continue
            v = s.get(k); kind = SIMPLE.get(code)
            if k in ENUM_FIELDS:
                fields.append({"name": k, "type": "enum", "value": v, "options": {str(i): n for i, n in getattr(db, ENUM_FIELDS[k]).items()}})
            elif kind and k not in READONLY and isinstance(v, (int, float)): fields.append({"name": k, "type": kind, "value": num(v)})
            else: fields.append({"name": k, "type": "ro", "value": str(num(v) if not isinstance(v, (list, tuple, dict)) else v)})
        return {"name": s["name"], "fields": fields, "advantages": [db.eff(x) for x in s["AdvantageList"]], "penalties": [db.eff(x) for x in s["PenaltyList"]],
                "source": self.src_for(self.table_files[0], fresh),
                "research": self.research(name, fresh),
                "vet": {"ranks": [{"rank": r["Rank"], "xp": r["XPRequired"]} for r in db.res["CrewVeterancyRankInfo"]],
                        "effects": {str(i): n for i, n in db.effect.items()},
                        "current": [{"rank": rk["Rank"], "effects": [{"effect": e["Effect"], "value": num(e["BonusAmount"])} for e in rk["Effects"]]} for rk in s["CrewVeterancyRankEffects"]]}}

    def clone_ship(self, body):
        """Create a new ship by cloning an existing database row and game bundle."""
        import datetime, shutil
        donor_name = str(body.get("donor", "")).strip()
        new_name = str(body.get("name", "")).strip()
        display_name = str(body.get("displayName", new_name)).strip()
        bundle_name = str(body.get("bundleName", "")).strip()
        header = str(body.get("descriptionHeader", display_name)).strip()
        description = str(body.get("description", "")).strip() or "New ship."
        research_list = body.get("researchList", "capital")
        donor_layer = str(body.get("donorLayer", "work")).strip().lower()
        if donor_layer not in ("base", "work"):
            raise ValueError("Choose either the configured base folder or current work copy as the donor source.")
        if not donor_name or not new_name or not display_name or not bundle_name:
            raise ValueError("Choose a donor and provide the new ship name, display name, and bundle name.")
        if any(c in new_name for c in "\0\r\n|") or any(c in bundle_name for c in "\0\r\n/\\<>:\"|?*") or bundle_name.endswith((".", " ")):
            raise ValueError("Ship and bundle names must be plain names without path separators.")
        if bundle_name.lower().endswith(".dec"):
            raise ValueError("Enter a game bundle name without the .dec extension.")
        # Game ship bundles use lowercase StreamingAssets names. Normalize on
        # disk so newly cloned ships resolve on case-sensitive filesystems.
        bundle_name = bundle_name.lower()
        if research_list not in sd.UNLOCK_KEYS:
            raise ValueError("Choose a valid research list.")
        with self.lock:
            db0 = sd.Db(self.dll, self.src_for(self.table_files[0]), self.S)
            max_id = 0
            for level_file in self.table_files:
                current_db = sd.Db(self.dll, self.src_for(level_file), self.S)
                try: current_db.find(new_name)
                except SystemExit: pass
                else: raise ValueError("A ship named %r already exists in %s." % (new_name, os.path.basename(level_file)))
                current_db.find(donor_name)
                max_id = max(max_id, *(int(item["id"]) for item in current_db.ships))
            # The work overlay is the source of the user's active modded ship
            # configuration. Use base data only when explicitly selected.
            donor_paths = {}
            for level_file in self.table_files:
                base_path = os.path.join(self.levels, os.path.basename(level_file))
                work_path = self.src_for(level_file)
                if donor_layer == "base":
                    if not os.path.isfile(base_path):
                        raise ValueError("Configured base level file is missing: %s" % base_path)
                    donor_paths[os.path.basename(level_file)] = base_path
                else:
                    if not os.path.isfile(work_path) or os.path.abspath(work_path) == os.path.abspath(base_path):
                        raise ValueError("The selected ship has no level-data copy in the work folder: %s" % os.path.basename(level_file))
                    donor_paths[os.path.basename(level_file)] = work_path
            bundle_choice = str(body.get("donorBundle", "")).strip()
            bundle_source, _ = self.bundle_src(bundle_choice, prefer_work=(donor_layer == "work"), strict_layer=True)
            bundle_target = os.path.join(self.work_paths["bundles"], bundle_name)
            if os.path.exists(bundle_target):
                raise ValueError("A work bundle named %r already exists." % bundle_name)

            levels_root = self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)
            loc_target = os.path.join(self.game_data, "resources.assets")
            candidates = [loc_target, os.path.join(levels_root, "resources.assets"),
                          os.path.join(os.path.dirname(os.path.dirname(self.dll)), "resources.assets")]
            if self.bundles: candidates.insert(2, os.path.join(os.path.dirname(self.bundles), "resources.assets"))
            loc_source = next((p for p in candidates if os.path.isfile(p)), None)
            if not loc_source:
                raise ValueError("Could not find resources.assets under the configured game data paths.")
            catalogue = loc.Localisation(open(loc_source, "rb").read())
            def add_text(table, text):
                nonlocal catalogue
                info = catalogue.table(table); string_id = info["next_id"]
                updated = catalogue.update(table, catalogue.revision, string_id, True,
                    {language: text for language in info["languages"]})
                catalogue = loc.Localisation(updated)
                return string_id
            name_id = add_text("default", display_name)
            header_id = add_text("blueprint_descriptions", header)
            body_id = add_text("blueprint_descriptions", description)

            next_id = max_id + 1
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            staged = os.path.join(self.stage_root, "ship-clone-" + stamp)
            os.makedirs(staged, exist_ok=True)
            outputs = []
            try:
                for original in self.table_files:
                    db = sd.Db(self.dll, self.src_for(original), self.S)
                    donor_db = sd.Db(self.dll, donor_paths[os.path.basename(original)], self.S)
                    template = donor_db.find(donor_name)
                    mono = bytes(db.buf[db.base:db.base + db.size])
                    donor_index = donor_db.ships.index(template)
                    donor_mono = bytes(donor_db.buf[donor_db.base:donor_db.base + donor_db.size])
                    row_start = template["_off"]["name"][0]
                    row_end = donor_db.ships[donor_index + 1]["_off"]["name"][0] if donor_index + 1 < len(donor_db.ships) else donor_db.res["_off"]["CrewVeterancyRankInfo"][0]
                    old_id_start = template["_off"]["id"][0] - row_start
                    encoded = new_name.encode("utf-8")
                    name_field = struct.pack("<i", len(encoded)) + encoded
                    name_field += b"\0" * ((-len(name_field)) % 4)
                    delta = len(name_field) - old_id_start
                    row = bytearray(name_field + donor_mono[row_start + old_id_start:row_end])
                    struct.pack_into("<i", row, len(name_field), next_id)
                    for field, value in (("descriptionID", name_id), ("DescriptionHeaderID", header_id), ("DescriptionBodyID", body_id)):
                        offset = template["_off"][field][0] - row_start + delta
                        struct.pack_into("<i", row, offset, value)
                    array_end = db.res["_off"]["CrewVeterancyRankInfo"][0]
                    new_mono = bytearray(mono[:array_end] + bytes(row) + mono[array_end:])
                    struct.pack_into("<i", new_mono, db.res["_off"]["objects"][0], len(db.ships) + 1)
                    updated = sd.resize_object(bytes(db.buf), db.pid, bytes(new_mono))
                    out = os.path.join(staged, os.path.basename(original))
                    with open(out, "wb") as stream: stream.write(updated)
                    check = sd.Db(self.dll, out, self.S).find(new_name)
                    if check["id"] != next_id or check["descriptionID"] != name_id:
                        raise ValueError("Cloned ship record did not verify in %s." % os.path.basename(original))
                    outputs.append((out, os.path.join(self.work_paths["levels"], os.path.basename(original))))

                research_out = os.path.join(staged, "research-output")
                research_files = os.path.join(research_out, "levels")
                cmd = tool_cmd("shipdb") + ["addblueprint", "--levels", staged, "--dll", self.dll,
                    "--out", research_out, "--like", donor_name, "--list", research_list, "--keep-build-stats",
                    "--label", "".join(word[0] for word in new_name.split() if word)[:12] or "NEW", new_name]
                result = run_cmd(cmd, 120)
                if result.returncode:
                    raise RuntimeError("Research entries could not be added; nothing was saved:\n" + (result.stdout + result.stderr)[-6000:])
                for filename in os.listdir(research_files):
                    research_file = os.path.join(research_files, filename)
                    if os.path.isfile(research_file):
                        shutil.copy2(research_file, os.path.join(staged, filename))
                shutil.rmtree(research_out, ignore_errors=True)

                # Fleet/skirmish selection resolves ship IDs through the scene
                # factories as well as the ship and research tables. Add the
                # donor's prefab references for the new ID in every scene file.
                factory_out = os.path.join(staged, "factory-output")
                factory_cmd = tool_cmd("shipdb") + ["fleeticon", staged, new_name, donor_name,
                    "--factory", "all", "--add-missing", "--bundle-name", bundle_name,
                    "--dll", self.dll, "-o", factory_out]
                factory_result = run_cmd(factory_cmd, 120)
                if factory_result.returncode:
                    raise RuntimeError("Fleet/skirmish factory entries could not be added; nothing was saved:\n" +
                                        (factory_result.stdout + factory_result.stderr)[-6000:])
                factory_levels = os.path.join(factory_out, "levels")
                factory_files = [name for name in os.listdir(factory_levels)
                                 if os.path.isfile(os.path.join(factory_levels, name))] if os.path.isdir(factory_levels) else []
                for filename in factory_files:
                    shutil.copy2(os.path.join(factory_levels, filename), os.path.join(staged, filename))
                shutil.rmtree(factory_out, ignore_errors=True)

                for filename in os.listdir(staged):
                    path = os.path.join(staged, filename)
                    if os.path.isfile(path):
                        target = os.path.join(self.work_paths["levels"], filename)
                        outputs = [item for item in outputs if item[1] != target]
                        outputs.append((path, target))

                raw = open(bundle_source, "rb").read()
                if raw.startswith(b"UnityFS\0"):
                    from deadlock_tools.bundle_xor import DEFAULT_KEY, xor
                    raw = xor(raw, DEFAULT_KEY)
                elif bundle_source.lower().endswith(".dec"):
                    raw = wmod.xor_bytes(raw)
                    if not raw.startswith(b"UnityFS\0"):
                        raise ValueError("Selected .dec donor is not a UnityFS bundle.")
                    from deadlock_tools.bundle_xor import DEFAULT_KEY, xor
                    raw = xor(raw, DEFAULT_KEY)
                raw = wmod.rename_bundle_asset(raw, bundle_name, next_id)
                bundle_stage = os.path.join(staged, "ship-bundle")
                with open(bundle_stage, "wb") as stream: stream.write(raw)
                outputs.append((bundle_stage, bundle_target))
                loc_stage = os.path.join(staged, "resources.assets")
                with open(loc_stage, "wb") as stream: stream.write(catalogue.data)
                outputs.append((loc_stage, loc_target))

                # Fleet placement builds its collision-radius map by iterating
                # the ShipType enum. A new database row alone is not enough:
                # add the same ship ID to the working DLL so Skirmish and
                # campaign fleet placement can resolve its radius.
                enum_work_dll = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
                enum_source = enum_work_dll if os.path.isfile(enum_work_dll) else self.dll
                managed_candidates = [os.path.join(levels_root, "Managed"), os.path.dirname(self.dll)]
                if self.bundles:
                    managed_candidates.insert(1, os.path.join(os.path.dirname(self.bundles), "Managed"))
                managed = next((p for p in managed_candidates
                                if os.path.isfile(os.path.join(p, "UnityEngine.CoreModule.dll"))), None)
                if not managed:
                    raise ValueError("Could not find the game's Managed folder needed to register the new ship type.")
                build_root = config.PROJECT_DIR
                if config.ZIPAPP:
                    from zipfile import ZipFile
                    build_root = os.path.join(staged, "ship-enum-builder")
                    with ZipFile(config.ZIPAPP) as archive:
                        for name in ("scripts/add_ship_type_enum.sh", "scripts/add_ship_type_enum.cs"):
                            archive.extract(name, build_root)
                enum_candidate = os.path.join(staged, "Assembly-CSharp.ship-enum.dll")
                enum_script = os.path.join(build_root, "scripts", "add_ship_type_enum.sh")
                enum_result = run_cmd(["bash", enum_script, managed, enum_source, enum_candidate,
                                       str(next_id), "CustomShip%d" % next_id], 600)
                if enum_result.returncode or not os.path.isfile(enum_candidate):
                    raise RuntimeError("could not register the new ship type in the DLL; no files were saved:\n" +
                                       (enum_result.stdout + enum_result.stderr)[-8000:])
                outputs.append((enum_candidate, enum_work_dll))

                backup_root = os.path.join(self.game_data, ".deadlock-backups", "ship-clone-" + stamp)
                backups, promoted = {}, []
                try:
                    for _, target in outputs:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        if os.path.isfile(target):
                            if os.path.commonpath([os.path.abspath(target), os.path.abspath(self.game_data)]) == os.path.abspath(self.game_data):
                                backup = os.path.join(backup_root, os.path.relpath(target, self.game_data))
                            else:
                                backup = os.path.join(self.work, ".deadlock-backups", os.path.basename(backup_root),
                                                      os.path.relpath(target, self.work))
                            os.makedirs(os.path.dirname(backup), exist_ok=True); shutil.copy2(target, backup); backups[target] = backup
                    for source, target in outputs:
                        temp = target + ".ship-clone-tmp"; shutil.copy2(source, temp); os.replace(temp, target); promoted.append(target)
                except BaseException:
                    for target in reversed(promoted):
                        if target in backups: shutil.copy2(backups[target], target)
                        elif os.path.exists(target): os.unlink(target)
                    raise
            finally:
                shutil.rmtree(staged, ignore_errors=True)
            self._view = None
            return {"ok": True, "id": next_id, "name": new_name, "bundle": bundle_target,
                    "files": [os.path.basename(target) for _, target in outputs],
                    "backup": backup_root if backups else None, "message": result.stdout.strip()}

    def commanders(self, fresh=False):
        try:
            saved_ranks = json.load(open(self.commander_levels_path, encoding="utf-8"))
            saved_ranks = {int(key): int(value) for key, value in saved_ranks.items()}
        except FileNotFoundError:
            saved_ranks = {}
        except (ValueError, TypeError, OSError):
            saved_ranks = {}
        for f in self.table_files:
            path = self.src_for(f, fresh)
            try:
                data = open(path, "rb").read()
                result = cmod.read(data, self.S)
                for ability in result["abilities"]:
                    ability["unlockRank"] = saved_ranks.get(int(ability["id"]))
                result["source"] = path
                return result
            except LookupError:
                continue
        raise ValueError("No commander skill-tree tables were found in the configured level data.")

    def _commander_dll_candidate(self, transaction_dir, has_custom, unlock_ranks, needs_level_lookup=False):
        if not has_custom and not unlock_ranks and not needs_level_lookup:
            work_dll = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
            with open(work_dll if os.path.isfile(work_dll) else self.dll, "rb") as stream:
                if b"CustomCommanderAbilityLevels" not in stream.read(): return None
        import shutil
        work_dll = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
        source = work_dll if os.path.isfile(work_dll) else self.dll
        with open(source, "rb") as stream: source_bytes = stream.read()
        levels = self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)
        candidates = [os.path.join(levels, "Managed"), os.path.dirname(self.dll)]
        if self.bundles:
            candidates.insert(1, os.path.join(os.path.dirname(self.bundles), "Managed"))
        managed = next((path for path in candidates if os.path.isfile(os.path.join(path, "UnityEngine.CoreModule.dll"))), None)
        if not managed:
            raise ValueError("Could not find the game's Managed folder needed to build commander tree DLL support.")
        build_root = config.PROJECT_DIR
        if config.ZIPAPP:
            from zipfile import ZipFile
            build_root = os.path.join(transaction_dir, "builder")
            with ZipFile(config.ZIPAPP) as archive:
                for name in ("scripts/build_commander_modifiers.sh", "scripts/build_commander_ability_levels.sh",
                             "scripts/merge_custom_modules.cs", "dll_modules/CustomCommanderStatModifiers.cs",
                             "dll_modules/CustomCommanderAbilityLevels.cs"):
                    archive.extract(name, build_root)
        current = source
        if has_custom and b"CustomCommanderStatModifiers" not in source_bytes:
            output = os.path.join(transaction_dir, "Assembly-CSharp.commander-stat.dll")
            script = os.path.join(build_root, "scripts", "build_commander_modifiers.sh")
            result = run_cmd(["bash", script, managed, current, output], 600)
            if result.returncode or not os.path.isfile(output):
                raise RuntimeError("could not build commander stat effects; no level changes were saved:\n" + (result.stdout + result.stderr)[-10000:])
            current = output
        existing_levels = b"CustomCommanderAbilityLevels" in source_bytes
        if unlock_ranks or needs_level_lookup or existing_levels:
            template_path = os.path.join(build_root, "dll_modules", "CustomCommanderAbilityLevels.cs")
            template = open(template_path, encoding="utf-8").read()
            cases = "\n".join("            case %d: return %d;" % (int(ability_id), int(rank))
                                for ability_id, rank in sorted(unlock_ranks.items()))
            generated = os.path.join(transaction_dir, "CommanderAbilityRanks.Generated.cs")
            with open(generated, "w", encoding="utf-8") as stream:
                stream.write(template.replace("// GENERATED_RANK_CASES", cases))
            output = os.path.join(transaction_dir, "Assembly-CSharp.commander.dll")
            script = os.path.join(build_root, "scripts", "build_commander_ability_levels.sh")
            result = run_cmd(["bash", script, managed, current, output, generated], 600)
            if result.returncode or not os.path.isfile(output):
                raise RuntimeError("could not build commander ability unlock ranks; no level changes were saved:\n" + (result.stdout + result.stderr)[-10000:])
            current = output
        return current if current != source else None

    def _stage_commander_localisation(self, abilities, fresh, transaction_dir):
        if not abilities:
            return None
        target = os.path.join(self.game_data, "resources.assets")
        levels = self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)
        candidates = [os.path.join(levels, "resources.assets"),
                      os.path.join(os.path.dirname(os.path.dirname(self.dll)), "resources.assets")]
        if self.bundles:
            candidates.insert(1, os.path.join(os.path.dirname(self.bundles), "resources.assets"))
        source = target if os.path.isfile(target) and not fresh else next((p for p in candidates if os.path.isfile(p)), None)
        if not source:
            raise ValueError("Choose a game's resources.assets file before adding localized commander abilities.")
        with open(source, "rb") as stream:
            original = stream.read()
        catalogue = loc.Localisation(original)
        for ability in abilities:
            for field, text_key, fallback in (("titleId", "titleText", ability.get("name", "New ability")),
                                               ("descriptionId", "descriptionText", "Commander stat modifier")):
                text = str(ability.get(text_key, fallback)).strip()
                table = catalogue.table("default")
                string_id = table["next_id"]
                translations = {language: text for language in table["languages"]}
                updated = catalogue.update("default", catalogue.revision, string_id, True, translations)
                catalogue = loc.Localisation(updated)
                ability[field] = string_id
        output = os.path.join(transaction_dir, "resources.assets")
        with open(output, "wb") as stream:
            stream.write(catalogue.data)
        return output, target

    def save_commanders(self, body):
        assignments = body.get("assignments")
        ability_edits = body.get("abilities")
        new_abilities = body.get("newAbilities", [])
        submitted_ranks = body.get("unlockRanks", {})
        fresh = bool(body.get("fresh", False))
        if not isinstance(submitted_ranks, dict):
            raise ValueError("Commander unlock ranks must be an ability-to-rank mapping.")
        with self.lock:
            source_data = None
            for level_path in self.table_files:
                candidate = self.src_for(level_path, fresh)
                try:
                    source_data = open(candidate, "rb").read()
                    cmod.read(source_data, self.S)
                    break
                except LookupError:
                    source_data = None
            if source_data is None: raise ValueError("No commander skill-tree tables were found in the configured level data.")
            current_commander = cmod.read(source_data, self.S)
            known_ids = {int(item["id"]) for item in current_commander["abilities"]}
            known_ids.update(int(item.get("id", -1)) for item in new_abilities if isinstance(item, dict))
            unlock_ranks = {}
            for ability_id, rank in submitted_ranks.items():
                aid = int(ability_id)
                if aid not in known_ids: raise ValueError("unknown commander ability ID %s" % ability_id)
                if rank in (None, "", 0, "0"): continue
                unlock_rank = int(rank)
                if not 1 <= unlock_rank <= current_commander["rankCount"]:
                    raise ValueError("unlock rank must be between 1 and %d" % current_commander["rankCount"])
                unlock_ranks[aid] = unlock_rank
            before = self.prepare_stage("levels")
            transaction_dir = os.path.join(self.stage_root, "commander-transaction")
            if os.path.isdir(transaction_dir):
                import shutil
                shutil.rmtree(transaction_dir)
            os.makedirs(transaction_dir, exist_ok=True)
            localisation_output = self._stage_commander_localisation(new_abilities, fresh, transaction_dir)
            changed = []
            has_custom = False
            for source in self.table_files:
                path = self.src_for(source, fresh)
                data = open(path, "rb").read()
                try:
                    updated, checked = cmod.edit(data, self.S, assignments, ability_edits, new_abilities)
                except LookupError:
                    continue
                has_custom = has_custom or any(item["custom"] for item in checked["abilities"])
                target = os.path.join(self.stage_dir("levels"), os.path.basename(source))
                with open(target, "wb") as stream:
                    stream.write(updated)
                changed.append(os.path.basename(source))
            if not changed:
                raise ValueError("No commander skill-tree tables were found in the configured level data.")
            for ability in new_abilities:
                aid = int(ability["id"])
                if aid not in unlock_ranks: unlock_ranks[aid] = 1
            # The game's rank gate array has only three slots. Trees with more
            # than three choices need the per-ability lookup even when every
            # ability is still using its default unlock rank. Otherwise one
            # out-of-range CIC choice can prevent that branch from rendering.
            needs_level_lookup = any(len(choices) > 3 for choices in assignments.values())
            dll_candidate = self._commander_dll_candidate(
                transaction_dir, has_custom, unlock_ranks, needs_level_lookup
            )
            level_outputs = []
            staged = self.stage_dir("levels")
            for filename in os.listdir(staged):
                source = os.path.join(staged, filename)
                if not os.path.isfile(source): continue
                stat = os.stat(source)
                if (before or {}).get(filename) == (stat.st_size, stat.st_mtime_ns): continue
                level_outputs.append((source, os.path.join(self.work_paths["levels"], filename)))
            outputs = list(level_outputs)
            if localisation_output:
                outputs.append(localisation_output)
            if dll_candidate:
                outputs.append((dll_candidate, os.path.join(self.work_paths["dll"], os.path.basename(self.dll))))
            ranks_stage = os.path.join(transaction_dir, "commander_ability_levels.json")
            with open(ranks_stage, "w", encoding="utf-8") as stream:
                json.dump({str(key): value for key, value in sorted(unlock_ranks.items())}, stream, indent=2)
                stream.write("\n")
            outputs.append((ranks_stage, self.commander_levels_path))
            import shutil
            import datetime
            backup_root = os.path.join(self.game_data, ".deadlock-backups",
                                       "commander-editor-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
            backups = {}
            promoted = []
            try:
                for _, target in outputs:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.isfile(target):
                        if os.path.commonpath([os.path.abspath(target), os.path.abspath(self.game_data)]) == os.path.abspath(self.game_data):
                            backup = os.path.join(backup_root, os.path.relpath(target, self.game_data))
                        else:
                            backup = os.path.join(self.work, ".deadlock-backups", os.path.basename(backup_root),
                                                  os.path.relpath(target, self.work))
                        os.makedirs(os.path.dirname(backup), exist_ok=True)
                        shutil.copy2(target, backup); backups[target] = backup
                for source, target in outputs:
                    temporary = target + ".commander-tmp"
                    shutil.copy2(source, temporary)
                    os.replace(temporary, target)
                    promoted.append(target)
            except BaseException:
                for target in reversed(promoted):
                    if target in backups: shutil.copy2(backups[target], target)
                    elif os.path.exists(target): os.unlink(target)
                raise
            self._view = None
        return {"ok": True, "files": [os.path.basename(target) for _, target in level_outputs],
                "tables": changed, "dll": os.path.join(self.work_paths["dll"], os.path.basename(self.dll)) if dll_candidate else None,
                "localisation": os.path.join(self.game_data, "resources.assets") if localisation_output else None,
                "unlockRanks": unlock_ranks, "backup": backup_root if backups else None}

    # ---- squadrons (fighter squadron prefabs in sharedassets9.assets) ----
    def _squadron_source(self, fresh=False):
        from deadlock_tools import squadrons as sqmod
        original = os.path.join(self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels), sqmod.ASSET_FILE)
        if not os.path.isfile(original):
            raise ValueError("%s was not found in the level data folder" % sqmod.ASSET_FILE)
        return original, self.src_for(original, fresh)

    def squadrons(self, fresh=False):
        """every squadron with its editable fields, plus the ship-database names of its fighter type"""
        from deadlock_tools import squadrons as sqmod
        _original, source = self._squadron_source(fresh)
        with open(source, "rb") as stream:
            data = stream.read()
        names = {s["id"]: s["name"] for s in self.view(fresh).ships}
        out = []
        for squadron in sqmod.load(self.S, data):
            d = sqmod.describe(squadron)
            d["fighterShip"] = names.get(d["shipType"])           # SquadronShipType is a separate squadron enum, not a ship ID
            out.append(d)
        return {"source": source, "maxFighters": sqmod.MAX_FIGHTERS, "squadrons": out}

    def _promote(self, outputs, label):
        """copy [(staged bytes, target path)] into the work tree atomically, backing up what they replace;
        on any failure every target is restored. Returns the backup folder (or None)."""
        import datetime, shutil
        backup_root = os.path.join(self.game_data, ".deadlock-backups", label + "-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        backups, promoted = {}, []
        try:
            for _data, target in outputs:
                if os.path.isfile(target):
                    backup = os.path.join(backup_root, os.path.basename(target))
                    os.makedirs(backup_root, exist_ok=True); shutil.copy2(target, backup); backups[target] = backup
            for data, target in outputs:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                temporary = target + "." + label + "-tmp"
                with open(temporary, "wb") as stream:
                    stream.write(data)
                os.replace(temporary, target); promoted.append(target)
        except BaseException:
            for target in reversed(promoted):
                if target in backups: shutil.copy2(backups[target], target)
                elif os.path.exists(target): os.unlink(target)
            raise
        return backup_root if backups else None

    def _squadron_table_files(self, fresh=False):
        """[(original path, source path, bytes)] of every level file holding the SquadronDataTable"""
        from deadlock_tools import squadron_table as st
        out = []
        for original in sd.files_of(self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)):
            if not os.path.basename(original).startswith("level") or original.endswith(".resS"): continue
            source = self.src_for(original, fresh)
            with open(source, "rb") as stream:
                data = stream.read()
            try:
                if st.find(self.S, data): out.append((original, source, data))
            except Exception:
                continue
        if not out: raise ValueError("no level file holds the SquadronDataTable")
        return out

    def save_squadrons(self, body):
        """apply {name: {field key: value, 'MaxShipCount': n, 'ShipLoss': [..]}} to the prefabs in sharedassets9.assets,
        mirror fighter count / hull into the level SquadronDataTable, and promote everything together"""
        from deadlock_tools import squadrons as sqmod, squadron_table as st
        edits = body.get("edits") or {}
        if not isinstance(edits, dict) or not edits:
            raise ValueError("no squadron changes to save")
        fresh = bool(body.get("fresh"))
        _original, source = self._squadron_source(fresh)
        with self.lock:
            with open(source, "rb") as stream:
                data = stream.read()
            log, table_edits = [], {}
            for name, changes in edits.items():
                if not isinstance(changes, dict): raise ValueError("bad changes for %s" % name)
                data, lines = sqmod.apply(self.S, data, name, changes, scale_thresholds=bool(body.get("scale", True)))
                log += lines
                d = sqmod.describe(sqmod.find(sqmod.load(self.S, data), name))
                table_edits[d["squadronShipType"]] = {"MaxShipCount": d["count"], "MaxHullPoints": d["hull"]}
            outputs = [(data, os.path.join(self.work_paths["levels"], sqmod.ASSET_FILE))]
            for original, _src, level in self._squadron_table_files(fresh):
                pid_table = st.find(self.S, level)[3]
                wanted = {k: v for k, v in table_edits.items() if any(e["id"] == k for e in pid_table["objects"])}
                if not wanted: continue
                new, lines = st.apply(self.S, level, wanted)
                if new != level:
                    outputs.append((new, os.path.join(self.work_paths["levels"], os.path.basename(original))))
                    if not any(l.startswith("level copies") for l in log) and lines:
                        log.append("level copies (SquadronDataTable): " + "; ".join(lines))
            backup = self._promote(outputs, "squadrons")
        return {"ok": True, "dst": outputs[0][1], "files": [os.path.basename(t) for _d, t in outputs], "backup": backup, "log": log or ["no changes"]}

    # ---- launch bays / munition slots (visual bay editor) ----
    def bay_bundles(self):
        """ship bundles to edit: work-tree copies first (edits stack), then the configured bundle folder"""
        out, seen = [], set()
        for folder, work in ((self.work_paths["bundles"], True), (self.bundles, False)):
            if not folder or not os.path.isdir(folder): continue
            for name in sorted(os.listdir(folder)):
                path = os.path.join(folder, name)
                stem = name[:-4] if name.endswith(".dec") else name
                if not os.path.isfile(path) or stem in seen or name.startswith(".") or name.endswith((".manifest", ".tmp")): continue
                seen.add(stem); out.append({"name": stem, "path": path, "work": work})
        return out

    def _bundle_ship(self, path):
        """the ship-database entry whose name matches the bundle name (colonialmercurymk2 -> Colonial Mercury MK2)"""
        stem = os.path.basename(path); stem = stem[:-4] if stem.endswith(".dec") else stem
        key = lambda t: "".join(ch for ch in t.lower() if ch.isalnum())
        return next((s for s in self.view().ships if key(s["name"]) == key(stem)), None)

    def bays(self, path):
        from deadlock_tools import bays as bymod
        if not os.path.isfile(path): raise ValueError("bundle not found: %s" % path)
        layout = bymod.read_layout(path)
        ship = self._bundle_ship(path)
        layout["ship"] = {"name": ship["name"], "fighterSlots": ship["FighterSlots"], "utilitySlots": ship["UtilityFighterSlots"],
                          "munitionSlots": ship["MunitionSlots"], "id": ship["id"]} if ship else None
        layout["path"] = path
        return layout

    def save_bays(self, body):
        """apply a bay layout to the bundle, promote it into the work tree and lower the ship's slot counts for removals"""
        import datetime, shutil, tempfile
        from deadlock_tools import bays as bymod
        path = body["path"]
        if not os.path.isfile(path): raise ValueError("bundle not found: %s" % path)
        stem = os.path.basename(path); stem = stem[:-4] if stem.endswith(".dec") else stem
        target = os.path.join(self.work_paths["bundles"], stem)
        ship = self._bundle_ship(path)
        with self.lock:
            fd, staged = tempfile.mkstemp(prefix=".bays-", dir=self.stage_root if os.path.isdir(self.stage_root) else None); os.close(fd)
            try:
                result = bymod.apply(path, staged, body.get("layout") or {})
                backup = None
                if os.path.isfile(target):
                    backup_root = os.path.join(self.game_data, ".deadlock-backups", "bays-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
                    os.makedirs(backup_root, exist_ok=True); backup = os.path.join(backup_root, stem); shutil.copy2(target, backup)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(staged, target + ".bays-tmp"); os.replace(target + ".bays-tmp", target)
            finally:
                if os.path.exists(staged): os.unlink(staged)
        edits = []
        if ship and (result["removedSlots"] or result["removedMunitions"]):
            fighter, utility = ship["FighterSlots"], ship["UtilityFighterSlots"]
            for k in result["removedSlots"]:                     # fighter slots come first, then utility slots
                if k < fighter: fighter -= 1
                else: utility -= 1
            edits = [{"ship": ship["name"], "field": "FighterSlots", "value": max(0, fighter)},
                     {"ship": ship["name"], "field": "UtilityFighterSlots", "value": max(0, utility)},
                     {"ship": ship["name"], "field": "MunitionSlots", "value": max(0, ship["MunitionSlots"] - result["removedMunitions"])}]
            edits = [e for e in edits if e["value"] != ship[e["field"]]]
            if edits: result["log"] += self.apply(edits)[-len(edits):]
        elif result["removedSlots"] or result["removedMunitions"]:
            result["log"].append("WARNING: no ship-database entry matches %s; lower its FighterSlots / UtilityFighterSlots / MunitionSlots on the Ships page" % stem)
        result.update(dst=target, backup=backup)
        return result

    def allocations(self, fresh=False):
        """squadron types (SquadronDataTable) and every carrier, with what each carrier may launch"""
        from deadlock_tools import squadron_table as st
        files = self._squadron_table_files(fresh)
        res = st.find(self.S, files[0][2])[3]
        db = self.view(fresh)
        carriers = [{"id": s["id"], "name": s["name"], "faction": s["Faction"], "factionName": db.faction.get(s["Faction"], str(s["Faction"])),
                     "fighterSlots": s["FighterSlots"], "utilitySlots": s["UtilityFighterSlots"]}
                    for s in db.ships if s.get("FighterSlots", 0) or s.get("UtilityFighterSlots", 0)]
        types = []
        for e in st.entries(res):
            e = dict(e); e.pop("Icon", None)
            e["fighterShip"] = next((s["name"] for s in db.ships if s["id"] == e["ShipType"]), None)
            types.append(e)
        return {"files": [os.path.basename(o) for o, _s, _d in files], "source": files[0][1], "factions": {str(k): v for k, v in db.faction.items()},
                "fields": {k: {"kind": v[0], "label": v[1], "help": v[2]} for k, v in st.FIELDS.items()},
                "types": types, "carriers": carriers, "ships": [{"id": s["id"], "name": s["name"]} for s in db.ships]}

    def save_allocations(self, body):
        """apply {type id: {field: value, 'shipExclusionList': [ids]}} to every SquadronDataTable copy"""
        from deadlock_tools import squadron_table as st
        edits = {int(k): v for k, v in (body.get("edits") or {}).items()}
        if not edits: raise ValueError("no squadron allocation changes to save")
        with self.lock:
            outputs, log = [], []
            for original, _src, level in self._squadron_table_files(bool(body.get("fresh"))):
                new, lines = st.apply(self.S, level, edits)
                log = log or lines
                if new != level: outputs.append((new, os.path.join(self.work_paths["levels"], os.path.basename(original))))
            backup = self._promote(outputs, "squadron-allocations") if outputs else None
            self._view = None
        return {"ok": True, "files": [os.path.basename(t) for _d, t in outputs], "backup": backup, "log": log or ["no changes"]}

    # ---- research (the war-room blueprint lists: which campaign can research the ship, for how many RP) ----
    def _war_path(self, fresh=False):
        for f in self.table_files:
            p = self.src_for(f, fresh)
            try: sd.load_wardata(self.S, open(p, "rb").read()); return p
            except LookupError: continue
        return None

    def research(self, name, fresh=False):
        p = self._war_path(fresh)
        if not p: return None
        db = self.view(fresh); s = db.find(name); sid = s["id"]; cls = str(db.sclass.get(s["ShipClass"], "")).lower()
        pid, off, size, res = sd.load_wardata(self.S, open(p, "rb").read()); rows = []
        for it in res["BlueprintInfo"]:
            row = None
            for key in ("CapitalShipUnlocks", "BattlestarShipUnlocks"):
                for u in it[key]:
                    if u["ODOMECMDGKJ"] == sid and row is None:
                        row = {"campaign": it["Campaign"], "on": True, "key": key, "cost": u["GKIMOHIEFNI"], "mission": u["GEIBBHBCKAM"], "label": u["NPFHKIHKKIC"], "unlocked": bool(u["HJGFMHOLHPG"])}
            rows.append(row or {"campaign": it["Campaign"], "on": False, "key": "BattlestarShipUnlocks" if "battlestar" in cls else "CapitalShipUnlocks", "cost": 3000, "mission": -1, "label": "Mc", "unlocked": False})
        return rows

    def _set_research(self, dst, name, rows):
        buf = open(dst, "rb").read()
        try: pid, off, size, res = sd.load_wardata(self.S, buf)
        except LookupError: return                                         # this level file has no research table
        mono = bytearray(buf[off:off + size]); sd.check_unlock_layout(bytes(mono), res)   # refuses to edit if the entry layout is not the expected one
        sid = sd.Db(self.dll, dst, self.S).find(name)["id"]; want = {int(r["campaign"]): r for r in rows}; jobs = []
        pk = lambda u: sd.pack_unlock(u["ODOMECMDGKJ"], u["GEIBBHBCKAM"], u["GKIMOHIEFNI"], u["NPFHKIHKKIC"], u["HJGFMHOLHPG"], u["COPOFKMOPIH"])
        for ci, it in enumerate(res["BlueprintInfo"]):
            r = want.get(it["Campaign"])
            if r is None: continue
            key = r["key"]
            if key not in sd.UNLOCK_KEYS.values(): raise ValueError("bad research list %r" % key)
            cost, mission, label = int(float(r["cost"])), int(float(r["mission"])), str(r["label"])
            if not 0 <= cost <= 10 ** 7: raise ValueError("research cost must be 0 to 10000000")
            if not -1 <= mission <= 10 ** 4: raise ValueError("mission must be -1 (none) or a mission number")
            if len(label) > 12 or not label.isascii(): raise ValueError("label: at most 12 plain characters")
            cpos, first, end = sd.list_span(res, ci, key); new = []; placed = False
            for u in it[key]:
                if u["ODOMECMDGKJ"] != sid: new.append(pk(u)); continue
                if r["on"] and not placed: new.append(sd.pack_unlock(sid, mission, cost, label, bool(r["unlocked"]), bool(u["COPOFKMOPIH"]))); placed = True
            if r["on"] and not placed: new.append(sd.pack_unlock(sid, mission, cost, label, bool(r["unlocked"]), False))
            jobs.append((cpos, end, struct.pack("<i", len(new)) + b"".join(new)))
        for cpos, end, blob in sorted(jobs, reverse=True): mono[cpos:end] = blob
        open(dst, "wb").write(sd.resize_object(buf, pid, bytes(mono)))
        p2, o2, s2, r2 = sd.load_wardata(self.S, open(dst, "rb").read()); sd.Db(self.dll, dst, self.S)
        for it in r2["BlueprintInfo"]:
            r = want.get(it["Campaign"])
            if r is None: continue
            got = [u for u in it[r["key"]] if u["ODOMECMDGKJ"] == sid]
            if bool(got) != bool(r["on"]): raise ValueError("verification failed for the research list of %s (campaign %s)" % (name, it["Campaign"]))
            if got and (got[0]["GKIMOHIEFNI"], got[0]["GEIBBHBCKAM"], got[0]["NPFHKIHKKIC"], bool(got[0]["HJGFMHOLHPG"])) != (int(float(r["cost"])), int(float(r["mission"])), str(r["label"]), bool(r["unlocked"])):
                raise ValueError("verification failed for the research entry of %s (campaign %s)" % (name, it["Campaign"]))

    def _vet_clean(self, db, ranks):
        valid = {r["Rank"] for r in db.res["CrewVeterancyRankInfo"]}; clean = []
        for r in sorted(ranks, key=lambda x: int(x["rank"])):
            rk = int(r["rank"]); effs = []; seen = set()
            if rk not in valid: raise ValueError("rank %d does not exist in this game (ranks: %s)" % (rk, sorted(valid)))
            for e in r["effects"]:
                eid = int(e["effect"])
                if eid not in db.effect: raise ValueError("unknown effect %s" % eid)
                if eid in seen: raise ValueError("rank %d lists %s twice" % (rk, db.effect[eid]))
                seen.add(eid); effs.append((eid, float(e["value"])))
            if effs: clean.append((rk, effs))               # a rank with no effects is simply left out
        return clean

    def _set_vet(self, db, dst, name, ranks):
        """replace a ship's whole veterancy list (the object changes size, so the file is written and reloaded)"""
        s = db.find(name); clean = self._vet_clean(db, ranks)
        blob = struct.pack("<i", len(clean)) + b"".join(struct.pack("<ii", rk, len(effs)) + b"".join(struct.pack("<if", e_, v_) for e_, v_ in effs) for rk, effs in clean)
        ts, te = db.veterancy_span(s); mono = bytes(db.buf[db.base:db.base + db.size])
        open(dst, "wb").write(sd.resize_object(bytes(db.buf), db.pid, mono[:ts] + blob + mono[te:]))
        return sd.Db(self.dll, dst, self.S), clean

    def apply(self, edits, fresh=False):
        log = []; os.makedirs(self.work_paths["levels"], exist_ok=True)
        scalar = [e for e in edits if "field" in e]; vets = [e for e in edits if "vet" in e]; resea = [e for e in edits if "research" in e]
        with self.lock:
            for f in self.table_files:
                db = sd.Db(self.dll, self.src_for(f, fresh), self.S)
                for e in scalar:
                    s = db.find(e["ship"]); fld = e["field"]
                    if fld not in s["_off"] or fld in READONLY or not isinstance(s[fld], (int, float)): raise ValueError("%s cannot be edited" % fld)
                    off, code = s["_off"][fld]; kind = SIMPLE.get(code); v = e["value"]
                    if kind == "float": db.write_float(off, float(v))
                    elif kind == "int":
                        fv = float(v)
                        if not fv.is_integer() or abs(fv) > 2 ** 31 - 1: raise ValueError("%s needs a whole number" % fld)
                        db.write_int(off, int(fv))
                    elif kind == "bool": struct.pack_into("<B", db.buf, db.base + off, 1 if v in (True, 1, "1", "true", "True") else 0)
                    else: raise ValueError("%s is not a simple number/flag" % fld)
                dst = os.path.join(self.work_paths["levels"], os.path.basename(f)); config.write(dst, db.buf, f)
                wanted = {}
                for e in vets:
                    db, clean = self._set_vet(db, dst, e["ship"], e["vet"]); wanted[e["ship"]] = clean
                for e in resea: self._set_research(dst, e["ship"], e["research"])
                chk = sd.Db(self.dll, dst, self.S)
                for e in scalar:
                    got = chk.find(e["ship"])[e["field"]]; want = e["value"]
                    ok = abs(float(got) - float(want)) <= 1e-5 * max(1.0, abs(float(want))) if not isinstance(got, bool) else bool(got) == (want in (True, 1, "1", "true", "True"))
                    if not ok: raise ValueError("verification failed for %s.%s: wrote %s, read back %s" % (e["ship"], e["field"], want, got))
                for name, clean in wanted.items():
                    have = {rk["Rank"]: {x["Effect"]: x["BonusAmount"] for x in rk["Effects"]} for rk in chk.find(name)["CrewVeterancyRankEffects"]}
                    want = {rk: dict(effs) for rk, effs in clean}
                    if set(have) != set(want) or any(set(have[k]) != set(want[k]) or any(abs(have[k][i] - want[k][i]) > 1e-4 for i in want[k]) for k in want):
                        raise ValueError("verification failed for the veterancy of %s" % name)
                log.append("wrote %s" % dst)
            for e in scalar: log.append("%s.%s = %s" % (e["ship"], e["field"], e["value"]))
            for e in resea: log.append("%s: research entries updated (%d campaign(s))" % (e["ship"], len(e["research"])))
            for e in vets: log.append("%s: veterancy updated (%d rank(s) with effects)" % (e["ship"], len([r for r in e["vet"] if r["effects"]])))
        self._view = None
        return log

    # ---- the other command-line commands (shipdb) ----
    def tools(self):
        assets = os.path.join(self.levels, "sharedassets9.assets")
        out = []
        for t in TOOLS:
            fields = [dict(f, default=(assets if f["default"] == "@assets" and os.path.isfile(assets) else "" if f["default"] == "@assets" else f["default"])) for f in t["fields"]]
            out.append({k: t.get(k) for k in ("id", "group", "title", "help", "current")} | {"writes": bool(t.get("writes")), "fields": fields})
        return out

    def run_tool(self, tid, v, fresh=False):
        t = TOOLS_BY_ID.get(tid)
        if not t: raise ValueError("unknown tool")
        argv = t["build"](self, v or {})
        if "--levels" not in argv: argv += ["--levels", self.levels]
        argv += ["--dll", self.dll]
        argv += ["-o", self.stage_root] + (["--fresh"] if fresh else [])
        command = tool_cmd("shipdb") + argv
        r = self.run_staged("levels", command, 900) if t.get("writes") else run_cmd(command, 900)
        if t.get("writes"): self._view = None
        output = (r.stdout + r.stderr).replace(self.stage_root, self.work)
        return {"ok": r.returncode == 0, "output": output[-30000:] if t.get("writes") else output, "cmd": "shipdb " + " ".join(argv)}

    # ---- bundles (weapons) ----
    def bundle_files(self):
        def files(folder):
            if not folder or not os.path.isdir(folder): return set()
            return {name for name in os.listdir(folder)
                    if not name.startswith(".") and not name.endswith(".bak") and not name.endswith(".manifest")
                    and os.path.isfile(os.path.join(folder, name))}
        work_files, source_files = files(self.work_paths["bundles"]), files(self.bundles)
        normalise = lambda value: "".join(char for char in value.lower() if char.isalnum())
        ship_names = {normalise(ship["name"]) for ship in self.view().ships}
        def is_known_ship(name):
            stem = name[:-4] if name.lower().endswith(".dec") else os.path.splitext(name)[0]
            key = normalise(stem)
            return any(key == ship or key.endswith(ship) for ship in ship_names)
        # The configured game folder can contain hundreds of non-ship bundles.
        # Match its files against the live ship database; keep all work copies so
        # directly imported mod bundles remain available even before DB records exist.
        return sorted(work_files | {name for name in source_files if is_known_ship(name)})

    def base_bundle_files(self):
        if not self.bundles or not os.path.isdir(self.bundles): return []
        return sorted(name for name in os.listdir(self.bundles)
                      if not name.startswith(".") and not name.endswith((".bak", ".manifest"))
                      and os.path.isfile(os.path.join(self.bundles, name)))

    def bundle_form(self):
        ap = wmod.build_parser(); out = []
        ability_group = {
            option: ability.replace("_", " ").capitalize()
            for ability, fields in wmod.SUPPORT_SETTINGS.items()
            for option in (ability, *fields)
        }
        for label, prefixes in (
            ("Micro-FTL", ("mini_jump", "jump_")),
            ("Predictive Augury", ("augury",)),
            ("Stealth", ("stealth",)),
        ):
            ability_group.update({a.dest: label for a in ap._actions if a.dest.startswith(prefixes)})
        for g in ap._action_groups:
            if g.title in ("positional arguments", "options"): continue
            for a in g._group_actions:
                if not a.option_strings or a.dest in ("help", "out", "bundles", "inspect") or a.help == argparse.SUPPRESS: continue
                donor_choice = a.dest in ("defender_donor", "celestra_donor", "stealth")
                choices = self.bundle_files() if donor_choice else (list(a.choices) if a.choices else None)
                kind = "choice" if donor_choice else "bool" if isinstance(a, argparse._StoreTrueAction) else "choice" if a.choices else "list" if isinstance(a, argparse._AppendAction) else "text"
                out.append({"group": g.title, "ability": ability_group.get(a.dest), "dest": a.dest, "flag": a.option_strings[-1], "kind": kind, "help": (a.help or "").replace("%%", "%"), "choices": choices,
                            "default": None if a.default in (None, False) else str(a.default), "metavar": a.metavar or ""})
        return out

    def bundle_src(self, bundle, prefer_work=True, strict_layer=False):
        """(path, file name) of the bundle chosen on the Ships page"""
        if os.path.isabs(bundle):                                      # a path picked with the Browse button
            if not os.path.isfile(bundle): raise ValueError("bundle file not found: %s" % bundle)
            return bundle, os.path.basename(bundle)
        staged = os.path.join(self.work_paths["bundles"], bundle)
        source = os.path.join(self.bundles, bundle) if self.bundles else ""
        if prefer_work and os.path.isfile(staged): return staged, bundle
        if prefer_work and strict_layer:
            raise ValueError("The selected donor bundle is not in the work folder: %s" % bundle)
        if source and os.path.isfile(source): return source, bundle
        if not prefer_work and os.path.isfile(staged):
            raise ValueError("The selected donor bundle is not in the configured base folder: %s" % bundle)
        raise ValueError("choose a bundle file")

    def import_bundle(self, bundle):
        """Copy a selected default bundle into the editable work overlay."""
        import shutil, tempfile, time
        source, filename = self.bundle_src(bundle)
        bundle_dir = self.work_paths["bundles"]
        os.makedirs(bundle_dir, exist_ok=True)
        target = os.path.join(bundle_dir, filename)
        if config.same_file(source, target): return {"path": target, "backup": None, "imported": False}
        backup = None
        with self.lock:
            if os.path.isfile(target):
                backup = target + ".bak"
                if os.path.exists(backup): backup = target + "." + time.strftime("%Y%m%d-%H%M%S") + ".bak"
                shutil.copy2(target, backup)
            fd, temporary = tempfile.mkstemp(prefix=".bundle-import-", dir=bundle_dir)
            os.close(fd)
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
        return {"path": target, "backup": backup, "imported": True}

    def run_xor(self, bundle, mode, key=""):
        """Run bundle conversion; encrypted output is promoted into the work tree."""
        if mode not in ("decrypt", "encrypt", "check"): raise ValueError("bad mode")
        bundle = bundle.strip()
        if bundle and not os.path.isdir(bundle):
            bundle, _ = self.bundle_src(bundle)
        cmd = tool_cmd("xor") + [mode]
        if bundle:
            cmd.append(bundle)
        cmd += ["-o", self.stage_root]
        if self.bundles:
            cmd += ["--bundles", self.bundles]
        if key.strip():
            key = key.strip()
            bytes.fromhex(key)
            cmd += ["--key", key]
        if mode == "encrypt":
            r = self.run_staged("bundles", cmd, 300)
        else:
            with self.lock: r = run_cmd(cmd, 300)
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).replace(self.stage_root, self.work), "cmd": " ".join(cmd[3:])}

    def localisation(self, body):
        import tempfile

        staged = os.path.join(self.game_data, "resources.assets")
        requested = body.get("source") or ""
        levels = self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)
        if requested:
            requested = os.path.abspath(os.path.expanduser(requested))
        candidates = [os.path.join(levels, "resources.assets"),
                      os.path.join(os.path.dirname(os.path.dirname(self.dll)), "resources.assets")]
        if self.bundles:
            candidates.insert(1, os.path.join(os.path.dirname(self.bundles), "resources.assets"))
        with self.lock:
            source = staged if os.path.isfile(staged) else requested or next(
                (path for path in candidates if os.path.isfile(path)), None)
            if not source or not os.path.isfile(source):
                raise ValueError("Choose resources.assets from the game's BSG_Data folder.")
            with open(source, "rb") as stream:
                original = stream.read()
            catalogue = loc.Localisation(original)
            table = body.get("table", "default")
            if "edit" in body:
                edit = body["edit"]
                output = catalogue.update(table, edit["revision"], edit["id"], edit["create"],
                                          edit["translations"], edit.get("note", ""))
                if output != original:
                    os.makedirs(self.game_data, exist_ok=True)
                    fd, temporary = tempfile.mkstemp(prefix=".localisation-", dir=self.game_data)
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(output)
                        with open(source, "rb") as stream:
                            if stream.read() != original:
                                raise ValueError("Localisation changed on disk. Reload before saving.")
                        if source != staged and os.path.exists(staged):
                            raise ValueError("A working localisation file appeared. Reload before saving.")
                        os.replace(temporary, staged)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                    source, catalogue = staged, loc.Localisation(output)
            result = catalogue.table(table)
            result.update({
                "source": source, "staged": source == staged, "revision": catalogue.revision,
                "tables": [name for name in loc.TABLE_NAMES if name in catalogue.tables],
            })
            if body.get("include_ships"):
                result["ships"] = {ship["name"]: [
                    {"table": related, "label": label, "id": ship[field]}
                    for field, (related, label) in loc.SHIP_REFERENCES.items()
                    if type(ship.get(field)) is int and ship[field] > 0
                ] for ship in self.view().ships}
            return result

    def armament_sources(self, kind):
        from pathlib import Path

        levels = Path(self.levels)
        if not levels.is_dir():
            levels = levels.parent
        required = wd.SCENES if kind == "munitions" else (wd.SCRIPTS, wd.SHARED, *wd.SCENES)
        resolved = wd.source_files(levels, Path(self.game_data))
        files = {name: resolved[name] for name in required}
        missing = [name for name, path in files.items() if not path.is_file()]
        if missing:
            raise ValueError(f"Missing armament files in the level-data folder ({levels}): " + ", ".join(missing))
        return levels, files

    def stage_armaments(self, sources, original, changes):
        import shutil
        import tempfile
        from pathlib import Path

        if not changes:
            return []
        os.makedirs(self.game_data, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".armaments-", dir=self.game_data) as directory:
            patch = Path(directory)
            backup = patch / "before"
            backup.mkdir()
            for name, data in changes.items():
                if sources[name].read_bytes() != original[name]:
                    raise ValueError(f"{name} changed on disk. Reload before saving.")
                target = Path(self.game_data) / name
                if target.is_file():
                    if target != sources[name]:
                        raise ValueError(f"A working copy of {name} appeared. Reload before saving.")
                    shutil.copy2(target, backup / name)
                (patch / name).write_bytes(data)
            committed = []
            try:
                for name in changes:
                    os.replace(patch / name, Path(self.game_data) / name)
                    committed.append(name)
            except OSError:
                for name in reversed(committed):
                    target = Path(self.game_data) / name
                    previous = backup / name
                    if previous.exists():
                        os.replace(previous, target)
                    else:
                        target.unlink()
                raise
        return [str(Path(self.game_data) / name) for name in committed]

    def armaments(self, body):
        from hashlib import sha256

        kind, edit = body.get("kind"), body.get("edit")
        with self.lock:
            game, paths = self.armament_sources(kind)
            result = {"source": str(game), "output": []}
            if kind == "weapons":
                original = {name: path.read_bytes() for name, path in paths.items()}
                data = wd.WeaponDatabase(original, self.S)
                if edit is not None:
                    key, changes = data.update(edit)
                    verified = wd.WeaponDatabase({**original, **changes}, self.S)
                    row = next(row for row in verified.rows() if row["id"] == key)
                    if row["name"] != edit["name"].strip() or any(
                            abs(row["fields"][field] - value) > max(1e-5, abs(value)*1e-6)
                            for field, value in edit["fields"].items()):
                        raise ValueError("The weapon failed its round-trip check.")
                    if edit["clone"] and next(row for row in verified.rows() if row["id"] == edit["id"]) != next(
                            row for row in data.rows() if row["id"] == edit["id"]):
                        raise ValueError("Cloning changed the donor weapon.")
                    result.update(id=key, output=self.stage_armaments(paths, original, changes))
                    data = verified
                result.update(rows=data.rows(), revision=data.revision, fields=wd.FIELDS)
            elif kind == "munitions":
                original = {name: paths[name].read_bytes() for name in wd.SCENES}

                def revision(files):
                    return sha256(b"".join(files.values())).hexdigest()

                if edit is not None:
                    if edit.get("revision") != revision(original) or type(edit.get("id")) is not int:
                        raise ValueError("Reload the munition table before saving.")
                    key = edit["id"]
                    submitted = edit.get("fields")

                    def fields_for(data):
                        matching = [row["fields"] for row in wd.munition_rows(data, self.S) if row["id"] == key]
                        if len(matching) != 1:
                            raise ValueError(f"Expected a single munition #{key}.")
                        return matching[0]

                    baseline = fields_for(original["level5"])
                    if not isinstance(submitted, dict) or submitted.keys() != baseline.keys():
                        raise ValueError("Munition edit has missing or unsupported stats.")
                    edited = {field: value for field, value in submitted.items() if value != baseline[field]}
                    changes = {}
                    if edited:
                        for name, data in original.items():
                            output = wd.update_munition(data, self.S, key, {**fields_for(data), **edited})
                            verified = fields_for(output)
                            if any(abs(verified[field] - value) > max(1e-5, abs(value)*1e-6)
                                   for field, value in edited.items()):
                                raise ValueError(f"The munition failed its round-trip check in {name}.")
                            if output != data:
                                changes[name] = output
                    result.update(id=key, output=self.stage_armaments(paths, original, changes))
                    original = {**original, **changes}
                result.update(rows=wd.munition_rows(original["level5"], self.S),
                              revision=revision(original), fields=wd.MUNITION_FIELDS)
            else:
                raise ValueError("Unknown armament table.")
            return result

    def bundle_layout(self, bundle):
        with self.lock:
            path, _ = self.bundle_src(bundle)
            result = wmod.bundle_mount_layout(path)
            mount = self.S.typedef_index("WeaponMountPoint")
            signature = next(sig for name, sig, _ in self.S.fields(mount) if name == "WeaponType")
            _, enum_index, _ = sd.Reader(self.S, b"").token(signature, 2)
            result["weapons"] = sd.enum_names(self.S, enum_index - 1)
            working = os.path.join(self.game_data, "level5")
            if os.path.isfile(working):
                try:
                    _, _, factory = sd.load_factory(self, working, "WeaponFactory")
                except LookupError:
                    pass
                else:
                    for row in factory["objects"]:
                        result["weapons"].setdefault(row["id"], row["name"])
            return result

    def run_bundle(self, bundle, opts, prefab_fields=None, overwrite=False, mount_layout=None):
        src, bundle = self.bundle_src(bundle)
        ap = wmod.build_parser()
        stem = bundle[:-4] if bundle.endswith(".dec") else bundle
        inspect = bool(opts.get("list_mounts") or opts.get("inspect"))
        plain = src.lower().endswith(".dec") if overwrite else bool(opts.get("no_encrypt"))
        outname = stem + (".dec" if plain else "")
        dst = None if inspect else (src if overwrite else os.path.join(self.work_paths["bundles"], outname))
        cmd = tool_cmd("weapons") + [src, "-o", self.stage_root] + (["--bundles", self.bundles] if self.bundles else [])
        for a in ap._actions:
            v = opts.get(a.dest)
            if not a.option_strings or a.dest in ("help", "out", "bundles", "encrypt", "mount_layout") or v in (None, "", False): continue
            if isinstance(a, argparse._StoreTrueAction): cmd.append(a.option_strings[-1])
            elif isinstance(a, argparse._AppendAction):
                for line in str(v).splitlines():
                    if line.strip(): cmd += [a.option_strings[-1], line.strip()]
            else: cmd += [a.option_strings[-1], str(v)]
        if prefab_fields:
            cmd += ["--prefab-fields", json.dumps(prefab_fields)]
        backup = None
        if overwrite and inspect: raise ValueError("inspection cannot overwrite a bundle")
        if overwrite:
            if plain and "--no-encrypt" not in cmd: cmd.append("--no-encrypt")
            if not plain: cmd = [arg for arg in cmd if arg != "--no-encrypt"]
        with self.lock:
            if not inspect: self.prepare_stage("bundles")
            produced = os.path.join(self.stage_dir("bundles"), outname) if not inspect else None
            if produced and os.path.exists(produced): os.unlink(produced)
            if mount_layout is None:
                r = run_cmd(cmd, 900)
            else:
                import tempfile
                with tempfile.TemporaryDirectory(prefix="layout-", dir=self.stage_root) as directory:
                    spec = os.path.join(directory, "mounts.json")
                    with open(spec, "w", encoding="utf-8") as stream:
                        json.dump(mount_layout, stream)
                    r = run_cmd(cmd + ["--mount-layout", spec], 900)
            if r.returncode == 0 and not inspect:
                if not os.path.isfile(produced):
                    r = subprocess.CompletedProcess(cmd, 1, r.stdout, r.stderr + "\nExpected bundle output was not created: " + str(produced))
                else:
                    import shutil, tempfile, time
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if overwrite:
                        backup = src + ".bak"
                        if os.path.exists(backup): backup = src + "." + time.strftime("%Y%m%d-%H%M%S") + ".bak"
                        shutil.copy2(src, backup)
                    fd, temporary = tempfile.mkstemp(prefix=".bundle-save-", dir=os.path.dirname(dst)); os.close(fd)
                    try:
                        shutil.copy2(produced, temporary); os.replace(temporary, dst)
                    finally:
                        if os.path.exists(temporary): os.unlink(temporary)
        output = r.stdout + r.stderr
        if r.returncode == 0 and produced:
            lines = (line for line in output.splitlines() if not line.startswith("Back up the game's "))
            output = "\n".join(lines) + "\nSaved bundle to " + dst + "\n"
        return {"ok": r.returncode == 0, "output": output[-20000:], "dst": dst if r.returncode == 0 and not inspect else None,
                "backup": backup, "cmd": " ".join(cmd[3:])}

    # ---- DLL patches (the work-tree copy is the continuing source) ----
    def dll_src(self, fresh=False):
        staged = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
        return self.dll if fresh or not os.path.isfile(staged) else staged

    def dll_check(self, fresh=False):
        with self.lock:
            source = self.dll_src(fresh)
            r = run_cmd(tool_cmd("dll") + ["check", source], 120)
        return {"source": source, "output": r.stdout + r.stderr}

    def dll_patch(self, o):
        dst = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
        cmd = tool_cmd("dll") + ["patch", self.dll, "--levels", self.levels, "-o", self.stage_root]; base = len(cmd)
        for key, flag in (("max_rank", "--max-rank"), ("command_nodes", "--command-nodes"),
                          ("command_step", "--command-step"), ("command_first_rank", "--command-first-rank")):
            if str(o.get(key, "")).strip(): cmd += [flag, str(o[key]).strip()]
        if o.get("munition_load") in (True, "true", 1, "1"): cmd.append("--munition-load")
        if str(o.get("repair_bay_actions", "")).strip(): cmd += ["--repair-bay-actions", str(o["repair_bay_actions"]).strip()]
        if not any(c.startswith("--") and c not in ("--command-step", "--command-first-rank") for c in cmd[base:]): raise ValueError("fill in at least one patch value")
        if o.get("fresh") in (True, "true", 1, "1"): cmd.append("--fresh")
        r = self.run_staged("dll", cmd, 300)
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).replace(self.stage_root, self.work), "dst": dst}

    def editor_update(self, bundle_path, patches=None):
        """Keep bundle, custom-module DLL, and repair shortcut assets in sync in the work overlay."""
        import datetime, shutil, tempfile
        import re
        patches = dict(patches or {})
        bundle_data = wmod.read_bundle(bundle_path)
        if not bundle_data.get("nodes"):
            raise ValueError("the saved bundle contains no serialized asset")
        offset, size, *_ = bundle_data["nodes"][0]
        serialized = wmod.SerFile(bundle_data["data"][offset:offset + size])
        script_names = {obj.pid: serialized.read(obj)[0].get("m_ClassName", "")
                        for obj in serialized.objs if obj.classid == 115}
        component_names = []
        for obj in serialized.objs:
            if obj.classid != 114:
                continue
            component = serialized.read(obj)[0]
            script = component.get("m_Script", {}).get("m_PathID")
            name = script_names.get(script)
            if name:
                component_names.append(name)
        custom_types = {"CustomArmourRepairModule", "CustomSquadronReplacementModule",
                        "CustomDroneResupplyModule", "CustomMagazineCapacityModule",
                        "CustomInstantPlatingModule", "CustomInstantPlatingAction"}
        custom_types.update({"CustomEmpPulseModule", "CustomEmpPulseAction",
                             "CustomTargetedEmpModule", "CustomTargetedEmpAction"})
        module_count = sum(name in custom_types for name in component_names)
        action_count = component_names.count("TurnaroundRepairBayAction")
        repair_module_count = component_names.count("TurnaroundRepairBayModule")
        if action_count != repair_module_count:
            raise ValueError("saved bundle has %d repair action(s) but %d repair module(s); correct the bundle before building the editor update" %
                             (action_count, repair_module_count))
        if action_count > 8:
            raise ValueError("the editor supports at most 8 repair bays")

        wants_custom = bool(module_count or action_count)
        if not wants_custom:
            if patches:
                return self.dll_patch(patches)
            return {"ok": True, "updated": False, "output": "No custom modules or DLL patches in this bundle."}

        levels = self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)
        managed_dirs = [os.path.join(levels, "Managed"), os.path.dirname(self.dll)]
        if self.bundles:
            managed_dirs.insert(1, os.path.join(os.path.dirname(self.bundles), "Managed"))
        managed = next((path for path in managed_dirs if os.path.isfile(os.path.join(path, "UnityEngine.CoreModule.dll"))), managed_dirs[0])
        if not os.path.isdir(managed):
            raise ValueError("the configured level-data folder must contain Managed/ so the custom-module DLL can be built")
        metadata = os.path.join(self.work, ".deadlock-tools")
        base_dir = os.path.join(metadata, "base")
        base_dll = os.path.join(base_dir, os.path.basename(self.dll))
        os.makedirs(base_dir, exist_ok=True)
        if not os.path.isfile(base_dll):
            with open(self.dll, "rb") as stream:
                source_bytes = stream.read()
            if any(signature in source_bytes for signature in (b"CustomArmourRepairModule", b"CustomSquadronReplacementModule",
                                                               b"CustomDroneResupplyModule", b"CustomMagazineCapacityModule",
                                                               b"CustomRepairBaySlots", b"CustomInstantPlatingModule",
                                                               b"CustomCommanderStatModifiers")):
                raise ValueError("the configured DLL already has custom modules, but no clean base exists. Put a clean Assembly-CSharp.dll at " + base_dll)
            shutil.copy2(self.dll, base_dll)

        effective = dict(patches)
        existing_dll = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
        shortcut_count = action_count
        if os.path.isfile(existing_dll):
            status = run_cmd(tool_cmd("dll") + ["check", existing_dll], 120)
            if status.returncode:
                raise RuntimeError("could not read existing work-tree DLL settings; refusing to replace them:\n" + (status.stdout + status.stderr)[-8000:])
            text = status.stdout + status.stderr
            found = re.search(r"commander max rank: (\d+)", text)
            if found and int(found.group(1)) != 10 and not str(effective.get("max_rank", "")).strip():
                effective["max_rank"] = found.group(1)
            found = re.search(r"command skill nodes: (\d+), one every (\d+) rank\(s\) from rank (\d+)", text)
            if found and not str(effective.get("command_nodes", "")).strip():
                effective.update(command_nodes=found.group(1), command_step=found.group(2), command_first_rank=found.group(3))
            if "munition load patch: INSTALLED" in text and effective.get("munition_load") not in (True, "true", 1, "1"):
                effective["munition_load"] = True

        transaction_root = os.path.join(metadata, "stage", "editor-update")
        if os.path.isdir(transaction_root):
            shutil.rmtree(transaction_root)
        os.makedirs(transaction_root, exist_ok=True)
        # Existing work-tree key assets take priority over the configured defaults.
        key_stage = os.path.join(transaction_root, "key-assets")
        os.makedirs(key_stage, exist_ok=True)
        for name in ("level3", "resources.assets"):
            source = os.path.join(self.work_paths["levels"], name)
            if os.path.isfile(source):
                shutil.copy2(source, os.path.join(key_stage, name))

        dll_out_root = os.path.join(transaction_root, "dll-stage")
        dll_args = tool_cmd("dll") + ["patch", base_dll, "--levels", self.levels, "-o", dll_out_root]
        for key, flag in (("max_rank", "--max-rank"), ("command_nodes", "--command-nodes"),
                          ("command_step", "--command-step"), ("command_first_rank", "--command-first-rank")):
            if str(effective.get(key, "")).strip():
                dll_args += [flag, str(effective[key]).strip()]
        if effective.get("munition_load") in (True, "true", 1, "1"):
            dll_args.append("--munition-load")
        has_dll_options = any(str(effective.get(key, "")).strip() for key in
                              ("max_rank", "command_nodes")) or \
                          effective.get("munition_load") in (True, "true", 1, "1")
        logs = ""
        if has_dll_options:
            dll_args += ["--fresh"]
            run = run_cmd(dll_args, 300)
            logs = run.stdout + run.stderr
            if run.returncode:
                raise RuntimeError("preparing the custom-module DLL failed:\n" + logs[-12000:])
            patched_base = os.path.join(dll_out_root, "dll", os.path.basename(self.dll))
            if not os.path.isfile(patched_base):
                raise RuntimeError("DLL patch step succeeded but did not produce " + patched_base)
        else:
            patched_base = base_dll

        candidate = os.path.join(transaction_root, os.path.basename(self.dll))
        build_root = config.PROJECT_DIR
        if config.ZIPAPP:
            from zipfile import ZipFile
            build_root = os.path.join(transaction_root, "builder")
            with ZipFile(config.ZIPAPP) as archive:
                for name in archive.namelist():
                    if name in ("scripts/build_custom_modules.sh", "scripts/build_commander_modifiers.sh", "scripts/merge_custom_modules.cs") or (
                            name.startswith("dll_modules/") and name.endswith(".cs")):
                        archive.extract(name, build_root)
        build_script = os.path.join(build_root, "scripts", "build_custom_modules.sh")
        run = run_cmd(["bash", build_script, managed, patched_base, candidate], 600)
        logs += run.stdout + run.stderr
        if run.returncode or not os.path.isfile(candidate):
            raise RuntimeError("custom-module DLL build failed:\n" + logs[-16000:])

        asset_outputs = []
        if shortcut_count:
            from deadlock_tools.repair_shortcuts import update_key_assets
            update_key_assets(self.levels, key_stage, shortcut_count)
            for name in ("level3", "resources.assets"):
                path = os.path.join(key_stage, name)
                if not os.path.isfile(path):
                    raise RuntimeError("repair shortcut build did not produce " + name)
                asset_outputs.append((path, os.path.join(self.work_paths["levels"], name)))

        dll_target = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
        outputs = [(candidate, dll_target)] + asset_outputs
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = os.path.join(metadata, "backups", "editor-update-" + stamp)
        promoted = []
        with self.lock:
            try:
                for _, target in outputs:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.isfile(target):
                        backup = os.path.join(backup_root, os.path.relpath(target, self.game_data))
                        os.makedirs(os.path.dirname(backup), exist_ok=True)
                        shutil.copy2(target, backup)
                for source, target in outputs:
                    fd, temporary = tempfile.mkstemp(prefix=".editor-update-", dir=os.path.dirname(target))
                    os.close(fd)
                    try:
                        shutil.copy2(source, temporary)
                        os.replace(temporary, target)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                    promoted.append(target)
            except BaseException:
                # Restore the previous work files if promotion was interrupted.
                for target in reversed(promoted):
                    backup = os.path.join(backup_root, os.path.relpath(target, self.game_data))
                    if os.path.isfile(backup):
                        shutil.copy2(backup, target)
                    elif os.path.isfile(target):
                        os.unlink(target)
                raise
        return {"ok": True, "updated": True, "dst": dll_target, "assets": [target for _, target in asset_outputs],
                "backup": backup_root if os.path.isdir(backup_root) else None,
                "output": "Built custom modules for %d component(s), %d repair action(s), preserving %d indexed shortcut(s).\n%s" % (module_count, action_count, shortcut_count, logs[-12000:])}

    def dll_revert(self, o):
        """Undo selected DLL patches in the work-tree copy."""
        dst = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
        cmd = tool_cmd("dll") + ["revert", self.dll, "--levels", self.levels, "-o", self.stage_root]
        for key, flag in (("armour", "--armour"), ("revive", "--revive"), ("max_rank", "--max-rank"), ("command_nodes", "--command-nodes"), ("munition_load", "--munition-load"), ("repair_bay_shortcuts", "--repair-bay-shortcuts")):
            if o.get(key) in (True, "true", 1, "1"): cmd.append(flag)
        r = self.run_staged("dll", cmd, 300)
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).replace(self.stage_root, self.work), "dst": dst}

PATH_KEYS = ("levels", "dll", "bundles", "work")
STATE = {"cfg": {"levels": "", "dll": "", "bundles": "", "work": config.resolve(config.DEFAULTS["work"])}, "P": None, "error": ""}


def _need(v, k, what):
    x = str(v.get(k, "")).strip()
    if not x: raise ValueError(what + " is required")
    return x


def _pairs(v, k, *, allow_bool=False):
    toks = []
    for ln in str(v.get(k, "")).splitlines():
        p = ln.split()
        if not p: continue
        if len(p) != 2: raise ValueError("each line needs a field name and a value, e.g. MaxMissiles 80 (got %r)" % ln)
        if not (allow_bool and p[0] == "HasUnlimitedMunitions" and p[1].lower() in ("true", "false", "yes")):
            float(p[1])
        toks += p
    if not toks: raise ValueError("enter at least one line: field value")
    return toks


def _bool_flag(v, k, flag): return [flag] if v.get(k) in (True, "true", "1", 1) else []


F = lambda name, label, kind="text", default="", help="": {"name": name, "label": label, "kind": kind, "default": default, "help": help}
# every command of shipdb: id, group, title, help, writes?, fields, argv builder (P = Project, v = form values)
TOOLS = [
    {"id": "commanders", "group": "Commanders", "title": "Show commanders", "help": "Ranks, XP table, leadership interval and trait counts.", "fields": [],
     "build": lambda P, v: ["commanders"]},
    {"id": "setcommanders", "current": "commanders", "group": "Commanders", "title": "Add commander ranks", "writes": True,
     "help": "Adds ranks above the original maximum. Afterwards patch the DLL (DLL tab: max rank 20, command nodes 10) or the new ranks do nothing.",
     "fields": [F("add", "ranks to add", "int", "10"), F("every_level", "a fleet-size trait at every rank (leadership interval 1)", "bool", True), F("xp_growth", "XP growth per rank", "float", "1.2", "each new rank costs this much more XP than the previous one")],
     "build": lambda P, v: ["setcommanders", "--add", str(int(float(v.get("add") or 10))), "--xp-growth", str(float(v.get("xp_growth") or 1.2))] + _bool_flag(v, "every_level", "--every-level")},
    {"id": "fixspecs", "current": "commanders", "group": "Commanders", "title": "Specialisation lists (fixspecs)", "writes": True,
     "help": "Sets each subsystem's captain-skill list to its own three skills. Tick the box to put the game's original lists back.",
     "fields": [F("revert", "undo: restore the original lists", "bool", False)],
     "build": lambda P, v: ["fixspecs"] + _bool_flag(v, "revert", "--revert-specs")},
    {"id": "list", "group": "Ships", "title": "List all ships", "help": "Name, buildable, cost, time, rush cost, hull, fleet points, faction, class.", "fields": [],
     "build": lambda P, v: ["list"]},
    {"id": "show", "group": "Ships", "title": "Inspect ship", "help": "Show the selected ship’s stats and veterancy.",
     "fields": [F("ship", "Ship", "ship")], "build": lambda P, v: ["show", _need(v, "ship", "ship")]},
    {"id": "vet", "group": "Ships", "title": "Show veterancy", "help": "Show the XP table and bonuses. Leave Ship blank to see every ship.",
     "fields": [F("ship", "Ship", "ship")], "build": lambda P, v: ["vet"] + ([v["ship"]] if v.get("ship") else [])},
    {"id": "setxp", "current": "vet", "group": "Ships", "title": "XP needed per veterancy rank", "writes": True, "help": "Changes the XP a crew needs to reach a rank (the same for every ship).",
     "fields": [F("rank", "rank", "int", "2"), F("xp", "New XP needed", "int")],
     "build": lambda P, v: ["setxp", str(int(float(_need(v, "rank", "rank")))), str(int(float(_need(v, "xp", "XP"))))]},
    {"id": "copyvet", "current": "vet", "group": "Ships", "title": "Copy veterancy between ships", "writes": True, "help": "Copies every rank of one ship's veterancy bonuses onto another (replaces the target's).",
     "fields": [F("src", "copy FROM ship", "ship"), F("dst", "copy TO ship", "ship")],
     "build": lambda P, v: ["copyvet", _need(v, "src", "source ship"), _need(v, "dst", "target ship")]},
    {"id": "munitions", "group": "Munitions", "title": "List munition types", "help": "Capacity, salvo size, supply cost.", "fields": [F("name", "Munition (blank for all)")],
     "build": lambda P, v: ["munitions"] + ([v["name"].strip()] if v.get("name", "").strip() else [])},
    {"id": "setmunition", "current": "munitions", "group": "Munitions", "title": "Edit a munition type", "writes": True,
     "help": "Editable: StandardSalvoSize MaxMissiles HasUnlimitedMunitions SupplyCostPerMunition SurvivalCostPerMunition PointValue. One 'field value' per line.",
     "fields": [F("name", "Munition (exact name)"), F("settings", "New settings", "lines", "", "Example: MaxMissiles 80")],
     "build": lambda P, v: ["setmunition", _need(v, "name", "munition name")] + _pairs(v, "settings", allow_bool=True)},
    {"id": "blueprints", "group": "Research", "title": "List research unlocks", "help": "Per campaign: ships that can be researched, cost, mission, starts-unlocked. Edit one ship's entries on its Ships page.", "fields": [],
     "build": lambda P, v: ["blueprints"]},
    {"id": "addblueprint", "current": "blueprints", "group": "Research", "title": "Add a ship to research (bulk)", "writes": True,
     "help": "Adds the ship to the research lists AND makes it buildable (build stats copied from the reference ship). Needs a NEW campaign.",
     "fields": [F("ship", "ship", "ship"), F("cost", "research cost (blank = same as reference)", "int"), F("like", "reference ship", "ship", "Jupiter Mk2"), F("campaigns", "campaigns (e.g. 2,3,4,5; blank = all)"),
                F("mission", "mission required (-1 none)", "int", "-1"), F("label", "label", "text", "Mc"), F("list", "list", "choice:battlestar,capital", "battlestar"),
                F("unlocked", "available from the start", "bool", False), F("keep", "keep the ship's own build stats", "bool", False)],
     "build": lambda P, v: ["addblueprint", _need(v, "ship", "ship"), "--like", str(v.get("like") or "Jupiter Mk2"), "--mission", str(int(float(v.get("mission") or -1))), "--label", str(v.get("label") or "Mc"),
                            "--list", str(v.get("list") or "battlestar")] + (["--cost", str(int(float(v["cost"])))] if str(v.get("cost", "")).strip() else []) + (["--campaigns", str(v["campaigns"]).strip()] if str(v.get("campaigns", "")).strip() else [])
                           + _bool_flag(v, "unlocked", "--unlocked") + _bool_flag(v, "keep", "--keep-build-stats")},
    {"id": "fleetinfo", "group": "Fleet limits", "title": "Show fleet limits", "help": "MaxShips (per fleet group), StartingFleetPointCap ...", "fields": [],
     "build": lambda P, v: ["fleetinfo"]},
    {"id": "setfleetinfo", "current": "fleetinfo", "group": "Fleet limits", "title": "Change fleet limits", "writes": True, "help": "One 'field value' per line, e.g. MaxShips 10 and StartingFleetPointCap 6000.",
     "fields": [F("settings", "New settings", "lines", "", "Example: MaxShips 10")], "build": lambda P, v: ["setfleetinfo"] + _pairs(v, "settings")},
    {"id": "fleeticons", "group": "Fleet map icons", "title": "List icon tables", "help": "The war-room icon tables.", "fields": [F("factory", "Icon table", "choice:FleetModelFactory,ProjectionFactory,all", "FleetModelFactory")],
     "build": lambda P, v: ["fleeticons", "--factory", str(v.get("factory") or "FleetModelFactory")]},
    {"id": "fleeticon", "current": "fleeticons", "group": "Fleet map icons", "title": "Copy a fleet icon", "writes": True, "help": "Gives other ships the fleet-map icon of one ship (in every scene that has the table).",
     "fields": [F("ships", "ships to change (comma separated)"), F("like", "copy the icon of", "ship"), F("factory", "icon table", "choice:FleetModelFactory,ProjectionFactory,all", "FleetModelFactory")],
     "build": lambda P, v: ["fleeticon", _need(v, "ships", "ships"), _need(v, "like", "icon ship"), "--factory", str(v.get("factory") or "FleetModelFactory")]},
    {"id": "squadrons", "group": "Squadrons", "title": "List squadrons", "help": "Fighter count, hull pool, hull per fighter, loss thresholds (read from sharedassets9.assets).",
     "fields": [F("assets", "sharedassets9.assets", "file", "@assets")], "build": lambda P, v: ["squadrons", "--levels", _need(v, "assets", "assets file")]},
    {"id": "setsquadron", "current": "squadrons", "group": "Squadrons", "title": "Change squadron hull", "writes": True,
     "help": "HullFactor F multiplies the pool and every fighter-loss threshold; MaxHullPoints N sets the pool; FighterHull N sets hull per fighter. Name is part of the squadron name, or 'all'.",
     "fields": [F("assets", "sharedassets9.assets", "file", "@assets"), F("name", "squadron", "text", "all"), F("settings", "New settings", "lines", "", "Example: HullFactor 2")],
     "build": lambda P, v: ["setsquadron", _need(v, "name", "squadron")] + _pairs(v, "settings") + ["--levels", _need(v, "assets", "assets file")]},
]
TOOLS_BY_ID = {t["id"]: t for t in TOOLS}


def load_cfg():
    """the paths saved in deadlock.json, made absolute"""
    c = config.load()
    return {k: config.resolve(c.get(k, "")) for k in PATH_KEYS}


def configure(cfg, save=True):
    """Validate source paths and reload the editor with its single work-tree destination."""
    cfg = {k: os.path.abspath(os.path.expanduser(v)) if v else "" for k, v in ((k, (cfg.get(k) or "").strip()) for k in PATH_KEYS)}
    cfg["work"] = cfg["work"] or config.resolve(config.DEFAULTS["work"])
    migrated = config.migrate_legacy_out(cfg["work"])
    if not cfg["levels"] or not os.path.exists(cfg["levels"]): raise ValueError("level data folder not found: %r" % cfg["levels"])
    if not os.path.isfile(cfg["dll"]): raise ValueError("Assembly-CSharp.dll not found: %r" % cfg["dll"])
    if cfg["bundles"] and not os.path.isdir(cfg["bundles"]): raise ValueError("ship asset folder not found: %r" % cfg["bundles"])
    if config.same_file(os.path.join(cfg["work"], "BSG_Data"), cfg["levels"]):
        raise ValueError("the level data folder cannot be the work folder's BSG_Data tree; choose the game's default level-data folder")
    try: P = Project(cfg["levels"], cfg["dll"], cfg["work"], cfg["bundles"] or None)
    except BaseException as e: raise ValueError(str(e) or "could not read the level data")
    STATE.update(cfg=cfg, P=P, error="")
    if save or migrated:
        try: config.save({**config.load(), **cfg})
        except OSError: pass


def config_info():
    P = STATE["P"]; c = STATE["cfg"]
    return {**c, "ready": P is not None, "error": STATE["error"], "tables": len(P.table_files) if P else 0,
            "bundle_files": P.bundle_files() if P else [], "base_bundle_files": P.base_bundle_files() if P else [], "bundle_dir": P.bundles if P else c["bundles"],
            "home": os.path.expanduser("~")}


def browse(path, files):
    path = os.path.abspath(os.path.expanduser(path or "~"))
    while not os.path.isdir(path) and os.path.dirname(path) != path: path = os.path.dirname(path)
    entries = []
    try:
        for n in sorted(os.listdir(path), key=str.lower):
            if n.startswith("."): continue
            isd = os.path.isdir(os.path.join(path, n))
            if isd or files: entries.append({"name": n, "dir": isd})
    except OSError as e: return {"path": path, "parent": os.path.dirname(path), "entries": [], "error": str(e)}
    up = os.path.dirname(path)
    return {"path": path, "parent": up if up != path else None, "entries": entries[:2000]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, obj, code=200, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def local_only(self):
        """only this computer's own browser tab may use the API (blocks other sites calling it, and DNS rebinding)"""
        return self.headers.get("Host", "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

    def need(self):
        if STATE["P"] is None: raise ValueError("Set the level data folder and the DLL in the Settings tab first")
        return STATE["P"]

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if not self.local_only(): return self.send({"error": "forbidden"}, 403)
        try:
            if u.path == "/": return self.send(PAGE, ctype="text/html")
            if u.path in ("/api/state", "/api/config"): return self.send(config_info())
            if u.path == "/api/browse": return self.send(browse(q.get("path", [""])[0], q.get("files", ["0"])[0] == "1"))
            if u.path == "/api/ships": return self.send(self.need().ships())
            if u.path == "/api/bays/bundles":
                return self.send(self.need().bay_bundles())
            if u.path == "/api/allocations":
                return self.send(self.need().allocations(q.get("fresh") == ["1"]))
            if u.path == "/api/squadrons":
                project = self.need()
                return self.send(project.squadrons(q.get("fresh") == ["1"]))
            if u.path == "/api/commanders":
                project = self.need()
                with project.lock:
                    return self.send(project.commanders(q.get("fresh") == ["1"]))
            if u.path == "/api/ship":
                project = self.need()
                with project.lock:
                    ship = project.ship(q["name"][0], q.get("fresh") == ["1"])
                return self.send(ship)
            if u.path == "/api/bundle/form": return self.send(self.need().bundle_form())
            if u.path == "/api/dll/check": return self.send(self.need().dll_check(q.get("fresh") == ["1"]))
            if u.path == "/api/tools": return self.send(self.need().tools())
            self.send({"error": "not found"}, 404)
        except BaseException as e: self.send({"error": str(e)}, 400)

    def do_POST(self):
        if not self.local_only() or "application/json" not in self.headers.get("Content-Type", ""): return self.send({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/config": configure(body); return self.send(config_info())
            P = self.need()
            if self.path == "/api/apply": return self.send({"log": P.apply(body["edits"], body.get("fresh", False))})
            if self.path == "/api/ships/clone": return self.send(P.clone_ship(body))
            if self.path == "/api/commanders/save": return self.send(P.save_commanders(body))
            if self.path == "/api/squadrons/save": return self.send(P.save_squadrons(body))
            if self.path == "/api/allocations/save": return self.send(P.save_allocations(body))
            if self.path == "/api/bays": return self.send(P.bays(body["path"]))
            if self.path == "/api/bays/save": return self.send(P.save_bays(body))
            if self.path == "/api/armaments": return self.send(P.armaments(body))
            if self.path == "/api/localisation": return self.send(P.localisation(body))
            if self.path == "/api/bundle/import": return self.send(P.import_bundle(body["bundle"]))
            if self.path == "/api/bundle/layout":
                return self.send(P.bundle_layout(body["bundle"]))
            if self.path == "/api/bundle/layout/save":
                if not isinstance(body.get("layout"), dict):
                    raise ValueError("Missing weapon layout.")
                return self.send(P.run_bundle(body["bundle"], {}, mount_layout=body["layout"]))
            if self.path == "/api/bundle/stats":
                path, _ = P.bundle_src(body["bundle"])
                with P.lock:
                    return self.send(wmod.bundle_ship_stats(path))
            if self.path == "/api/bundle/run": return self.send(P.run_bundle(body["bundle"], body["options"], body.get("prefab_fields"), body.get("overwrite", False)))
            if self.path == "/api/editor/update": return self.send(P.editor_update(body["bundle"], body.get("patches")))
            if self.path == "/api/dll/patch": return self.send(P.dll_patch(body))
            if self.path == "/api/dll/revert": return self.send(P.dll_revert(body))
            if self.path == "/api/xor": return self.send(P.run_xor(body["bundle"], body["mode"], body.get("key", "")))
            if self.path == "/api/tools/run": return self.send(P.run_tool(body["id"], body.get("values", {}), body.get("fresh", False)))
            self.send({"error": "not found"}, 404)
        except BaseException as e: self.send({"error": str(e)}, 400)


def serve(a):
    given = {k: os.path.abspath(v) for k, v in (("levels", a.levels), ("dll", a.dll), ("bundles", a.bundles), ("work", a.work)) if v}
    cfg = {**{k: v for k, v in load_cfg().items() if v}, **given}
    STATE["cfg"].update(cfg)
    if cfg.get("levels") and cfg.get("dll"):
        try: configure(cfg, save=bool(given))
        except ValueError as e: STATE["error"] = str(e); print("could not load the saved paths:", e)
    for port in range(a.port, a.port + 20):
        try: srv = ThreadingHTTPServer(("127.0.0.1", port), Handler); break
        except OSError: continue
    else: raise SystemExit("no free port near %d" % a.port)
    url = "http://127.0.0.1:%d" % port; P = STATE["P"]
    print("ship editor running at %s  (%s; Ctrl+C to stop)" % (url, "%d level file(s) hold the ship table" % len(P.table_files) if P else "set the paths in the Settings tab"))
    if not a.no_browser: threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("stopped")


def main():
    ap = argparse.ArgumentParser(prog="deadlock gui", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("levels", nargs="?"); ap.add_argument("dll", nargs="?")
    ap.add_argument("--bundles"); ap.add_argument("--work"); ap.add_argument("--port", type=int, default=8765); ap.add_argument("--no-browser", action="store_true")
    argv = sys.argv[1:]
    if argv[:1] == ["gui"]: argv = argv[1:]                       # old form: deadlock_editor.py gui ...
    serve(ap.parse_args(argv))


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deadlock Ship Editor</title>
<style>
:root{--bg:#f6f6f4;--panel:#fff;--ink:#1d2024;--muted:#6a7078;--line:#dcdcd6;--accent:#c4501b;--accent-ink:#fff;--chg:#fff3e0;--ok:#1b7f3a;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14171a;--panel:#1c2024;--ink:#e6e8ea;--muted:#9097a0;--line:#30363c;--accent:#e2733a;--accent-ink:#111;--chg:#3a2a1a;--ok:#5fcf80;--bad:#ff8a80}}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5;flex-wrap:wrap}
h1{font-size:16px;margin:0 8px 0 0}nav button{background:none;border:0;padding:6px 10px;color:var(--muted);cursor:pointer;font:inherit;border-bottom:2px solid transparent}
nav button.on{color:var(--ink);border-color:var(--accent)}.sp{flex:1}
button.pri{background:var(--accent);color:var(--accent-ink);border:0;border-radius:6px;padding:7px 14px;font:inherit;cursor:pointer}button.pri:disabled{opacity:.4;cursor:default}
button.sec[aria-pressed=true]{outline:2px solid var(--accent);outline-offset:-2px}button.sec{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 12px;font:inherit;cursor:pointer}
main{display:grid;grid-template-columns:270px 1fr;gap:0;min-height:calc(100vh - 54px)}
#side{border-right:1px solid var(--line);background:var(--panel);padding:10px;overflow:auto;max-height:calc(100vh - 54px);position:sticky;top:54px}
.ability-options{border-top:1px solid var(--line);padding:8px 0}.ability-options summary{cursor:pointer;font-weight:600}.ability-options .grid{margin-top:8px}
.bundle-slot-fields:not(:empty){border-top:1px solid var(--line);margin-top:12px}
#side input{width:100%;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);margin-bottom:8px}
.ship{padding:6px 8px;border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;gap:6px}.ship:hover{background:var(--bg)}.ship.on{background:var(--chg);outline:1px solid var(--accent)}
.ship small{color:var(--muted)}.dot{color:var(--accent)}
#view{padding:16px 20px;max-width:980px}table.alloc{border-collapse:collapse}table.alloc td,table.alloc th{border-bottom:1px solid var(--line);padding:3px 6px}.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:14px}
.card h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px 18px}
label.f{display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--muted)}label.f input,label.f select{padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:14px system-ui}
label.f input.chg,label.f select.chg{background:var(--chg);border-color:var(--accent)}label.f.chk{flex-direction:row;align-items:center;gap:8px;color:var(--ink)}
.orig{font-size:11px}.ro{font-size:13px;color:var(--ink);word-break:break-word}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;white-space:pre-wrap;max-height:340px;overflow:auto;font-size:12px}
.msg-ok{color:var(--ok)}.msg-bad{color:var(--bad)}.hint{color:var(--muted);font-size:12px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.row input[type=number],.row input[type=text],.row select{padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
@media (max-width:760px){main{grid-template-columns:1fr}#side{position:static;max-height:240px;border-right:0;border-bottom:1px solid var(--line)}}
.ship-editor-tabs{margin:12px 0;gap:8px}
.ship-editor-tabs [aria-selected=true]{background:var(--accent);color:var(--accent-ink)}
.layout-tools,.layout-footer{margin:12px 0;gap:8px}
.layout-workspace:not([hidden]){display:grid;grid-template-columns:minmax(0,2fr) minmax(230px,1fr);gap:16px;align-items:start}
.layout-workspace canvas{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:8px;touch-action:none;cursor:crosshair}
.layout-placement{margin:0 0 12px;padding:10px;border:1px solid var(--line);border-radius:8px}
.layout-placement .row{align-items:end}
.layout-placement p{margin:6px 0 0}
.layout-workspace canvas.placing-turret{outline:2px solid var(--accent)}
.layout-properties{display:grid;gap:10px}
.layout-vector{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:0;border:1px solid var(--line);border-radius:6px}
.layout-properties input,.layout-properties select{width:100%;min-width:0;box-sizing:border-box}
@media(max-width:850px){.layout-workspace:not([hidden]){grid-template-columns:1fr}}
.localisation-source input{flex:1;min-width:200px}
.localisation-editor{display:grid;grid-template-columns:minmax(0,2fr) minmax(300px,3fr);gap:20px;margin-top:14px}
.localisation-editor input[type=search],.localisation-editor textarea{width:100%;box-sizing:border-box}
.localisation-editor textarea{resize:vertical;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:7px;font:inherit}
.localisation-editor form{display:grid;align-content:start;gap:10px}
.localisation-editor form>button{justify-self:start}
.localisation-results{max-height:70vh;overflow:auto;margin:8px 0}
.localisation-row{display:flex;gap:12px;width:100%;align-items:baseline;text-align:left;color:var(--ink);background:var(--panel);border:0;border-bottom:1px solid var(--line);padding:8px;cursor:pointer}
.localisation-row[aria-pressed=true]{background:var(--chg);outline:1px solid var(--accent);outline-offset:-1px}
.localisation-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cmd-tree-scroll{max-height:52vh;overflow:auto;padding:18px 4px 8px}.cmd-tree-root{width:max-content;margin:0 auto 18px;padding:10px 28px;border:2px solid var(--accent);border-radius:24px;background:var(--chg);font-weight:700;text-align:center}.cmd-tree-branches{display:grid;grid-template-columns:repeat(7,minmax(155px,1fr));gap:10px;min-width:1160px;position:relative}.cmd-tree-branch{position:relative;border-top:2px solid var(--line);padding:14px 6px 6px;text-align:center}.cmd-tree-branch:before{content:"";position:absolute;left:50%;top:-16px;height:14px;border-left:2px solid var(--line)}.cmd-tree-branch-title{display:block;margin:0 auto 10px;padding:8px 5px;border:1px solid var(--accent);border-radius:8px;background:var(--panel);font-weight:650}.cmd-tree-node{display:block;margin:8px 0;padding:8px 4px;border:1px solid var(--line);border-radius:7px;background:var(--panel);font-size:12px;line-height:1.35}.cmd-tree-node:before{content:"";display:block;width:0;height:10px;border-left:2px solid var(--line);margin:-18px auto 8px}.cmd-tree-node:first-of-type:before{height:12px;margin-top:-20px}.cmd-tree-effect{display:block;color:var(--muted);font-size:10px;overflow-wrap:anywhere}
.cmd-choice-scroll,.cmd-ability-scroll{max-height:58vh;overflow:auto;padding-right:6px;overscroll-behavior:contain}.cmd-choice-scroll{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;align-items:start}.cmd-ability-scroll{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;align-items:start}
@media(max-width:770px){.localisation-editor{grid-template-columns:1fr}.localisation-results{max-height:28vh}}
</style></head><body>
<header><h1>Deadlock Ship Editor</h1>
<nav><button data-t="ships" class="on">Ships</button><button data-t="dll">DLL</button><button data-t="commanders">Commanders</button><button data-t="squadrons">Squadrons</button><button data-t="tools">Database</button><button data-t="localisation">Localisation</button><button data-t="armaments">Armaments</button><button data-t="settings">Settings</button></nav>
<span class="sp"></span><span id="pend" class="hint"></span>
<label class="hint"><input type="checkbox" id="fresh"> start from the originals</label>
<button class="sec" id="discard" disabled>Discard</button><button class="pri" id="apply" disabled>Apply</button></header>
<main id="tab-ships"><aside id="side"><input id="q" placeholder="Search ships" autocomplete="off"><button class="sec" id="load-bundle">Load bundle…</button><button class="sec" id="copy-ship">Copy and add a ship…</button><button class="sec" id="ship-tools">Ship database tools</button><div id="list"></div></aside><section id="view"><p class="hint">Pick a ship or load a bundle.</p></section></main>
<section id="tab-dll" hidden style="padding:16px 20px;max-width:980px"></section>
<section id="tab-commanders" hidden style="padding:16px 20px;max-width:1250px"></section>
<section id="tab-squadrons" hidden style="padding:16px 20px;max-width:1250px"></section>
<section id="tab-tools" hidden style="padding:16px 20px;max-width:1100px"></section>
<section id="tab-armaments" hidden style="padding:16px 20px;max-width:1250px"></section>
<section id="tab-localisation" hidden style="padding:16px 20px;max-width:1250px"></section>
<section id="tab-settings" hidden style="padding:16px 20px;max-width:980px"></section>
<script>
const $=s=>document.querySelector(s);
const state={ships:[],cur:null,data:null,pending:{},info:{},toolGroup:'Ships'};
const HULL_ARMOUR_LINKS={MaxHullPoints:'ShipInfo.MaxHullPoints',ArmorFront:'ArmourInfo.defaultArmourFront',ArmorRear:'ArmourInfo.defaultArmourRear',ArmorLeft:'ArmourInfo.defaultArmourLeft',ArmorRight:'ArmourInfo.defaultArmourRight',ArmorTop:'ArmourInfo.defaultArmourTop',ArmorBottom:'ArmourInfo.defaultArmourBottom'};
let syncingHullArmour=false;
async function api(path,body){const r=await fetch(path,body!==undefined?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
const el=(t,a={},...kids)=>{const e=document.createElement(t);for(const k in a){if(k==='class')e.className=a[k];else if(k.startsWith('on'))e.addEventListener(k.slice(2),a[k]);else if(a[k]!==false&&a[k]!==null&&a[k]!==undefined)e.setAttribute(k,a[k]===true?'':a[k])}for(const c of kids.flat())e.append(c&&c.nodeType?c:document.createTextNode(c??''));return e};
const GROUPS=[
 ['Hull & armour',['MaxHullPoints','ArmorFront','ArmorRear','ArmorLeft','ArmorRight','ArmorTop','ArmorBottom','PlatingSupplyRequired']],
 ['Speed & handling',['MaxVelocity','MaxTurnRate','MaxDRADISRange','CollisionExclusionZoneRadius','MissileCooldownTurns']],
 ['Costs & fleet points',['FleetPointValue','BuildCost','BuildTime','RushCost','XP','Buildable']],
 ['Carrier & munition slots',['FighterSlots','UtilityFighterSlots','MunitionSlots','SupportSlotPriority']],
 ['Class & flags',['Faction','ShipClass','SizeClass','IsCivilian','AlwaysFlagship','AlwaysAllowInCampaign','IncludedInCodex','IncludedInMultiplayer','DLCRequired','DescriptionHeaderID']]];
const key=(s,f)=>s+'|'+f;
let syncingSlotValues=false;
function shipFieldValue(name){const pending=state.pending[key(state.cur,name)];return pending?pending.value:state.data?.fields.find(field=>field.name===name)?.value}
function setShipFieldValue(name,value){const input=$('#view [data-db-field="'+name+'"]');if(!input)return;input.value=String(value);input.dispatchEvent(new Event('input',{bubbles:true}))}
function hullArmourMismatches(bundle){
 return Object.entries(HULL_ARMOUR_LINKS).flatMap(([dbField,prefabKey])=>{
  const level=Number(shipFieldValue(dbField)),input=bundle?.stats?.querySelector('[data-prefab-field="'+prefabKey+'"]'),prefab=input?.valueAsNumber;
  return Number.isFinite(level)&&Number.isFinite(prefab)&&Math.abs(level-prefab)>1e-5*Math.max(1,Math.abs(level),Math.abs(prefab))
   ?[{dbField,prefabKey,level,prefab}]:[];
 });
}
function refreshHullArmourLinkUI(bundle=activeBundle){
 if(!bundle?.linkHint)return;
 const mismatches=hullArmourMismatches(bundle);
 bundle.linkHint.textContent=mismatches.length
  ?mismatches.length+' linked value'+(mismatches.length===1?' differs':'s differ')+'. Choose which file supplies the matching values.'
  :'Level data and bundle hull/armour values match 1:1.';
 if(bundle.linkRepairButtons)bundle.linkRepairButtons.hidden=!mismatches.length;
 if(bundle.linkDetails)bundle.linkDetails.replaceChildren(...mismatches.map(item=>el('div',{class:'hint'},item.dbField+': level '+item.level+' · bundle '+item.prefab)));
}
function syncBundleStatFromLevel(dbField,value){
 if(syncingHullArmour||!activeBundle?.linkBox?.checked||!activeBundle.stats.dataset.source||!HULL_ARMOUR_LINKS[dbField])return;
 const input=activeBundle.stats.querySelector('[data-prefab-field="'+HULL_ARMOUR_LINKS[dbField]+'"]');if(!input)return;
 syncingHullArmour=true;input.value=String(Number(value));input.dispatchEvent(new Event('input',{bubbles:true}));syncingHullArmour=false;refreshHullArmourLinkUI(activeBundle);
}
function linkedPrefabChanges(bundle){
 const changes={};if(!bundle?.linkBox?.checked||!bundle.stats.dataset.source)return changes;
 for(const [dbField,prefabKey] of Object.entries(HULL_ARMOUR_LINKS)){
     const input=bundle.stats.querySelector('[data-prefab-field="'+prefabKey+'"]');
  const value=input.valueAsNumber,original=JSON.parse(input.dataset.original);
  if(Number.isFinite(value)&&value!==original)changes[prefabKey]=value;
 }
 return changes;
}
function linkedHullArmourLabel(){
 return 'Level database and bundle prefab values sync 1:1. Use Apply in the top bar to save both files.';
}
function syncBundleSlotInputs(name,value){
 if(syncingSlotValues||!activeBundle?.slots)return;
 const target={MunitionSlots:'munition_slots',FighterSlots:'fighter_slots',UtilityFighterSlots:'utility_slots'}[name];
 if(!target)return;
 const input=activeBundle.slots.querySelector('[data-d="'+target+'"]');if(input)input.value=String(value);
 if(name==='FighterSlots'||name==='UtilityFighterSlots'){
   const groups=activeBundle.slots.querySelector('[data-d="squadron_groups"]'),split=activeBundle.slots.querySelector('[data-d="split_squadrons"]');
   const fighters=Number(shipFieldValue('FighterSlots')),support=Number(shipFieldValue('UtilityFighterSlots'));
   if(groups&&!groups.dataset.manual){if(split?.checked)groups.value='';else if(Number.isInteger(fighters)&&Number.isInteger(support))groups.value=[fighters,support].filter(count=>count>0).join(',')}
 }
}
function updPend(){const n=Object.keys(state.pending).length+Object.keys(linkedPrefabChanges(activeBundle)).length;$('#pend').textContent=n?n+' pending change'+(n>1?'s':''):'';$('#apply').disabled=!n;$('#discard').disabled=!n;renderList()}
function renderList(){const q=$('#q').value.toLowerCase();const dirty=new Set(Object.values(state.pending).map(p=>p.ship));const box=$('#list');box.replaceChildren(...state.ships.filter(s=>(s.name+' '+s.faction+' '+s.class).toLowerCase().includes(q)).map(s=>el('div',{class:'ship'+(s.name===state.cur?' on':''),onclick:()=>openShip(s.name)},el('span',{},dirty.has(s.name)?el('span',{class:'dot'},'● '):'',s.name),el('small',{},s.faction[0]+' · '+s.class))))}
function fieldInput(f){const k=key(state.cur,f.name),orig=f.value,pend=state.pending[k];const cur=pend?pend.value:orig;let inp;
 const mark=()=>{const v=read();const same=(f.type==='bool'||f.type==='enum')?String(v)===String(orig):Math.abs(Number(v)-Number(orig))<1e-6*Math.max(1,Math.abs(orig));if(same)delete state.pending[k];else state.pending[k]={ship:state.cur,field:f.name,value:(f.type==='bool'||f.type==='enum')?(f.type==='bool'?!!v:Number(v)):Number(v)};inp.classList.toggle('chg',!same);if(f.type!=='bool')note.textContent=same?'':'was '+orig;if(['MunitionSlots','FighterSlots','UtilityFighterSlots'].includes(f.name))syncBundleSlotInputs(f.name,v);if(!syncingHullArmour)syncBundleStatFromLevel(f.name,v);updPend()};
 let read;const note=el('span',{class:'orig'});
 if(f.type==='bool'){inp=el('input',{type:'checkbox','data-db-field':f.name,onchange:mark});inp.checked=!!cur;read=()=>inp.checked;return el('label',{class:'f chk'},inp,f.name)}
 if(f.type==='enum'){inp=el('select',{'data-db-field':f.name,onchange:mark},Object.entries(f.options).map(([i,n])=>el('option',{value:i},n+' ('+i+')')));inp.value=String(cur);read=()=>inp.value}
 else{inp=el('input',{type:'number','data-db-field':f.name,step:f.type==='int'?'1':'any',value:cur,oninput:mark});read=()=>inp.value===''?NaN:inp.value}
 if(pend)inp.classList.add('chg');if(pend)note.textContent='was '+orig;return el('label',{class:'f'},f.name,inp,note)}

function resNorm(r){return JSON.stringify(r.map(x=>[x.campaign,!!x.on,x.on?Number(x.cost):0,x.on?Number(x.mission):0,x.on?String(x.label):'',x.on?!!x.unlocked:false]))}
function researchCard(d){const rs=d.research,k=key(state.cur,'__res'),card=el('div',{class:'card'});
 if(!rs){card.append(el('h3',{},'Research'),el('p',{class:'hint'},'No research table was found in the level files.'));return card}
 const get=()=>JSON.parse(JSON.stringify(state.pending[k]?state.pending[k].research:rs));
 const put=c=>{if(resNorm(c)===resNorm(rs))delete state.pending[k];else state.pending[k]={ship:state.cur,research:c};updPend();draw()};
 const buildable=()=>{const p=state.pending[key(state.cur,'Buildable')];const f=d.fields.find(x=>x.name==='Buildable');return p?!!p.value:!!(f&&f.value)};
 function draw(){const c=get(),kids=[el('h3',{},'Research (war-room blueprints)'),el('p',{class:'hint'},'Per campaign: can the ship be researched, for how many research points, after which story mission (-1 = any) and whether it is available from the start. A new campaign is needed: an existing save keeps its own research state.')];
  const head=el('div',{class:'row hint',style:'font-weight:600'},el('span',{style:'width:90px'},'campaign'),el('span',{style:'width:90px'},'researchable'),el('span',{style:'width:100px'},'cost (RP)'),el('span',{style:'width:90px'},'mission'),el('span',{style:'width:80px'},'label'),el('span',{},'unlocked at start'));kids.push(head);
  c.forEach((r,i)=>{const set=(f,val)=>{const cc=get();cc[i][f]=val;put(cc)};const off=!r.on;
   kids.push(el('div',{class:'row',style:'margin:3px 0'},el('span',{style:'width:90px'},'Campaign '+r.campaign),
    el('input',{type:'checkbox',style:'width:90px',checked:r.on,onchange:e=>set('on',e.target.checked)}),
    el('input',{type:'number',step:'1',min:'0',value:r.cost,disabled:off,style:'width:100px',onchange:e=>set('cost',Number(e.target.value))}),
    el('input',{type:'number',step:'1',min:'-1',value:r.mission,disabled:off,style:'width:90px',onchange:e=>set('mission',Number(e.target.value))}),
    el('input',{type:'text',maxlength:'12',value:r.label,disabled:off,style:'width:80px',onchange:e=>set('label',e.target.value)}),
    el('input',{type:'checkbox',checked:r.unlocked,disabled:off,onchange:e=>set('unlocked',e.target.checked)})))});
  if(c.some(r=>r.on)&&!buildable())kids.push(el('p',{class:'msg-bad'},'This ship is not marked Buildable (Costs & fleet points), so researching it would not let you build it. Tick Buildable and set a build cost/time.'));
  card.replaceChildren(...kids)}
 draw();return card}
function vetNorm(c){return JSON.stringify(c.map(r=>({rank:r.rank,effects:r.effects.map(e=>[Number(e.effect),Number(e.value)]).sort((a,b)=>a[0]-b[0])})).filter(r=>r.effects.length).sort((a,b)=>a.rank-b.rank))}
function vetCard(d){const v=d.vet,k=key(state.cur,'__vet'),card=el('div',{class:'card'});
 const get=()=>JSON.parse(JSON.stringify(state.pending[k]?state.pending[k].vet:v.current));
 const put=c=>{if(vetNorm(c)===vetNorm(v.current))delete state.pending[k];else state.pending[k]={ship:state.cur,vet:c};updPend();draw()};
 const rankOf=(c,rk)=>{let r=c.find(x=>x.rank===rk);if(!r){r={rank:rk,effects:[]};c.push(r)}return r};
 function draw(){const c=get(),kids=[el('h3',{},'Veterancy - bonuses per rank'),el('p',{class:'hint'},'Each rank can carry several effects. Values are the bonus amount (0.1 = 10%). An empty rank is left out.')];
  for(const rinfo of v.ranks){const r=c.find(x=>x.rank===rinfo.rank)||{rank:rinfo.rank,effects:[]};
   const rows=r.effects.map((e,i)=>el('div',{class:'row',style:'margin:3px 0'},
    el('select',{onchange:ev=>{const cc=get(),rr=rankOf(cc,rinfo.rank);rr.effects[i].effect=Number(ev.target.value);put(cc)}},Object.entries(v.effects).map(([id,n])=>el('option',{value:id,selected:Number(id)===e.effect},n))),
    el('input',{type:'number',step:'any',value:e.value,style:'width:110px',onchange:ev=>{const cc=get(),rr=rankOf(cc,rinfo.rank);rr.effects[i].value=Number(ev.target.value);put(cc)}}),
    el('button',{class:'sec',title:'remove this effect',onclick:()=>{const cc=get(),rr=rankOf(cc,rinfo.rank);rr.effects.splice(i,1);put(cc)}},'x')));
   const used=new Set(r.effects.map(e=>e.effect)),free=Object.keys(v.effects).find(id=>!used.has(Number(id)));
   kids.push(el('div',{style:'margin:10px 0 2px'},el('strong',{},'Rank '+rinfo.rank),el('span',{class:'hint'},'  ('+rinfo.xp+' XP)')),...rows,
    el('div',{class:'row'},
      free!==undefined?el('button',{class:'sec',onclick:()=>{const cc=get(),rr=rankOf(cc,rinfo.rank);rr.effects.push({effect:Number(free),value:0});put(cc)}},'+ add effect'):'',
      r.effects.length?el('button',{class:'sec',onclick:()=>{const cc=get();rankOf(cc,rinfo.rank).effects=[];put(cc)}},'Clear rank effects'):''))}
  card.replaceChildren(...kids)}
 draw();return card}
function renderShip(){const d=state.data;if(!d)return;const by={};d.fields.forEach(f=>by[f.name]=f);const used=new Set();const view=$('#view'),v=el('div');
 if(activeBundle?.ship!==d.name)activeBundle=shipBundleFields(d.name);
 const bundle=activeBundle;
 view.replaceChildren(el('div',{class:'row'},el('h2',{style:'margin:0 0 12px'},d.name),
   el('button',{class:'sec',onclick:()=>{showTab('localisation');loadLocalisation(d.name)}},'Ship localisation')));
 v.append(el('p',{class:'hint'},'Level database values from '+d.source),bundle.source);
 for(const [title,names] of GROUPS){
   const fields=names.map(name=>by[name]).filter(Boolean);
   fields.forEach(field=>used.add(field.name));
   const prefab=title==='Carrier & munition slots'?bundle.slots:title==='Hull & armour'?bundle.stats:null;
   if(fields.length||prefab){
     v.append(el('div',{class:'card'},el('h3',{},title),el('div',{class:'grid'},fields.map(fieldInput)),title==='Hull & armour'?bundle.linkControl:null,prefab));
   }
 }
 v.append(bundle.sections,bundle.actions);
 const rest=d.fields.filter(f=>!used.has(f.name)&&f.type!=='ro'),ro=d.fields.filter(f=>f.type==='ro');
 if(rest.length)v.append(el('div',{class:'card'},el('h3',{},'Other editable fields'),el('div',{class:'grid'},rest.map(fieldInput))));
 v.append(el('div',{class:'card'},el('h3',{},'Advantages & penalties'),el('div',{class:'ro'},'Advantages: '+(d.advantages.join(', ')||'none')),el('div',{class:'ro'},'Penalties: '+(d.penalties.join(', ')||'none')),el('p',{class:'hint'},'Advantages and penalties are read only for now (shipdb edits veterancy in the same way).')));
 v.append(researchCard(d));
 v.append(vetCard(d));
 v.append(el('div',{class:'card'},el('h3',{},'Read-only fields'),el('div',{class:'grid'},ro.map(f=>el('div',{class:'ro'},el('span',{class:'hint'},f.name+': '),f.value)))));
 shipEditorTabs(view,bundle,v);
}
let shipRequest=0;
async function openShip(name){
 const request=++shipRequest;
 state.cur=name;renderList();
 try{
   const fresh=$('#fresh').checked?'&fresh=1':'';
   const data=await api('/api/ship?name='+encodeURIComponent(name)+fresh);
   if(request===shipRequest){state.data=data;renderShip()}
 }catch(e){
   if(request===shipRequest)$('#view').replaceChildren(el('p',{class:'msg-bad'},e.message));
 }
}
async function openBundleFile(file){
 if(!state.ships.length){try{state.ships=await api('/api/ships')}catch(error){alert('Could not load ship records: '+error.message);return}}
 const imported=await api('/api/bundle/import',{bundle:file});file=imported.path;
 const importedName=file.split(/[\\/]/).pop();if(!state.info.bundle_files.includes(importedName))state.info.bundle_files.push(importedName);
 const normalize=value=>value.toLowerCase().replace(/\.dec$/i,'').replace(/[^a-z0-9]/g,'');
 const base=file.split(/[\\/]/).pop().replace(/\.[^.]+$/,'');
 const match=state.ships.find(ship=>normalize(ship.name)===normalize(base));
 if(match){openedBundles.set(match.name,file);activeBundle=undefined;await openShip(match.name);return}
 ++shipRequest;state.cur=null;state.data=null;activeBundle=shipBundleFields(null,file);renderList();
 const choice=el('select',{},[el('option',{value:''},'Choose the ship database record to use'),...state.ships.map(ship=>el('option',{value:ship.name},ship.name+' · '+ship.faction+' · '+ship.class))]);
 const attach=el('button',{class:'sec',onclick:async()=>{if(!choice.value)return;openedBundles.set(choice.value,file);await openShip(choice.value)}},'Load with selected ship data');
 const view=$('#view');
 view.replaceChildren(el('h2',{style:'margin:0 0 12px'},'Ship bundle editor'));
 shipEditorTabs(view,activeBundle,el('div',{},el('p',{class:'hint'},'This bundle name did not match a ship record. Choose the database record whose stats, research, veterancy, and other ship data should be shown with this exact bundle.'),el('div',{class:'card'},el('h3',{},'Link this bundle to a ship record'),el('div',{class:'row'},choice,attach)),activeBundle.source,activeBundle.stats,activeBundle.installedAbilities,activeBundle.slots,activeBundle.sections,activeBundle.actions));
}
async function loadShips(){state.ships=await api('/api/ships');renderList()}
$('#q').addEventListener('input',renderList);
$('#copy-ship').onclick=()=>showCloneShipForm();
function showCloneShipForm(){
 if(!state.ships.length){alert('Load ship data first.');return}
 const view=$('#view'),donor=el('select',{},state.ships.map(s=>el('option',{value:s.name},s.name+' · '+s.faction+' · '+s.class)));
 const name=el('input',{type:'text',placeholder:'e.g. Colonial Mercury Mk II'}),display=el('input',{type:'text',placeholder:'Name shown in game'});
 const bundle=el('input',{type:'text',placeholder:'new game bundle key, without .dec'});
 const donorLayer=el('select',{},[el('option',{value:'work'},'Current work copy (default)'),el('option',{value:'base'},'Configured base folder')]);
 const donorBundle=el('select',{},[]);
 const header=el('input',{type:'text',placeholder:'Blueprint heading'}),description=el('textarea',{rows:4,placeholder:'Blueprint description (one line; use <br> for line breaks)'});
 const list=el('select',{},[el('option',{value:'capital'},'Capital ship research'),el('option',{value:'battlestar'},'Battlestar research')]);
 const message=el('p',{class:'hint'});
 const normalize=s=>s.toLowerCase().replace(/[^a-z0-9]/g,'');
 const refreshDonorBundles=()=>{const files=donorLayer.value==='base'?(state.info.base_bundle_files||[]):state.info.bundle_files;
  donorBundle.replaceChildren(el('option',{value:''},files.length?'Choose a donor ship bundle':'No bundles in this source folder'),...files.map(file=>el('option',{value:file},file)));
 };
 const suggest=()=>{const selected=state.ships.find(s=>s.name===donor.value);if(!selected)return;
  refreshDonorBundles();const key=normalize(selected.name),files=donorLayer.value==='base'?(state.info.base_bundle_files||[]):state.info.bundle_files;
 const file=files.find(f=>normalize(f.replace(/\.dec$/i,''))===key);
  if(file)donorBundle.value=file;
  if(!name.value)name.value=selected.name+' Copy';if(!display.value)display.value=selected.name+' Copy';if(!header.value)header.value=display.value;
  list.value=/battlestar/i.test(selected.class)?'battlestar':'capital';
 };
 donor.onchange=suggest;donorLayer.onchange=suggest;display.oninput=()=>{if(!header.value)header.value=display.value};suggest();
 const field=(label,input)=>el('label',{class:'f'},label,input);
 const save=el('button',{class:'pri',onclick:async()=>{save.disabled=true;message.className='hint';message.textContent='Copying ship data into the work folder…';
  try{const result=await api('/api/ships/clone',{donor:donor.value,donorLayer:donorLayer.value,name:name.value,displayName:display.value,bundleName:bundle.value,donorBundle:donorBundle.value,descriptionHeader:header.value,description:description.value,researchList:list.value});
   state.pending={};updPend();await loadShips();message.className='msg-ok';message.textContent='Created '+result.name+' (ID '+result.id+'). Bundle, research entries, level records and localization are in the work folder. '+result.files.join(', ');
   await openShip(result.name);
  }catch(error){message.className='msg-bad';message.textContent=error.message;save.disabled=false}
 }},'Create ship in work folder');
 view.replaceChildren(el('h2',{},'Copy and add a ship'),el('p',{class:'hint'},'Clones the selected ship’s complete ship record and bundle, creates new localized name and blueprint text, and adds it to the selected campaign research list. Files are written to the project work tree.'),
  el('div',{class:'card'},el('h3',{},'New ship'),el('div',{class:'grid'},field('Copy ship settings from',donor),field('Donor source',donorLayer),field('Database ship name',name),field('In-game display name',display),field('New bundle file name',bundle),field('Donor bundle file',donorBundle),field('Research list',list),field('Blueprint heading',header),field('Blueprint description',description)),el('p',{class:'hint'},'The current work copy is used by default so its edited stats, abilities, and bundle settings carry over. Choose the configured base folder to copy vanilla values. The new ship is written to the work tree.'),el('div',{class:'row'},save),message));
}
$('#load-bundle').onclick=async()=>{try{const file=await pickPath({files:true,start:state.info.bundle_dir||state.info.home,title:'Load a ship bundle'});if(file)openBundleFile(file)}catch(e){alert(e.message)}};
$('#discard').onclick=()=>{state.pending={};updPend();if(state.data){renderShip();if(activeBundle?.currentFile)activeBundle.activatePath(activeBundle.currentFile)}};
$('#apply').onclick=async()=>{
 const edits=Object.values(state.pending),bundle=activeBundle,prefabFields=linkedPrefabChanges(bundle),b=$('#apply');
 b.disabled=true;let bundleSaved=false;
 try{
  if(Object.keys(prefabFields).length){
   if(!bundle.currentFile)throw new Error('The linked bundle is not loaded; choose a bundle before applying linked hull and armour values.');
   const saved=await api('/api/bundle/run',{bundle:bundle.currentFile,options:{},prefab_fields:prefabFields,overwrite:false});
   if(!saved.ok||!saved.dst)throw new Error('Could not save linked bundle fields: '+saved.output);
   bundleSaved=true;bundle.currentFile=saved.dst;openedBundles.set(state.cur,saved.dst);bundle.activatePath(saved.dst);
  }
  const r=edits.length?await api('/api/apply',{edits,fresh:$('#fresh').checked}):{log:[]};
  state.pending={};await loadShips();if(state.cur)await openShip(state.cur);updPend();
  alert('Applied. '+(bundleSaved?'Linked ship bundle saved.\n':'')+r.log.slice(0,6).join('\n')+(r.log.length>6?'\n...':'')+'\n\nSaved in '+state.info.work+' (level data and bundle). Merge this tree into the game folder when ready.');
 }catch(e){alert((bundleSaved?'The linked bundle was saved, but another part of the apply failed: ':'Failed: ')+e.message);b.disabled=false}
};
document.querySelectorAll('header nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('header nav button').forEach(x=>x.classList.toggle('on',x===b));for(const t of ['ships','dll','commanders','squadrons','tools','localisation','armaments','settings'])$('#tab-'+t).hidden=t!==b.dataset.t;if(b.dataset.t==='dll')loadDll();if(b.dataset.t==='commanders')loadCommanders();if(b.dataset.t==='squadrons')loadSquadrons();if(b.dataset.t==='tools')loadTools();if(b.dataset.t==='localisation')loadLocalisation();if(b.dataset.t==='armaments')loadArmaments();if(b.dataset.t==='settings')loadSettings()});
let bundleForm;
let activeBundle;
const openedBundles=new Map();
function shipEditorTabs(view,bundle,details){
 const layout=bundle.layout.panel;
 details.setAttribute('role','tabpanel');
 details.setAttribute('aria-labelledby','ship-details-tab');
 details.id='ship-details-panel';
 layout.setAttribute('role','tabpanel');
 layout.setAttribute('aria-labelledby','ship-layout-tab');
 layout.id='ship-layout-panel';
 const bar=el('div',{class:'row ship-editor-tabs',role:'tablist','aria-label':'Ship editor views'});
 const show=selected=>{
   bundle.view=selected;
   details.hidden=selected==='layout';
   layout.hidden=selected!=='layout';
   for(const [name,tab] of [['details',detailTab],['layout',layoutTab]]){
     tab.setAttribute('aria-selected',String(selected===name));
     tab.tabIndex=selected===name?0:-1;
   }
   if(selected==='layout')bundle.layout.load();
 };
 const detailTab=el('button',{class:'sec',role:'tab',id:'ship-details-tab','aria-controls':details.id,onclick:()=>show('details')},'Ship details');
 const layoutTab=el('button',{class:'sec',role:'tab',id:'ship-layout-tab','aria-controls':layout.id,onclick:()=>show('layout')},'Weapon layout');
 bar.append(detailTab,layoutTab);
 bar.addEventListener('keydown',event=>{
   if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
   event.preventDefault();
   const tab=event.key==='Home'?detailTab:event.key==='End'?layoutTab:event.target===detailTab?layoutTab:detailTab;
   tab.click();tab.focus();
 });
 view.append(bar,details,layout);
 show(bundle.view||'details');
}

function weaponLayout(bundle){
 const panel=el('section',{class:'card',hidden:true,'aria-label':'Weapon layout'});
 const status=el('p',{class:'hint',role:'status'});
 const canvas=el('canvas',{width:720,height:440,'aria-label':'Weapon mount projection. Use the mount selector to edit with the keyboard.'});
 const mountSelect=el('select',{'aria-label':'Weapon mount',onchange:()=>{active=mountSelect.selectedIndex;renderFields();draw()}});
 const views={Top:['x','z'],Side:['z','y'],Front:['x','y']};
 const projection=el('select',{'aria-label':'Projection',onchange:draw},Object.keys(views).map(name=>el('option',{value:name},name)));
 const fields=el('div',{class:'layout-properties'});
 const form=el('form',{onsubmit:event=>{event.preventDefault();save()}},fields);
 const workspace=el('div',{class:'layout-workspace'},canvas,form);
 const addButton=el('button',{class:'sec','aria-expanded':'false',onclick:()=>{
   if(placing){endPlacement();return}
   const selected=rows[active];
   newWeapon.value=String(selected.weapon);
   placing=true;renderPlacement();newWeapon.focus();
 }},'Add turret');
 const newWeapon=el('select',{'aria-label':'New turret weapon'});
 const placeNear=el('button',{class:'sec',onclick:()=>placeTurret()},'Place near a mount');
 const cancelAdd=el('button',{class:'sec',onclick:()=>endPlacement(true)},'Cancel');
 const placement=el('div',{class:'layout-placement'},el('div',{class:'row'},
   el('label',{class:'f'},'New turret weapon',newWeapon),placeNear,cancelAdd),
   el('p',{class:'hint'},'Click a position in the hull view. The new turret inherits depth and facing from a nearby mount; refine them after placing.'));
 const tools=el('div',{class:'row layout-tools'},mountSelect,projection,addButton);
 const reload=el('button',{class:'sec',onclick:()=>{if(!dirty()||confirm('Discard unsaved weapon layout changes?'))load(true)}},'Reload');
 const saveButton=el('button',{class:'pri',onclick:save},'Save weapon layout');
 panel.append(el('h3',{},'Weapon layout'),status,tools,placement,workspace,el('div',{class:'row layout-footer'},saveButton,reload));
 let source='', loaded='', revision='', rows=[], original=[], hull=[], weapons={}, quadrants=[], active=0, request=0, busy=false, drag, clipboard, placing=false;
 const editable=row=>({id:row.id,clone:Boolean(row.clone),new_mount:Boolean(row.newMount),weapon:row.weapon,quadrant:row.quadrant,
   scale:row.scale,angles:row.angles,position:row.position});
 const changes=()=>rows.filter((row,index)=>row.clone||JSON.stringify(editable(row))!==JSON.stringify(editable(original[index]))).map(editable);
 const dirty=()=>changes().length>0;
 const label=(title,input)=>el('label',{class:'f'},title,input);
 const transform=(matrix,vector)=>Object.fromEntries([... 'xyz'].map((axis,i)=>[axis,
   matrix[i][3]+[... 'xyz'].reduce((sum,key,j)=>sum+matrix[i][j]*vector[key],0)]));
 const round=value=>Number(value.toFixed(5));
 const context=canvas.getContext('2d');
 let points=[], frame;

 function setSource(file,defer=false){
   if(file===source)return;
   source=file;loaded='';revision='';rows=[];original=[];hull=[];request++;busy=false;drag=null;
   placing=false;render();
   if(!panel.hidden&&!defer)load();
 }
 async function load(force=false){
   if(!source){status.textContent='Choose a ship bundle in Ship details.';return}
   if(!force&&(busy||loaded===source))return;
   const current=source,token=++request;
   busy=true;placing=false;render();status.textContent='Reading weapon mounts…';
   try{
     const result=await api('/api/bundle/layout',{bundle:current});
     if(token!==request)return;
     ({revision,quadrants,weapons,hull}=result);loaded=current;
     original=result.mounts;rows=structuredClone(original);active=0;
     const addTypes={...Object.fromEntries(original.map(row=>[row.weapon,'Type '+row.weapon])),...weapons};
     newWeapon.replaceChildren(...Object.entries(addTypes).map(([id,name])=>el('option',{value:id},name)));
     status.textContent=rows.length?'Select a mount or drag it in the projection. Position fields are local to its parent.':'This bundle has no weapon mounts to use as a template for adding a turret.';
   }catch(error){if(token===request)status.textContent=error.message}
   finally{if(token===request){busy=false;render()}}
 }
 function render(){
   const ready=loaded===source&&rows.length>0;
   tools.hidden=workspace.hidden=!ready;
   reload.disabled=busy||!source;
   saveButton.disabled=busy||!dirty();
   mountSelect.replaceChildren(...rows.map((row,index)=>el('option',{value:String(index)},row.name+(row.clone?' (new)':''))));
   mountSelect.selectedIndex=active;
   renderPlacement();renderFields();draw();
 }
 function draw(){
   if(!context||!rows.length)return;
   const [h,v]=views[projection.value];
   const reference=[...original.map(row=>transform(row.matrix,row.position)),...hull.map(([x,y,z])=>({x,y,z}))];
   const span=axis=>[Math.min(...reference.map(p=>p[axis])),Math.max(...reference.map(p=>p[axis]))];
   const [minH,maxH]=span(h),[minV,maxV]=span(v);
   const scale=Math.min(630/Math.max(1,maxH-minH),350/Math.max(1,maxV-minV));
   frame={h,v,scale,x:360-(minH+maxH)*scale/2,y:220+(minV+maxV)*scale/2};
   const toPoint=p=>[frame.x+p[h]*scale,frame.y-p[v]*scale];
   const ctx=context;
   ctx.fillStyle='#111b27';ctx.fillRect(0,0,720,440);
   ctx.strokeStyle='#32465b';ctx.beginPath();
   if(frame.x>=0&&frame.x<=720){ctx.moveTo(frame.x,0);ctx.lineTo(frame.x,440)}
   if(frame.y>=0&&frame.y<=440){ctx.moveTo(0,frame.y);ctx.lineTo(720,frame.y)}
   ctx.stroke();
   ctx.font='14px sans-serif';ctx.fillStyle='#a9bdd2';ctx.fillText(h.toUpperCase(),694,420);ctx.fillText(v.toUpperCase(),14,20);
   ctx.fillStyle='#405568';
   for(const [x,y,z] of hull){const [a,b]=toPoint({x,y,z});ctx.fillRect(a,b,2,2)}
   points=rows.map(row=>toPoint(transform(row.matrix,row.position)));
   const marker=(i,selected)=>{
     const [x,y]=points[i];ctx.beginPath();ctx.arc(x,y,selected?8:5,0,Math.PI*2);
     ctx.fillStyle=selected?'#f9bc60':rows[i].clone?'#85e0b0':'#82bdf5';ctx.fill();
     if(selected){ctx.strokeStyle='#fff';ctx.stroke()}
   };
   points.forEach((_,index)=>{if(index!==active)marker(index,false)});
   marker(active,true);
 }
 function renderFields(){
   const row=rows[active];
   fields.replaceChildren();
   if(!row)return;
   const title=el('strong',{},row.name);
   const select=(values,key)=>el('select',{value:String(row[key]),disabled:busy,onchange:event=>{
     row[key]=Number(event.target.value);saveButton.disabled=busy||!dirty();
   }},values.map(([id,name])=>el('option',{value:String(id),selected:id===row[key]},name)));
   const types=Object.entries({...weapons,[row.weapon]:weapons[row.weapon]||'Type '+row.weapon}).map(([id,name])=>[Number(id),name]);
   const facing=quadrants.map((name,id)=>[id,name[0].toUpperCase()+name.slice(1)]);
   if(!facing.some(([id])=>id===row.quadrant))facing.push([row.quadrant,'Quadrant '+row.quadrant]);
   const number=(value,write,min)=>el('input',{type:'number',step:'any',required:true,value,min,disabled:busy,
     oninput:event=>{
       const input=event.target;
       if(input.validity.valid&&Number.isFinite(input.valueAsNumber))write(input.valueAsNumber);
       saveButton.disabled=busy||!dirty();draw();
     }});
   const vector=(name,key)=>el('fieldset',{class:'layout-vector'},el('legend',{},name),...[... 'xyz'].map(axis=>
     label(axis.toUpperCase(),number(round(row[key][axis]),value=>{row[key][axis]=value}))));
   const duplicate=el('button',{type:'button',class:'sec',disabled:busy||!row.can_clone||rows.length-original.length>=1024,onclick:()=>{
     const clone=structuredClone(row);clone.clone=true;clone.position.x+=1.5;
     rows.push(clone);active=rows.length-1;render();
   }},'Clone mount');
   const copy=el('button',{type:'button',class:'sec',disabled:busy,onclick:()=>{
     clipboard={weapon:row.weapon,quadrant:row.quadrant,scale:row.scale,angles:{...row.angles}};
     renderFields();status.textContent='Mount settings copied.';
   }},'Copy settings');
   const paste=el('button',{type:'button',class:'sec',disabled:busy||!clipboard,onclick:()=>{
     Object.assign(row,structuredClone(clipboard));render();
   }},'Paste settings');
   const reset=el('button',{type:'button',class:'sec',disabled:busy,onclick:()=>{
     if(row.clone){rows.splice(active,1);active=Math.min(active,rows.length-1)}
     else rows[active]=structuredClone(original[active]);
     render();
   }},row.clone?'Remove unsaved mount':'Reset mount');
   fields.append(title,label('Weapon',select(types,'weapon')),label('Quadrant',select(facing,'quadrant')),vector('Local position','position'),
     vector('Initial angles','angles'),label('Weapon mesh scale',number(row.scale,value=>{row.scale=value},row.scale===0?0:0.000001)),
     el('div',{class:'row'},duplicate,reset),el('div',{class:'row'},copy,paste));
 }
 function renderPlacement(){
   addButton.disabled=busy||!original.some(row=>row.can_clone&&row.inverse)||rows.length-original.length>=1024;
   addButton.setAttribute('aria-expanded',String(placing));
   placement.hidden=!placing;
   canvas.classList.toggle('placing-turret',placing);
 }
 function endPlacement(focus=false){
   placing=false;renderPlacement();
   if(focus)addButton.focus();
 }
 function placeTurret(point){
   if(!placing||busy)return;
   const weapon=Number(newWeapon.value);
   const [h,v]=views[projection.value];
   const selected=rows[active];
   const origin=point||transform(selected.matrix,selected.position);
   const candidates=original.filter(row=>row.can_clone&&row.inverse);
   const distance=row=>{
     const pos=transform(row.matrix,row.position);
     return Math.hypot(pos[h]-origin[h],pos[v]-origin[v]);
   };
   const nearest=candidates.reduce((a,b)=>{
     const delta=distance(a)-distance(b);
     return delta>0||(delta===0&&b.weapon===weapon)?b:a;
   });
   const turret=structuredClone(nearest);
   turret.name='New turret';turret.clone=true;turret.newMount=true;turret.weapon=weapon;
   if(point){
     const position=transform(nearest.matrix,nearest.position);
     position[h]=point[h];position[v]=point[v];
     turret.position=Object.fromEntries(Object.entries(transform(nearest.inverse,position)).map(([key,value])=>[key,round(value)]));
   }else turret.position.x+=1.5;
   rows.push(turret);active=rows.length-1;
   placing=false;render();
   status.textContent='Turret added. Save the weapon layout to write it to the bundle.';
   fields.querySelector('select').focus({preventScroll:true});
 }
 panel.addEventListener('keydown',event=>{
   if(event.key==='Escape'&&placing){event.preventDefault();endPlacement(true)}
 });
 function pointer(event){
   const rect=canvas.getBoundingClientRect();
   return [(event.clientX-rect.left)*720/rect.width,(event.clientY-rect.top)*440/rect.height];
 }
 canvas.addEventListener('pointerdown',event=>{
   if(busy||!rows.length||event.button!==0)return;
   const [x,y]=pointer(event);
   if(placing){
     const {h,v,scale}=frame;
     placeTurret({[h]:(x-frame.x)/scale,[v]:(frame.y-y)/scale});
     return;
   }
   const hits=points.map(([a,b],i)=>[Math.hypot(a-x,b-y),i]).filter(([distance])=>distance<=13).sort((a,b)=>a[0]-b[0]);
   if(!hits.length)return;
   active=hits.find(([,i])=>i===active)?.[1]??hits[0][1];
   const row=rows[active];
   if(row.inverse){
     drag={id:event.pointerId,x,y,position:transform(row.matrix,row.position),local:{...row.position},frame};
     canvas.setPointerCapture(event.pointerId);
   }
   render();
 });
 canvas.addEventListener('pointermove',event=>{
   if(drag?.id!==event.pointerId||busy)return;
   const [x,y]=pointer(event),{h,v,scale}=drag.frame;
   if(!drag.moved&&x===drag.x&&y===drag.y)return;
   drag.moved=true;
   const row=rows[active],point={...drag.position};
   point[h]+=(x-drag.x)/scale;point[v]-=(y-drag.y)/scale;
   row.position=x===drag.x&&y===drag.y?drag.local:Object.fromEntries(Object.entries(transform(row.inverse,point)).map(([key,value])=>[key,round(value)]));
   renderFields();draw();saveButton.disabled=!dirty();
 });
 for(const name of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(name,()=>{drag=null});
 async function save(){
   if(busy||!dirty()||!form.reportValidity())return;
   const current=source,token=++request;
   busy=true;placing=false;render();status.textContent='Saving weapon layout…';
   try{
     const result=await api('/api/bundle/layout/save',{bundle:current,layout:{revision,changes:changes()}});
     if(token!==request)return;
     if(!result.ok)throw new Error(result.output);
     loaded='';busy=false;bundle.activatePath(result.dst,true);
     await load(true);
     if(source===result.dst&&loaded===source)status.textContent='Saved to '+result.dst;
   }catch(error){if(token===request)status.textContent=error.message}
   finally{if(token===request){busy=false;render()}}
 }
 render();
 return {panel,setSource,load};
}

function shipBundleFields(ship,initialFile=''){
 const bundle={
   ship,
   source:el('div',{class:'card ship-bundle'},el('h3',{},'Bundle file'),el('p',{class:'hint'},'Loading bundle tools…')),
   slots:el('div',{class:'bundle-slot-fields'}),
   slotStatus:el('p',{class:'hint'}),
   stats:el('div',{class:'bundle-slot-fields','aria-live':'polite'}),
   linkBox:el('input',{type:'checkbox',checked:true,disabled:true}),
   linkHint:el('span',{class:'hint'},'Load a matching bundle to link hull and armour values.'),
   linkRepairButtons:null,
   linkDetails:el('div',{class:'hint'}),
   linkControl:null,
   installedAbilities:el('div',{class:'bundle-slot-fields'}),
   sections:el('div'),
   actions:el('div',{class:'card'})
 };
 bundle.layout=weaponLayout(bundle);
 bundle.linkControl=el('div',{class:'row',style:'margin:8px 0'},el('label',{class:'f chk',title:'Synchronize level database and bundle prefab hull/armour values one-to-one.'},bundle.linkBox,'Link level and bundle hull & armour'),bundle.linkHint);
 const useLevelSource=el('button',{class:'sec',onclick:()=>{
   for(const [dbField,prefabKey] of Object.entries(HULL_ARMOUR_LINKS)){
     const input=bundle.stats.querySelector('[data-prefab-field="'+prefabKey+'"]'),value=Number(shipFieldValue(dbField));
     if(input&&Number.isFinite(value)){
       input.value=String(value);input.dispatchEvent(new Event('input',{bubbles:true}));
       state.pending[key(state.cur,dbField)]={ship:state.cur,field:dbField,value};
     }
   }
   refreshHullArmourLinkUI(bundle);updPend();
 }},'Use level data as source');
 const useBundleSource=el('button',{class:'sec',onclick:()=>{
   for(const [dbField,prefabKey] of Object.entries(HULL_ARMOUR_LINKS)){
     const input=bundle.stats.querySelector('[data-prefab-field="'+prefabKey+'"]');
     if(input&&Number.isFinite(input.valueAsNumber))setShipFieldValue(dbField,input.valueAsNumber);
   }
   refreshHullArmourLinkUI(bundle);
 }},'Use bundle as source');
 bundle.linkRepairButtons=el('div',{class:'row',hidden:true},useLevelSource,useBundleSource);
 bundle.linkControl.append(bundle.linkRepairButtons,bundle.linkDetails);
 bundle.linkBox.addEventListener('change',()=>{
   bundle.linkHint.textContent=linkedHullArmourLabel();
   if(bundle.linkBox.checked)for(const field of Object.keys(HULL_ARMOUR_LINKS))syncBundleStatFromLevel(field,shipFieldValue(field));
 });
 bundle.stats.addEventListener('input',event=>{
   const prefabKey=event.target.dataset.prefabField;
   const pair=Object.entries(HULL_ARMOUR_LINKS).find(([,key])=>key===prefabKey);
   if(!pair||syncingHullArmour||!bundle.linkBox.checked||!Number.isFinite(event.target.valueAsNumber))return;
   const [dbField]=pair;
   syncingHullArmour=true;setShipFieldValue(dbField,event.target.valueAsNumber);syncingHullArmour=false;refreshHullArmourLinkUI(bundle);updPend();
 });
 bundleForm??=api('/api/bundle/form');
 bundleForm.then(options=>loadBundleTools(bundle,options,initialFile)).catch(error=>{
   bundleForm=undefined;
   bundle.source.replaceChildren(el('h3',{},'Bundle file'),el('p',{class:'msg-bad'},error.message),
     el('button',{class:'sec',onclick:()=>{activeBundle=undefined;if(bundle.ship===null)openBundleFile(initialFile);else renderShip()}},'Retry'));
 });
 return bundle;
}
function loadBundleTools(bundle,bundleOptions,initialFile=''){
 const {ship,source,slots,sections,actions}=bundle;
 for(const option of bundleOptions)if(['defender_donor','celestra_donor','stealth'].includes(option.dest))option.choices=state.info.bundle_files.slice();
 const get=id=>source.querySelector('#'+id)??actions.querySelector('#'+id);
 const path=el('input',{type:'text',id:'bpath',value:initialFile,placeholder:'path to a ship bundle (encrypted game file or decrypted .dec)',style:'flex:1;min-width:280px;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'});
 let statsRequest=0,compareRequest=0,compareData=null;
 async function refreshStats(file,deferLayout=false){
   const request=++statsRequest;bundle.currentFile=file;bundle.layout.setSource(file,deferLayout);
   bundle.stats.replaceChildren();
   bundle.installedAbilities.replaceChildren();
   if(bundle.compare)bundle.compare.replaceChildren();
   delete bundle.stats.dataset.source;
   for(const area of [bundle.slots,bundle.sections])area.querySelectorAll('[data-d]').forEach(input=>{
     input.disabled=false;
     if(input.type==='checkbox')input.checked=false;
     else input.value='';
   });
   if(!file)return;
   bundle.stats.textContent='Reading current bundle settings…';
   try{
     const result=await api('/api/bundle/stats',{bundle:file});
     if(request!==statsRequest)return;
     const stored=Object.fromEntries(result.fields.map(field=>[field.key,field.value]));
     const setOption=(name,value)=>{const input=bundle.sections.querySelector('[data-d="'+name+'"]');if(input&&value!==undefined)input.type==='checkbox'?input.checked=Boolean(value):input.value=String(value)};
     const hullRate=stored['RegenerativeHullResinModule.hullPerTick'];
     if(hullRate!==undefined)setOption('hull_regen',Math.abs(Number(hullRate)));
     setOption('munition_factory',Boolean(result.abilities.MunitionFactoryModule));
     for(const [dest,script] of [['custom_armour_repair','CustomArmourRepairModule'],
       ['custom_squadron_replacement','CustomSquadronReplacementModule'],
       ['custom_drone_resupply','CustomDroneResupplyModule'],
       ['custom_magazine_capacity','CustomMagazineCapacityModule'],['custom_instant_plating','CustomInstantPlatingModule'],
       ['custom_emp_pulse','CustomEmpPulseModule'],['custom_targeted_emp','CustomTargetedEmpModule']]){
       const input=bundle.sections.querySelector('[data-d="'+dest+'"]');
       if(input){const installed=Boolean(result.abilities[script]);input.checked=installed;input.disabled=installed}
     }
     const groupsInput=bundle.slots.querySelector('[data-d="squadron_groups"]');
     if(groupsInput&&!groupsInput.dataset.manual)groupsInput.value=(result.squadron_groups||[]).join(',');
     bundle.slotStatus.replaceChildren();bundle.slotStatus.className='hint';
     if(result.squadron_groups?.length&&state.data?.name===ship){
       const expected=['FighterSlots','UtilityFighterSlots'].map(name=>Number(shipFieldValue(name)));
       const actual=result.squadron_groups.reduce((sum,count)=>sum+count,0),wanted=expected.reduce((sum,count)=>sum+count,0);
       if(actual!==wanted){bundle.slotStatus.className='msg-bad';bundle.slotStatus.textContent='This bundle has '+actual+' launch actions in groups '+result.squadron_groups.join(',')+', but the ship database has '+expected[0]+' fighter and '+expected[1]+' support slots ('+wanted+' total).';}
     }
     const labels={MaxHullPoints:'Hull',defaultArmourFront:'Front',defaultArmourRear:'Rear',
       defaultArmourLeft:'Port',defaultArmourRight:'Starboard',defaultArmourTop:'Top',defaultArmourBottom:'Bottom'};
     const modifierFields=result.modifier_fields||[];
     const fieldLabels={'CustomInstantPlatingModule.cooldownTurns':'Cooldown (battle turns)','CustomInstantPlatingModule.activationDelayTicks':'Activation delay after turn starts (ticks)','CustomEmpPulseModule.cooldownTurns':'Cooldown (battle turns)','CustomEmpPulseModule.detonationDelayTicks':'Activation delay after turn starts (ticks)','CustomEmpPulseModule.range':'EMP range (units)','CustomEmpPulseModule.subsystemDamage':'Subsystem maximum health reduced','CustomEmpPulseModule.recoveryTicks':'Subsystem recovery (ticks)','CustomEmpPulseModule.disableSquadronTicks':'Fighter stun duration (ticks)','CustomEmpPulseModule.fighterEvadeDebuffTicks':'Fighter evade debuff duration (ticks)','CustomTargetedEmpModule.range':'EMP blast radius (units)','CustomTargetedEmpModule.detonationDelayTicks':'Activation delay after turn starts (ticks)','CustomTargetedEmpModule.subsystemDamage':'Subsystem maximum health reduced','CustomTargetedEmpModule.recoveryTicks':'Subsystem recovery (ticks)','CustomTargetedEmpModule.disableSquadronTicks':'Fighter stun duration (ticks)','CustomTargetedEmpModule.fighterEvadeDebuffTicks':'Fighter evade debuff duration (ticks)','CustomTargetedEmpModule.chargesPerCooldown':'Charges per turn','CustomTargetedEmpModule.burstIntervalTicks':'Ticks between separate bursts','CustomTargetedEmpModule.targetRange':'Map targeting range (units)',baseRate:'Armour repair per tick, per facing',modifierRate:'Crew bonus weight',modifierField:'Crew bonus to use',
       baseTicksPerReplacement:'Base ticks to replace one squadron',basePlatingPerTick:'Drone reserve restored per tick',
       capacityMultiplier:'Magazine capacity multiplier','MunitionResupplyRange.Min':'Damaged Armory work rate (%)',
       'MunitionResupplyRange.Max':'Full Armory work rate (%)',portPlating:'Port plating per use',
       starboardPlating:'Starboard plating per use',forePlating:'Fore plating per use',aftPlating:'Aft plating per use',
       dorsalPlating:'Top plating per use',ventralPlating:'Bottom plating per use'};
     const fieldHelp={'CustomInstantPlatingModule.cooldownTurns':'1 allows use next turn. 2 skips one intervening turn.',modifierRate:'0 ignores the crew bonus. 1 applies all of it. 0.5 applies half of it.',
       'CustomEmpPulseModule.range':'Maximum distance from this ship; default is 1,000 game units.',
       'CustomEmpPulseModule.cooldownTurns':'Battle turns between successful activations; default is 1.',
       'CustomEmpPulseModule.detonationDelayTicks':'The EMP queues at turn start. 0 fires immediately at that event; 10 ticks is 1 second, 120 ticks is 1 turn.',
       'CustomInstantPlatingModule.activationDelayTicks':'Plating queues at turn start. 0 applies at that event; 10 ticks is 1 second, 120 ticks is 1 turn.',
       'CustomEmpPulseModule.subsystemDamage':'Temporary subsystem maximum-health reduction per subsystem.',
       'CustomEmpPulseModule.recoveryTicks':'Ticks for the subsystem EMP effect to recover; default is 240.',
       'CustomEmpPulseModule.disableSquadronTicks':'Ticks that enemy fighter squadrons remain stunned; default is 360 (3 turns).',
       'CustomEmpPulseModule.fighterEvadeDebuffTicks':'Ticks that enemy fighters remain jammed. This applies the game’s normal JammingPenalty, including the configured evade penalty; default is 360 (3 turns, matching the vanilla Cylon Virus Mine duration).',
       'CustomTargetedEmpModule.range':'Blast radius around each selected map point; defaults to 1,000 units.',
       'CustomTargetedEmpModule.targetRange':'Maximum PCM-style map targeting range from the ship; defaults to 4,000 units.',
       'CustomTargetedEmpModule.chargesPerCooldown':'Maximum uses per turn (default 4, maximum 16). Each activation selects one independent 3D point; unused charges refresh after the simulation ends.',
       'CustomTargetedEmpModule.burstIntervalTicks':'Simulation ticks between each independently detonated point; default is 10 ticks (1 second). The first point fires at turn start after the configured delay.',
       'CustomTargetedEmpModule.cooldownTurns':'Battle turns between targeted EMP salvos; default is 1.',
       'CustomTargetedEmpModule.detonationDelayTicks':'The selected EMP bursts detonate at turn start after this many ticks; 10 ticks is 1 second and 120 ticks is 1 turn.',
       'CustomTargetedEmpModule.subsystemDamage':'Temporary subsystem maximum-health reduction per subsystem.',
       'CustomTargetedEmpModule.recoveryTicks':'Ticks for the subsystem EMP effect to recover; default is 240.',
       'CustomTargetedEmpModule.disableSquadronTicks':'Ticks that enemy fighter squadrons remain stunned; default is 360 (3 turns).',
       'CustomTargetedEmpModule.fighterEvadeDebuffTicks':'Ticks that enemy fighters remain jammed. This applies the game’s normal JammingPenalty, including the configured evade penalty; default is 360 (3 turns, matching the vanilla Cylon Virus Mine duration).',
       baseRate:'Points restored to each damaged facing before crew modifiers. Repair stops at that facing’s normal maximum.',
       baseTicksPerReplacement:'Ticks to replace one destroyed squadron with no crew bonus. Lower values are faster.',
       basePlatingPerTick:'Reserve restored before crew modifiers. Returning drones refund unused payload separately.',
       'MunitionResupplyRange.Min':'A percentage of one work unit per tick at the lower Armory endpoint.',
       'MunitionResupplyRange.Max':'A percentage of one work unit per tick at full Armory effectiveness.'};
     const TICKS_PER_SECOND=10,TICKS_PER_TURN=120;
     const format=n=>Number(n.toFixed(4)).toString();
     const timedRates={
       'CustomArmourRepairModule.baseRate':'armour points per facing',
       'CustomDroneResupplyModule.basePlatingPerTick':'plating reserve points',
     };
     const rateDescription=(script,name,value)=>{
       if(value===''||!Number.isFinite(Number(value)))return '';
       const n=Number(value);
       if(name==='modifierRate'&&script!=='CustomSquadronReplacementModule')
         return `With a +25% crew bonus, this weight gives ${format(Math.max(0,1+0.25*n))}× the base rate`;
       if(script==='Armory'&&name.startsWith('MunitionResupplyRange.'))
         return `${format(n/100)} work/tick · ${format(n*TICKS_PER_SECOND/100)} work/sec · ${format(n*TICKS_PER_TURN/100)} work/turn`;
       return '';
     };
     const rateNote=el('p',{class:'hint'},'One turn is 120 ticks, or 12 simulation seconds. Instant Plating and both EMP abilities queue for turn start; their activation delays are configurable in ticks.');
     const primary=[],advanced=[];
     const subsystemGroups=new Map(Object.entries(result.subsystems||{}).map(([script,title])=>[script,{title,controls:[]}]));
     const subsystemLabel=name=>name.split('.').map(part=>part
       .replace(/([a-z0-9])([A-Z])/g,'$1 $2').replace(/ Range(?: Range)?$/,'')
       .replace(/^./,letter=>letter.toUpperCase())).join(' · ');
     const abilities=new Map(Object.keys(result.abilities).map(script=>[script,[]]));
     const numberInput=(attrs,value,step)=>el('input',{...attrs,type:'number',required:true,step,value});
     for(const field of result.fields){
       if(field.key==='CustomTargetedEmpModule.cooldownTurns')continue;
       const separator=field.key.indexOf('.');
       const script=field.key.slice(0,separator),name=field.key.slice(separator+1);
       const subsystem=subsystemGroups.get(script);
       const label=labels[name]||fieldLabels[field.key]||fieldLabels[name]||
         (subsystem?(name==='MaxHealth'?'Maximum health':subsystemLabel(name)):name);
       const original=field.kind==='bool'?Boolean(field.value):field.value;
       const attrs={'data-prefab-field':field.key,'data-original':JSON.stringify(original)};
       let control;
       if(field.kind==='vector'){
         control=el('fieldset',attrs,el('legend',{},label),
           ...['x','y','z'].map(axis=>el('label',{class:'f'},axis.toUpperCase(),
             numberInput({'data-axis':axis},field.value[axis],'any'))));
       }else if(name==='modifierField'){
         control=el('label',{class:'f',title:fieldHelp[field.key]||fieldHelp[name]||field.key},label,
           el('select',attrs,...modifierFields.map(option=>el('option',{value:option.value,selected:Number(original)===option.value},option.label))));
       }else{
         const input=field.kind==='bool'?el('input',{...attrs,type:'checkbox',checked:original})
           :numberInput(attrs,field.value,field.kind==='int'?'1':'any');
       if(['CustomInstantPlatingModule.cooldownTurns','CustomEmpPulseModule.cooldownTurns','CustomTargetedEmpModule.cooldownTurns'].includes(field.key)){input.min=1;input.max=999}
         else if(['CustomInstantPlatingModule.activationDelayTicks','CustomEmpPulseModule.detonationDelayTicks','CustomTargetedEmpModule.detonationDelayTicks'].includes(field.key)){input.min=0;input.max=99999}
         else if(['CustomEmpPulseModule.range','CustomTargetedEmpModule.range','CustomTargetedEmpModule.targetRange'].includes(field.key))input.min=0;
         else if(['CustomEmpPulseModule.subsystemDamage','CustomEmpPulseModule.recoveryTicks','CustomEmpPulseModule.fighterEvadeDebuffTicks','CustomTargetedEmpModule.subsystemDamage','CustomTargetedEmpModule.recoveryTicks','CustomTargetedEmpModule.fighterEvadeDebuffTicks'].includes(field.key))input.min=0;
         else if(field.key==='CustomTargetedEmpModule.chargesPerCooldown'){input.min=1;input.max=16}
         else if(field.key==='CustomTargetedEmpModule.burstIntervalTicks'){input.min=1;input.max=99999}
         else if(name==='baseTicksPerReplacement')input.min=1;
         else if(script==='CustomInstantPlatingModule')input.min=0;
         control=el('label',{class:'f',title:fieldHelp[field.key]||fieldHelp[name]||field.key},label,input);
       }
       const unit=timedRates[field.key];
       const readout=rateDescription(script,name,original);
       if(unit){
         const canonical=control.querySelector('[data-prefab-field]');
         canonical.min='0';
         const conversions=[[TICKS_PER_SECOND,'Per simulation second'],[TICKS_PER_TURN,'Per turn']].map(([factor,title])=>{
           const input=el('input',{type:'number',min:0,step:'any',value:format(original*factor)});
           return {factor,input,label:el('label',{class:'f'},title,input)};
         });
         const sync=(except)=>{for(const {factor,input} of conversions)if(input!==except)
           input.value=canonical.value===''?'':format(canonical.valueAsNumber*factor)};
         canonical.addEventListener('input',()=>sync());
         for(const {factor,input} of conversions)input.addEventListener('input',()=>{
           canonical.value=input.value===''||!input.validity.valid?'':String(input.valueAsNumber/factor);sync(input);
         });
         control=el('div',{class:'rate-setting'},control,el('div',{class:'grid'},conversions.map(entry=>entry.label)),
           el('p',{class:'hint'},unit+' before crew bonuses. Editing any unit updates the others.'));
       }else if(readout){
         const holder=el('div',{class:'rate-setting'},control,el('p',{class:'hint','data-rate-hint':field.key},readout));
         const editable=holder.querySelector('[data-prefab-field]');
         editable.addEventListener('input',()=>{holder.querySelector('[data-rate-hint]').textContent=rateDescription(script,name,editable.value)});
         control=holder;
       }
       const group=abilities.get(script)||subsystem?.controls||(labels[name]?primary:advanced);
       group.push(control);
     }
     const squadronFields=abilities.get('CustomSquadronReplacementModule');
     if(squadronFields?.length){
       const setting=name=>squadronFields.map(field=>field.querySelector('[data-prefab-field="CustomSquadronReplacementModule.'+name+'"]')).find(Boolean);
       const ticks=setting('baseTicksPerReplacement'),weight=setting('modifierRate');
       if(ticks&&weight){
         const bonus=el('input',{type:'number',value:0,step:'any'});
         const readout=el('strong',{'aria-live':'polite'});
         const timing=el('p',{class:'hint'});
         const update=()=>{
           const base=ticks.valueAsNumber,crew=bonus.valueAsNumber,crewWeight=weight.valueAsNumber;
           if(!ticks.validity.valid||![base,crew,crewWeight].every(Number.isFinite)){
             readout.textContent='Enter valid values to preview the replacement rate.';
             timing.textContent='';
             return;
           }
           const speed=Math.max(0.01,1+crew/100*crewWeight);
           const rebuildTicks=Math.max(1,Math.ceil(base/speed));
           const perTurn=TICKS_PER_TURN/rebuildTicks;
           readout.textContent=`${format(perTurn)} squadron replacements per turn, across all slots`;
           timing.textContent=`One replacement takes about ${rebuildTicks} battle ticks (${format(rebuildTicks/TICKS_PER_SECOND)} sim sec, ${format(rebuildTicks/TICKS_PER_TURN)} turns) at ${format(speed)}× crew speed.`;
         };
         for(const input of [ticks,weight,bonus])input.addEventListener('input',update);
         update();
         squadronFields.unshift(el('div',{class:'rate-setting',style:'grid-column:1/-1'},readout,timing,
           el('label',{class:'f'},'Crew bonus for preview (%)',bonus),
           el('p',{class:'hint'},'All destroyed slots share this rate on the mothership. Squadrons return one at a time with one fighter each. Regular hangar repair restores the remaining fighters.')));
       }
     }
     if(abilities.size){
       bundle.installedAbilities.replaceChildren(el('h4',{},'Installed abilities'),rateNote,
         ...Array.from(abilities,([script,fields])=>el('details',{class:'ability-options',open:fields.length>0},
           el('summary',{},result.abilities[script]),
           fields.length?el('div',{class:'grid'},fields):el('p',{class:'hint'},'This module has no editable settings.'))));
     }else{
       bundle.installedAbilities.replaceChildren(el('p',{class:'hint'},'No supported ability modules in this bundle.'));
     }
     const subsystemPanels=Array.from(subsystemGroups.values()).filter(group=>group.controls.length)
       .map(({title,controls})=>el('details',{class:'ability-options'},el('summary',{},title),el('div',{class:'grid'},controls)));
     bundle.stats.replaceChildren(el('h4',{},'Bundle hull & armour'),
       el('p',{class:'hint'},result.prefab?result.prefab+' · '+file:file),
       el('div',{class:'grid'},primary),
       subsystemPanels.length?el('section',{},el('h4',{},'Ship subsystems'),
         el('p',{class:'hint'},'Edit the values stored in this ship’s prefab. Min and Max are the game’s configured endpoints.'),
         ...subsystemPanels):null,
       advanced.length?el('details',{class:'ability-options'},el('summary',{},'Other prefab ship fields'),
         el('div',{class:'grid'},advanced)):null,
       result.fields.length?null:el('p',{class:'hint'},'No editable prefab ship fields found.'));
     bundle.stats.dataset.source=file;
     bundle.stats.dataset.result=JSON.stringify(result);
     bundle.linkBox.disabled=!result.fields.some(field=>Object.values(HULL_ARMOUR_LINKS).includes(field.key));
     bundle.linkHint.textContent=bundle.linkBox.disabled?'This bundle has no matching hull/armour prefab fields.':linkedHullArmourLabel();
     if(bundle.linkBox.checked)for(const field of Object.keys(HULL_ARMOUR_LINKS))if(state.pending[key(state.cur,field)])syncBundleStatFromLevel(field,shipFieldValue(field));
     refreshHullArmourLinkUI(bundle);
   }catch(error){
     if(request===statsRequest)bundle.stats.textContent='Bundle stats unavailable. '+error.message;
   }
 }
 function selectBundle(file,deferLayout=false){
   path.value=file;
   openedBundles.set(ship,file);
   const groupsInput=bundle.slots.querySelector('[data-d="squadron_groups"]');
   if(groupsInput){groupsInput.value='';delete groupsInput.dataset.manual}
   get('bcurrent').hidden=true;
   compareData=null;compareRequest++;
   refreshStats(file,deferLayout);
   if(file)inspectBundle(get);
 }
 bundle.activatePath=selectBundle;
 const pick=el('button',{class:'sec',onclick:async()=>{
   const file=await pickPath({files:true,start:path.value||state.info.bundle_dir||state.info.home,title:'Open a ship bundle'});
   if(file){const imported=await api('/api/bundle/import',{bundle:file});selectBundle(imported.path)}
 }},'Load bundle…');
 path.addEventListener('change',async()=>{
   const file=path.value.trim();if(!file)return;
   try{const imported=await api('/api/bundle/import',{bundle:file});path.value=imported.path;selectBundle(imported.path)}
   catch(error){bundle.stats.replaceChildren(el('p',{class:'msg-bad'},error.message))}
 });
 const sel=el('select',{onchange:event=>{
   if(event.target.value)selectBundle(event.target.value);
 }},[el('option',{value:''},state.info.bundle_files.length?'Choose a configured bundle':'No bundle folder configured'),
    ...state.info.bundle_files.map(name=>el('option',{value:name},name))]);
 async function chooseShipFolder(){
   const folder=await pickPath({files:false,start:state.info.bundle_dir||state.info.home,title:'Choose the default ship-bundle folder'});
   if(!folder)return;
   try{
     state.info=await api('/api/config',{levels:state.info.levels,dll:state.info.dll,bundles:folder,work:state.info.work});
     bundleForm=undefined;
     sel.replaceChildren(el('option',{value:''},state.info.bundle_files.length?'Choose a bundle from this folder':'No bundles found in this folder'),...state.info.bundle_files.map(name=>el('option',{value:name},name)));
     for(const dest of ['defender_donor','celestra_donor','stealth']){
       const donor=sections.querySelector('[data-d="'+dest+'"]');
       if(donor){const previous=donor.value;donor.replaceChildren(el('option',{value:''},'Choose a bundle from this folder'),...state.info.bundle_files.map(name=>el('option',{value:name},name)));if(state.info.bundle_files.includes(previous))donor.value=previous}
     }
     const target=state.info.bundle_files.find(name=>normalize(name.replace(/\.dec$/i,''))===normalize(ship||''));
     if(target)selectBundle(target);
     else{path.value='';openedBundles.delete(ship);refreshStats('')}
   }catch(error){alert(error.message)}
 }
 const normalize=name=>name.toLowerCase().replace(/[^a-z0-9]/g,'');
 const remembered=openedBundles.get(ship);
 if(remembered!==undefined){path.value=remembered}
 else if(ship){const match=state.info.bundle_files.find(name=>normalize(name.replace(/\.dec$/i,''))===normalize(ship));if(match)path.value=match}
 const compare=el('div',{class:'card',hidden:true});bundle.compare=compare;
 async function compareBundle(){
   const current=JSON.parse(bundle.stats.dataset.result||'null');
   if(!current){alert('Load a bundle first.');return}
   const file=await pickPath({files:true,start:path.value||state.info.bundle_dir||state.info.home,title:'Choose a bundle to compare'});
   if(!file)return;
   const token=++compareRequest;compare.hidden=false;compare.replaceChildren(el('h3',{},'Bundle comparison'),el('p',{class:'hint'},'Reading '+file+'…'));
   try{
     const other=await api('/api/bundle/stats',{bundle:file});if(token!==compareRequest)return;
     const left=new Map(current.fields.map(f=>[f.key,f.value])),right=new Map(other.fields.map(f=>[f.key,f.value]));
     for(const input of bundle.stats.querySelectorAll('[data-prefab-field]')){
       const value=input.matches('fieldset')?Object.fromEntries([...input.querySelectorAll('input')].map(axis=>[axis.dataset.axis,axis.valueAsNumber])):input.type==='checkbox'?input.checked:input.valueAsNumber;
       left.set(input.dataset.prefabField,value);
     }
     for(const input of bundle.installedAbilities.querySelectorAll('[data-prefab-field]')){
       const value=input.matches('fieldset')?Object.fromEntries([...input.querySelectorAll('input')].map(axis=>[axis.dataset.axis,axis.valueAsNumber])):input.type==='checkbox'?input.checked:input.valueAsNumber;
       left.set(input.dataset.prefabField,value);
     }
     const keys=[...new Set([...left.keys(),...right.keys()])].sort();
     const norm=v=>v===undefined?'(not present)':typeof v==='object'?JSON.stringify(v):String(v);
     const rows=keys.map(key=>({key,a:norm(left.get(key)),b:norm(right.get(key))}));
     const la=new Set(Object.keys(current.abilities||{})),ra=new Set(Object.keys(other.abilities||{}));
     const abilityRows=[...new Set([...la,...ra])].sort().map(key=>({key:(current.abilities[key]||other.abilities[key]||key)+' ability',a:la.has(key)?'present':'(not present)',b:ra.has(key)?'present':'(not present)'}));
     const all=[...rows,...abilityRows],diffs=all.filter(row=>row.a!==row.b);
     const table=(items)=>el('div',{},...items.map(row=>el('div',{class:'row',style:'padding:5px 0;border-top:1px solid var(--line)'},el('strong',{style:'flex:1;min-width:180px'},row.key),el('span',{style:'flex:1'},row.a),el('span',{style:'flex:1'},row.b))));
     const rowNodes=items=>items.map(row=>el('div',{class:'row',style:'padding:5px 0;border-top:1px solid var(--line)'},el('strong',{style:'flex:1;min-width:180px'},row.key),el('span',{style:'flex:1'},row.a),el('span',{style:'flex:1'},row.b)));
     const listing=el('div');listing.replaceChildren(...rowNodes(all));
     const only=el('input',{type:'checkbox',onchange:()=>listing.replaceChildren(...rowNodes(only.checked?diffs:all))});
     compare.replaceChildren(el('h3',{},'Bundle comparison'),el('p',{class:'hint'},current.prefab+' (editing) compared with '+other.prefab+' · '+diffs.length+' differences'),el('p',{class:'hint'},'Field values show the bundle currently loaded in the editor and the chosen comparison file. Unsaved form edits are not included.'),el('div',{class:'row'},el('label',{class:'f chk'},only,'Show differences only'),el('button',{class:'sec',onclick:()=>{compareData=null;compare.hidden=true;compareRequest++}},'Close comparison')),el('div',{class:'row hint',style:'font-weight:600;padding:6px 0'},el('strong',{style:'flex:1'},'Field'),el('span',{style:'flex:1'},'Editing bundle'),el('span',{style:'flex:1'},'Comparison bundle')),listing);
   }catch(error){if(token===compareRequest)compare.replaceChildren(el('h3',{},'Bundle comparison'),el('p',{class:'msg-bad'},error.message));}
 }
 const groups={};bundleOptions.forEach(o=>(groups[o.group]=groups[o.group]||[]).push(o));
 source.replaceChildren(el('h3',{},'Bundle file'),el('div',{class:'row'},path,pick,el('button',{class:'sec',onclick:compareBundle},'Compare with bundle…'),el('button',{class:'sec',onclick:chooseShipFolder},'Choose ship folder…'),el('button',{class:'sec',onclick:async()=>{const r=await pickPath({files:false,start:state.info.bundle_dir||state.info.home,title:'Choose a folder for bundle conversion'});if(r){path.value=r;refreshStats('')}}},'Browse folders…')),el('div',{class:'row',style:'margin-top:8px'},sel,el('button',{class:'sec',id:'binspect',onclick:()=>inspectBundle(get)},'Inspect current bundle')),
  el('pre',{id:'bcurrent',hidden:true,'aria-live':'polite'}),compare,
  el('div',{class:'card'},el('h3',{},'Decrypt / encrypt'),el('label',{class:'f'},'Hex key override',el('input',{id:'xor-key',placeholder:'Automatic'})),el('div',{class:'row'},...[['decrypt','Decrypt (to '+state.info.work+'/.deadlock-tools/decrypted/)'],['encrypt','Encrypt (to '+state.info.work+'/BSG_Data/StreamingAssets/)'],['check','Check']].map(([m,l])=>el('button',{class:'sec',onclick:()=>runXor(get,m)},l)),el('span',{id:'xmsg',class:'hint'})),
   el('p',{class:'hint',style:'margin:6px 0 0'},'Choose a bundle file or browse for a folder to convert in bulk.'),el('pre',{id:'xout',hidden:true})));
 if(path.value){refreshStats(path.value);inspectBundle(get)}
 const optionsByGroup=Object.entries(groups);
 for(const [group,options] of optionsByGroup){
  const buildGrid=fields=>el('div',{class:'grid'},fields.map(o=>{let i;
  if(o.kind==='bool'){
    const attrs={type:'checkbox','data-d':o.dest};
    if(o.dest==='split_squadrons')attrs.onchange=event=>{
      if(event.target.checked){const groups=bundle.slots.querySelector('[data-d="squadron_groups"]');if(groups)groups.value=''}
    };
    if(o.dest==='turnaround_repair_bay')attrs.onchange=event=>{
      if(!event.target.checked||!state.data)return;
      const stored=name=>shipFieldValue(name);
      const fighters=Number(stored('FighterSlots')),support=Number(stored('UtilityFighterSlots'));
      if(!Number.isInteger(fighters)||!Number.isInteger(support)||fighters+support<1)return;
      const input=dest=>bundle.slots.querySelector('[data-d="'+dest+'"]');
      const fighterInput=input('fighter_slots'),supportInput=input('utility_slots'),groupsInput=input('squadron_groups');
      if(fighterInput&&supportInput&&groupsInput){
        fighterInput.value=String(fighters);supportInput.value=String(support);
      const splitInput=input('split_squadrons');
      if(!splitInput?.checked&&!groupsInput.dataset.manual)groupsInput.value=[fighters,support].filter(count=>count>0).join(',');
      }
    };
    i=el('input',attrs);return el('label',{class:'f chk',title:o.help},i,bundleFieldLabel(o))
  }
  if(o.kind==='choice'){i=el('select',{'data-d':o.dest},[el('option',{value:''},o.choices?'Choose a bundle':'(default)'),...(o.choices||[]).map(c=>el('option',{value:c},c))])}
  else if(o.kind==='list'){i=el('textarea',{'data-d':o.dest,rows:2,placeholder:'one per line, e.g. '+(o.metavar||'fore=1'),style:'padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'})}
  else i=el('input',{type:'text','data-d':o.dest,placeholder:o.default||o.metavar||''});
  if(o.dest==='squadron_groups')i.addEventListener('input',()=>{
    i.dataset.manual='1';
    if(i.value.trim()){
      const split=bundle.slots.querySelector('[data-d="split_squadrons"]');if(split)split.checked=false;
      const groups=i.value.split(',').map(value=>value.trim());
      if(groups.length===2&&groups.every(value=>/^\d+$/.test(value))){
        const [fighters,support]=groups.map(Number);
        if(fighters+support>0){
          syncingSlotValues=true;
          const fighterInput=bundle.slots.querySelector('[data-d="fighter_slots"]'),supportInput=bundle.slots.querySelector('[data-d="utility_slots"]');
          if(fighterInput)fighterInput.value=String(fighters);if(supportInput)supportInput.value=String(support);
          setShipFieldValue('FighterSlots',fighters);setShipFieldValue('UtilityFighterSlots',support);
          syncingSlotValues=false;
        }
      }
    }
  });
  return el('label',{class:'f',title:o.help},bundleFieldLabel(o),i,el('span',{class:'orig'},o.help))}));
  if(group==='Abilities'){
    const card=el('div',{class:'card'},el('h3',{},'Abilities'),bundle.installedAbilities,el('h4',{},'Add abilities'),el('p',{class:'hint'},'Multiple Actions-menu abilities are grouped under one Abilities submenu. Enabling the turnaround repair bay uses this ship’s database squadron counts and separates fighter and support launch buttons into submenus when needed to make room in the Fighters menu.'));
    const categories=new Map();
    for(const option of options){
      const name=option.ability||'Ability menu and donors';
      if(!categories.has(name))categories.set(name,[]);
      categories.get(name).push(option);
    }
    for(const [name,fields] of categories){
      card.append(el('details',{class:'ability-options'},el('summary',{},name),buildGrid(fields)));
    }
    sections.append(card);
    continue;
  }
  const grid=buildGrid(options);
  if(group==='Squadron / munition slots'){
    slots.replaceChildren(el('h4',{},'Bundle slot controls'),grid,bundle.slotStatus);
  }else if(group==='Custom ship modules'){
    sections.append(el('div',{class:'card'},el('h3',{},group),
      el('p',{class:'hint'},'Adding a custom module also switches off its Mercury legacy hook where applicable. The matching custom-module DLL must be installed before launching the game.'),grid));
  }else{
    sections.append(el('div',{class:'card'},el('h3',{},group==='Output'?'Bundle output':group),grid));
  }
 }
 const actionsAbilityFlags=new Set(['mini_jump','augury','stealth','targeted_resupply','deploy_plating','technical_support','marine_reinforcement','custom_instant_plating','custom_emp_pulse','custom_targeted_emp']);
 bundle.sections.addEventListener('change',event=>{
   if(!actionsAbilityFlags.has(event.target.dataset.d))return;
   const enabled=[...bundle.sections.querySelectorAll('input[type=checkbox][data-d]')].filter(input=>actionsAbilityFlags.has(input.dataset.d)&&input.checked).length;
   const submenu=bundle.sections.querySelector('[data-d="ability_submenu"]');
   if(enabled>1&&submenu)submenu.checked=true;
 });
 if(ship&&state.data?.name===ship){
   for(const [field,dest] of [['MunitionSlots','munition_slots'],['FighterSlots','fighter_slots'],['UtilityFighterSlots','utility_slots']]){
     const input=bundle.slots.querySelector('[data-d="'+dest+'"]');
     const value=shipFieldValue(field);
     if(input&&input.value===''&&Number.isFinite(Number(value)))input.value=String(value);
   }
 }
 actions.replaceChildren(el('div',{class:'row'},
   el('button',{class:'pri',id:'brun',onclick:()=>runBundle(bundle,get)},'Save bundle edits'),
   el('button',{class:'sec',id:'boverwrite',onclick:()=>runBundle(bundle,get,true)},'Overwrite opened bundle'),
   el('span',{id:'bmsg',class:'hint'},'Saves into the work tree under BSG_Data/StreamingAssets/')),
   el('pre',{id:'bout',hidden:true}));
}
function bundleFieldLabel(option){
 const label=option.flag.replace(/^--/,'').replaceAll('-',' ');
 return label.charAt(0).toUpperCase()+label.slice(1);
}
async function runXor(get,mode){const m=get('xmsg');m.className='hint';m.textContent='running...';
 try{if(!get('bpath').value.trim())throw new Error('Choose a bundle or folder first.');const r=await api('/api/xor',{bundle:get('bpath').value,mode,key:get('xor-key').value});get('xout').hidden=false;get('xout').textContent=r.cmd+'\n\n'+r.output;m.textContent=r.ok?'done':'failed';m.className=r.ok?'msg-ok':'msg-bad'}catch(e){m.textContent=e.message;m.className='msg-bad'}}
async function inspectBundle(get){
 const button=get('binspect'),out=get('bcurrent');
 button.disabled=true;out.hidden=false;out.textContent='Reading selected bundle…';
 try{
   const result=await api('/api/bundle/run',{bundle:get('bpath').value,options:{inspect:true}});
   out.textContent=result.output;
 }catch(e){out.textContent=e.message}
 finally{button.disabled=false}
}
async function bundlePatchOptions(options){
 if(options.list_mounts)return {};
 const required=[
 ].filter(patch=>options[patch.option]);
 const patches={};
 if(options.turnaround_repair_bay){
   const count=Number(options.turnaround_repair_actions||1);
   if(!Number.isInteger(count)||count<1||count>8)throw new Error('Turnaround repair bays must be between 1 and 8.');
   patches.repair_bay_actions=count;
 }
 if(!required.length)return patches;
 const check=await api('/api/dll/check');
 for(const patch of required){
   const status=check.output.split('\n').find(line=>line.startsWith(patch.name+' patch: '));
   if(status?.startsWith(patch.name+' patch: INSTALLED'))continue;
   if(!status?.startsWith(patch.name+' patch: not installed')){
     throw new Error('Could not verify the '+patch.name+' patch. Check the configured DLL on the DLL page.');
   }
   const value=window.prompt('This bundle edit needs the '+patch.name+' DLL patch.\n'+patch.label,patch.value);
   if(value===null)return null;
   const number=Number(value);
   if(!value.trim()||!Number.isFinite(number)||number<patch.min||(!patch.allowMin&&number===patch.min)){
     throw new Error('Enter a '+(patch.allowMin?'non-negative':'positive')+' number for '+patch.label.toLowerCase()+'.');
   }
   patches[patch.field]=number;
 }
 return patches;
}
async function runBundle(bundle,get,overwrite=false){
 const opts={};
 for(const area of [bundle.slots,bundle.sections]){
   area.querySelectorAll('[data-d]').forEach(input=>{
     opts[input.dataset.d]=input.type==='checkbox'?input.checked:input.value;
   });
 }
 const button=get(overwrite?'boverwrite':'brun'),message=get('bmsg'),output=get('bout');
 const source=get('bpath').value.trim();
 const prefabFields={};
 let saved=false;
 button.disabled=true;
 message.className='hint';
 output.hidden=true;
 try{
   if(!source)throw new Error('Choose a bundle file first.');
   if(overwrite&&!window.confirm('Replace this bundle file? A backup will be created beside it.\n\n'+source))return;
   if(bundle.stats.dataset.source===source){
     for(const area of [bundle.stats,bundle.installedAbilities]){
       for(const input of area.querySelectorAll('[data-prefab-field]')){
         const inputs=input.matches('fieldset')?[...input.querySelectorAll('input')]:[input];
         if(inputs.some(field=>!field.reportValidity())){
           throw new Error('Enter a valid value for the highlighted prefab field.');
         }
         const value=input.matches('fieldset')
           ?Object.fromEntries(inputs.map(field=>[field.dataset.axis,field.valueAsNumber]))
           :input.type==='checkbox'?input.checked:input.tagName==='SELECT'?Number(input.value):input.valueAsNumber;
         const original=JSON.parse(input.dataset.original);
         const unchanged=input.matches('fieldset')
           ?Object.keys(value).every(axis=>value[axis]===original[axis])
           :value===original;
         if(!unchanged)prefabFields[input.dataset.prefabField]=value;
       }
     }
   }
   message.textContent='Checking bundle requirements…';
   const patches=await bundlePatchOptions(opts);
   if(patches===null){
     message.textContent='Bundle edit cancelled.';
     return;
   }
   message.textContent='Saving bundle…';
   const result=await api('/api/bundle/run',{bundle:source,options:opts,prefab_fields:prefabFields,overwrite});
   output.textContent=result.cmd+'\n\n'+result.output;
   output.hidden=false;
   if(!result.ok)throw new Error('The bundle tool reported an error.');
   saved=Boolean(result.dst);
   if(saved)bundle.activatePath(result.dst);
   message.textContent='Updating related DLL and UI assets…';
   const update=await api('/api/editor/update',{bundle:result.dst,patches});
   output.textContent+='\n\n'+update.output;
   if(!update.ok)throw new Error('The combined editor update failed. See the output below.');
   message.textContent=update.updated
     ?'Saved bundle, rebuilt custom-module DLL'+(update.assets?.length?' and repair shortcut assets':'')+'. Work-tree DLL: '+update.dst
     :(overwrite?'Overwrote bundle. Backup: '+result.backup:result.dst?'Saved bundle to '+result.dst:'Done');
   message.className='msg-ok';
 }catch(error){
   message.textContent=(saved?'Bundle saved. ':'')+error.message;
   message.className='msg-bad';
 }finally{
   button.disabled=false;
 }
}
async function loadDll(){const box=$('#tab-dll');if(!state.info.ready){box.replaceChildren(el('div',{class:'card'},el('p',{},'Set the paths in the Settings tab first.')));return}const fld=(id,l,h)=>el('label',{class:'f'},l,el('input',{type:'number',id,step:'any'}),el('span',{class:'orig'},h));
 if(!box.dataset.ok){box.dataset.ok=1;box.replaceChildren(el('div',{class:'card'},el('h3',{},'Current DLL'),el('pre',{id:'dchk'},'checking...')),
  el('div',{class:'card'},el('h3',{},'Patch'),el('p',{class:'hint'},'Commander max rank and command-tree nodes are set on the Commanders tab. Custom-module and commander DLL code is rebuilt automatically when you save a bundle or the commander tree.'),el('div',{class:'grid'},fld('d_repair_actions','Repair action shortcuts','Unique, automatically indexed shortcuts (1–16)'),el('label',{class:'f chk',title:'Existing saves pick up a raised MunitionSlots count in battle'},el('input',{type:'checkbox',id:'d_mun'}),'Munition load fix'),el('label',{class:'f chk',title:'Patches continue from the edited work-tree DLL unless you choose otherwise'},el('input',{type:'checkbox',id:'d_fresh',onchange:loadDll}),'Start from the original DLL (drops earlier patches)')),
   el('div',{class:'row',style:'margin-top:10px'},el('button',{class:'pri',id:'dgo',onclick:patchDll},'Patch DLL'),el('span',{id:'dmsg',class:'hint'}))),
  el('div',{class:'card'},el('h3',{},'Revert'),el('div',{class:'grid'},...[['r_armour','Legacy armour regen (removed feature)'],['r_revive','Legacy squadron revive (removed feature)'],['r_mun','Munition load fix'],['r_repair_key','Indexed repair action shortcuts']].map(([id,l])=>el('label',{class:'f chk'},el('input',{type:'checkbox',id}),l))),
   el('div',{class:'row',style:'margin-top:10px'},el('button',{class:'sec',id:'rgo',onclick:revertDll},'Revert ticked (none ticked = all)'),el('span',{id:'rmsg',class:'hint'}))),el('pre',{id:'dout',hidden:true}))}
 try{const r=await api('/api/dll/check'+($('#d_fresh').checked?'?fresh=1':''));$('#dchk').textContent=r.output+'\n(source: '+r.source+')'}catch(e){$('#dchk').textContent=e.message}}
async function patchDll(){$('#dgo').disabled=true;try{const r=await api('/api/dll/patch',{repair_bay_actions:$('#d_repair_actions').value,munition_load:$('#d_mun').checked,fresh:$('#d_fresh').checked});$('#dout').hidden=false;$('#dout').textContent=r.output;$('#dmsg').textContent=r.ok?'written to '+r.dst+' and work-tree key mapping assets':'failed';$('#dmsg').className=r.ok?'msg-ok':'msg-bad';if(r.ok)$('#d_fresh').checked=false;loadDll()}catch(e){$('#dmsg').textContent=e.message;$('#dmsg').className='msg-bad'}$('#dgo').disabled=false}

const joinPath=(a,b)=>a.endsWith('/')?a+b:a+'/'+b;
function showTab(t){document.querySelector('header nav button[data-t='+t+']').click()}
$('#ship-tools').onclick=()=>{state.toolGroup='Ships';delete $('#tab-tools').dataset.ok;showTab('tools')};
$('#fresh').addEventListener('change',()=>{
 if(state.cur)openShip(state.cur);
 $('#refresh-current')?.click();
});
function pickPath({files=false,start='',title='Choose'}){return new Promise(res=>{
 let cur=start;const back=el('div',{style:'position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:50'});
 const dlg=el('div',{class:'card',style:'width:min(640px,94vw);max-height:80vh;display:flex;flex-direction:column;margin:0'});
 const pth=el('input',{type:'text',style:'flex:1;padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'});
 const list=el('div',{style:'overflow:auto;flex:1;min-height:220px;border:1px solid var(--line);border-radius:6px;margin:8px 0'});
 const done=v=>{back.remove();res(v)};
 async function go(p){try{const d=await api('/api/browse?path='+encodeURIComponent(p)+'&files='+(files?1:0));cur=d.path;pth.value=d.path;
   const rows=d.entries.map(e=>el('div',{class:'ship',onclick:()=>e.dir?go(joinPath(d.path,e.name)):done(joinPath(d.path,e.name))},(e.dir?'[folder] ':'')+e.name));
   list.replaceChildren(...(d.parent?[el('div',{class:'ship',onclick:()=>go(d.parent)},'.. (up one level)')]:[]),...rows,...(d.error?[el('p',{class:'msg-bad'},d.error)]:[]))}
  catch(e){list.replaceChildren(el('p',{class:'msg-bad'},e.message))}}
 pth.addEventListener('keydown',e=>{if(e.key==='Enter')go(pth.value)});
 dlg.append(el('h3',{},title),el('div',{class:'row'},pth,el('button',{class:'sec',onclick:()=>go(pth.value)},'Go')),list,
  el('div',{class:'row'},files?'':el('button',{class:'pri',onclick:()=>done(cur)},'Use this folder'),el('button',{class:'sec',onclick:()=>done(null)},'Cancel'),files?el('span',{class:'hint'},'Click a file to choose it.'):''));
 back.append(dlg);back.addEventListener('mousedown',e=>{if(e.target===back)done(null)});document.body.append(back);go(start||state.info.home)})}async function revertDll(){const m=$('#rmsg');$('#rgo').disabled=true;try{const r=await api('/api/dll/revert',{armour:$('#r_armour').checked,revive:$('#r_revive').checked,max_rank:$('#r_rank').checked,command_nodes:$('#r_nodes').checked,munition_load:$('#r_mun').checked,repair_bay_shortcuts:$('#r_repair_key').checked});$('#dout').hidden=false;$('#dout').textContent=r.output;m.textContent=r.ok?'written to '+r.dst:'failed';m.className=r.ok?'msg-ok':'msg-bad';loadDll()}catch(e){m.textContent=e.message;m.className='msg-bad'}$('#rgo').disabled=false}

let localisationEditor;
function loadLocalisation(ship=''){
 const box=$('#tab-localisation');
 if(!state.info.ready){
   box.replaceChildren(el('p',{class:'hint'},'Set the game data paths in Settings first.'));
   return;
 }
 if(localisationEditor){if(ship)localisationEditor.selectShip(ship);return}
 const labels={default:'General strings and ship names',blueprint_descriptions:'Blueprint descriptions'};
 const title=name=>labels[name]||name.replaceAll('_',' ').replace(/^./,char=>char.toUpperCase());
 const path=el('input',{type:'text',placeholder:'Path to resources.assets','aria-label':'Localisation source'});
 const status=el('p',{role:'status',class:'hint'});
 const table=el('select',{'aria-label':'Localisation table',onchange:()=>load(table.value)});
 const ships=el('select',{'aria-label':'Ship references',onchange:()=>{selectedShip=ships.value;drawReferences()}});
 const refs=el('div',{class:'row'});
 const query=el('input',{type:'search',placeholder:'Search by ID, text or note','aria-label':'Search strings',oninput:()=>drawRows()});
 const rows=el('div',{class:'localisation-results'});
 const form=el('form',{onsubmit:event=>{event.preventDefault();save()}});
 const nextPage=el('button',{class:'sec',onclick:()=>drawRows(limit+100)},'Show 100 more');
 const add=el('button',{class:'pri',onclick:()=>openEntry()},'Add string');
 const loadButton=el('button',{class:'sec',onclick:()=>load(table.value||'default',undefined,true)},'Load file');
 const browseButton=el('button',{class:'sec',onclick:async()=>{
   const file=await pickPath({files:true,start:path.value||state.info.levels,title:'Choose resources.assets'});
   if(file){path.value=file;load(table.value||'default',undefined,true)}
 }},'Browse…');
 const source=el('div',{class:'card'},el('h2',{},'Localisation'),
   el('div',{class:'row localisation-source'},path,browseButton,loadButton),status);
 const workspace=el('div',{class:'card',hidden:true},
   el('div',{class:'row'},table,ships,add),refs,
   el('div',{class:'localisation-editor'},el('div',{},query,rows,nextPage),form));
 box.replaceChildren(source,workspace);
 let model, shipRefs={}, selectedShip=ship, selectedId, draft, limit=100, request=0, busy=false;
 localisationEditor={selectShip(name){selectedShip=name;ships.value=name;drawReferences()}};

 function drawReferences(){
   refs.replaceChildren(...(shipRefs[selectedShip]||[]).map(ref=>
     el('button',{class:'sec',onclick:()=>load(ref.table,ref.id)},ref.label+' #'+ref.id)));
 }
 function message(text,kind='hint'){status.className=kind;status.textContent=text}
 async function load(name='default',id,fromPath=false){
   const token=++request;
   busy=true;workspace.hidden=true;message('Reading localisation…');
   try{
     const result=await api('/api/localisation',{table:name,source:fromPath?path.value.trim():model?.source||path.value.trim(),include_ships:true});
     if(token!==request)return;
     show(result,name,id);
     message((result.staged?'Working copy: ':'Source: ')+result.source);
   }catch(error){if(token===request)message(error.message,'msg-bad')}
   finally{if(token===request)busy=false}
 }
 function show(result,name,id){
   model=result;table.replaceChildren(...result.tables.map(key=>el('option',{value:key},title(key))));table.value=name;
   shipRefs=result.ships||shipRefs;
   ships.replaceChildren(el('option',{value:''},'Find strings for a ship'),...Object.keys(shipRefs).sort().map(name=>el('option',{value:name},name)));
   ships.value=selectedShip;drawReferences();
   path.value=result.source;
   workspace.hidden=false;query.value='';
   if(id!==undefined)openEntry(result.rows.find(row=>row.id===id),id);
   else{draft=null;selectedId=undefined;form.replaceChildren(el('p',{class:'hint'},'Select an existing string or add one.'));drawRows()}
 }
 function drawRows(maximum=100){
   limit=maximum;
   const term=query.value.trim().toLowerCase();
   const matches=(model?.rows||[]).filter(row=>!term||String(row.id).includes(term)||
     Object.values(row.translations).some(value=>(value.text+' '+value.note).toLowerCase().includes(term)));
   rows.replaceChildren(...matches.slice(0,limit).map(row=>{
     const value=row.translations.en||Object.values(row.translations)[0];
     return el('button',{type:'button',class:'localisation-row','aria-pressed':String(row.id===selectedId),onclick:()=>openEntry(row)},
       el('strong',{},'#'+row.id),el('span',{},value.text));
   }),...(matches.length?[]:[el('p',{class:'hint'},'No strings match.')]));
   nextPage.hidden=matches.length<=limit;
 }
 function openEntry(row,requested){
   selectedId=row?.id;
   const id=el('input',{type:'number',required:true,min:1,max:2147483647,step:1,value:row?.id??requested??model.next_id,readOnly:Boolean(row)});
   const note=el('input',{type:'text',placeholder:'Optional context for translators'});
   const translations=Object.fromEntries(Object.entries(model.languages).map(([lang,name])=>{
     const original=row?.translations[lang];
     const duplicate=row?.duplicates.includes(lang);
     const field=el('textarea',{rows:3,disabled:duplicate,required:!row&&lang==='en'});
     field.value=original?.text||'';
     return [lang,{field,original:original?.text||'',label:el('label',{class:'f'},name,field,
       duplicate?el('span',{class:'hint'},'This language has duplicate rows for this ID.'):original?.note?el('span',{class:'hint'},original.note):null)}];
   }));
   draft={id,note,translations,create:!row};
   form.replaceChildren(el('h3',{},row?'Edit string #'+row.id:'Add string'),
     el('label',{class:'f'},'String ID',id),
     row?null:el('label',{class:'f'},'Translator note',note),
     ...Object.values(translations).map(item=>item.label),
     el('p',{class:'hint'},row?'Use <br> for a line break.': 'Blank translations use the English text. Use <br> for a line break.'),
     el('button',{type:'submit',class:'pri'},'Save string'));
   drawRows(limit);
 }
 async function save(){
   if(!draft||busy)return;
   const {id,note,translations,create}=draft;
   const english=translations.en?.field.value;
   const values={};
   for(const [lang,{field,original}] of Object.entries(translations)){
     if(field.disabled)continue;
     if(create)values[lang]=field.value.trim()?field.value:english;
     else if(field.value!==original)values[lang]=field.value;
   }
   if(!Object.keys(values).length){message('No translation changes to save.');return}
   const name=table.value,token=++request;
   busy=true;
   const button=form.querySelector('button[type=submit]');
   button.disabled=true;message('Saving localisation…');
   try{
     const result=await api('/api/localisation',{table:name,source:model.source,
       edit:{revision:model.revision,id:id.valueAsNumber,create,translations:values,note:note.value}});
     if(token!==request)return;
     show(result,name,id.valueAsNumber);
     message('Saved string #'+id.value+' to '+result.source,'msg-ok');
   }catch(error){if(token===request)message(error.message,'msg-bad')}
   finally{if(token===request)busy=false;button.disabled=false}
 }
 load();
}

function loadArmaments(){
 const box=$('#tab-armaments');
 if(!state.info.ready){box.replaceChildren(el('p',{class:'hint'},'Set the game data paths in Settings first.'));return}
 if(box.dataset.ready)return;
 box.dataset.ready='1';
 const source=el('p',{class:'hint'});
 const status=el('p',{class:'hint',role:'status'});
 const list=el('div',{class:'localisation-results'});
 const search=el('input',{type:'search',placeholder:'Name or ID','aria-label':'Search armaments',oninput:drawList});
 const form=el('form',{onsubmit:event=>{event.preventDefault();save()}});
 const reload=el('button',{class:'sec',onclick:()=>load()},'Reload');
 const buttons=Object.fromEntries(['weapons','munitions'].map(key=>[key,el('button',{class:'sec',onclick:()=>{
   kind=key;search.value='';load();
 }},key==='weapons'?'Weapons':'Munitions')]));
 box.replaceChildren(el('div',{class:'card'},el('h2',{},'Armaments'),el('div',{class:'row'},...Object.values(buttons)),
   source,reload,status),
   el('div',{class:'card localisation-editor'},el('div',{},search,list),form));
 let kind='weapons', model, selected, request=0, busy=false, draft;
 async function load(){
   const token=++request;
   busy=true;model=null;selected=null;drawList();form.replaceChildren();
   for(const [key,button] of Object.entries(buttons))button.setAttribute('aria-pressed',String(kind===key));
   status.className='hint';status.textContent='Reading '+kind+'…';
   try{
     const result=await api('/api/armaments',{kind});
     if(token!==request)return;
     show(result);
     status.textContent='Changes save to '+joinPath(state.info.work,'BSG_Data')+'.';
   }catch(error){if(token===request){status.className='msg-bad';status.textContent=error.message}}
   finally{if(token===request)busy=false}
 }
 function show(result){
   model=result;source.textContent='Level data folder · '+result.source;
   selected=result.rows.find(row=>row.id===result.id)||result.rows[0];
   drawList();drawForm();
 }
 function drawList(){
   const term=search.value.trim().toLowerCase();
   list.replaceChildren(...(model?.rows||[]).filter(row=>row.name.toLowerCase().includes(term)||String(row.id).includes(term)).map(row=>
     el('button',{type:'button',class:'localisation-row','aria-pressed':String(selected?.id===row.id),onclick:()=>{
       if(busy)return;selected=row;drawList();drawForm();
     }},el('strong',{},'#'+row.id),el('span',{},row.name))));
 }
 function drawForm(clone=false){
   form.replaceChildren();draft=null;
   if(!selected)return;
   const row=selected;
   const name=el('input',{type:'text',required:true,value:clone?row.name+' copy':row.name,maxlength:256});
   const description=el('input',{type:'number',required:true,step:1,min:-1,max:2147483647,value:row.descriptionID});
   const label=(text,input)=>el('label',{class:'f'},text,input);
   const fields=Object.fromEntries(Object.entries(model.fields).map(([key,title])=>{
     const value=row.fields[key];
     const input=typeof value==='boolean'?el('input',{type:'checkbox',checked:value}):
       el('input',{type:'number',step:Number.isInteger(value)&&['fireMin','fireMax','DelayBetweenMissilesInTicks','StandardSalvoSize','MaxMissiles','PointValue'].includes(key)?'1':'any',required:true,value});
     return [key,{input,label:label(title,input)}];
   }));
   draft={name,description,fields,clone,id:row.id};
   const saveButton=el('button',{type:'submit',class:'pri'},clone?'Save clone':kind==='weapons'?'Save weapon':'Save munition');
   form.append(el('h3',{},clone?'Clone '+row.name:row.name+' #'+row.id));
   if(kind==='weapons'){
     form.append(label('Weapon name',name),label('Localisation string ID',description));
     if(clone)form.append(el('p',{class:'hint'},'The clone receives a new weapon ID and keeps the donor’s visual and audio assets.'));
   }
   form.append(el('div',{class:'grid'},Object.values(fields).map(entry=>entry.label)));
   if(row.details)form.append(el('div',{class:'grid'},Object.entries(row.details).map(([key,value])=>el('div',{class:'ro'},key+' '+value))));
   form.append(el('div',{class:'row'},saveButton,
     kind==='weapons'?el('button',{type:'button',class:'sec',onclick:()=>{if(!busy)drawForm(!clone)}},clone?'Cancel':'Clone weapon'):null));
 }
 async function save(){
   if(busy||!draft)return;
   const token=++request;
   const {name,description,fields,clone,id}=draft;
   const values=Object.fromEntries(Object.entries(fields).map(([key,{input}])=>[key,input.type==='checkbox'?input.checked:input.valueAsNumber]));
   const button=form.querySelector('button[type=submit]');
   busy=true;button.disabled=true;status.className='hint';status.textContent='Saving…';
   try{
     const result=await api('/api/armaments',{kind,edit:{id,revision:model.revision,clone,
       name:name.value,descriptionID:description.valueAsNumber,fields:values}});
     if(token!==request)return;
     show(result);status.className='msg-ok';
     status.textContent=result.output.length?(clone?'Cloned weapon #':'Saved #')+result.id+' to '+result.output.length+' working files.':'No changes to save.';
   }catch(error){if(token===request){status.className='msg-bad';status.textContent=error.message}}
   finally{if(token===request)busy=false;button.disabled=false}
 }
 load();
}

function loadSettings(){const box=$('#tab-settings'),I=state.info;
 const row=(id,label,hint,val,files)=>{const inp=el('input',{type:'text',id,value:val||'',style:'flex:1;min-width:260px;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'});
  return el('div',{class:'card'},el('h3',{},label),el('div',{class:'row'},inp,el('button',{class:'sec',onclick:async()=>{const r=await pickPath({files,start:inp.value||I.home,title:label});if(r)inp.value=r}},'Browse...')),el('p',{class:'hint',style:'margin:6px 0 0'},hint))};
 box.replaceChildren(
  row('s_levels','Default level-data folder','Choose the game’s BSG_Data folder. The DLL and ship folders can be elsewhere and are set separately.',I.levels,false),
  row('s_bundles','Default ship-bundle folder','Choose the game’s StreamingAssets folder or another folder of ship bundles. Same-named files in the work copy take priority.',I.bundles,false),
  row('s_dll','Default game DLL file','Choose Assembly-CSharp.dll wherever it is installed. Edited DLL copies go into the work tree under BSG_Data/Managed/.',I.dll,true),
  row('s_work','Work folder','Keeps your edits in a merge-ready tree: BSG_Data/ level files, StreamingAssets/ ship bundles, and Managed/Assembly-CSharp.dll. Decrypted scratch files stay in a hidden tool folder.',I.work,false),
  el('div',{class:'row'},el('button',{class:'pri',id:'s_save',onclick:saveSettings},'Save and load'),el('span',{id:'s_msg',class:I.error?'msg-bad':'hint'},I.error||'')))}
async function saveSettings(){const m=$('#s_msg');$('#s_save').disabled=true;m.className='hint';m.textContent='loading the level data (about 15 seconds)...';
 try{state.info=await api('/api/config',{levels:$('#s_levels').value,dll:$('#s_dll').value,bundles:$('#s_bundles').value,work:$('#s_work').value});bundleForm=undefined;localisationEditor=undefined;delete $('#tab-armaments').dataset.ready;
  shipRequest++;activeBundle=undefined;openedBundles.clear();state.pending={};state.cur=null;state.data=null;$('#view').replaceChildren(el('p',{class:'hint'},'Pick a ship on the left.'));
  for(const t of ['dll','tools'])delete $('#tab-'+t).dataset.ok;await loadShips();updPend();m.className='msg-ok';m.textContent='loaded '+state.info.tables+' level file(s) with the ship table.';showTab('ships')}
 catch(e){m.className='msg-bad';m.textContent=e.message}$('#s_save').disabled=false}
async function loadSquadrons(message){
 const box=$('#tab-squadrons');
 if(!state.info.ready){box.replaceChildren(el('div',{class:'card'},el('p',{},'Set the paths in the Settings tab first.')));return}
 box.replaceChildren(el('p',{class:'hint'},'Loading squadrons…'));
 let data;
 try{data=await api('/api/squadrons?fresh='+($('#fresh').checked?'1':'0'))}catch(e){box.replaceChildren(el('p',{class:'msg-bad'},e.message));return}
 const pending={};let current=data.squadrons[0]?.name;
 const list=el('div',{class:'sq-list',style:'display:flex;flex-direction:column;gap:4px;min-width:230px'});
 const detail=el('div',{style:'flex:1;min-width:0'});
 const status=el('span',{class:message?'msg-ok':'hint'},message||'');
 const scale=el('input',{type:'checkbox',checked:true});
 const save=el('button',{class:'pri',onclick:saveSquadrons},'Save squadron changes');
 const intro=el('div',{class:'card'},el('h2',{},'Fighter squadrons'),
  el('p',{class:'hint'},'Every squadron prefab in '+data.source+'. A squadron shares one hull pool; the game counts active fighters as 1 + the loss thresholds below the current hull (full hull = all fighters), so the order of the thresholds does not matter. Changing the fighter count also resizes the thresholds and formation slots (up to '+data.maxFighters+' fighters, the largest squadron the game ships with).'),
  el('p',{class:'hint'},'Per-fighter combat stats (weapons, veterancy) are in the ship database: use the link on each squadron to open its fighter on the Ships page. How many squadrons a carrier launches is its FighterSlots / UtilityFighterSlots there.'),
  el('div',{class:'row'},el('label',{class:'chk'},scale,' Scale loss thresholds when the hull pool changes'),save,status));
 function changes(name){return pending[name]||(pending[name]={})}
 function drawList(){
  list.replaceChildren(...data.squadrons.map(q=>el('button',{class:q.name===current?'pri':'sec',style:'text-align:left',onclick:()=>{current=q.name;drawList();drawDetail()}},
   q.name.replace(/Squadron$/,'')+(pending[q.name]&&Object.keys(pending[q.name]).length?' *':''),el('span',{class:'hint'},'  '+q.count+'× · hull '+q.hull))));
 }
 function input(q,key,kind,value){
  const c=changes(q.name);const cur=key in c?c[key]:value;
  if(kind==='bool'){const i=el('input',{type:'checkbox'});i.checked=!!cur;i.onchange=()=>{if(i.checked===!!value)delete c[key];else c[key]=i.checked;drawList()};return i}
  const i=el('input',{type:'number',step:kind==='int'?'1':'any',value:cur});
  i.onchange=()=>{const v=Number(i.value);if(i.value===''||v===Number(value))delete c[key];else c[key]=v;drawList()};return i;
 }
 function drawDetail(){
  const q=data.squadrons.find(x=>x.name===current);if(!q){detail.replaceChildren();return}
  const c=changes(q.name);
  const count=el('input',{type:'number',min:1,max:data.maxFighters,step:1,value:c.MaxShipCount??q.count});
  const losses=el('input',{type:'text',value:(c.ShipLoss??q.shipLoss).join(', '),style:'width:100%'});
  count.onchange=()=>{const n=Number(count.value);if(n===q.count)delete c.MaxShipCount;else c.MaxShipCount=n;if(!c.ShipLoss)losses.value=n===q.count?q.shipLoss.join(', '):'(recalculated on save from the current curve)';drawList()};
  losses.onchange=()=>{const v=losses.value.trim();if(!v||v.startsWith('('))delete c.ShipLoss;else c.ShipLoss=v.split(/[ ,]+/).filter(Boolean).map(Number);drawList()};
  const link=q.fighterShip?el('button',{class:'sec',onclick:async()=>{showTab('ships');await openShip(q.fighterShip)}},'Open fighter "'+q.fighterShip+'" on the Ships page'):el('span',{class:'hint'},'fighter ship type '+q.shipType);
  const groups={};for(const f of q.fields)(groups[f.component]=groups[f.component]||[]).push(f);
  const titles={ShipInfo:'Hull, handling and sensors (ShipInfo)',PropulsionInfo:'Speed (PropulsionInfo)',FighterSquadron:'Behaviour and ranges (FighterSquadron)',SquadronMovementAgent:'Launch, landing and collisions (SquadronMovementAgent)',WeaponsTarget:'Targeting (WeaponsTarget)'};
  detail.replaceChildren(
   el('div',{class:'card'},el('h3',{},q.name),el('div',{class:'row'},link,el('span',{class:'hint'},'Components: '+q.components.join(', '))),
    el('div',{class:'grid'},el('label',{class:'f'},'Fighters',count,el('span',{class:'orig'},'Game value '+q.count)),
     el('label',{class:'f',style:'grid-column:span 2'},'Loss thresholds ('+'count − 1 hull values)',losses,el('span',{class:'orig'},'Game value '+q.shipLoss.join(', '))))),
   ...['ShipInfo','PropulsionInfo','FighterSquadron','SquadronMovementAgent','WeaponsTarget'].filter(g=>groups[g]).map(g=>el('div',{class:'card'},el('h3',{},titles[g]),
    el('div',{class:'grid'},groups[g].map(f=>el('label',{class:f.kind==='bool'?'f chk':'f',title:f.help},f.label,input(q,f.key,f.kind,f.value),el('span',{class:'orig'},(f.help?f.help+' · ':'')+'game value '+f.value)))))));
 }
 async function saveSquadrons(){
  const edits={};for(const [n,c] of Object.entries(pending))if(Object.keys(c).length)edits[n]=c;
  if(!Object.keys(edits).length){status.textContent='No changes to save.';status.className='hint';return}
  save.disabled=true;status.className='hint';status.textContent='Saving…';
  try{const r=await api('/api/squadrons/save',{edits,scale:scale.checked,fresh:$('#fresh').checked});
   await loadSquadrons('Saved to '+r.dst+(r.backup?' (backup: '+r.backup+')':'')+'. '+r.log.join(' | '));}
  catch(e){status.className='msg-bad';status.textContent=e.message}
  finally{save.disabled=false}
 }
 drawList();drawDetail();
 const views=el('div',{class:'row'},el('button',{class:'pri'},'Squadron prefabs'),el('button',{class:'sec',onclick:()=>loadAllocations()},'Carrier allocations'));
 box.replaceChildren(views,intro,el('div',{class:'row',style:'align-items:flex-start;gap:16px'},list,detail));
}
async function loadAllocations(message){
 const box=$('#tab-squadrons');box.replaceChildren(el('p',{class:'hint'},'Loading squadron allocations…'));
 let data;try{data=await api('/api/allocations?fresh='+($('#fresh').checked?'1':'0'))}catch(e){box.replaceChildren(el('p',{class:'msg-bad'},e.message));return}
 const carrierIds=new Set(data.carriers.map(c=>c.id));
 const excl=Object.fromEntries(data.types.map(t=>[t.id,new Set(t.shipExclusionList)]));
 const fieldEdits={};
 const shipName=id=>(data.ships.find(s=>s.id===id)||{}).name||('ship '+id);
 let faction=String(data.carriers[0]?.faction??0);
 const status=el('span',{class:message?'msg-ok':'hint'},message||'');
 const save=el('button',{class:'pri',onclick:saveAllocations},'Save allocations');
 const pick=el('select',{onchange:()=>{faction=pick.value;draw()}},Object.entries(data.factions).filter(([k])=>data.carriers.some(c=>String(c.faction)===k)||data.types.some(t=>String(t.Faction)===k)).map(([k,v])=>el('option',{value:k},v)));
 pick.value=faction;
 const body=el('div',{});
 const views=el('div',{class:'row'},el('button',{class:'sec',onclick:()=>loadSquadrons()},'Squadron prefabs'),el('button',{class:'pri'},'Carrier allocations'));
 const intro=el('div',{class:'card'},el('h2',{},'Carrier squadron allocations'),
  el('p',{class:'hint'},'Which squadron types each capital ship may carry (the level-data SquadronDataTable, saved to '+data.files.join(', ')+'). The game offers a squadron type in a carrier slot when the carrier is not on its exclusion list, the faction matches, its DLC is owned and it is unlocked in the campaign. Fighter slots take any type; utility slots only take utility types (marked U). Types marked † are story-only (not in multiplayer or the codex).'),
  el('p',{class:'hint'},'How many slots a carrier has is FighterSlots / UtilityFighterSlots on the Ships page.'),
  el('div',{class:'row'},el('label',{},'Faction ',pick),save,status));
 function changed(t){return fieldEdits[t.id]||(fieldEdits[t.id]={})}
 function draw(){
  const types=data.types.filter(t=>String(t.Faction)===faction);
  const carriers=data.carriers.filter(c=>String(c.faction)===faction);
  const story=t=>!t.includedInMultiplayer&&!t.IncludedInCodex;
  const head=el('tr',{},el('th',{style:'text-align:left'},'Carrier (fighter / utility slots)'),...types.map(t=>el('th',{title:(t.fighterShip||'')+' · id '+t.id,style:'writing-mode:vertical-rl;transform:rotate(180deg);padding:4px;font-weight:normal'},t.name+(t.IsUtility?' U':'')+(story(t)?' †':''))));
  const rows=carriers.map(c=>el('tr',{},el('td',{},c.name+' ',el('span',{class:'hint'},c.fighterSlots+' / '+c.utilitySlots)),...types.map(t=>{
   const box=el('input',{type:'checkbox',title:t.name+' on '+c.name});box.checked=!excl[t.id].has(c.id);
   box.onchange=()=>{if(box.checked)excl[t.id].delete(c.id);else excl[t.id].add(c.id);status.textContent='Unsaved changes';status.className='hint'};
   return el('td',{style:'text-align:center'},box)})));
  const allCol=el('tr',{},el('td',{class:'hint'},'all carriers of this faction'),...types.map(t=>el('td',{style:'text-align:center'},
   el('button',{class:'sec',title:'Allow on every carrier',onclick:()=>{for(const c of carriers)excl[t.id].delete(c.id);draw()}},'✓'),
   el('button',{class:'sec',title:'Deny on every carrier',onclick:()=>{for(const c of carriers)excl[t.id].add(c.id);draw()}},'✗'))));
  const matrix=el('div',{class:'card',style:'overflow:auto'},el('h3',{},'Allowed squadron types per carrier'),el('table',{class:'alloc'},el('thead',{},head),el('tbody',{},...rows,allCol)));
  const fieldNames=Object.keys(data.fields);
  const fhead=el('tr',{},el('th',{style:'text-align:left'},'Squadron type'),...fieldNames.map(f=>el('th',{title:data.fields[f].help,style:'font-weight:normal;padding:2px 6px'},data.fields[f].label)));
  const frows=types.map(t=>el('tr',{},el('td',{title:'id '+t.id+(t.fighterShip?' · fighter: '+t.fighterShip:'')},t.name),...fieldNames.map(f=>{
   const kind=data.fields[f].kind,c=changed(t),cur=f in c?c[f]:t[f];let i;
   if(kind==='bool'){i=el('input',{type:'checkbox'});i.checked=!!cur;i.onchange=()=>{if(i.checked===!!t[f])delete c[f];else c[f]=i.checked;status.textContent='Unsaved changes'}}
   else if(f==='Faction'){i=el('select',{},...Object.entries(data.factions).map(([k,v])=>el('option',{value:k},v)));i.value=String(cur);i.onchange=()=>{const v=Number(i.value);if(v===t[f])delete c[f];else c[f]=v;status.textContent='Unsaved changes'}}
   else{i=el('input',{type:'number',step:kind==='int'?'1':'any',value:cur,style:'width:6em'});i.onchange=()=>{const v=Number(i.value);if(i.value===''||v===t[f])delete c[f];else c[f]=v;status.textContent='Unsaved changes'}}
   return el('td',{},i)})));
  const fields=el('div',{class:'card',style:'overflow:auto'},el('h3',{},'Squadron type settings'),el('table',{class:'alloc'},el('thead',{},fhead),el('tbody',{},...frows)),
   el('p',{class:'hint'},'Changing a type’s faction moves it to that faction’s grid after saving. “Fighters/Hull (level copy)” are kept in step automatically when you save in Squadron prefabs.'));
  const others=types.map(t=>[t,[...excl[t.id]].filter(id=>!carrierIds.has(id))]).filter(([,ids])=>ids.length);
  body.replaceChildren(matrix,fields,others.length?el('p',{class:'hint'},'Also excluded (ships that currently have no squadron slots): '+others.map(([t,ids])=>t.name+': '+ids.map(shipName).join(', ')).join(' · ')):'');
 }
 async function saveAllocations(){
  const edits={};
  for(const t of data.types){
   const now=[...excl[t.id]].sort((a,b)=>a-b),was=[...t.shipExclusionList].sort((a,b)=>a-b);
   const e=Object.assign({},fieldEdits[t.id]||{});
   if(now.join()!==was.join())e.shipExclusionList=now;
   if(Object.keys(e).length)edits[t.id]=e;
  }
  if(!Object.keys(edits).length){status.textContent='No changes to save.';status.className='hint';return}
  save.disabled=true;status.textContent='Saving…';status.className='hint';
  try{const r=await api('/api/allocations/save',{edits,fresh:$('#fresh').checked});await loadAllocations('Saved '+r.files.join(', ')+(r.backup?' (backup: '+r.backup+')':'')+'. '+r.log.join(' | '))}
  catch(e){status.textContent=e.message;status.className='msg-bad'}finally{save.disabled=false}
 }
 draw();
 box.replaceChildren(views,intro,body);
}
async function runCommanderTool(id,values){
 const r=await api('/api/tools/run',{id,values,fresh:$('#fresh').checked});
 if(!r.ok)throw new Error(r.output||'shipdb '+id+' failed');
 return r;
}
function specListControls(){
 const msg=el('span',{class:'hint'});
 const run=async(revert)=>{
  if(!confirm(revert?'Put the game’s original subsystem lists back in the work-tree level files? Unsaved tree edits on this page are discarded.':'Set each subsystem’s list to its own three skills in the work-tree level files? Unsaved tree edits on this page are discarded.'))return;
  msg.className='hint';msg.textContent='Writing…';
  try{await runCommanderTool('fixspecs',{revert});await loadCommanders()}catch(e){msg.className='msg-bad';msg.textContent=e.message}
 };
 return el('div',{class:'row',style:'margin:6px 0 10px'},
  el('button',{class:'sec',title:'shipdb fixspecs',onclick:()=>run(false)},'Reset lists: each subsystem’s own three skills'),
  el('button',{class:'sec',title:'shipdb fixspecs --revert-specs',onclick:()=>run(true)},'Restore the game’s original lists'),msg);
}
function commanderRankCard(data){
 const num=(value,attrs={})=>el('input',{type:'number',step:1,value,...attrs});
 const add=num(0,{min:0}),growth=el('input',{type:'number',step:'any',value:'1.2',min:1}),every=el('input',{type:'checkbox',checked:true});
 const maxRank=num(data.rankCount,{min:1}),nodes=num('',{min:1,placeholder:'unchanged'}),step=num(1,{min:1}),first=num(1,{min:1});
 const dllState=el('pre',{},'Reading the DLL…'),msg=el('span',{class:'hint'}),table=el('pre',{},'');
 let dllMax=null,dllNodes=null;
 add.addEventListener('input',()=>{const n=Number(add.value)||0;maxRank.value=String(data.rankCount+Math.max(0,n))});
 api('/api/dll/check'+($('#fresh').checked?'?fresh=1':'')).then(r=>{
  const lines=r.output.split('\n').filter(line=>/^command/.test(line));
  dllMax=Number((r.output.match(/commander max rank: (\d+)/)||[])[1])||null;
  dllNodes=Number((r.output.match(/command skill nodes: (\d+)/)||[])[1])||null;
  if(dllNodes){nodes.placeholder=String(dllNodes);const m=r.output.match(/one every (\d+) rank\(s\) from rank (\d+)/);if(m){step.value=m[1];first.value=m[2]}}
  const warn=dllMax!==null&&dllMax!==data.rankCount?'\n⚠ The level data has '+data.rankCount+' ranks but the DLL caps commanders at '+dllMax+'. Save with Max rank = '+data.rankCount+' to make them agree.':'';
  dllState.textContent='Level data: '+data.rankCount+' commander ranks\n'+lines.join('\n')+'\n(DLL: '+r.source+')'+warn;
 }).catch(e=>{dllState.textContent=e.message});
 const details=el('details',{class:'ability-options',ontoggle:async()=>{if(!details.open||table.textContent)return;table.textContent='Reading…';try{table.textContent=(await runCommanderTool('commanders',{})).output}catch(e){table.textContent=e.message}}},el('summary',{},'Rank, XP and leadership table (shipdb commanders)'),table);
 const save=el('button',{class:'pri',onclick:async()=>{
  save.disabled=true;msg.className='hint';
  try{
   const extra=Math.max(0,Number(add.value)||0),log=[];
   if(extra){msg.textContent='Adding '+extra+' ranks to the level data…';await runCommanderTool('setcommanders',{add:String(extra),xp_growth:growth.value,every_level:every.checked});log.push('added '+extra+' ranks')}
   const patch={};
   if(maxRank.value&&Number(maxRank.value)!==dllMax)patch.max_rank=maxRank.value;
   if(nodes.value)Object.assign(patch,{command_nodes:nodes.value,command_step:step.value,command_first_rank:first.value});
   if(Object.keys(patch).length){msg.textContent='Patching the DLL…';const r=await api('/api/dll/patch',patch);if(!r.ok)throw new Error(r.output);log.push('DLL patched')}
   if(!log.length){msg.textContent='Nothing to change.';return}
   await loadCommanders();
  }catch(e){msg.className='msg-bad';msg.textContent=e.message}finally{save.disabled=false}
 }},'Save ranks & command tree');
 const revert=el('button',{class:'sec',title:'dll revert --max-rank --command-nodes',onclick:async()=>{
  if(!confirm('Put the DLL back to the game’s 10-rank cap and 4 command nodes? Extra ranks in the level data stay but cannot be reached.'))return;
  try{const r=await api('/api/dll/revert',{max_rank:true,command_nodes:true});if(!r.ok)throw new Error(r.output);await loadCommanders()}catch(e){msg.className='msg-bad';msg.textContent=e.message}
 }},'Restore DLL defaults (rank 10, 4 nodes)');
 const field=(label,input,help)=>el('label',{class:'f',title:help||''},label,input,help?el('span',{class:'orig'},help):null);
 return el('div',{class:'card'},el('h3',{},'Ranks & command tree'),
  el('p',{class:'hint'},'Ranks live in the level data; the rank cap and Command-column node count are hard-coded in the DLL. Saving here does both so they stay in step.'),
  dllState,
  el('h4',{},'Level data'),el('div',{class:'grid'},field('Ranks to add',add,'0 keeps the current '+data.rankCount),field('XP growth per new rank',growth,'Each new rank costs this much more XP than the previous'),el('label',{class:'f chk'},every,'Fleet-size trait at every rank (leadership interval 1)')),
  el('h4',{},'DLL'),el('div',{class:'grid'},field('Max rank',maxRank,'Follows the level rank count'),field('Command-tree nodes',nodes,'Game default 4; 10 = one per rank 1–10'),field('Ranks between nodes',step),field('First node at rank',first)),
  el('div',{class:'row',style:'margin-top:10px'},save,revert,msg),details);
}
async function loadCommanders(){
 const box=$('#tab-commanders');box.replaceChildren(el('p',{class:'hint'},'Loading commander skill trees…'));
 try{
  const data=await api('/api/commanders?fresh='+($('#fresh').checked?'1':'0'));
  const abilityById=Object.fromEntries(data.abilities.map(a=>[a.id,a]));
  const allocations=Object.fromEntries(data.subsystems.map(s=>[s.id,[...s.choices]]));
  const originalAllocations=Object.fromEntries(data.subsystems.map(s=>[s.id,[...s.choices]]));
  const newAbilities=[];
  const editor=el('div',{}),status=el('p',{class:'hint'});
  const intro=el('div',{class:'card'},el('h2',{},'Commander skill trees'),
   el('p',{class:'hint'},'Choose which existing ability options appear under each subsystem. Add or remove choices freely, then edit each built-in effect’s bonus, display style, flagship applicability, and localisation IDs. Text itself is edited in the Localisation tab.'),
   el('p',{class:'hint'},'Create new commander abilities as flat or percentage ship-stat modifiers. Their custom DLL bridge uses the selected stat and bonus, and saves new ability records into the level-data work folder.'),
   el('div',{class:'row'},el('button',{class:'sec',onclick:()=>{for(const s of data.subsystems)allocations[s.id]=[...originalAllocations[s.id]];drawTrees()}},'Restore loaded tree mapping')));
  const preview=el('div',{class:'cmd-tree-scroll'}),trees=el('div',{class:'cmd-choice-scroll'}),abilityCards=el('div',{class:'cmd-ability-scroll'});
  const save=el('button',{class:'pri',onclick:saveCommanders},'Save commander tree');
  const customAbilityCard=buildCustomAbilityForm();
  editor.append(intro,commanderRankCard(data),customAbilityCard,el('div',{class:'card'},el('h3',{},'Commander tree preview'),preview,el('h3',{},'Edit subsystem choices ('+data.abilities.length+' known abilities)'),el('p',{class:'hint'},'The game data stores which abilities each subsystem can offer. Set each ability’s unlock rank in the ability list; assignments are saved with the work DLL.'),specListControls(),trees),el('div',{class:'card'},el('h3',{},'Ability effect values ('+data.abilities.length+')'),el('p',{class:'hint'},'All '+data.abilities.length+' ability records currently present in the configured level data are listed below, including their bonus values and unlock ranks.'),abilityCards),
   el('div',{class:'card'},save,status));
  function drawPreview(){
   const branches=el('div',{class:'cmd-tree-branches'});
   for(const s of data.subsystems){
    const branch=el('div',{class:'cmd-tree-branch'},el('span',{class:'cmd-tree-branch-title'},s.name));
    for(const id of allocations[s.id]){const a=abilityById[id];branch.append(el('div',{class:'cmd-tree-node'},a?'#'+id+' '+a.name:'Missing ability #'+id,a?el('span',{class:'cmd-tree-effect'},a.effect+' · '+a.bonus):null))}
    branches.append(branch);
   }
   preview.replaceChildren(el('div',{class:'cmd-tree-root'},'Commander abilities'),branches);
  }
  function drawTrees(){
   trees.replaceChildren(...data.subsystems.map(s=>{
    const list=allocations[s.id];const card=el('div',{class:'card'},el('h3',{},s.name));
    const rows=el('div',{});
    function redraw(){
     rows.replaceChildren(...list.map((id,index)=>{
      const select=el('select',{style:'flex:1;min-width:180px'});
      select.append(...data.abilities.map(a=>el('option',{value:a.id},'#'+a.id+' '+a.name+' — '+a.effect)));
      select.value=String(id);select.onchange=()=>{const next=Number(select.value);if(list.includes(next)){select.value=String(id);return}list[index]=next;drawPreview()};
      return el('div',{class:'row',style:'margin:5px 0'},select,el('button',{class:'sec',disabled:list.length<=1,onclick:()=>{list.splice(index,1);redraw();drawPreview()}},'Remove'))
     }));
    }
    redraw();
    const add=el('button',{class:'sec',onclick:()=>{const id=data.abilities.find(a=>!list.includes(a.id))?.id;if(id===undefined){alert('All supported abilities are already assigned to this subsystem.');return}list.push(id);redraw();drawPreview()}},'Add ability choice');
    card.append(rows,add);return card;
   }));drawPreview();
  }
  function buildCustomAbilityForm(){
   const name=el('input',{type:'text',maxlength:80,placeholder:'Ability name'});
   const target=el('select',{},data.abilities.filter(a=>!a.custom&&a.id>=3).map(a=>el('option',{value:a.id},a.effect+' — '+a.name)));
   const mode=el('select',{},el('option',{value:'flat'},'Flat amount'),el('option',{value:'percentage'},'Percentage'));
   const bonus=el('input',{type:'number',step:'any',value:0});
   const flagship=el('input',{type:'checkbox'});
   const desc=el('textarea',{rows:2,placeholder:'What this ability does'});
   const subsystem=el('select',{},data.subsystems.map(s=>el('option',{value:s.id},s.name)));
   const message=el('p',{class:'hint'});
   const create=el('button',{class:'sec',onclick:()=>{
    const targetId=Number(target.value),kind=mode.value,isFlagship=flagship.checked;
    const used=new Set(data.abilities.map(a=>a.id));
    let id;
    for(let slot=0;slot<10;slot++){const candidate=1000+targetId*100+(kind==='percentage'?20:0)+(isFlagship?10:0)+slot;if(!used.has(candidate)){id=candidate;break}}
    if(id===undefined){message.className='msg-bad';message.textContent='All 10 ability slots for this stat and modifier type are used.';return}
    if(!name.value.trim()){message.className='msg-bad';message.textContent='Enter an ability name.';return}
    const stat=data.abilities.find(a=>a.id===targetId);
    const ability={id,name:name.value.trim(),effect:stat.effect+' · '+kind,custom:true,target:targetId,mode:kind,unlockRank:1,
      titleText:name.value.trim(),descriptionText:desc.value.trim()||('Adds '+bonus.value+' '+kind+' bonus to '+stat.effect+'.'),
      titleId:0,descriptionId:0,displayType:kind==='percentage'?1:0,flagship:isFlagship,bonus:bonus.valueAsNumber};
    newAbilities.push({...ability});data.abilities.push(ability);abilityById[id]=ability;
    allocations[Number(subsystem.value)].push(id);drawTrees();drawAbilities();
    message.className='msg-ok';message.textContent='Added #'+id+' to '+subsystem.options[subsystem.selectedIndex].text+'. Save to write the new ability and build the work-tree DLL.';
    name.value='';bonus.value='0';desc.value='';
   }},'Add ability to selected subsystem');
   const field=(label,input)=>el('label',{class:'f'},label,input);
   return el('div',{class:'card'},el('h3',{},'Create a commander ability'),el('p',{class:'hint'},'Choose one of the game’s stat calculation paths. Flat bonuses add a value; percentage bonuses multiply the calculated value. A custom ability is assigned a unique effect ID and can then be placed in the tree.'),
    el('div',{class:'grid'},field('Ability name',name),field('Ship stat',target),field('Modifier',mode),field('Bonus amount',bonus),field('Subsystem branch',subsystem),field('Description',desc),el('label',{class:'f chk'},flagship,'Flagship only')),
    el('div',{class:'row'},create,message));
  }
  function drawAbilities(){
   abilityCards.replaceChildren(...data.abilities.map(a=>{
    const bonus=el('input',{type:'number',step:'any',value:a.bonus,'data-ability':a.id,'data-field':'bonus'});
    const unlock=el('select',{'data-ability':a.id,'data-field':'unlockRank'},[el('option',{value:''},'Use game default'),...Array.from({length:data.rankCount},(_,i)=>el('option',{value:i+1},'Rank '+(i+1)))]);unlock.value=a.unlockRank==null?'':String(a.unlockRank);
    const display=el('select',{'data-ability':a.id,'data-field':'displayType',disabled:a.custom},['Regular','Percentage','Per tick'].map((x,i)=>el('option',{value:i},x)));display.value=a.displayType;
    const flagship=el('input',{type:'checkbox',checked:a.flagship,'data-ability':a.id,'data-field':'flagship',disabled:a.custom});
    const title=el('input',{type:'number',min:0,step:1,value:a.titleId,'data-ability':a.id,'data-field':'titleId'});
    const desc=el('input',{type:'number',min:0,step:1,value:a.descriptionId,'data-ability':a.id,'data-field':'descriptionId'});
    return el('div',{class:'card'},el('h3',{},'#'+a.id+' '+a.name),el('p',{class:'hint'},a.effect),el('div',{class:'grid'},
     el('label',{class:'f'},'Bonus amount',bonus),el('label',{class:'f'},'Unlock rank',unlock),el('label',{class:'f'},'Display style',display),el('label',{class:'f chk'},flagship,'Applies to flagship'),
     el('label',{class:'f'},'Title localisation ID',title),el('label',{class:'f'},'Description localisation ID',desc)));
   }));
  }
  async function saveCommanders(){
   save.disabled=true;status.className='hint';status.textContent='Saving to the work folder…';
   try{
    const pendingIds=new Set(newAbilities.map(a=>a.id));
    const abilities=Object.fromEntries(data.abilities.filter(a=>!pendingIds.has(a.id)).map(a=>{
     const val=field=>editor.querySelector('[data-ability="'+a.id+'"][data-field="'+field+'"]').value;
     return [a.id,{bonus:Number(val('bonus')),displayType:Number(val('displayType')),flagship:editor.querySelector('[data-ability="'+a.id+'"][data-field="flagship"]').checked,titleId:Number(val('titleId')),descriptionId:Number(val('descriptionId'))}]
    }));
    const pendingAbilities=newAbilities.map(a=>{const value=field=>editor.querySelector('[data-ability="'+a.id+'"][data-field="'+field+'"]').value;
     return {...a,bonus:Number(value('bonus'))}});
    const assignments=Object.fromEntries(Object.entries(allocations).map(([k,v])=>[k,[...v]]));
    const unlockRanks=Object.fromEntries(data.abilities.map(a=>[a.id,editor.querySelector('[data-ability="'+a.id+'"][data-field="unlockRank"]').value||null]));
    const result=await api('/api/commanders/save',{assignments,abilities,newAbilities:pendingAbilities,unlockRanks,fresh:$('#fresh').checked});
    status.className='msg-ok';status.textContent='Saved commander tables and ability unlock ranks to the work folder: '+result.files.join(', ')+(result.dll?' and built '+result.dll:'')+(result.backup?' · backup: '+result.backup:'');
    await loadCommanders();
   }catch(error){status.className='msg-bad';status.textContent=error.message;save.disabled=false}
  }
  drawTrees();drawAbilities();box.replaceChildren(editor);
 }catch(error){box.replaceChildren(el('div',{class:'card'},el('h2',{},'Commander skill trees'),el('p',{class:'msg-bad'},error.message),el('button',{class:'sec',onclick:loadCommanders},'Retry')))}
}

async function loadTools(){const box=$('#tab-tools');if(box.dataset.ok)return;
 if(!state.info.ready){box.replaceChildren(el('div',{class:'card'},el('p',{},'Set the paths in the Settings tab first.')));return}
 box.dataset.ok=1;const tools=(await api('/api/tools')).filter(t=>t.group!=='Commanders');const groups={};tools.forEach(t=>(groups[t.group]=groups[t.group]||[]).push(t));
 const side=el('div',{class:'row'}),form=el('div',{style:'min-width:280px'});
 const operations=el('div',{class:'row',style:'margin:12px 0'});
 const byId=Object.fromEntries(tools.map(t=>[t.id,t]));
 const dl=el('datalist',{id:'shipnames'},state.ships.map(s=>el('option',{value:s.name})));
 function show(t){
  operations.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.id===t.id)));
  const initialShip=t.group==='Ships'?state.cur:'';

  const vals={};const inputs=t.fields.map(f=>{let i;
   if(f.kind==='bool'){i=el('input',{type:'checkbox'});i.checked=!!f.default;vals[f.name]=()=>i.checked;return el('label',{class:'f chk',title:f.help},i,f.label)}
   if(f.kind==='lines'){i=el('textarea',{rows:'3',style:'padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:13px monospace'});i.value=f.default}
   else if(f.kind.startsWith('choice:')){i=el('select',{},f.kind.slice(7).split(',').map(o=>el('option',{value:o},o)));i.value=f.default}
   else{i=el('input',{type:f.kind==='int'||f.kind==='float'?'number':'text',step:f.kind==='int'?'1':'any',value:f.default||(['ship','dst'].includes(f.name)?initialShip:''),list:f.kind==='ship'?'shipnames':null})}
   vals[f.name]=()=>i.value;
   const row=el('label',{class:'f',title:f.help,'data-current-selector':['assets','factory','dst','name'].includes(f.name)?'':null},f.label,f.kind==='file'?el('div',{class:'row'},i,el('button',{class:'sec',type:'button',onclick:async()=>{const r=await pickPath({files:true,start:i.value||state.info.levels,title:f.label});if(r){i.value=r;i.dispatchEvent(new Event('change',{bubbles:true}))}}},'Browse...')):i);return row});
  const values=()=>Object.fromEntries(Object.entries(vals).map(([key,get])=>[key,get()]));
  const current=el('pre',{'aria-live':'polite'});
  let inspection=0;
  async function refreshCurrent(fresh=$('#fresh').checked){
    const request=++inspection;
    const v=values();
    const args={assets:v.assets, factory:v.factory, name:v.name, ship:t.id==='copyvet'?v.dst:undefined};
    current.textContent=fresh?'Reading source files…':'Reading staged copies and source files…';
    try{
      const r=await api('/api/tools/run',{id:t.current,values:args,fresh});
      if(request===inspection)current.textContent=(fresh?'Source values\n':'Current values (staged copies where available)\n')+(r.output||'No values found.');
    }catch(e){if(request===inspection)current.textContent=e.message}
  }
  const refresh=el('button',{class:'sec',id:'refresh-current',onclick:()=>refreshCurrent()},'Refresh current values');
  const out=el('pre',{hidden:true}),msg=el('span',{class:'hint'});
  const run=el('button',{class:'pri',onclick:async()=>{
    run.disabled=true;msg.className='hint';msg.textContent='Running…';out.hidden=true;
    try{
      const r=await api('/api/tools/run',{id:t.id,values:values(),fresh:$('#fresh').checked});
      out.textContent=r.output;out.hidden=false;
      msg.className=r.ok?'msg-ok':'msg-bad';msg.textContent=r.ok?'Done':'The tool reported an error';
      if(r.ok&&t.writes){
        if(t.current)await refreshCurrent(false);
        await loadShips();
        if(state.cur)await openShip(state.cur);
      }
    }catch(e){msg.className='msg-bad';msg.textContent=e.message}
    finally{run.disabled=false}
  }},t.writes?'Run and save':'Read values');
  const grid=el('div',{class:'grid'},inputs);
  form.replaceChildren(el('div',{class:'card'},el('h3',{},t.title),el('p',{class:'hint'},t.help),
    ...(t.current?[el('div',{class:'row'},el('strong',{},'Current values'),refresh),current]:[]),
    grid,el('div',{class:'row',style:'margin-top:10px'},run,msg)),out,dl);
  if(t.current){
    grid.addEventListener('change',e=>{
      if(e.target.closest('[data-current-selector]'))refreshCurrent();
    });
    refreshCurrent();
  }
 }
 function showGroup(group){
   state.toolGroup=group;
   side.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.group===group)));
   operations.replaceChildren(...groups[group].map(t=>el('button',{class:'sec','data-id':t.id,onclick:()=>show(byId[t.id])},t.title)));
   show(groups[group][0]);
 }
 for(const group of Object.keys(groups)){
   side.append(el('button',{class:'sec','data-group':group,onclick:()=>showGroup(group)},group));
 }
 box.replaceChildren(side,operations,form);
 showGroup(groups[state.toolGroup]?state.toolGroup:tools[0].group);
}
(async()=>{try{state.info=await api('/api/config');if(state.info.ready)await loadShips();else showTab('settings')}catch(e){$('#view').replaceChildren(el('p',{class:'msg-bad'},e.message))}})();
</script></body></html>
"""

if __name__ == "__main__":
    main()
