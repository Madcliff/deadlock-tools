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
from deadlock_tools import ship_types as stypes
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


def bundle_stats_with_modifiers(path):
    """bundle_ship_stats plus the ship's subsystem crew modifiers and the stat/source catalog for the editor."""
    from deadlock_tools import subsystem_modifiers as sm
    result = wmod.bundle_ship_stats(path)
    try:
        entries, error = sm.read(path)["entries"], None
    except ValueError as exc:
        entries, error = [], str(exc)
    result["subsystem_modifiers"] = {
        "entries": entries, "error": error,
        "stats": [{"code": code, "name": info["name"], "mode": info["mode"], "when": info.get("when"),
                   "stock_source": sm.stock_source(code)} for code, info in sm.STATS.items()],
        "sources": [{"code": code, "label": labels[1]} for code, labels in sm.SOURCES.items()],
    }
    from deadlock_tools import module_subsystems as msub
    try:
        drivers, error = msub.read(path)["entries"], None
    except ValueError as exc:
        drivers, error = [], str(exc)
    result["module_subsystems"] = {
        "entries": drivers, "error": error,
        "modules": [{"code": code, "name": info["name"], "script": info["script"], "stock": info["stock"],
                     "installed": info["script"] in result.get("abilities", {})} for code, info in msub.MODULES.items()],
        "sources": [{"code": code, "label": labels[1]} for code, labels in msub.SOURCES.items()],
    }
    return result

class Project:
    def __init__(self, levels, dll, work, bundles, backups=None):
        ab = lambda x: os.path.abspath(x) if x else x                  # the helper tools run from the script's folder, so no relative paths
        self.levels, self.dll, self.work, self.bundles = ab(levels), ab(dll), ab(work), ab(bundles)
        self.backups = config.backups_root(self.work, backups)          # outside the work folder (config.backups_root)
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
                from deadlock_tools import mechanics
                magazine = mechanics.magazine_output(bundle_stage, self.work_paths["bundles"], self.bundles)
                if magazine:
                    value, target = magazine
                    preview_stage = os.path.join(staged, os.path.basename(target))
                    with open(preview_stage, "wb") as stream:
                        stream.write(value)
                    outputs.append((preview_stage, target))
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

                backup_root = os.path.join(self.backups, "ship-clone-" + stamp)
                backups, promoted = {}, []
                try:
                    for _, target in outputs:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        if os.path.isfile(target):
                            backup = self.backup_target(backup_root, target)
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
        if has_custom and b"GrantTurnTylium" not in source_bytes:
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
            backup_root = os.path.join(self.backups, "commander-editor-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
            backups = {}
            promoted = []
            try:
                for _, target in outputs:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.isfile(target):
                        backup = self.backup_target(backup_root, target)
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
        from deadlock_tools import squadron_types as sqt
        abilities, catalogue, prefabs, error, stealth, settings, setting_specs = {}, {}, [], None, {}, {}, {}
        try:                                                     # ability editing needs the optional UnityPy support
            P = self._prefabs(data)
            catalogue = {key: {"label": sqt.ABILITIES[key][0], "donors": donors, "builtin": key in sqt.BUILT_IN}
                         for key, donors in P.catalogue().items()}
            stealth = {name: P.stealth_settings(name) for name in P.roots}
            settings = {name: P.ability_settings(name) for name in P.roots}
            setting_specs = {key: [{"field": f, "kind": k, "label": l, "min": lo, "max": hi} for f, k, l, lo, hi in fields]
                             for key, (_component, fields) in sqt.ABILITY_SETTINGS.items()}
            prefabs = sorted(P.roots)
            for name in P.roots:
                try:
                    abilities[name] = [{k: item[k] for k in ("key", "label", "menu", "slot")} for item in P.abilities(name)]
                except ValueError:
                    continue
        except Exception as e:
            error = str(e)
        from deadlock_tools import squadron_table as st
        levels = {os.path.basename(o): d for o, _src, d in self._squadron_table_files(fresh)}
        types = {e["id"]: e for e in st.find(self.S, next(iter(levels.values())))[3]["objects"]}
        try:
            munitions = [{"id": i, "name": e["name"], "faction": e["Faction"], "dlc": e["DLCRequired"]}
                         for i, e in sorted(sqt.munition_types(self.S, levels).items())]
        except ValueError:
            munitions = []
        from deadlock_tools import squadron_research as sr
        research = next((r for r in (sr.read(self.S, d) for d in levels.values()) if r), {})
        out = []
        for squadron in sqmod.load(self.S, data):
            d = sqmod.describe(squadron)
            d["fighterShip"] = names.get(d["shipType"])           # SquadronShipType is a separate squadron enum, not a ship ID
            d["abilities"] = abilities.get(d["name"])
            d["stealth"] = stealth.get(d["name"])
            d["abilitySettings"] = settings.get(d["name"], {})
            d["research"] = {str(c): e for c, items in research.items() for e in items if e["id"] == d["squadronShipType"]}
            entry = types.get(d["squadronShipType"])
            d["tableName"] = entry["name"] if entry else None
            d["faction"] = entry["Faction"] if entry else None
            d["munitions"] = list(entry["MunitionSlots"]) if entry else None
            out.append(d)
        return {"source": source, "maxFighters": sqmod.MAX_FIGHTERS, "squadrons": out, "munitionTypes": munitions,
                "campaigns": {str(c): n for c, n in sr.CAMPAIGNS.items() if c in research}, "abilitySettingSpecs": setting_specs,
                "maxMunitions": sqt.MAX_MUNITIONS, "abilityCatalogue": catalogue, "abilityError": error, "prefabs": prefabs}

    def _work_dll(self, target=False):
        """the work-tree DLL (target=True: its path even when it does not exist yet), else the configured one"""
        work = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
        return work if target or os.path.isfile(work) else self.dll

    def _fighter_bundle(self, name):
        """a fighter bundle's bytes: the work-tree copy first, then the configured bundle folder"""
        for folder in (self.work_paths["bundles"], self.bundles):
            path = os.path.join(folder or "", name)
            if folder and os.path.isfile(path):
                return open(path, "rb").read()
        raise ValueError("fighter bundle %s not found in the work tree or the configured bundle folder" % name)

    def _dll_with_ship_types(self, wanted):
        """bytes of the work DLL plus ShipType enum members {id: name}"""
        import shutil, tempfile
        managed = self.managed_dir()
        if not managed:
            raise ValueError("Could not find the game's Managed folder needed to register a fighter copy.")
        staged = tempfile.mkdtemp(prefix="fighter-enum-", dir=self.stage_root if os.path.isdir(self.stage_root) else None)
        try:
            candidate = os.path.join(staged, "Assembly-CSharp.dll")
            result = stypes.add(managed, self._work_dll(), candidate, wanted, staged)
            if result.returncode or not os.path.isfile(candidate):
                raise RuntimeError("could not register the fighter copy in the DLL; nothing was saved:\n" +
                                   (result.stdout + result.stderr)[-8000:])
            return open(candidate, "rb").read()
        finally:
            shutil.rmtree(staged, ignore_errors=True)

    def _bundle_path(self, name):
        """a ship bundle's path: the work-tree copy first, then the configured bundle folder"""
        for folder in (self.work_paths["bundles"], self.bundles):
            if folder and os.path.isfile(os.path.join(folder, name)):
                return os.path.join(folder, name)
        return None

    def _launch_bay_outputs(self, levels, type_id, like_id):
        """[(bundle bytes, work target)] adding squadron type `type_id` to its carriers' bays that take `like_id`"""
        import tempfile
        from deadlock_tools import bays, squadron_types as sqt
        jobs, log = sqt.plan_launch_bays(self.S, levels, type_id, like_id, self._bundle_path)
        outputs = []
        for asset, src, spec in jobs:
            fd, temporary = tempfile.mkstemp(prefix=".bays-", dir=self.stage_root if os.path.isdir(self.stage_root) else None)
            os.close(fd)
            try:
                bays.apply(src, temporary, spec, encrypt=not src.endswith(".dec"))
                outputs.append((open(temporary, "rb").read(), os.path.join(self.work_paths["bundles"], asset)))
            finally:
                os.unlink(temporary)
        return outputs, log

    def create_munition(self, body):
        """copy a munition as a new munition type: missile prefab, munition and image tables, MissileFactory, optional
        text and research, and the munition enum member in the work DLL, promoted together"""
        import shutil, tempfile
        from deadlock_tools import munition_types as mt, squadron_types as sqt
        donor = body.get("donor")
        name = str(body.get("name", "")).strip()
        texts = [str(body.get(k, "")).strip() or None for k in ("displayName", "heading", "description")]
        if donor in (None, "") or not name:
            raise ValueError("Choose a munition to copy and give the new one a name.")
        _original, source = self._squadron_source(False)
        with self.lock:
            P = self._prefabs(open(source, "rb").read())
            levels = {os.path.basename(o): d for o, _src, d in self._squadron_table_files(False)}
            work_dll = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
            enum_source = work_dll if os.path.isfile(work_dll) else self.dll
            enum_ids = stypes.members(enum_source, munition=True)
            munition_id = mt.next_munition_id(self.S, levels, enum_ids)
            outputs, text_ids = [], {}
            if any(texts):
                levels_root = self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)
                loc_target = os.path.join(self.game_data, "resources.assets")
                loc_source = next((p for p in (loc_target, os.path.join(levels_root, "resources.assets"),
                                               os.path.join(os.path.dirname(os.path.dirname(self.dll)), "resources.assets"))
                                   if os.path.isfile(p)), None)
                if not loc_source:
                    raise ValueError("Could not find resources.assets under the configured game data paths.")
                resources, text_ids = sqt.add_texts(open(loc_source, "rb").read(), *texts)
                outputs.append((resources, loc_target))
            changed, log = mt.create_munition(self.S, P, levels, int(donor) if str(donor).lstrip("-").isdigit() else donor,
                                              name, munition_id, text_ids, bool(body.get("copyResearch", True)))
            outputs.append((P.save(), os.path.join(self.work_paths["levels"], mt.ASSET_FILE)))
            outputs += [(new, os.path.join(self.work_paths["levels"], file_name)) for file_name, new in changed.items()]
            managed = self.managed_dir()
            if not managed:
                raise ValueError("Could not find the game's Managed folder needed to register the new munition.")
            staged = tempfile.mkdtemp(prefix="munition-type-", dir=self.stage_root if os.path.isdir(self.stage_root) else None)
            try:
                candidate = os.path.join(staged, "Assembly-CSharp.munition-enum.dll")
                member = stypes.enum_name(name)
                result = stypes.add(managed, enum_source, candidate, {munition_id: member}, staged, munition=True)
                if result.returncode or not os.path.isfile(candidate):
                    raise RuntimeError("could not register the new munition in the DLL; no files were saved:\n" +
                                       (result.stdout + result.stderr)[-8000:])
                outputs.append((open(candidate, "rb").read(), work_dll))
                log.append("%s: munition enum %s = %d" % (os.path.basename(work_dll), member, munition_id))
            finally:
                shutil.rmtree(staged, ignore_errors=True)
            backup = self._promote(outputs, "munition-type")
            self._view = None
        return {"ok": True, "id": munition_id, "name": name, "files": [os.path.basename(t) for _d, t in outputs],
                "backup": backup, "log": log}

    def munition_combat(self, body):
        """a munition's combat stats (missile prefab) and the field list"""
        from deadlock_tools import munition_types as mt
        _o, source = self._squadron_source(False)
        P = self._prefabs(open(source, "rb").read())
        levels = {os.path.basename(o): d for o, _src, d in self._squadron_table_files(False)}
        stats = mt.combat_stats(self.S, P, levels, int(body["id"]))
        return {"stats": stats, "fields": [{"key": k, "kind": v[2], "label": v[3], "min": v[4], "max": v[5]}
                                           for k, v in mt.COMBAT_FIELDS.items() if k in stats]}

    def save_munition_combat(self, body):
        from deadlock_tools import munition_types as mt
        with self.lock:
            _o, source = self._squadron_source(False)
            P = self._prefabs(open(source, "rb").read())
            levels = {os.path.basename(o): d for o, _src, d in self._squadron_table_files(False)}
            changed, log = mt.set_combat_stats(self.S, P, levels, int(body["id"]), body.get("values") or {})
            if not log:
                return {"ok": True, "log": ["no change"], "backup": None}
            outputs = [(P.save(), os.path.join(self.work_paths["levels"], mt.ASSET_FILE))]
            outputs += [(data, os.path.join(self.work_paths["levels"], name)) for name, data in changed.items()]
            backup = self._promote(outputs, "munition-combat")
        return {"ok": True, "log": log, "backup": backup}

    def ship_jamming(self, bundle_path):
        """this ship's per-jammer penalties ({key: value}) or None when the bundle's ship type cannot be read"""
        from deadlock_tools import jamming as jm
        try:
            ship = wmod.bundle_ship_type(bundle_path)
        except Exception:
            return None
        return {"ship": ship, "values": jm.read_overrides(self._jamming_file()).get(("ship", ship), {})}

    def _jamming_file(self):
        from deadlock_tools import jamming as jm
        for folder in (self.work_paths["bundles"], self.bundles):
            if folder and os.path.isfile(os.path.join(folder, jm.FILE)):
                return os.path.join(folder, jm.FILE)
        return None

    def mechanics_settings(self, body=None):
        from deadlock_tools import mechanics
        target = os.path.join(self.work_paths["bundles"], mechanics.FILE)
        with self.lock:
            source = target
            if not os.path.isfile(source) and self.bundles:
                source = os.path.join(self.bundles, mechanics.FILE)
            settings = mechanics.read(source)
            if body is not None:
                for key in mechanics.DEFAULTS:
                    if key in body:
                        if type(body[key]) is not bool:
                            raise ValueError(key + " must be true or false")
                        settings[key] = body[key]
                mechanics.write(target, settings)
        return settings

    def _skirmish_file(self):
        from deadlock_tools import skirmish
        return os.path.join(self.work_paths["bundles"], skirmish.FILE)

    def skirmish_settings(self, body=None):
        """DLL page: the skirmish options of the custom-module DLL (deadlock-skirmish.txt) and the level-data limit"""
        from deadlock_tools import skirmish
        path = self._skirmish_file()
        game = os.path.join(self.bundles, skirmish.FILE) if self.bundles else None
        settings = skirmish.read(path if os.path.isfile(path) else game)
        level = campaign = None
        try:
            S = sd.Schema(self.dll_src())
            for name in ("level5", "level85"):
                source = self.src_for(os.path.join(self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels), name))
                if os.path.isfile(source):
                    campaign = sd.load_wardata(S, open(source, "rb").read())[3]["FleetInfo"]["MaxShips"]; break
            for name in ("level6", "level9"):
                source = self.src_for(os.path.join(self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels), name))
                if os.path.isfile(source):
                    level = sd.load_skirmish(S, open(source, "rb").read())[3]["maxShips"]; break
        except Exception:
            level = None
        return {"ships": settings["ships"], "fleet_caps": list(settings["fleet_caps"]) if settings["fleet_caps"] is not None else None,
                "campaign_ships": settings["campaign_ships"], "level_campaign_ships": campaign,
                "default_caps": list(skirmish.DEFAULT_CAPS), "level_ships": level, "file": path}

    def save_skirmish_settings(self, body):
        from deadlock_tools import skirmish
        ships = str(body.get("ships", "")).strip()
        campaign = str(body.get("campaign_ships", "")).strip()
        caps = body.get("fleet_caps")
        with self.lock:
            settings, log = skirmish.update(self._skirmish_file(),
                                            ships=int(ships) if ships else None, clear_ships=not ships,
                                            fleet_caps=skirmish.parse_caps(caps) if caps is not None and str(caps).strip().lower() != "default" else None,
                                            clear_caps=caps is None or str(caps).strip().lower() == "default",
                                            campaign_ships=int(campaign) if campaign else None, clear_campaign=not campaign)
        return {"ok": True, "log": log or ["no change"], **self.skirmish_settings()}

    def jamming(self, body=None):
        """global jamming penalty, per-jammer overrides and the squadron types / ships to choose from"""
        from deadlock_tools import jamming as jm, squadron_table as st
        levels = {os.path.basename(o): d for o, _src, d in self._squadron_table_files(False)}
        types = st.find(self.S, next(iter(levels.values())))[3]["objects"]
        overrides = jm.read_overrides(self._jamming_file())
        dll = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
        has_hook = b"CustomJamming" in open(dll if os.path.isfile(dll) else self.dll, "rb").read()
        return {"global": jm.global_penalty(self.S, levels), "keys": list(jm.KEYS), "dllHasHook": has_hook,
                "overrides": [{"kind": k, "id": i, "values": v} for (k, i), v in sorted(overrides.items())],
                "squadrons": [{"id": e["id"], "name": e["name"]} for e in types],
                "ships": [{"id": s_["id"], "name": s_["name"]} for s_ in self.view().ships]}

    def save_jamming(self, body):
        """replace the global penalty and the per-jammer overrides"""
        from deadlock_tools import jamming as jm
        with self.lock:
            levels = {os.path.basename(o): d for o, _src, d in self._squadron_table_files(False)}
            outputs, log = [], []
            changed, lines = jm.set_global(self.S, levels, body.get("global") or {})
            outputs += [(data, os.path.join(self.work_paths["levels"], name)) for name, data in changed.items()]
            log += lines
            overrides = {}
            for row in body.get("overrides") or []:
                values = {k: v for k, v in (row.get("values") or {}).items() if v not in (None, "")}
                overrides, _l = jm.set_override(overrides, row["kind"], int(row["id"]), values)
            before = jm.read_overrides(self._jamming_file())
            if overrides != before:
                outputs.append((jm.render(overrides), os.path.join(self.work_paths["bundles"], jm.FILE)))
                log.append("per-jammer overrides: %d jammer(s) -> %s" % (len(overrides), jm.FILE))
            backup = self._promote(outputs, "jamming") if outputs else None
        return {"ok": True, "log": log or ["no change"], "backup": backup}

    def squadron_bays(self, body):
        """add an existing squadron type to the launch bays of its carriers that take another type"""
        from deadlock_tools import squadron_types as sqt
        name = str(body.get("name", "")).strip()
        with self.lock:
            _original, source = self._squadron_source(False)
            P = self._prefabs(open(source, "rb").read())
            levels = {os.path.basename(o): d for o, _src, d in self._squadron_table_files(False)}
            for original in self.table_files:
                levels.setdefault(os.path.basename(original), open(self.src_for(original), "rb").read())
            name = P.root(name)[0]
            like = body.get("like")
            like_id = P.squadron_type(P.root(like)[0]) if like else sqt.guess_like(self.S, levels, P, name)
            if like_id is None:
                raise ValueError("Choose the squadron whose launch bays %s should use." % name)
            outputs, log = self._launch_bay_outputs(levels, P.squadron_type(name), like_id)
            backup = self._promote(outputs, "squadron-bays") if outputs else None
        return {"ok": True, "files": [os.path.basename(t) for _d, t in outputs], "backup": backup, "log": log or ["no change"]}

    def _prefabs(self, data):
        from deadlock_tools import squadron_types as sqt
        managed, game_data = sqt.game_paths(self.levels, self.dll, self.managed_dir())
        return sqt.Prefabs(data, managed, game_data)

    def create_squadron(self, body):
        """copy a squadron prefab as a new squadron type: prefab, SquadronDataTable entries, SquadronFactory entry,
        optional localisation strings and the squadron enum member in the work DLL, promoted together"""
        import shutil, tempfile
        from deadlock_tools import squadron_types as sqt
        donor = str(body.get("donor", "")).strip()
        prefab_name = str(body.get("prefabName", "")).strip()
        table_name = str(body.get("tableName", "")).strip() or prefab_name[:-len("Squadron")]
        texts = [str(body.get(k, "")).strip() or None for k in ("displayName", "heading", "description")]
        if not donor or not prefab_name:
            raise ValueError("Choose a donor squadron and give the new prefab a name ending in 'Squadron'.")
        _original, source = self._squadron_source(False)
        with self.lock:
            data = open(source, "rb").read()
            P = self._prefabs(data)
            files = self._squadron_table_files(False)
            levels = {os.path.basename(o): d for o, _src, d in files}
            level9 = os.path.join(self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels), "level9")
            if "level9" not in levels and os.path.isfile(level9):
                levels["level9"] = open(self.src_for(level9), "rb").read()
            work_dll = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
            enum_source = work_dll if os.path.isfile(work_dll) else self.dll
            enum_ids = stypes.members(enum_source, squadron=True)
            type_id = sqt.next_type_id(self.S, P, levels, enum_ids)
            outputs, changes = [], {}
            if any(texts):
                levels_root = self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)
                loc_target = os.path.join(self.game_data, "resources.assets")
                candidates = [loc_target, os.path.join(levels_root, "resources.assets"),
                              os.path.join(os.path.dirname(os.path.dirname(self.dll)), "resources.assets")]
                loc_source = next((p for p in candidates if os.path.isfile(p)), None)
                if not loc_source:
                    raise ValueError("Could not find resources.assets under the configured game data paths.")
                resources, changes = sqt.add_texts(open(loc_source, "rb").read(), *texts)
                outputs.append((resources, loc_target))
            changed, log = sqt.create_type(self.S, P, levels, donor, prefab_name, table_name, type_id, changes)
            if body.get("copyResearch", True):
                from deadlock_tools import squadron_research as sr
                merged = dict(levels); merged.update(changed)
                more, lines = sr.copy_entries(self.S, merged, P.squadron_type(donor), type_id)
                changed.update(more); log += lines
            # launch bays: the new type launches from every bay of its carriers that takes the donor
            ship_levels = dict(levels); ship_levels.update(changed)
            for original in self.table_files:
                ship_levels.setdefault(os.path.basename(original), open(self.src_for(original), "rb").read())
            bay_outputs, lines = self._launch_bay_outputs(ship_levels, type_id, P.squadron_type(donor))
            outputs += bay_outputs; log += lines
            outputs.append((P.save(), os.path.join(self.work_paths["levels"], sqt.ASSET_FILE)))
            outputs += [(new, os.path.join(self.work_paths["levels"], name)) for name, new in changed.items()]
            managed = self.managed_dir()
            if not managed:
                raise ValueError("Could not find the game's Managed folder needed to register the new squadron type.")
            staged = tempfile.mkdtemp(prefix="squadron-type-", dir=self.stage_root if os.path.isdir(self.stage_root) else None)
            try:
                candidate = os.path.join(staged, "Assembly-CSharp.squadron-enum.dll")
                member = stypes.enum_name(prefab_name)
                result = stypes.add(managed, enum_source, candidate, {type_id: member}, staged, squadron=True)
                if result.returncode or not os.path.isfile(candidate):
                    raise RuntimeError("could not register the new squadron type in the DLL; no files were saved:\n" +
                                       (result.stdout + result.stderr)[-8000:])
                outputs.append((open(candidate, "rb").read(), work_dll))
                log.append("%s: squadron enum %s = %d" % (os.path.basename(work_dll), member, type_id))
            finally:
                shutil.rmtree(staged, ignore_errors=True)
            backup = self._promote(outputs, "squadron-type")
            self._view = None
        return {"ok": True, "id": type_id, "name": prefab_name, "files": [os.path.basename(t) for _d, t in outputs],
                "backup": backup, "log": log}

    def _promote(self, outputs, label):
        """copy [(staged bytes, target path)] into the work tree atomically, backing up what they replace;
        on any failure every target is restored. Returns the backup folder (or None)."""
        import datetime, shutil
        backup_root = os.path.join(self.backups, label + "-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        backups, promoted = {}, []
        try:
            for _data, target in outputs:
                if os.path.isfile(target):
                    backup = self.backup_target(backup_root, target)
                    os.makedirs(os.path.dirname(backup), exist_ok=True); shutil.copy2(target, backup); backups[target] = backup
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
            log, table_edits, ability_edits, munition_edits, research_edits, setting_edits = [], {}, {}, {}, {}, {}
            for name, changes in edits.items():
                if not isinstance(changes, dict): raise ValueError("bad changes for %s" % name)
                changes = dict(changes)
                abilities = changes.pop("abilities", None)
                if abilities and (abilities.get("add") or abilities.get("remove")):
                    ability_edits[name] = abilities
                if "munitions" in changes:
                    munition_edits[name] = [int(m) for m in changes.pop("munitions") or []]
                if "research" in changes:
                    research_edits[name] = changes.pop("research") or {}
                if "abilitySettings" in changes:
                    setting_edits[name] = changes.pop("abilitySettings") or {}
                if not changes:
                    continue
                data, lines = sqmod.apply(self.S, data, name, changes, scale_thresholds=bool(body.get("scale", True)))
                log += lines
                d = sqmod.describe(sqmod.find(sqmod.load(self.S, data), name))
                table_edits[d["squadronShipType"]] = {"MaxShipCount": d["count"], "MaxHullPoints": d["hull"]}
            files = self._squadron_table_files(fresh)
            levels = {os.path.basename(original): level for original, _src, level in files}
            for original in self.table_files:                   # fighter copies go into every ship table
                if os.path.basename(original) not in levels:
                    levels[os.path.basename(original)] = open(self.src_for(original, fresh), "rb").read()
            before = dict(levels)
            P, fighter = None, None
            if ability_edits or munition_edits:
                from deadlock_tools import squadron_types as sqt
                fighter = {"read_bundle": self._fighter_bundle, "enum_ids": stypes.members(self._work_dll())}
            if ability_edits:
                P = self._prefabs(data)
                for name, abilities in ability_edits.items():
                    changed, lines = sqt.edit_abilities(self.S, P, levels, name,
                                                        add=[(item[0], item[1] or None, item[2] if len(item) > 2 else None)
                                                             for item in abilities.get("add") or []],
                                                        remove=abilities.get("remove") or [], fighter=fighter)
                    levels.update(changed); log += lines
                    if any(sqt.ability_key(item[0]) == "stealth" for item in abilities.get("add") or []):
                        note = sqt.stealth_dll_note(self._work_dll())
                        if note not in log: log.append(note)
            if munition_edits:
                loaded = sqmod.load(self.S, data)                 # ability edits never change squadron type ids
                for name, munitions in munition_edits.items():
                    type_id = sqmod.describe(sqmod.find(loaded, name))["squadronShipType"]
                    if sqt.needs_own_fighter(self.S, levels, type_id, munitions):
                        P = P or self._prefabs(data)          # the squadron gets its own fighter copy
                        changed, bundles, enum, lines = sqt.munitions_with_fighter(
                            self.S, P, levels, name, munitions, self._fighter_bundle,
                            set(stypes.members(self._work_dll())) | set(fighter.get("enum", {})))
                        fighter.setdefault("bundles", {}).update(bundles); fighter.setdefault("enum", {}).update(enum)
                    else:
                        changed, lines = sqt.set_munitions(self.S, levels, type_id, munitions)
                        P = P or self._prefabs(data)          # one Fire munitions button per munition
                        lines += P.sync_fire_buttons(name, len(munitions))
                    levels.update(changed); log += lines
            if setting_edits:
                P = P or self._prefabs(data)
                for name, per_ability in setting_edits.items():
                    for key, values in per_ability.items():
                        line = P.set_ability_settings(name, key, values)
                        if line: log.append(line)
            if research_edits:
                from deadlock_tools import squadron_research as sr
                loaded = sqmod.load(self.S, data)
                for name, changes in research_edits.items():
                    type_id = sqmod.describe(sqmod.find(loaded, name))["squadronShipType"]
                    changed, lines = sr.apply_all(self.S, levels, type_id, {int(c): v for c, v in changes.items()})
                    levels.update(changed); log += lines
            if P is not None:
                data = P.save()
            outputs = [(data, os.path.join(self.work_paths["levels"], sqmod.ASSET_FILE))]
            if fighter and fighter.get("bundles"):
                outputs += [(raw, os.path.join(self.work_paths["bundles"], name)) for name, raw in fighter["bundles"].items()]
            if fighter and fighter.get("enum"):
                outputs.append((self._dll_with_ship_types(fighter["enum"]), self._work_dll(target=True)))
                log.append("%s: ShipType enum %s" % (os.path.basename(self.dll), ", ".join(
                    "%s = %d" % (n, i) for i, n in sorted(fighter["enum"].items()))))
            for name in list(levels):
                level = levels[name]
                found = st.find(self.S, level)
                pid_table = found[3] if found else {"objects": []}       # ship-table-only levels (fighter copies)
                wanted = {k: v for k, v in table_edits.items() if any(e["id"] == k for e in pid_table["objects"])}
                if wanted:
                    new, lines = st.apply(self.S, level, wanted)
                    levels[name] = new
                    if new != level and not any(l.startswith("level copies") for l in log) and lines:
                        log.append("level copies (SquadronDataTable): " + "; ".join(lines))
                if levels[name] != before[name]:
                    outputs.append((levels[name], os.path.join(self.work_paths["levels"], name)))
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
                    backup_root = os.path.join(self.backups, "bays-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
                    backup = self.backup_target(backup_root, target); os.makedirs(os.path.dirname(backup), exist_ok=True); shutil.copy2(target, backup)
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
        import tempfile

        if not edits:
            return []
        log = []
        outputs = []
        scalar = [e for e in edits if "field" in e]; vets = [e for e in edits if "vet" in e]; resea = [e for e in edits if "research" in e]
        with self.lock, tempfile.TemporaryDirectory(prefix="ship-levels-") as staged:
            for f in self.table_files:
                name = os.path.basename(f)
                target = os.path.join(self.work_paths["levels"], name)
                if os.path.exists(target) and config.same_file(target, f):
                    raise ValueError("refusing to overwrite the original %s - choose another output folder" % f)
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
                dst = os.path.join(staged, name); config.write(dst, db.buf, f)
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
                with open(dst, "rb") as stream:
                    outputs.append((stream.read(), target))
                log.append("wrote %s" % target)
            self._promote(outputs, "ship-levels")
            self._view = None
            for e in scalar: log.append("%s.%s = %s" % (e["ship"], e["field"], e["value"]))
            for e in resea: log.append("%s: research entries updated (%d campaign(s))" % (e["ship"], len(e["research"])))
            for e in vets: log.append("%s: veterancy updated (%d rank(s) with effects)" % (e["ship"], len([r for r in e["vet"] if r["effects"]])))
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
            ("Jamming", ("jamming", "jam_")),
        ):
            ability_group.update({a.dest: label for a in ap._actions if a.dest.startswith(prefixes)})
        for g in ap._action_groups:
            if g.title in ("positional arguments", "options"): continue
            for a in g._group_actions:
                if not a.option_strings or a.dest in ("help", "out", "bundles", "inspect", "bay_layout", "list_bays", "realign_bays") or a.help == argparse.SUPPRESS: continue   # bays: Weapon layout view
                donor_choice = a.dest == "stealth" or a.dest.removesuffix("_donor") in wmod.SUPPORT_DONOR_BUNDLES and a.dest.endswith("_donor")
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
        from deadlock_tools import mechanics
        source, filename = self.bundle_src(bundle)
        bundle_dir = self.work_paths["bundles"]
        target = os.path.join(bundle_dir, filename)
        if config.same_file(source, target): return {"path": target, "backup": None, "imported": False}
        with self.lock:
            with open(source, "rb") as stream:
                outputs = [(stream.read(), target)]
            magazine = mechanics.magazine_output(source, bundle_dir, self.bundles)
            if magazine:
                outputs.append(magazine)
            backup_root = self._promote(outputs, "bundle-import")
            old_bundle = self.backup_target(backup_root, target) if backup_root else None
            backup = old_bundle if old_bundle and os.path.isfile(old_bundle) else None
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
        from deadlock_tools import bays as bymod
        with self.lock:
            path, _ = self.bundle_src(bundle)
            result = wmod.bundle_mount_layout(path)
            try:
                bay_layout = bymod.read_layout(path)
            except (ValueError, KeyError, StopIteration) as error:     # not a ship prefab with bays
                result["bays"] = {"error": str(error)}
            else:
                for item in bay_layout["bays"] + bay_layout["launchers"]:
                    item["inverse"] = wmod.inverse_transform(item["matrix"])
                try:
                    names = {t["id"]: t["name"] for t in self.allocations()["types"]}
                except Exception:                                        # squadron table unreadable: show numbers
                    names = {}
                result["bays"] = {key: bay_layout[key] for key in ("bays", "launchers", "launchButtons", "munitionButtons")}
                result["bays"]["squadronTypes"] = {str(k): v for k, v in sorted(names.items())}
                result["bays"]["fighterTypes"] = sorted(wmod.FIGHTER_SQUADRON_TYPES)
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
            if not a.option_strings or a.dest in ("help", "out", "bundles", "encrypt", "mount_layout", "bay_layout", "list_bays") or v in (None, "", False): continue
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
            bay_layout = None
            if mount_layout is not None:
                mount_layout = dict(mount_layout); bay_layout = mount_layout.pop("bays", None)
                if not mount_layout.get("changes"): mount_layout = None
            if mount_layout is None and not bay_layout:
                r = run_cmd(cmd, 900)
            else:
                import tempfile
                with tempfile.TemporaryDirectory(prefix="layout-", dir=self.stage_root) as directory:
                    extra = []
                    for name, flag, value in (("mounts.json", "--mount-layout", mount_layout), ("bays.json", "--bay-layout", bay_layout)):
                        if not value: continue
                        spec = os.path.join(directory, name)
                        with open(spec, "w", encoding="utf-8") as stream:
                            json.dump(value, stream)
                        extra += [flag, spec]
                    r = run_cmd(cmd + extra, 900)
            if r.returncode == 0 and not inspect:
                if not os.path.isfile(produced):
                    r = subprocess.CompletedProcess(cmd, 1, r.stdout, r.stderr + "\nExpected bundle output was not created: " + str(produced))
                else:
                    from deadlock_tools import mechanics
                    with open(produced, "rb") as stream:
                        outputs = [(stream.read(), dst)]
                    magazine = mechanics.magazine_output(produced, self.work_paths["bundles"], self.bundles)
                    if magazine:
                        outputs.append(magazine)
                    backup_root = self._promote(outputs, "bundle-save")
                    old_bundle = self.backup_target(backup_root, dst) if backup_root else None
                    backup = old_bundle if overwrite and old_bundle and os.path.isfile(old_bundle) else None
        output = r.stdout + r.stderr
        penalties = {key: (None if str(opts.get(opt)).strip().lower() in ("global", "default", "-", "") else opts.get(opt))
                     for opt, key in wmod.JAM_OVERRIDE_OPTIONS.items() if opts.get(opt) is not None and opts.get(opt) is not False}
        if r.returncode == 0 and produced and penalties:
            try:
                output += "\n" + wmod.set_ship_jamming(dst, self.work_paths["bundles"], [self.bundles] if self.bundles else [], penalties)
            except ValueError as error:
                output += "\njamming penalties not saved: %s" % error
        if r.returncode == 0 and produced:
            lines = (line for line in output.splitlines() if not line.startswith("Back up the game's "))
            output = "\n".join(lines) + "\nSaved bundle to " + dst + "\n"
        return {"ok": r.returncode == 0, "output": output[-20000:], "dst": dst if r.returncode == 0 and not inspect else None,
                "backup": backup, "cmd": " ".join(cmd[3:])}

    def backup_target(self, backup_root, target):
        """where a replaced file goes inside a backup folder: its path relative to the work folder (or its name)"""
        target = os.path.abspath(target)
        inside = os.path.commonpath([target, self.work]) == self.work
        return os.path.join(backup_root, os.path.relpath(target, self.work) if inside else os.path.basename(target))

    # ---- DLL patches (the work-tree copy is the continuing source) ----
    def managed_dir(self):
        """The game's Managed folder (Unity references for DLL builds)."""
        levels = self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)
        candidates = [os.path.join(levels, "Managed"), os.path.dirname(self.dll)]
        if self.bundles:
            candidates.insert(1, os.path.join(os.path.dirname(self.bundles), "Managed"))
        return next((path for path in candidates if os.path.isfile(os.path.join(path, "UnityEngine.CoreModule.dll"))), None)

    def keep_ship_types(self, dll_path, previous, stage_dir, previous_squadrons=None, previous_munitions=None):
        """Put back ShipType and squadron enum members a rebuilt DLL lost.

        Rebuilding from a clean base drops enum members added for copied
        ships; without them, adding the ship to a fleet throws
        KeyNotFoundException. Restores `previous` members (the replaced DLL)
        and covers every ship ID in the work level tables; the same for squadron
        types (`previous_squadrons`, the SquadronDataTable ids). Edits dll_path.
        """
        logs = ""
        for kind in ("ship", "squadron", "munition"):
            # ship types (fleet placement), squadron and munition types (campaign stock data) all iterate their enum
            squadron, munition = kind == "squadron", kind == "munition"
            current = stypes.members(dll_path, squadron=squadron, munition=munition)
            if munition:
                level_ids = stypes.level_munition_ids(dll_path, [self.src_for(f) for f in self.table_files])
                old = previous_munitions or {}
            elif squadron:
                try:
                    tables = [src for _o, src, _d in self._squadron_table_files()]
                except ValueError:                               # level data without squadron tables
                    tables = []
                level_ids = stypes.level_squadron_ids(dll_path, tables)
                old = previous_squadrons or {}
            else:
                level_ids = stypes.level_ship_ids(dll_path, [self.src_for(f) for f in self.table_files])
                old = previous
            wanted = stypes.missing(current, old, level_ids,
                                    prefix="CustomMunition" if munition else "CustomSquadron" if squadron else "CustomShip")
            if not wanted:
                continue
            managed = self.managed_dir()
            if not managed:
                raise ValueError("Could not find the game's Managed folder needed to keep custom ship and squadron types.")
            output = dll_path + ".ship-types"
            result = stypes.add(managed, dll_path, output, wanted, stage_dir, squadron=squadron, munition=munition)
            if result.returncode or not os.path.isfile(output):
                raise RuntimeError("could not keep custom %s types in the rebuilt DLL:\n" % kind +
                                   (result.stdout + result.stderr)[-8000:])
            os.replace(output, dll_path)
            logs += result.stdout
        return logs

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
        fresh = o.get("fresh") in (True, "true", 1, "1")
        if fresh: cmd.append("--fresh")
        previous = stypes.members(dst) if fresh and os.path.isfile(dst) else {}
        previous_squadrons = stypes.members(dst, squadron=True) if fresh and os.path.isfile(dst) else {}
        previous_munitions = stypes.members(dst, munition=True) if fresh and os.path.isfile(dst) else {}
        r = self.run_staged("dll", cmd, 300)
        output = r.stdout + r.stderr
        if r.returncode == 0 and fresh and os.path.isfile(dst):
            with self.lock:
                output += self.keep_ship_types(dst, previous, self.stage_dir("dll"), previous_squadrons, previous_munitions)
        return {"ok": r.returncode == 0, "output": output.replace(self.stage_root, self.work), "dst": dst}

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
                        "CustomInstantPlatingModule", "CustomInstantPlatingAction",
                        "CustomArmourRepairDroneModule", "CustomArmourRepairDroneAction"}
        custom_types.update({"CustomSubsystemModifiers", "CustomModuleSubsystems"})
        custom_types.update({"CustomEmpPulseModule", "CustomEmpPulseAction",
                             "CustomTargetedEmpModule", "CustomTargetedEmpAction",
                             "CustomVortexCrushModule", "CustomVortexCrushAction"})
        module_count = sum(name in custom_types for name in component_names)
        # These stock prefab types also use hooks from the custom DLL.
        uses_ability_hooks = any(name in component_names for name in ("Jammer", "DeployPlatingModule", "HullRepairModule"))
        action_count = component_names.count("TurnaroundRepairBayAction")
        repair_module_count = component_names.count("TurnaroundRepairBayModule")
        if action_count != repair_module_count:
            raise ValueError("saved bundle has %d repair action(s) but %d repair module(s); correct the bundle before building the editor update" %
                             (action_count, repair_module_count))
        if action_count > 8:
            raise ValueError("the editor supports at most 8 repair bays")

        wants_custom = bool(module_count or action_count or uses_ability_hooks)
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
                                                               b"CustomCommanderStatModifiers", b"CustomSubsystemModifiers", b"CustomModuleSubsystems",
                                                               b"CustomStealthFallback", b"CustomJamming", b"CustomCampaignRefit")):
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
        # The build starts from the clean base, which has none of the ship
        # types added for copied ships. Carry them over from the DLL being replaced.
        previous_types = stypes.members(existing_dll) if os.path.isfile(existing_dll) else {}
        previous_squadrons = stypes.members(existing_dll, squadron=True) if os.path.isfile(existing_dll) else {}
        previous_munitions = stypes.members(existing_dll, munition=True) if os.path.isfile(existing_dll) else {}
        logs += self.keep_ship_types(candidate, previous_types, transaction_root, previous_squadrons, previous_munitions)

        asset_outputs = []
        if shortcut_count:
            from deadlock_tools.repair_shortcuts import update_key_assets
            update_key_assets(self.levels, key_stage, shortcut_count, managed)
            for name in ("level3", "resources.assets"):
                path = os.path.join(key_stage, name)
                if not os.path.isfile(path):
                    raise RuntimeError("repair shortcut build did not produce " + name)
                asset_outputs.append((path, os.path.join(self.work_paths["levels"], name)))

        from deadlock_tools import mechanics
        if "ShipInfo" in component_names:
            filename, value = mechanics.magazine_preview(bundle_path)
            target = os.path.join(self.work_paths["bundles"], filename)
            installed = os.path.join(self.bundles, filename) if self.bundles else ""
            if "CustomMagazineCapacityModule" in component_names or os.path.isfile(target) or os.path.isfile(installed):
                preview = os.path.join(transaction_root, filename)
                with open(preview, "w", encoding="ascii") as stream:
                    stream.write(value)
                asset_outputs.append((preview, target))

        dll_target = os.path.join(self.work_paths["dll"], os.path.basename(self.dll))
        outputs = [(candidate, dll_target)] + asset_outputs
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = os.path.join(self.backups, "editor-update-" + stamp)
        promoted = []
        with self.lock:
            try:
                for _, target in outputs:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.isfile(target):
                        backup = self.backup_target(backup_root, target)
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
        for key, flag in (("max_rank", "--max-rank"), ("command_nodes", "--command-nodes"), ("munition_load", "--munition-load"), ("repair_bay_shortcuts", "--repair-bay-shortcuts")):
            if o.get(key) in (True, "true", 1, "1"): cmd.append(flag)
        r = self.run_staged("dll", cmd, 300)
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).replace(self.stage_root, self.work), "dst": dst}

PATH_KEYS = ("levels", "dll", "bundles", "work", "backups")
STATE = {"cfg": {"levels": "", "dll": "", "bundles": "", "work": config.resolve(config.DEFAULTS["work"]), "backups": ""}, "P": None, "error": ""}


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
    {"id": "skirmishinfo", "group": "Fleet limits", "title": "Show skirmish fleet size", "help": "Ships per skirmish and multiplayer fleet (maxShips, stock 10 on PC; maxShipsConsole 8).", "fields": [],
     "build": lambda P, v: ["skirmishinfo"]},
    {"id": "setskirmishinfo", "current": "skirmishinfo", "group": "Fleet limits", "title": "Change skirmish fleet size", "writes": True, "help": "Ships per skirmish and multiplayer fleet on PC (stock 10, 1-99). Both players of a multiplayer game need the same value; saved fleets above the limit are deleted on load.",
     "fields": [F("maxShips", "Ships per fleet", "int", "20", "maxShips in level6/7/8/9")], "build": lambda P, v: ["setskirmishinfo", "maxShips", str(v["maxShips"])]},
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
    try: config.backups_root(cfg["work"], cfg["backups"] or None)
    except SystemExit as e: raise ValueError(str(e))
    try: P = Project(cfg["levels"], cfg["dll"], cfg["work"], cfg["bundles"] or None, cfg["backups"] or None)
    except BaseException as e: raise ValueError(str(e) or "could not read the level data")
    STATE.update(cfg=cfg, P=P, error="")
    if save or migrated:
        try: config.save({**config.load(), **cfg})
        except OSError: pass


def config_info():
    P = STATE["P"]; c = STATE["cfg"]
    return {**c, "backups_effective": P.backups if P else "", "ready": P is not None, "error": STATE["error"], "tables": len(P.table_files) if P else 0,
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
            if self.path == "/api/squadrons/create": return self.send(P.create_squadron(body))
            if self.path == "/api/squadrons/bays": return self.send(P.squadron_bays(body))
            if self.path == "/api/munitions/create": return self.send(P.create_munition(body))
            if self.path == "/api/jamming": return self.send(P.jamming(body))
            if self.path == "/api/munitions/combat": return self.send(P.munition_combat(body))
            if self.path == "/api/munitions/combat/save": return self.send(P.save_munition_combat(body))
            if self.path == "/api/jamming/save": return self.send(P.save_jamming(body))
            if self.path == "/api/allocations/save": return self.send(P.save_allocations(body))
            if self.path == "/api/bays": return self.send(P.bays(body["path"]))
            if self.path == "/api/bays/realign":
                from deadlock_tools import bays as bymod
                path, _ = P.bundle_src(body["bundle"])
                with P.lock:
                    rows, notes = bymod.realign_spec(bymod.read_layout(path))
                return self.send({"bays": rows, "notes": notes})
            if self.path == "/api/bays/save": return self.send(P.save_bays(body))
            if self.path == "/api/armaments": return self.send(P.armaments(body))
            if self.path == "/api/localisation": return self.send(P.localisation(body))
            if self.path == "/api/bundle/import": return self.send(P.import_bundle(body["bundle"]))
            if self.path == "/api/bundle/layout":
                return self.send(P.bundle_layout(body["bundle"]))
            if self.path == "/api/bundle/stats":
                path, _ = P.bundle_src(body["bundle"])
                with P.lock:
                    result = bundle_stats_with_modifiers(path)
                    result["jamming"] = P.ship_jamming(path)
                    return self.send(result)
            if self.path == "/api/bundle/run":
                layout = body.get("mount_layout")
                if layout is not None and not isinstance(layout, dict): raise ValueError("Invalid weapon layout.")
                return self.send(P.run_bundle(body["bundle"], body["options"], body.get("prefab_fields"), body.get("overwrite", False), mount_layout=layout))
            if self.path == "/api/editor/update": return self.send(P.editor_update(body["bundle"], body.get("patches")))
            if self.path == "/api/dll/patch": return self.send(P.dll_patch(body))
            if self.path == "/api/dll/skirmish": return self.send(P.skirmish_settings(body))
            if self.path == "/api/dll/mechanics": return self.send(P.mechanics_settings())
            if self.path == "/api/dll/mechanics/save": return self.send(P.mechanics_settings(body))
            if self.path == "/api/dll/skirmish/save": return self.send(P.save_skirmish_settings(body))
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
.ability-options{border-top:1px solid var(--line);padding:8px 0}.ability-options summary{cursor:pointer;font-weight:600}.ability-options .grid{margin-top:8px}.flak-diagram{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin:8px 0}.flak-diagram figure{margin:0}.flak-svg{width:100%;height:auto;background:var(--bg);border:1px solid var(--line);border-radius:6px;touch-action:none}.flak-svg .grid{stroke:var(--line);stroke-width:.5;vector-effect:non-scaling-stroke}.flak-svg .axis{stroke:var(--muted)}.flak-svg .hull{fill:var(--muted);fill-opacity:.35;stroke:var(--ink);stroke-width:1;vector-effect:non-scaling-stroke}.flak-svg .zone{fill:var(--accent);fill-opacity:.3;stroke:var(--accent);stroke-width:1.5;vector-effect:non-scaling-stroke}.flak-svg .zone.alt{fill-opacity:.12;stroke-dasharray:4 3}.flak-svg .zone-axis{stroke:var(--accent);stroke-width:1;stroke-dasharray:2 3;vector-effect:non-scaling-stroke}.flak-svg .handle{fill:var(--panel);stroke:var(--accent);stroke-width:2;vector-effect:non-scaling-stroke;cursor:grab}.flak-svg .lbl{fill:var(--muted);font-size:11px;vector-effect:non-scaling-stroke}.flak-svg .lbl.mid{text-anchor:middle;dominant-baseline:middle}.ability-options summary.sq{display:flex;gap:8px;align-items:center;list-style:none}.ability-options summary.sq::-webkit-details-marker{display:none}.ability-options summary.sq::before{content:'\25B8';width:1em}.ability-options[open]>summary.sq::before{content:'\25BE'}
.bundle-slot-fields:not(:empty){border-top:1px solid var(--line);margin-top:12px}
#side input{width:100%;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);margin-bottom:8px}
.ship{padding:6px 8px;border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;gap:6px}.ship:hover{background:var(--bg)}.ship.on{background:var(--chg);outline:1px solid var(--accent)}
.ship small{color:var(--muted)}.dot{color:var(--accent)}
#view{padding:16px 20px;max-width:980px}table.alloc{border-collapse:collapse}table.alloc td,table.alloc th{border-bottom:1px solid var(--line);padding:3px 6px}.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:14px}
.card h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px 18px}
label.f{display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--muted)}label.f input,label.f select{padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:14px system-ui}
label.f input.chg,label.f select.chg{background:var(--chg);border-color:var(--accent)}label.f.chk{flex-direction:row;align-items:center;gap:8px;color:var(--ink)}
.module-status{margin-inline-start:auto;text-align:end;font-weight:600}
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
.layout-compat{display:grid;gap:2px;max-height:220px;overflow:auto}.layout-compat label.f.chk{display:flex;flex-direction:row;gap:6px;align-items:center;justify-content:flex-start;text-align:left}.layout-slots tr.removed td{text-decoration:line-through;opacity:.6}.layout-slots tr.removed td:last-child{text-decoration:none;opacity:1}
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
.agreement-table{width:100%;margin-top:8px}.agreement-table th{text-align:left;color:var(--muted);font-weight:600;font-size:12px}.agreement-table td .row{gap:6px;flex-wrap:nowrap}.agreement-table td:nth-child(n+2){white-space:nowrap}.agreement-table td:last-child button{padding:4px 10px}.agreement-scroll{overflow-x:auto}
.cmd-choice-scroll,.cmd-ability-scroll{max-height:58vh;overflow:auto;padding-right:6px;overscroll-behavior:contain}.cmd-choice-scroll{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;align-items:start}.cmd-ability-scroll{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;align-items:start}
@media(max-width:770px){.localisation-editor{grid-template-columns:1fr}.localisation-results{max-height:28vh}}
</style></head><body>
<header><h1>Deadlock Ship Editor</h1>
<nav><button data-t="ships" class="on">Ships</button><button data-t="dll">DLL</button><button data-t="commanders">Commanders</button><button data-t="squadrons">Squadrons</button><button data-t="tools">Database</button><button data-t="localisation">Localisation</button><button data-t="armaments">Armaments</button><button data-t="settings">Settings</button></nav>
<span class="sp"></span><span id="pend" class="hint"></span>
<label class="hint"><input type="checkbox" id="fresh"> start from the originals</label>
<button class="sec" id="discard" disabled title="Throw away unsaved ship and bundle edits">Discard changes</button><button class="pri" id="apply" disabled title="Write the level record, bundle and weapon layout edits for this ship to the work tree">Save ship</button></header>
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
function refreshHullArmourLinkUI(bundle=activeBundle){bundle?.refreshAgreement?.()}
function syncBundleStatFromLevel(dbField,value){
 if(syncingHullArmour||!activeBundle?.linkBox?.checked||!activeBundle.stats.dataset.source||!HULL_ARMOUR_LINKS[dbField])return;
 const input=activeBundle.stats.querySelector('[data-prefab-field="'+HULL_ARMOUR_LINKS[dbField]+'"]');if(!input)return;
 syncingHullArmour=true;input.value=String(Number(value));input.dispatchEvent(new Event('input',{bubbles:true}));syncingHullArmour=false;refreshHullArmourLinkUI(activeBundle);
}
function checkSquadronGroups(input){
 const groups=input.value.split(',').map(value=>value.trim()).filter(Boolean);
 const fighters=Number(shipFieldValue('FighterSlots')),support=Number(shipFieldValue('UtilityFighterSlots'));
 let message='';
 if(groups.length&&!groups.every(value=>/^\d+$/.test(value)&&Number(value)>0))message='Use positive whole numbers separated by commas, e.g. 4,2,2.';
 else if(groups.length&&Number.isInteger(fighters)&&Number.isInteger(support)){
   const total=groups.reduce((sum,value)=>sum+Number(value),0);
   if(total!==fighters+support)message='The groups add up to '+total+', but the ship has '+(fighters+support)+' squadron slots ('+fighters+' fighter + '+support+' support).';
 }
 input.setCustomValidity(message);input.title=message;
 const note=input.parentElement?.querySelector('.groups-check');
 if(note){note.textContent=message;note.hidden=!message}
}
function syncBundleSlotInputs(name,value){
 if(syncingSlotValues||!activeBundle?.slots)return;
 const target={MunitionSlots:'munition_slots',FighterSlots:'fighter_slots',UtilityFighterSlots:'utility_slots'}[name];
 if(!target)return;
 const input=activeBundle.slots.querySelector('[data-d="'+target+'"]');if(input)input.value=String(value);
 if(name==='FighterSlots'||name==='UtilityFighterSlots'){
   const groups=activeBundle.slots.querySelector('[data-d="squadron_groups"]'),split=activeBundle.slots.querySelector('[data-d="split_squadrons"]');
   const fighters=Number(shipFieldValue('FighterSlots')),support=Number(shipFieldValue('UtilityFighterSlots'));
   // Refill only the default fighter/support split; a bundle or user layout with other groups is kept and re-checked.
   const simple=groups&&groups.value.split(',').filter(value=>value.trim()).length<=2;
   if(groups&&!groups.dataset.manual&&simple){if(split?.checked)groups.value='';else if(Number.isInteger(fighters)&&Number.isInteger(support))groups.value=[fighters,support].filter(count=>count>0).join(',')}
   if(groups)checkSquadronGroups(groups);
 }
}
// The bundle's slot button counts after the bay editor's pending removals (Weapon layout view).
function plannedStats(bundle){
 const stats=bundle?.statsResult;if(!stats)return stats;
 const removed=bundle.layout?.removals?.()||{slots:0,munitions:0};
 return removed.slots||removed.munitions?{...stats,launch_actions:stats.launch_actions-removed.slots,munition_actions:stats.munition_actions-removed.munitions}:stats;
}
function bundleOptionValues(bundle){
 const opts={};
 for(const area of [bundle.slots,bundle.sections,bundle.installedAbilities])area.querySelectorAll('[data-d]').forEach(input=>{opts[input.dataset.d]=input.type==='checkbox'?input.checked:input.value});
 return opts;
}
// Everything the Save ship button would write to the bundle. strict=true validates and throws.
function shipBundleChanges(bundle,strict=false){
 const empty={count:0,options:{},changedOptions:[],prefab:{},layout:null};
 if(!bundle?.stats?.dataset.source||!bundle.optionBaseline)return empty;
 const stats=plannedStats(bundle)||{},opts=bundleOptionValues(bundle),base=bundle.optionBaseline,changed=[];
 for(const [name,value] of Object.entries(opts)){
   if(['munition_slots','fighter_slots','utility_slots'].includes(name))continue;
   if(String(value)!==String(base[name]??''))changed.push(name);
 }
 // Slot options only count when they would change the bundle's actual action counts.
 const slotValue=name=>String(opts[name]??'').trim();
 if(slotValue('munition_slots')&&Number(slotValue('munition_slots'))!==stats.munition_actions)changed.push('munition_slots');
 if(slotValue('fighter_slots')&&slotValue('utility_slots')&&Number(slotValue('fighter_slots'))+Number(slotValue('utility_slots'))!==stats.launch_actions)changed.push('fighter_slots','utility_slots');
 const groupsInput=bundle.slots?.querySelector('[data-d="squadron_groups"]');
 if(strict&&changed.includes('squadron_groups')&&groupsInput&&!groupsInput.checkValidity())throw new Error('Squadron groups: '+groupsInput.validationMessage);
 if(strict)for(const input of bundle.installedAbilities.querySelectorAll('[data-d]')){
   if(changed.includes(input.dataset.d)&&!input.reportValidity())throw new Error(input.validationMessage);
 }
 const options={};
 for(const [name,value] of Object.entries(opts)){
   if(name.startsWith('jam_battery_')){
     if(changed.includes(name))options[name]=value.trim()||'default';
     continue;
   }
   if(name==='group_repair_bays')continue;
   if(name==='turnaround_repair_actions'&&!changed.includes(name)&&!opts.turnaround_repair_bay&&!opts.group_repair_bays)continue;
   if(['munition_slots','fighter_slots','utility_slots','squadron_groups'].includes(name)&&!changed.includes(name))continue;
   options[name]=value;
 }
 const prefab={};
 for(const area of [bundle.stats,bundle.installedAbilities]){
   for(const input of area.querySelectorAll('[data-prefab-field]')){
     const inputs=input.matches('fieldset')?[...input.querySelectorAll('input')]:[input];
     if(strict&&inputs.some(field=>!field.reportValidity()))throw new Error('Enter a valid value for the highlighted bundle field.');
     const priorityList=input.dataset.prefabField==='RSpamModule.shipPrioritiesAI';
     const value=priorityList?(input.value.trim()?input.value.split(',').map(Number):[])
       :input.matches('fieldset')?Object.fromEntries(inputs.map(field=>[field.dataset.axis,field.valueAsNumber]))
       :input.type==='checkbox'?input.checked:input.tagName==='SELECT'?Number(input.value):input.valueAsNumber;
     const original=JSON.parse(input.dataset.original);
     const same=priorityList?JSON.stringify(value)===JSON.stringify(original)
       :input.matches('fieldset')?Object.keys(value).every(axis=>value[axis]===original[axis]):value===original;
     if(!same&&!(typeof value==='number'&&Number.isNaN(value)&&!strict))prefab[input.dataset.prefabField]=value;
   }
 }
 const layout=strict?bundle.layout.pending():null,layoutCount=bundle.layout.count();
 return {count:changed.length+Object.keys(prefab).length+layoutCount,options,changedOptions:changed,prefab,layout};
}
let savingShip=false;
const pendingBundleUpdates=new Map();
function updPend(){
 const n=Object.keys(state.pending).length+shipBundleChanges(activeBundle).count;
 const dllPending=pendingBundleUpdates.has(activeBundle?.currentFile);
 if(n&&state.saveStatus?.cls==='msg-ok')state.saveStatus=null;
 const count=n?n+' unsaved change'+(n>1?'s':''):'';
 if(!savingShip){
   $('#pend').className=state.saveStatus?.cls||'hint';
   $('#pend').textContent=[state.saveStatus?.text,count,dllPending?'DLL/UI update pending':''].filter(Boolean).join(' · ');
 }
 $('#apply').disabled=savingShip||(!n&&!dllPending);$('#discard').disabled=savingShip||!n;renderList();
 activeBundle?.refreshAgreement?.();
}
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
 v.append(el('p',{class:'hint'},'Level database values from '+d.source),bundle.source,bundle.agreement);
 for(const [title,names] of GROUPS){
   const fields=names.map(name=>by[name]).filter(Boolean);
   fields.forEach(field=>used.add(field.name));
   const prefab=title==='Carrier & munition slots'?bundle.slots:title==='Hull & armour'?bundle.stats:null;
   if(fields.length||prefab){
     v.append(el('div',{class:'card'},el('h3',{},title),el('div',{class:'grid'},fields.map(fieldInput)),prefab));
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
$('#discard').onclick=()=>{state.pending={};state.saveStatus=null;if(state.data){renderShip();if(activeBundle?.currentFile){activeBundle.activatePath(activeBundle.currentFile);activeBundle.layout.refresh()}}updPend()};
// The one save for the Ships tab: level record edits, bundle prefab fields, bundle options and the weapon layout.
async function saveShip(){
 if(savingShip)return;
 const b=$('#apply'),pend=$('#pend'),bundle=activeBundle,edits=Object.values(state.pending);
 const fresh=$('#fresh').checked;
 const say=(text,cls='hint')=>{state.saveStatus=cls==='hint'?null:{text,cls};pend.className=cls;pend.textContent=text;if(bundle?.message){bundle.message.className=cls;bundle.message.textContent=text}};
 let bundleSaved=null,levelSaved=false,dllUpdated=false,changes;
 const updateBundle=async file=>{
   say('Updating the custom-module DLL and UI assets…');
   const update=await api('/api/editor/update',{bundle:file,patches:pendingBundleUpdates.get(file)});
   bundle.output.hidden=false;
   bundle.output.textContent+='\n\n'+update.output;
   if(!update.ok)throw new Error('DLL/UI update failed (see the bundle output).');
   pendingBundleUpdates.delete(file);
   dllUpdated=update.updated;
 };
 try{changes=shipBundleChanges(bundle,true)}catch(e){say(e.message,'msg-bad');return}
 const writesBundle=changes.count>0;
 if(writesBundle&&!bundle.currentFile){say('Choose a bundle file before saving bundle changes.','msg-bad');return}
 const overwrite=writesBundle&&bundle.overwriteBox?.checked;
 if(overwrite&&!confirm('Replace the opened bundle file? A backup is made beside it.\n\n'+bundle.currentFile))return;
 savingShip=true;
 b.disabled=$('#discard').disabled=true;
 try{
  if(pendingBundleUpdates.has(bundle?.currentFile))await updateBundle(bundle.currentFile);
  if(writesBundle){
   say('Checking bundle requirements…');
   const patches=await bundlePatchOptions(changes.options);
   if(patches===null){say('Save cancelled.');return}
   say('Saving the bundle…');
   const result=await api('/api/bundle/run',{bundle:bundle.currentFile,options:changes.options,prefab_fields:changes.prefab,overwrite,mount_layout:changes.layout});
   bundle.output.textContent=result.cmd+'\n\n'+result.output;bundle.output.hidden=false;
   if(!result.ok)throw new Error('The bundle tool reported an error (see the output under Saving the bundle).');
   bundleSaved=result;
   pendingBundleUpdates.set(result.dst,patches);
   bundle.activatePath(result.dst,true);
   bundle.layout.refresh();
   await updateBundle(result.dst);
  }
  if(edits.length){
   say('Saving the level data…');
   await api('/api/apply',{edits,fresh});
   levelSaved=true;
   for(const [id,edit] of Object.entries(state.pending))if(edits.includes(edit))delete state.pending[id];
  }
  await loadShips();if(state.cur)await openShip(state.cur);
  const parts=[];
  if(levelSaved)parts.push('level data ('+edits.length+' change'+(edits.length>1?'s':'')+')');
  if(bundleSaved)parts.push('bundle '+bundleSaved.dst.split(/[\\/]/).pop()+(overwrite?' (over the opened file; backup made)':''));
  if(dllUpdated||!parts.length)parts.push('DLL/UI update');
  say('Saved '+parts.join(', ')+(overwrite?'.':' to the work folder.'),'msg-ok');
 }catch(e){
  say((bundleSaved?'Bundle saved. ':'')+(levelSaved?'Level data saved. ':'')+e.message,'msg-bad');
 }finally{savingShip=false;updPend()}
}
$('#apply').onclick=saveShip;
document.querySelectorAll('header nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('header nav button').forEach(x=>x.classList.toggle('on',x===b));for(const id of ['#pend','#apply','#discard'])$(id).hidden=b.dataset.t!=='ships';for(const t of ['ships','dll','commanders','squadrons','tools','localisation','armaments','settings'])$('#tab-'+t).hidden=t!==b.dataset.t;if(b.dataset.t==='dll')loadDll();if(b.dataset.t==='commanders')loadCommanders();if(b.dataset.t==='squadrons')loadSquadrons();if(b.dataset.t==='tools')loadTools();if(b.dataset.t==='localisation')loadLocalisation();if(b.dataset.t==='armaments')loadArmaments();if(b.dataset.t==='settings')loadSettings()});
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
 const mountSelect=el('select',{'aria-label':'Selected object',onchange:()=>{
   if(layer==='mounts')active=mountSelect.selectedIndex;else sel[layer]=mountSelect.selectedIndex;renderFields();draw()}});
 const layerSelect=el('select',{'aria-label':'Edit',onchange:()=>{layer=layerSelect.value;placing=false;render()}},
   [['mounts','Turrets'],['bays','Launch bays'],['launchers','Munition launchers']].map(([value,text])=>el('option',{value},text)));
 const slotPanel=el('div',{class:'layout-slots'});
 const views={Top:['x','z'],Side:['z','y'],Front:['x','y']};
 const projection=el('select',{'aria-label':'Projection',onchange:draw},Object.keys(views).map(name=>el('option',{value:name},name)));
 const fields=el('div',{class:'layout-properties'});
 const form=el('form',{onsubmit:event=>event.preventDefault()},fields);
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
 const tools=el('div',{class:'row layout-tools'},layerSelect,mountSelect,projection,addButton);
 // Change every turret of one weapon type to another (same as weapons --swap-weapon); staged for Save ship.
 const swapFrom=el('select',{'aria-label':'Change turrets with weapon'}),swapTo=el('select',{'aria-label':'to weapon'});
 const swapWhere=el('select',{'aria-label':'in'});
 const swapButton=el('button',{class:'sec',type:'button',onclick:()=>{
   const from=Number(swapFrom.value),to=Number(swapTo.value),where=swapWhere.value;let hits=0;
   rows.forEach(row=>{if(row.weapon===from&&(where==='all'||String(row.quadrant)===where)){row.weapon=to;hits++}});
   status.textContent=hits?hits+' turret(s) changed to '+(weapons[to]||'type '+to)+' (not saved yet).':'No turret matched.';
   render();
 }},'Change');
 const swapRow=el('div',{class:'row layout-tools'},el('span',{class:'hint'},'Change all'),swapFrom,el('span',{class:'hint'},'to'),swapTo,
   el('span',{class:'hint'},'in'),swapWhere,swapButton);
 function renderSwap(){
   const typeName=id=>weapons[id]||'Type '+id;
   const present=[...new Set(rows.map(row=>row.weapon))].sort((a,b)=>a-b);
   const keep=(select,value)=>{if([...select.options].some(o=>o.value===value))select.value=value};
   const prevFrom=swapFrom.value,prevTo=swapTo.value,prevWhere=swapWhere.value;
   swapFrom.replaceChildren(...present.map(id=>el('option',{value:id},typeName(id)+' ('+rows.filter(r=>r.weapon===id).length+')')));
   const all=[...new Set([...Object.keys(weapons).map(Number),...present])].sort((a,b)=>a-b);
   swapTo.replaceChildren(...all.map(id=>el('option',{value:id},typeName(id))));
   swapWhere.replaceChildren(el('option',{value:'all'},'all quadrants'),...quadrants.map((name,id)=>el('option',{value:String(id)},name)));
   keep(swapFrom,prevFrom);keep(swapTo,prevTo);keep(swapWhere,prevWhere);
   swapRow.hidden=layer!=='mounts'||!rows.length;
 }
 const reload=el('button',{class:'sec',onclick:()=>{if(!dirty()||confirm('Discard unsaved weapon layout changes?'))load(true)}},'Reload');
 const changed=()=>{if(typeof updPend==='function')updPend()};
 panel.append(el('h3',{},'Weapon layout'),el('p',{class:'hint'},'Turrets, squadron launch bays and munition launchers. Changes are saved with the rest of the ship by Save ship in the top bar.'),status,tools,swapRow,placement,workspace,slotPanel,el('div',{class:'row layout-footer'},reload));
 let source='', loaded='', revision='', rows=[], original=[], hull=[], weapons={}, quadrants=[], active=0, request=0, busy=false, drag, clipboard, placing=false;
 let layer='mounts', bayInfo=null, bays=[], bayOriginal=[], launchers=[], launcherOriginal=[], sel={bays:0,launchers:0};
 let removedSlots=new Set(), removedMunitions=new Set(), removedLaunchers=new Set(), levelBase=null;
 const bayEditable=bay=>({id:bay.id,name:bay.name,position:bay.position,rotation:bay.rotation,launchPath:bay.launchPath,...(bay.icon?{icon:bay.icon}:{}),
   slot:bay.slot,canLaunch:bay.canLaunch,canDock:bay.canDock,dockRange:bay.dockRange,compat:[...bay.compat].sort((a,b)=>a-b)});
 const launcherEditable=item=>({id:item.id,position:item.position,launchPoints:item.launchPoints});
 const bayGone=bay=>removedSlots.has(bay.slot);
 const bayChanges=()=>bays.filter((bay,index)=>!bayGone(bay)&&JSON.stringify(bayEditable(bay))!==JSON.stringify(bayEditable(bayOriginal[index]))).map(bayEditable);
 const launcherChanges=()=>launchers.filter((item,index)=>!removedLaunchers.has(item.id)&&JSON.stringify(launcherEditable(item))!==JSON.stringify(launcherEditable(launcherOriginal[index]))).map(launcherEditable);
 const bayCount=()=>bayChanges().length+launcherChanges().length+removedSlots.size+removedMunitions.size+removedLaunchers.size;
 const baySpec=()=>bayCount()?{revision,bays:bayChanges(),launchers:launcherChanges(),removeSlots:[...removedSlots].sort((a,b)=>a-b),
   removeMunitions:[...removedMunitions],removeLaunchers:[...removedLaunchers]}:null;
 const layerItems=()=>layer==='bays'?bays:layer==='launchers'?launchers:rows;
 const itemGone=item=>layer==='bays'?bayGone(item):layer==='launchers'?removedLaunchers.has(item.id):false;
 const editable=row=>({id:row.id,clone:Boolean(row.clone),new_mount:Boolean(row.newMount),weapon:row.weapon,quadrant:row.quadrant,
   scale:row.scale,angles:row.angles,position:row.position});
 const changes=()=>rows.filter((row,index)=>row.clone||JSON.stringify(editable(row))!==JSON.stringify(editable(original[index]))).map(editable);
 const dirty=()=>changes().length>0||bayCount()>0;
 const label=(title,input)=>el('label',{class:'f'},title,input);
 const transform=(matrix,vector)=>Object.fromEntries([... 'xyz'].map((axis,i)=>[axis,
   matrix[i][3]+[... 'xyz'].reduce((sum,key,j)=>sum+matrix[i][j]*vector[key],0)]));
 const round=value=>Number(value.toFixed(5));
 const context=canvas.getContext('2d');
 let points=[], frame;

 function setSource(file,defer=false){
   if(file===source)return;
   source=file;loaded='';revision='';rows=[];original=[];hull=[];request++;busy=false;drag=null;resetBays(null);
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
     original=result.mounts;rows=structuredClone(original);active=0;resetBays(result.bays);
     const addTypes={...Object.fromEntries(original.map(row=>[row.weapon,'Type '+row.weapon])),...weapons};
     newWeapon.replaceChildren(...Object.entries(addTypes).map(([id,name])=>el('option',{value:id},name)));
     status.textContent=rows.length?'Select an object or drag it in the projection. Position fields are local to its parent.':'This bundle has no weapon mounts to use as a template for adding a turret.';
   }catch(error){if(token===request)status.textContent=error.message}
   finally{if(token===request){busy=false;render()}}
 }
 function resetBays(info){
   bayInfo=info&&!info.error?info:null;
   bays=bayInfo?structuredClone(bayInfo.bays):[];bayOriginal=bayInfo?structuredClone(bayInfo.bays):[];
   launchers=bayInfo?structuredClone(bayInfo.launchers):[];launcherOriginal=bayInfo?structuredClone(bayInfo.launchers):[];
   sel={bays:0,launchers:0};removedSlots=new Set();removedMunitions=new Set();removedLaunchers=new Set();levelBase=null;
   for(const option of layerSelect.options)if(option.value!=='mounts')option.disabled=!(option.value==='bays'?bays.length:launchers.length);
   if(layerSelect.selectedOptions[0]?.disabled){layer='mounts';layerSelect.value='mounts'}
 }
 function render(){
   const ready=loaded===source&&rows.length>0;
   tools.hidden=workspace.hidden=!ready;
   reload.disabled=busy||!source;
   changed();
   const items=layerItems(),index=layer==='mounts'?active:sel[layer];
   mountSelect.replaceChildren(...items.map((item,i)=>el('option',{value:String(i)},
     layer==='bays'?item.name+' · slot '+item.slot+(bayGone(item)?' (removed)':''):
     layer==='launchers'?item.name+(removedLaunchers.has(item.id)?' (removed)':''):item.name+(item.clone?' (new)':''))));
   mountSelect.selectedIndex=index;
   addButton.hidden=layer!=='mounts';
   renderSwap();renderPlacement();renderFields();renderSlots();draw();
 }
 function draw(){
   if(!context||!rows.length)return;
   const [h,v]=views[projection.value];
   const reference=[...original.map(row=>transform(row.matrix,row.position)),...hull.map(([x,y,z])=>({x,y,z})),
     ...bayOriginal.map(bay=>transform(bay.matrix,bay.position)),...launcherOriginal.map(item=>transform(item.matrix,item.position))];
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
   const turretPoints=rows.map(row=>toPoint(transform(row.matrix,row.position)));
   const bayPoints=bays.map(bay=>toPoint(transform(bay.matrix,bay.position)));
   const launcherPoints=launchers.map(item=>toPoint(transform(item.matrix,item.position)));
   points=layer==='bays'?bayPoints:layer==='launchers'?launcherPoints:turretPoints;
   const current=layer==='mounts'?active:sel[layer];
   const shape=(kind,[x,y],r)=>{
     ctx.beginPath();
     if(kind==='bays')ctx.rect(x-r,y-r,2*r,2*r);
     else if(kind==='launchers'){ctx.moveTo(x,y-r*1.2);ctx.lineTo(x+r,y+r*0.8);ctx.lineTo(x-r,y+r*0.8);ctx.closePath()}
     else ctx.arc(x,y,r,0,Math.PI*2);
   };
   const colour=(kind,item)=>kind==='bays'?(bayGone(item)?'#6b3a3a':item.canLaunch?'#c39bf0':'#7a8fa3')
     :kind==='launchers'?(removedLaunchers.has(item.id)?'#6b3a3a':'#f08a7a'):item.clone?'#85e0b0':'#82bdf5';
   for(const [kind,list,pts] of [['mounts',rows,turretPoints],['bays',bays,bayPoints],['launchers',launchers,launcherPoints]]){
     ctx.globalAlpha=kind===layer?1:0.35;
     list.forEach((item,i)=>{if(kind===layer&&i===current)return;shape(kind,pts[i],kind===layer?5:4);ctx.fillStyle=colour(kind,item);ctx.fill()});
   }
   ctx.globalAlpha=1;
   if(points[current]){
     shape(layer,points[current],8);ctx.fillStyle='#f9bc60';ctx.fill();ctx.strokeStyle='#fff';ctx.stroke();
     const item=layerItems()[current];
     if(layer==='bays'&&item.launchPath?.length||layer==='launchers'&&item.launchPoints?.length){   // launch path, local to the object
       const base=transform(item.matrix,item.position),path=(layer==='bays'?item.launchPath:item.launchPoints).map(p=>toPoint({x:base.x+p.x,y:base.y+p.y,z:base.z+p.z}));
       ctx.strokeStyle='#f9bc60';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(...points[current]);path.forEach(p=>ctx.lineTo(...p));ctx.stroke();ctx.setLineDash([]);
     }
   }
   ctx.font='12px sans-serif';ctx.fillStyle='#a9bdd2';
   ctx.fillText('● turrets  ■ launch bays  ▲ munition launchers',14,432);
 }
 function renderFields(){
   if(layer!=='mounts'){renderObjectFields();return}
   const row=rows[active];
   fields.replaceChildren();
   if(!row)return;
   const title=el('strong',{},row.name);
   const select=(values,key)=>el('select',{value:String(row[key]),disabled:busy,onchange:event=>{
     row[key]=Number(event.target.value);changed();
   }},values.map(([id,name])=>el('option',{value:String(id),selected:id===row[key]},name)));
   const types=Object.entries({...weapons,[row.weapon]:weapons[row.weapon]||'Type '+row.weapon}).map(([id,name])=>[Number(id),name]);
   const facing=quadrants.map((name,id)=>[id,name[0].toUpperCase()+name.slice(1)]);
   if(!facing.some(([id])=>id===row.quadrant))facing.push([row.quadrant,'Quadrant '+row.quadrant]);
   const number=(value,write,min)=>el('input',{type:'number',step:'any',required:true,value,min,disabled:busy,
     oninput:event=>{
       const input=event.target;
       if(input.validity.valid&&Number.isFinite(input.valueAsNumber))write(input.valueAsNumber);
       changed();draw();
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
 function renderObjectFields(){
   const item=layerItems()[sel[layer]];
   fields.replaceChildren();
   if(!item)return;
   const gone=itemGone(item);
   const edit=()=>{changed();draw();renderSlots()};
   const number=(value,write,attrs={})=>el('input',{type:'number',step:'any',required:true,value,disabled:busy||gone,...attrs,
     oninput:event=>{const input=event.target;if(input.validity.valid&&Number.isFinite(input.valueAsNumber))write(input.valueAsNumber);edit()}});
   const vector=(name,target,axes='xyz')=>el('fieldset',{class:'layout-vector'},el('legend',{},name),...[...axes].map(axis=>
     label(axis.toUpperCase(),number(axes==='xyzw'?target[axis]:round(target[axis]),value=>{target[axis]=value}))));
   const points=layer==='bays'?item.launchPath:item.launchPoints;
   const pointsBox=el('div',{class:'layout-points'});
   const renderPoints=()=>pointsBox.replaceChildren(...points.map((point,index)=>el('div',{class:'row'},
     vector('Launch point '+(index+1),point),
     el('button',{type:'button',class:'sec',disabled:busy||gone||points.length<2,onclick:()=>{
       points.splice(index,1);renderPoints();edit()}},'Remove point'))));
   const addPoint=el('button',{type:'button',class:'sec',disabled:busy||gone,onclick:()=>{
     points.push({...points.at(-1)||{x:0,y:0,z:0}});renderPoints();edit()}},'Add point');
   renderPoints();
   const original=(layer==='bays'?bayOriginal:launcherOriginal)[sel[layer]];
   const reset=el('button',{type:'button',class:'sec',disabled:busy||gone,onclick:()=>{
     (layer==='bays'?bays:launchers)[sel[layer]]=structuredClone(original);render()}},'Reset');
   if(layer==='bays'){
     const check=(text,key,help)=>el('label',{class:'f chk',title:help},el('input',{type:'checkbox',checked:item[key],disabled:busy||gone,
       onchange:event=>{item[key]=event.target.checked;edit()}}),text);
     const names=bayInfo.squadronTypes||{},fighterTypes=new Set(bayInfo.fighterTypes||[]);
     const known=[...new Set([...Object.keys(names).map(Number),...item.compat,...bayOriginal.flatMap(bay=>bay.compat)])].sort((a,b)=>a-b);
     const compat=el('fieldset',{class:'layout-compat'},el('legend',{},'Squadron types it can launch / dock'),
       ...known.map(code=>el('label',{class:'f chk'},el('input',{type:'checkbox',checked:item.compat.includes(code),disabled:busy||gone,onchange:event=>{
         item.compat=event.target.checked?[...item.compat,code]:item.compat.filter(value=>value!==code);edit()}}),
         (names[code]||'type '+code)+' ('+code+(fighterTypes.has(code)?', fighter':', support')+')')));
     fields.append(label('Name',el('input',{value:item.name,disabled:busy||gone,required:true,maxlength:64,
         oninput:event=>{if(event.target.value.trim()){item.name=event.target.value.trim();changed();renderSlots()}}})),el('span',{class:'hint'},item.path),
       label('Slot (launches this squadron slot)',number(item.slot,value=>{item.slot=Math.round(value)},{step:1,min:0})),
       el('div',{class:'row'},check('Can launch','canLaunch','Squadrons leave from here'),check('Can dock','canDock','Squadrons return here')),
       label('Dock range',number(item.dockRange,value=>{item.dockRange=value},{min:0})),
       vector('Local position',item.position),vector('Local rotation (quaternion)',item.rotation,'xyzw'),compat,
       el('h4',{},'Launch path'),pointsBox,el('div',{class:'row'},addPoint,reset));
   }else{
     const alive=launchers.filter(other=>!removedLaunchers.has(other.id));
     const needed=bayInfo.munitionButtons.length-removedMunitions.size>0;
     const remove=el('button',{type:'button',class:'sec',disabled:busy||(!gone&&alive.length<=1&&needed),
       title:'A ship with munition slots must keep at least one launcher',onclick:()=>{
         if(gone)removedLaunchers.delete(item.id);else removedLaunchers.add(item.id);render()}},gone?'Keep launcher':'Remove launcher');
     fields.append(el('strong',{},item.name),el('span',{class:'hint'},item.path),vector('Local position',item.position),
       el('p',{class:'hint'},'Munitions leave from these points (offsets from the launcher).'),pointsBox,
       el('div',{class:'row'},addPoint,reset,remove));
   }
 }
 // Squadron slots and munition slots: buttons in the battle radial menus. Removing one also lowers the ship's level count.
 function renderSlots(){
   slotPanel.replaceChildren();
   if(!bayInfo||loaded!==source)return;
   const launchButtons=bayInfo.launchButtons,munitionButtons=bayInfo.munitionButtons;
   const slotNumbers=[...new Set([...bays.map(bay=>bay.slot),...launchButtons.map((_,index)=>index)])].sort((a,b)=>a-b);
   const fighters=levelBase?levelBase.FighterSlots:Number(shipFieldValue('FighterSlots'));
   const slotRows=slotNumbers.map(slot=>{
     const owned=bays.filter(bay=>bay.slot===slot),gone=removedSlots.has(slot),button=launchButtons[slot];
     return el('tr',{class:gone?'removed':''},el('td',{},String(slot)),el('td',{},Number.isFinite(fighters)?(slot<fighters?'fighter':'support'):''),
       el('td',{},owned.map(bay=>bay.name).join(', ')||'no bay'),el('td',{},button?button.menu:'no button'),
       el('td',{},el('button',{class:'sec',type:'button',disabled:busy||!owned.length||!button,onclick:()=>toggle(removedSlots,slot)},gone?'Keep':'Remove slot')));
   });
   const munitionRows=munitionButtons.map((button,index)=>{
     const gone=removedMunitions.has(button.id);
     return el('tr',{class:gone?'removed':''},el('td',{},String(index)),el('td',{},button.menu),el('td',{},'position '+button.radial),
       el('td',{},el('button',{class:'sec',type:'button',disabled:busy,onclick:()=>toggle(removedMunitions,button.id)},gone?'Keep':'Remove')));
   });
   const realign=el('button',{class:'sec',type:'button',disabled:busy||!bays.length,onclick:async()=>{
     realign.disabled=true;
     try{
       const result=await api('/api/bays/realign',{bundle:source});
       for(const row of result.bays){
         const bay=bays.find(item=>item.id===row.id);if(!bay)continue;
         Object.assign(bay,structuredClone({name:row.name,position:row.position,rotation:row.rotation,launchPath:row.launchPath,...(row.icon?{icon:row.icon}:{})}));
       }
       layer='bays';layerSelect.value='bays';render();
       status.textContent='Realigned '+result.bays.length+' launch bays into mirrored pairs (not saved yet). '+(result.notes.at(-1)||'');
     }catch(error){status.textContent=error.message}
     finally{realign.disabled=false}
   }},'Realign launch bays');
   slotPanel.append(el('div',{class:'row'},realign,el('span',{class:'hint'},'Lays the cloned launch bays out as mirrored port/starboard pairs in slot order, next to the stock rear bays. Review in the Launch bays layer, then Save ship.')),
     el('h4',{},'Squadron slots'),
     el('p',{class:'hint'},'Slot i is launched by button i and leaves from the bays numbered i. Removing a slot removes its bays and button, renumbers the slots above it and lowers FighterSlots or UtilityFighterSlots (fighter slots come first).'),
     el('table',{class:'agreement-table'},el('tr',{},...['Slot','Kind','Bays','Button menu',''].map(text=>el('th',{},text))),...slotRows),
     el('h4',{},'Munition slots'),
     el('p',{class:'hint'},'One button per munition slot. Removing one lowers MunitionSlots; a continuation page left empty loses its button too.'),
     el('table',{class:'agreement-table'},el('tr',{},...['Slot','Menu','Radial',''].map(text=>el('th',{},text))),...munitionRows));
 }
 function toggle(set,value){
   if(set.has(value))set.delete(value);else set.add(value);
   syncLevelCounts();render();
 }
 function syncLevelCounts(){
   if(!state.data||state.data.name!==bundle.ship)return;
   if(!levelBase)levelBase={FighterSlots:Number(shipFieldValue('FighterSlots')),UtilityFighterSlots:Number(shipFieldValue('UtilityFighterSlots')),MunitionSlots:Number(shipFieldValue('MunitionSlots'))};
   const fighterGone=[...removedSlots].filter(slot=>slot<levelBase.FighterSlots).length;
   const want={FighterSlots:levelBase.FighterSlots-fighterGone,UtilityFighterSlots:levelBase.UtilityFighterSlots-(removedSlots.size-fighterGone),
     MunitionSlots:levelBase.MunitionSlots-removedMunitions.size};
   // Change only the level fields: the bundle's slot controls must stay as they are, or Save ship would also try to
   // rebuild the slot buttons the bay layout is removing.
   syncingSlotValues=true;
   try{for(const [name,value] of Object.entries(want))if(Number(shipFieldValue(name))!==Math.max(0,value))setShipFieldValue(name,Math.max(0,value))}
   finally{syncingSlotValues=false}
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
   const current=layer==='mounts'?active:sel[layer];
   const picked=hits.find(([,i])=>i===current)?.[1]??hits[0][1];
   if(layer==='mounts')active=picked;else sel[layer]=picked;
   const row=layerItems()[picked];
   if(row.inverse&&!itemGone(row)){
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
   const row=layerItems()[layer==='mounts'?active:sel[layer]],point={...drag.position};
   point[h]+=(x-drag.x)/scale;point[v]-=(y-drag.y)/scale;
   row.position=x===drag.x&&y===drag.y?drag.local:Object.fromEntries(Object.entries(transform(row.inverse,point)).map(([key,value])=>[key,round(value)]));
   renderFields();draw();changed();
 });
 for(const name of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(name,()=>{drag=null});
 function refresh(){loaded='';rows=[];original=[];request++;busy=false;placing=false;resetBays(null);render();if(!panel.hidden)load(true)}
 function pending(){
   if(!dirty())return null;
   if(!form.reportValidity())throw new Error('Enter valid weapon layout values.');
   if(loaded!==source)return null;
   const spec={revision,changes:changes()},bayLayout=baySpec();
   if(bayLayout)spec.bays=bayLayout;
   return spec;
 }
 render();
 return {panel,setSource,load,refresh,pending,count:()=>loaded===source?changes().length+bayCount():0,
   removals:()=>loaded===source?{slots:removedSlots.size,munitions:removedMunitions.size}:{slots:0,munitions:0}};
}

function shipBundleFields(ship,initialFile=''){
 const bundle={
   ship,
   source:el('div',{class:'card ship-bundle'},el('h3',{},'Bundle file'),el('p',{class:'hint'},'Loading bundle tools…')),
   slots:el('div',{class:'bundle-slot-fields'}),
   stats:el('div',{class:'bundle-slot-fields','aria-live':'polite'}),
   linkBox:el('input',{type:'checkbox',checked:true,disabled:true}),
   installedAbilities:el('div',{class:'bundle-slot-fields'}),
   sections:el('div'),
   actions:el('div',{class:'card'})
 };
 bundle.layout=weaponLayout(bundle);
 // One table for every value the level ship record and the bundle prefab must agree on.
 const agreementBody=el('tbody'),agreementNote=el('p',{class:'hint'});
 const allLevel=el('button',{class:'sec',onclick:()=>{for(const row of agreementRows())if(!row.same&&row.useLevel)row.useLevel()}},'Make the bundle match the level data');
 const allBundle=el('button',{class:'sec',onclick:()=>{for(const row of agreementRows())if(!row.same&&row.useBundle)row.useBundle()}},'Make the level data match the bundle');
 bundle.agreement=el('div',{class:'card agreement'},el('h3',{},'Level data ↔ bundle'),
   el('p',{class:'hint'},'These values exist in both the level ship record and the ship bundle and must be equal in game. Edit either side; mismatches are flagged here and fixed with one click. Save ship writes both files.'),
   el('label',{class:'f chk',title:'Synchronize level database and bundle prefab hull/armour values one-to-one.'},bundle.linkBox,'Keep hull & armour linked while editing'),
   el('div',{class:'agreement-scroll'},el('table',{class:'alloc agreement-table'},el('thead',{},el('tr',{},el('th',{},'Value'),el('th',{},'Level data'),el('th',{},'Bundle'),el('th',{},''),el('th',{},''))),agreementBody)),
   el('div',{class:'row',style:'margin-top:8px'},allLevel,allBundle),agreementNote);
 const prefabInput=name=>bundle.stats.querySelector('[data-prefab-field="'+name+'"]');
 const optionInput=name=>bundle.slots.querySelector('[data-d="'+name+'"]');
 const setOptionValue=(name,value)=>{const input=optionInput(name);if(input){input.value=String(value);input.dispatchEvent(new Event('input',{bubbles:true}))}};
 const levelNumber=name=>Number(shipFieldValue(name));
 function agreementRows(){
   const rows=[],stats=plannedStats(bundle);
   if(!stats||!state.data||state.data.name!==bundle.ship)return rows;
   const ARMOUR_LABELS={MaxHullPoints:'Hull',ArmorFront:'Armour front',ArmorRear:'Armour rear',ArmorLeft:'Armour port',ArmorRight:'Armour starboard',ArmorTop:'Armour top',ArmorBottom:'Armour bottom'};
   for(const [dbField,prefabKey] of Object.entries(HULL_ARMOUR_LINKS)){
     const input=prefabInput(prefabKey);if(!input)continue;
     const level=levelNumber(dbField),prefab=input.valueAsNumber;
     rows.push({label:ARMOUR_LABELS[dbField],level,bundle:prefab,
       same:Number.isFinite(level)&&Number.isFinite(prefab)&&Math.abs(level-prefab)<=1e-5*Math.max(1,Math.abs(level)),
       useLevel:()=>{syncingHullArmour=true;input.value=String(level);input.dispatchEvent(new Event('input',{bubbles:true}));syncingHullArmour=false},
       useBundle:()=>{syncingHullArmour=true;setShipFieldValue(dbField,prefab);syncingHullArmour=false}});
   }
   // Slot counts: the bundle value is its current action count, or the count Save ship will build.
   const planned=name=>{const v=String(optionInput(name)?.value??'').trim();return v===''?null:Number(v)};
   const munitions=planned('munition_slots')??stats.munition_actions,levelMunitions=levelNumber('MunitionSlots');
   rows.push({label:'Munition slots (battle buttons)',level:levelMunitions,bundle:munitions,pending:planned('munition_slots')!==null&&planned('munition_slots')!==stats.munition_actions,
     same:munitions===levelMunitions,useLevel:optionInput('munition_slots')?()=>setOptionValue('munition_slots',levelMunitions):null,
     useBundle:()=>setShipFieldValue('MunitionSlots',munitions)});
   const fighters=levelNumber('FighterSlots'),support=levelNumber('UtilityFighterSlots');
   const plannedLaunch=planned('fighter_slots')!==null&&planned('utility_slots')!==null?planned('fighter_slots')+planned('utility_slots'):null;
   const launch=plannedLaunch??stats.launch_actions;
   const groups=stats.squadron_groups||[];
   rows.push({label:'Squadron launch buttons (fighter + support)',level:fighters+support,bundle:launch,
     detail:fighters+' fighter + '+support+' support'+(groups.length?' · bundle groups '+groups.join(','):''),
     pending:plannedLaunch!==null&&plannedLaunch!==stats.launch_actions,same:launch===fighters+support,
     useLevel:optionInput('fighter_slots')&&optionInput('utility_slots')?()=>{setOptionValue('fighter_slots',fighters);setOptionValue('utility_slots',support);
       const g=optionInput('squadron_groups'),split=bundle.slots.querySelector('[data-d="split_squadrons"]');
       if(g&&!g.dataset.manual&&!split?.checked&&g.value.split(',').filter(value=>value.trim()).length<=2)g.value=[fighters,support].filter(count=>count>0).join(',');
       if(g)checkSquadronGroups(g)}:null,
     // Submenu groups say nothing about which slots are fighters or supports: keep the support count, fit the fighters.
     useBundle:launch>=support?()=>setShipFieldValue('FighterSlots',launch-support):null});
   return rows;
 }
 bundle.refreshAgreement=()=>{
   const rows=agreementRows();
   if(!rows.length){
     agreementBody.replaceChildren();allLevel.hidden=allBundle.hidden=true;
     agreementNote.textContent=bundle.statsResult?'Link this bundle to a ship record to compare values.':'Load the ship’s bundle to compare it with the level data.';
     return;
   }
   const bad=rows.filter(row=>!row.same).length;
   allLevel.hidden=allBundle.hidden=!bad;
   agreementNote.className=bad?'msg-bad':'msg-ok';
   agreementNote.textContent=bad?bad+' value'+(bad>1?'s differ':' differs')+'. Pick which side is right for each row, or use the buttons above for all of them.':'Level data and bundle agree.';
   agreementBody.replaceChildren(...rows.map(row=>el('tr',{},
     el('td',{},row.label,row.detail?el('div',{class:'hint'},row.detail):null),
     el('td',{},String(row.level)),
     el('td',{},String(row.bundle),row.pending?el('span',{class:'hint'},' (on save)'):null),
     el('td',{class:row.same?'msg-ok':'msg-bad'},row.same?'✓ match':'✗ differs'),
     el('td',{},row.same?'':el('span',{class:'row'},
       row.useLevel?el('button',{class:'sec',onclick:()=>{row.useLevel();updPend()}},'Use level'):null,
       row.useBundle?el('button',{class:'sec',onclick:()=>{row.useBundle();updPend()}},'Use bundle'):null)))));
 };
 for(const area of [bundle.stats,bundle.installedAbilities,bundle.slots,bundle.sections])
   for(const type of ['input','change'])area.addEventListener(type,()=>updPend());
 bundle.linkBox.addEventListener('change',()=>{
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
 for(const option of bundleOptions)if(option.dest==='stealth'||option.dest.endsWith('_donor'))option.choices=state.info.bundle_files.slice();
 const get=id=>source.querySelector('#'+id)??actions.querySelector('#'+id);
 const path=el('input',{type:'text',id:'bpath',value:initialFile,placeholder:'path to a ship bundle (encrypted game file or decrypted .dec)',style:'flex:1;min-width:280px;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'});
 const customModuleScripts={
   custom_armour_repair:'CustomArmourRepairModule',
   armour_repair_drones:'CustomArmourRepairDroneModule',
   custom_squadron_replacement:'CustomSquadronReplacementModule',
   custom_drone_resupply:'CustomDroneResupplyModule',
   custom_magazine_capacity:'CustomMagazineCapacityModule',
   custom_instant_plating:'CustomInstantPlatingModule',
   custom_emp_pulse:'CustomEmpPulseModule',
   custom_targeted_emp:'CustomTargetedEmpModule',
   custom_vortex_crush:'CustomVortexCrushModule'
 };
 function showModuleStatus(input){
   const status=input.closest('label').querySelector('[data-module-status]');
   if(!status)return;
   status.textContent=input.disabled?'Installed':input.checked?'Will add on save':
     bundle.statsResult?'Available to add':'Load a ship to check status';
   status.className='module-status '+(input.disabled?'msg-ok':'hint');
 }
 let statsRequest=0,compareRequest=0,compareData=null;
 async function refreshStats(file,deferLayout=false){
   const request=++statsRequest;bundle.currentFile=file;bundle.layout.setSource(file,deferLayout);
   bundle.stats.replaceChildren();
   bundle.installedAbilities.replaceChildren();
   bundle.statsResult=null;delete bundle.optionBaseline;
   if(bundle.compare)bundle.compare.replaceChildren();
   delete bundle.stats.dataset.source;
   for(const area of [bundle.slots,bundle.sections])area.querySelectorAll('[data-d]').forEach(input=>{
     input.disabled=false;
     if(input.dataset.d.startsWith('jam_battery_')||input.dataset.d==='turnaround_repair_actions')
       input.closest('label').hidden=false;
     if(input.type==='checkbox')input.checked=false;
     else input.value='';
     if(Object.hasOwn(customModuleScripts,input.dataset.d))showModuleStatus(input);
   });
   if(activeBundle===bundle)updPend();else bundle.refreshAgreement();
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
     bundle.statsResult=result;
     for(const [dest,script] of Object.entries({...customModuleScripts,jamming:'Jammer'})){
       const input=bundle.sections.querySelector('[data-d="'+dest+'"]');
       if(input){
         const installed=Boolean(result.abilities[script]);
         input.checked=installed;
         input.disabled=installed;
         if(dest!=='jamming')showModuleStatus(input);
       }
     }
     for(const [dest,key] of [['jam_speed','speed'],['jam_evade','evade'],['jam_accuracy','accuracy'],['jam_battery_drain','drain'],['jam_battery_reserve','reserve'],['jam_battery_recharge','recharge']]){
       const input=bundle.sections.querySelector('[data-d="'+dest+'"]');
       if(input){const v=result.jamming?.values?.[key];input.value=v===undefined?'':String(v);input.placeholder=dest.startsWith('jam_battery_')?'default':'global'}
     }
     const groupsInput=bundle.slots.querySelector('[data-d="squadron_groups"]');
     if(groupsInput&&!groupsInput.dataset.manual)groupsInput.value=(result.squadron_groups||[]).join(',');
     const labels={MaxHullPoints:'Hull',defaultArmourFront:'Front',defaultArmourRear:'Rear',
       defaultArmourLeft:'Port',defaultArmourRight:'Starboard',defaultArmourTop:'Top',defaultArmourBottom:'Bottom'};
     const modifierFields=result.modifier_fields||[];
     const fieldLabels={'CustomVortexCrushModule.hullOverflowPercent':'Hull damage from armour overflow (%)','CustomVortexCrushModule.friendlyFire':'Friendly fire','CustomVortexCrushModule.radius':'Pull and collapse radius','CustomVortexCrushModule.pullPerTick':'Capital pull per movement tick','CustomVortexCrushModule.squadronPullMultiplier':'Squadron pull multiplier','CustomVortexCrushModule.collapseDamage':'Maximum collapse armour damage','CustomVortexCrushModule.cooldownTurns':'Cooldown (battle turns)','CustomVortexCrushModule.targetRange':'Map targeting range (units)',armourDronesPerTick:'Armour drones replenished per battle tick',hullDronesPerTick:'Hull drones replenished per battle tick',resupplyHullRepairDrones:'Resupply Hull Repair Drones',resupplyArmourRepairDrones:'Resupply Armour Repair Drones',repairArmour:'Repair armour instead of hull',droneStack:'Drone capacity',droneSalvo:'Drones per launch',droneRepairRate:'Drone repair rate',droneLifetime:'Drone lifetime',launchDelayTicks:'Launch delay (ticks)','Jammer.jamRange':'Jam range','Jammer.jamMissileChance':'Chance to jam each missile (0-1)','CustomInstantPlatingModule.cooldownTurns':'Cooldown (battle turns)','CustomInstantPlatingModule.activationDelayTicks':'Activation delay after turn starts (ticks)','CustomEmpPulseModule.cooldownTurns':'Cooldown (battle turns)','CustomEmpPulseModule.detonationDelayTicks':'Activation delay after turn starts (ticks)','CustomEmpPulseModule.range':'EMP range (units)','CustomEmpPulseModule.subsystemDamage':'Subsystem maximum health reduced','CustomEmpPulseModule.recoveryTicks':'Subsystem recovery (ticks)','CustomEmpPulseModule.disableSquadronTicks':'Fighter stun duration (ticks)','CustomEmpPulseModule.fighterEvadeDebuffTicks':'Fighter evade debuff duration (ticks)','CustomTargetedEmpModule.range':'EMP blast radius (units)','CustomTargetedEmpModule.detonationDelayTicks':'Activation delay after turn starts (ticks)','CustomTargetedEmpModule.subsystemDamage':'Subsystem maximum health reduced','CustomTargetedEmpModule.recoveryTicks':'Subsystem recovery (ticks)','CustomTargetedEmpModule.disableSquadronTicks':'Fighter stun duration (ticks)','CustomTargetedEmpModule.fighterEvadeDebuffTicks':'Fighter evade debuff duration (ticks)','CustomTargetedEmpModule.chargesPerCooldown':'Charges per turn','CustomTargetedEmpModule.burstIntervalTicks':'Ticks between separate bursts','CustomTargetedEmpModule.targetRange':'Map targeting range (units)',baseRate:'Armour repair per tick, per facing',modifierRate:'Crew bonus weight',modifierField:'Crew bonus to use',
       baseTicksPerReplacement:'Base ticks to replace one squadron',basePlatingPerTick:'Deploy Plating reserve per battle tick',
       capacityMultiplier:'Magazine capacity multiplier','MunitionResupplyRange.Min':'Damaged Armory work rate (%)',
       'MunitionResupplyRange.Max':'Full Armory work rate (%)',portPlating:'Port plating per use',
       starboardPlating:'Starboard plating per use',forePlating:'Fore plating per use',aftPlating:'Aft plating per use',
       dorsalPlating:'Top plating per use',ventralPlating:'Bottom plating per use'};
     const fieldHelp={hullDronesPerTick:'Use -1 to follow the plating rate, 0 to pause hull drone resupply, or enter a custom rate.','CustomInstantPlatingModule.cooldownTurns':'1 allows use next turn. 2 skips one intervening turn.',modifierRate:'0 ignores the crew bonus. 1 applies all of it. 0.5 applies half of it.',
       'CustomEmpPulseModule.range':'Maximum distance from this ship; default is 1,000 game units.',
       'CustomEmpPulseModule.cooldownTurns':'Battle turns between successful activations; default is 1.',
       'CustomEmpPulseModule.detonationDelayTicks':'The EMP queues at turn start. 0 fires immediately at that event; 10 ticks is 1 second, 120 ticks is 1 turn.',
       'CustomInstantPlatingModule.activationDelayTicks':'Plating queues at turn start. 0 applies at that event; 10 ticks is 1 second, 120 ticks is 1 turn.',
       'CustomEmpPulseModule.subsystemDamage':'Temporary subsystem maximum-health reduction per subsystem.',
       'CustomEmpPulseModule.recoveryTicks':'Ticks for the subsystem EMP effect to recover; default is 240.',
       'CustomEmpPulseModule.disableSquadronTicks':'Ticks that enemy fighter squadrons remain stunned; default is 360 (3 turns).',
       'CustomEmpPulseModule.fighterEvadeDebuffTicks':'Ticks that enemy fighters remain jammed. This applies the game’s normal JammingPenalty, including the configured evade penalty; default is 360 (3 turns, matching the vanilla Cylon Virus Mine duration).',
       'CustomVortexCrushModule.hullOverflowPercent':'Percentage of collapse damage left after armour that passes to the hull. Set to 0 for armour-only damage.',
       'CustomVortexCrushModule.friendlyFire':'Allow the vortex to pull and damage friendly units, including the caster.',
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
       basePlatingPerTick:'Deploy Plating reserve restored before crew modifiers. Set to 0 to pause plating resupply. Returning drones refund unused payload separately.',
       'MunitionResupplyRange.Min':'A percentage of one work unit per tick at the lower Armory endpoint.',
       'MunitionResupplyRange.Max':'A percentage of one work unit per tick at full Armory effectiveness.'};
     const TICKS_PER_SECOND=10,TICKS_PER_TURN=120;
     const format=n=>Number(n.toFixed(4)).toString();
     const timedRates={
       'CustomArmourRepairModule.baseRate':'armour points per facing',
       'CustomDroneResupplyModule.basePlatingPerTick':'plating reserve points',
       'CustomDroneResupplyModule.armourDronesPerTick':'armour repair drones',
     };
     const rateDescription=(script,name,value)=>{
       if(value===''||!Number.isFinite(Number(value)))return '';
       const n=Number(value);
       if(script==='CustomDroneResupplyModule'&&name==='hullDronesPerTick')
         return n<0?'Uses the Deploy Plating reserve rate':`${format(n*TICKS_PER_TURN)} hull repair drones per turn before bonuses`;
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
       if(field.key==='CustomTargetedEmpModule.cooldownTurns'||field.key==='Jammer.assignToSelf')continue;
       const separator=field.key.indexOf('.');
       const script=field.key.slice(0,separator),name=field.key.slice(separator+1);
       const subsystem=subsystemGroups.get(script);
       const label=labels[name]||fieldLabels[field.key]||fieldLabels[name]||
         (subsystem?(name==='MaxHealth'?'Maximum health':subsystemLabel(name)):name);
       const original=field.kind==='bool'?Boolean(field.value):field.value;
       const attrs={'data-prefab-field':field.key,'data-original':JSON.stringify(original)};
       let control;
       if(field.kind==='int_list'){
         control=el('label',{class:'f'},'AI ship priorities (ship type IDs)',
           el('input',{...attrs,value:original.join(', '),pattern:' *[0-9]+( *, *[0-9]+)* *',
             title:'Comma-separated ship type IDs, highest priority first. Clear the field to use no explicit priorities.'}));
       }else if(field.kind==='vector'){
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
         else if(field.key==='CustomVortexCrushModule.hullOverflowPercent'){input.min=0;input.max=100}
         else if(field.key==='CustomTargetedEmpModule.chargesPerCooldown'){input.min=1;input.max=16}
         else if(field.key==='CustomTargetedEmpModule.burstIntervalTicks'){input.min=1;input.max=99999}
         else if(name==='baseTicksPerReplacement'||name==='droneSalvo')input.min=1;
         else if(script==='HullRepairModule')input.min=0;
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
     const jammerFields=abilities.get('Jammer');
     if(jammerFields){
       for(const [name,label,fallback,min] of [
         ['jam_battery_drain','Battery drain per turn',10,0],
         ['jam_battery_reserve','Reserve capacity without Stealth',100,0],
         ['jam_battery_recharge','Reserve recharge per turn',24,0]]){
         const original=bundle.sections.querySelector('[data-d="'+name+'"]');
         if(!original)continue;
         original.closest('label').hidden=true;
         jammerFields.push(el('label',{class:'f'},label,
           el('input',{type:'number',step:'any',min,max:1000,value:original.value,placeholder:String(fallback),'data-d':name})));
       }
       jammerFields.push(el('p',{class:'hint'},'Ships with Stealth share its battery. Clear an override to restore its default.'));
     }
     const repairFields=abilities.get('TurnaroundRepairBayModule');
     if(repairFields){
       const count=[...abilities.keys()].filter(name=>name.startsWith('TurnaroundRepairBayModule')).length;
       const original=bundle.sections.querySelector('[data-d="turnaround_repair_actions"]');
       if(original)original.closest('label').hidden=true;
       repairFields.push(el('label',{class:'f'},'Total turnaround repair bays',
         el('input',{type:'number',step:1,min:count,max:8,value:count,required:true,'data-d':'turnaround_repair_actions'}),
         el('span',{class:'orig'},'Increase this total and use Save ship to add indexed repair bays.')));
       repairFields.push(el('label',{class:'f chk'},
         el('input',{type:'checkbox','data-d':'group_repair_bays'}),
         'Group existing bays under Fighters → Repair Bay'));
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
     if(bundle.linkBox.checked)for(const field of Object.keys(HULL_ARMOUR_LINKS))if(state.pending[key(state.cur,field)])syncBundleStatFromLevel(field,shipFieldValue(field));
     bundle.setSubsystemModifiers?.(result.subsystem_modifiers);
     bundle.setModuleSubsystems?.(result.module_subsystems);
     bundle.setFlak?.(result.flak);
     bundle.optionBaseline=bundleOptionValues(bundle);
     if(activeBundle===bundle)updPend();else bundle.refreshAgreement();
   }catch(error){
     if(request===statsRequest){bundle.stats.textContent='Bundle stats unavailable. '+error.message;bundle.refreshAgreement()}
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
     for(const dest of ['defender_donor','celestra_donor','hydra_donor','gorgon_donor','medusa_donor','stealth']){
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
       if(input.dataset.prefabField==='RSpamModule.shipPrioritiesAI'){
         left.set(input.dataset.prefabField,input.value.trim()?input.value.split(',').map(Number):[]);
         continue;
       }
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
    i=el('input',attrs);
    const status=Object.hasOwn(customModuleScripts,o.dest)
      ?el('small',{class:'module-status hint','data-module-status':'','aria-live':'polite'},'Load a ship to check status'):null;
    return el('label',{class:'f chk',title:o.help},i,bundleFieldLabel(o),status)

  }
  if(o.kind==='choice'){const autoDonor={defender_donor:'colonialdefender',celestra_donor:'colonialcelestra',hydra_donor:'cylonhydra',gorgon_donor:'cylongorgon',medusa_donor:'cylonmedusa'}[o.dest];
    i=el('select',{'data-d':o.dest},[el('option',{value:''},autoDonor?'Automatic ('+autoDonor+' from the configured bundle folder)':o.choices?'Choose a bundle':'(default)'),...(o.choices||[]).map(c=>el('option',{value:c},c))])}
  else if(o.kind==='list'){i=el('textarea',{'data-d':o.dest,rows:2,placeholder:'one per line, e.g. '+(o.metavar||'fore=1'),style:'padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'})}
  else i=el('input',{type:'text','data-d':o.dest,placeholder:o.default||o.metavar||''});
  if(o.dest==='squadron_groups')i.addEventListener('input',()=>{
    i.dataset.manual='1';
    // Groups only split the launch buttons into Fighters submenus (any number of groups, in slot order).
    // They never change FighterSlots / UtilityFighterSlots; a total that does not match is flagged instead.
    if(i.value.trim()){
      const split=bundle.slots.querySelector('[data-d="split_squadrons"]');if(split)split.checked=false;
    }
    checkSquadronGroups(i);
  });
  return el('label',{class:'f',title:o.help},bundleFieldLabel(o),i,
    o.dest==='squadron_groups'?el('span',{class:'msg-bad groups-check',hidden:true}):null,el('span',{class:'orig'},o.help))}));
  if(group==='Abilities'){
    const card=el('div',{class:'card'},el('h3',{},'Abilities'),bundle.installedAbilities,el('h4',{},'Add abilities'),el('p',{class:'hint'},'Multiple Actions-menu abilities are grouped under one Abilities submenu. Enabling the turnaround repair bay uses this ship’s database squadron counts and separates fighter and support launch buttons into submenus when needed to make room in the Fighters menu.'));
    const categories=new Map();
    for(const option of options.filter(o=>o.dest!=='flak')){
      const name=option.ability||'Ability menu and donors';
      if(!categories.has(name))categories.set(name,[]);
      categories.get(name).push(option);
    }
    for(const [name,fields] of categories){
      card.append(el('details',{class:'ability-options'},el('summary',{},name),buildGrid(fields)));
    }
    sections.append(card);
    if(options.some(o=>o.dest==='flak'))sections.append(flakCard(bundle));
    continue;
  }
  const modifierDests=['subsystem_modifier','clear_subsystem_modifiers','module_subsystem','clear_module_subsystems'];
  const grid=buildGrid(options.filter(o=>!modifierDests.includes(o.dest)));
  if(group==='Custom ship modules'&&options.some(o=>o.dest==='module_subsystem'))sections.append(moduleSubsystemCard(bundle));
  if(group==='Custom ship modules'&&options.some(o=>o.dest==='subsystem_modifier'))sections.append(subsystemModifierCard(bundle));
  if(group==='Squadron / munition slots'){
    slots.replaceChildren(el('h4',{},'Bundle slot controls'),el('p',{class:'hint'},'Leave blank to keep the bundle as it is. Level data ↔ bundle at the top compares these with the ship record.'),grid);
  }else if(group==='Custom ship modules'){
    sections.append(el('div',{class:'card'},el('h3',{},group),
      el('p',{class:'hint'},'Choose modules to add when you save. To change the settings for installed modules, open Abilities → Installed abilities.'),grid));
  }else{
    sections.append(el('div',{class:'card'},el('h3',{},group==='Output'?'Bundle output':group),grid));
  }
 }
 const actionsAbilityFlags=new Set(['mini_jump','augury','stealth','jamming','targeted_resupply','deploy_plating','hull_repair_drones','armour_repair_drones','coordinated_reload','machine_learning_matrix','rspam','technical_support','marine_reinforcement','custom_instant_plating','custom_emp_pulse','custom_targeted_emp','custom_vortex_crush']);
 bundle.sections.addEventListener('change',event=>{
   if(Object.hasOwn(customModuleScripts,event.target.dataset.d))showModuleStatus(event.target);
   if(!actionsAbilityFlags.has(event.target.dataset.d))return;
   const enabled=[...bundle.sections.querySelectorAll('input[type=checkbox][data-d]')].filter(input=>actionsAbilityFlags.has(input.dataset.d)&&input.checked).length;
   const submenu=bundle.sections.querySelector('[data-d="ability_submenu"]');
   if(enabled>1&&submenu)submenu.checked=true;
 });
 bundle.overwriteBox=el('input',{type:'checkbox'});
 bundle.message=el('span',{class:'hint'},'Save ship in the top bar writes the bundle into the work tree under BSG_Data/StreamingAssets/.');
 bundle.output=el('pre',{hidden:true});
 actions.replaceChildren(el('h3',{},'Saving the bundle'),
   el('label',{class:'f chk',title:'The file is first copied to the backups folder (Settings)'},bundle.overwriteBox,'Save over the opened bundle file instead of the work tree (makes a backup)'),
   el('div',{class:'row'},bundle.message),bundle.output);
}
// Subsystem crew modifiers (weapons --subsystem-modifier / --clear-subsystem-modifiers): a table editor that keeps
// two hidden [data-d] inputs current, so Save ship's option diff and /api/bundle/run handle it like any other option.
function subsystemModifierCard(bundle){
 const spec=el('input',{type:'hidden','data-d':'subsystem_modifier'});
 const clear=el('input',{type:'checkbox','data-d':'clear_subsystem_modifiers',hidden:true});
 const body=el('div'),status=el('p',{class:'hint'},'Load a ship bundle to see its modifiers.');
 let catalog={stats:[],sources:[]},rows=[],loadedCount=0;
 const num=v=>Number(Number(v).toPrecision(7)).toString();
 const statInfo=code=>catalog.stats.find(stat=>stat.code===Number(code));
 const effect=row=>{
   const info=statInfo(row.stat);
   if(!info)return '';
   if(!info.mode)return 'No stock game code reads this stat; only custom modules that use it as their crew-modifier field.';
   const lo=Number(row.min),hi=Number(row.max);
   const show=v=>info.mode==='percent'?(v>=0?'+':'')+num(v*100)+'%':(v>=0?'+':'')+num(v);
   const when=info.when==='live'?'re-read during battle, so damage to the source lowers it':'read once at spawn, normally at full source health';
   return show(lo)+' at 0 health → '+show(hi)+' at full health ('+(info.mode==='percent'?'percentage':'flat')+' bonus, '+when+').';
 };
 const sync=()=>{
   spec.value=rows.map(row=>[row.stat,row.source,num(row.min),num(row.max)].join(':')).join('\n');
   clear.checked=!rows.length&&loadedCount>0;
   if(typeof updPend==='function')updPend();
 };
 const render=()=>{
   if(!catalog.stats.length){body.replaceChildren();return}
   const statOptions=selected=>catalog.stats.map(stat=>el('option',{value:stat.code,selected:stat.code===Number(selected)},
     stat.name+' ('+stat.code+')'+(stat.mode?'':' · no stock effect')));
   const sourceOptions=selected=>catalog.sources.map(source=>el('option',{value:source.code,selected:source.code===Number(selected)},source.label));
   const lines=rows.map((row,index)=>{
     const note=el('p',{class:'hint',style:'margin:2px 0 0'},effect(row));
     const update=(name,value)=>{row[name]=value;note.textContent=effect(row);sync()};
     const number=name=>el('input',{type:'number',step:'any',required:true,value:num(row[name]),style:'width:7em',
       oninput:event=>{if(event.target.value!==''&&Number.isFinite(event.target.valueAsNumber))update(name,event.target.valueAsNumber)}});
     return el('div',{style:'padding:6px 0;border-top:1px solid var(--line)'},
       el('div',{class:'row'},
         el('label',{class:'f'},'Crew stat',el('select',{onchange:event=>update('stat',Number(event.target.value))},...statOptions(row.stat))),
         el('label',{class:'f'},'Source subsystem',el('select',{onchange:event=>update('source',Number(event.target.value))},...sourceOptions(row.source))),
         el('label',{class:'f',title:'Bonus when the source subsystem is at 0 health'},'At 0 health',number('min')),
         el('label',{class:'f',title:'Bonus when the source subsystem is at full health'},'At full health',number('max')),
         el('button',{class:'sec',type:'button',onclick:()=>{rows.splice(index,1);render();sync()}},'Remove')),
       note);
   });
   const add=el('button',{class:'sec',type:'button',onclick:()=>{
     const stat=catalog.stats.find(item=>item.mode)||catalog.stats[0];
     rows.push({stat:stat.code,source:stat.stock_source,min:0,max:0.1});render();sync();
   }},'Add modifier');
   body.replaceChildren(...lines,el('div',{class:'row',style:'margin-top:6px'},add));
 };
 bundle.setSubsystemModifiers=data=>{
   catalog=data||{stats:[],sources:[]};
   rows=(data?.entries||[]).map(entry=>({...entry}));loadedCount=rows.length;
   status.className=data?.error?'msg-bad':'hint';
   status.textContent=data?.error?'Cannot edit modifiers on this bundle: '+data.error
     :rows.length?rows.length+' modifier'+(rows.length>1?'s':'')+' in this bundle.':'No modifiers in this bundle.';
   spec.value=rows.map(row=>[row.stat,row.source,num(row.min),num(row.max)].join(':')).join('\n');clear.checked=false;
   render();
 };
 return el('div',{class:'card'},el('h3',{},'Crew bonuses from subsystem health'),
   el('p',{class:'hint'},'Not the subsystem values themselves (those are under Ship subsystems): this lets one subsystem’s health add to a crew stat, as a crew veterancy bonus would. The bonus runs linearly from the “0 health” value to the “full health” value. Percentage stats use 0.25 for +25%; flat stats add the number as is. It stacks with veterancy and applies even when crew veterancy is off. Needs the custom-module DLL, which Save ship rebuilds. Details: docs/game/subsystems.md.'),
   status,body,spec,clear);
}
// Module subsystems (weapons --module-subsystem / --clear-module-subsystems): which subsystems drive each module
// this toolkit adds. Same hidden-input pattern as the crew card, so Save ship's option diff picks it up.
function moduleSubsystemCard(bundle){
 const spec=el('input',{type:'hidden','data-d':'module_subsystem'});
 const clear=el('input',{type:'checkbox','data-d':'clear_module_subsystems',hidden:true});
 const body=el('div'),status=el('p',{class:'hint'},'Load a ship bundle to see its module drivers.');
 let catalog={modules:[],sources:[]},rows=[],loadedCount=0;
 const num=v=>Number(Number(v).toPrecision(7)).toString();
 const lines=()=>rows.map(row=>[row.module,row.source,num(row.min),num(row.max)].join(':')).join('\n');
 const summary=el('div',{class:'hint'});
 const sourceName=code=>catalog.sources.find(s=>s.code===Number(code))?.label||'subsystem '+code;
 const sync=()=>{spec.value=lines();clear.checked=!rows.length&&loadedCount>0;if(typeof updPend==='function')updPend();renderSummary()};
 function renderSummary(){
   summary.replaceChildren(...catalog.modules.filter(m=>m.installed||rows.some(r=>r.module===m.code)).map(m=>{
     const own=rows.filter(r=>r.module===m.code);
     const lo=own.reduce((t,r)=>t+Number(r.min),0),hi=own.reduce((t,r)=>t+Number(r.max),0);
     return el('div',{},el('strong',{},m.name+(m.installed?'':' (module not on this ship)')+': '),
       own.length?num(lo)+'% with those subsystems destroyed → '+num(hi)+'% at full health, from '+own.map(r=>sourceName(r.source)).join(' + ')
         :'stock ('+m.stock+')');
   }));
   if(!summary.childNodes.length&&catalog.modules.length)summary.textContent='This ship has none of the modules this card drives yet; add one under Ship systems or Custom ship modules.';
 }
 function render(){
   if(!catalog.modules.length){body.replaceChildren();return}
   const moduleOptions=selected=>catalog.modules.map(m=>el('option',{value:m.code,selected:m.code===Number(selected)},m.name+(m.installed?'':' (not on this ship)')));
   const sourceOptions=selected=>catalog.sources.map(s=>el('option',{value:s.code,selected:s.code===Number(selected)},s.label));
   body.replaceChildren(...rows.map((row,index)=>{
     const number=name=>el('input',{type:'number',step:'any',required:true,value:num(row[name]),style:'width:7em',
       oninput:event=>{if(event.target.value!==''&&Number.isFinite(event.target.valueAsNumber)){row[name]=event.target.valueAsNumber;sync()}}});
     return el('div',{class:'row',style:'padding:6px 0;border-top:1px solid var(--line)'},
       el('label',{class:'f'},'Module',el('select',{onchange:event=>{row.module=Number(event.target.value);sync()}},...moduleOptions(row.module))),
       el('label',{class:'f'},'Driven by',el('select',{onchange:event=>{row.source=Number(event.target.value);sync()}},...sourceOptions(row.source))),
       el('label',{class:'f',title:'Contribution when this subsystem is at 0 health'},'At 0 health (%)',number('min')),
       el('label',{class:'f',title:'Contribution when this subsystem is at full health'},'At full health (%)',number('max')),
       el('button',{class:'sec',type:'button',onclick:()=>{rows.splice(index,1);render();sync()}},'Remove'));
   }),el('div',{class:'row',style:'margin-top:6px'},el('button',{class:'sec',type:'button',onclick:()=>{
     const first=catalog.modules.find(m=>m.installed)||catalog.modules[0];
     rows.push({module:first.code,source:first.code===1?2:1,min:20,max:100});render();sync();
   }},'Add subsystem')));
 }
 bundle.setModuleSubsystems=data=>{
   catalog=data||{modules:[],sources:[]};
   rows=(data?.entries||[]).map(entry=>({...entry}));loadedCount=rows.length;
   status.className=data?.error?'msg-bad':'hint';
   status.textContent=data?.error?'Cannot edit module drivers on this bundle: '+data.error:'';
   spec.value=lines();clear.checked=false;render();renderSummary();
 };
 return el('div',{class:'card'},el('h3',{},'Module subsystems'),
   el('p',{class:'hint'},'Choose which subsystems drive each module this toolkit adds, and how strongly. Each row adds its own percentage, from “at 0 health” to “at full health” of that subsystem; the rows for a module add up. With rows, Hull Resin heals hull-per-tick × that total and the Munition Factory works at that total, instead of Engineering’s hull repair range or the Armoury’s resupply range; the custom modules multiply their rate by it. A module without rows keeps its stock behaviour. Needs the custom-module DLL, which Save ship rebuilds.'),
   status,summary,body,spec,clear);
}
// Flak field (weapons --flak FIELD=VALUE, repeatable): the ship's FlakField component. Same hidden-input pattern as
// the cards above: the hidden [data-d=flak] input holds one FIELD=VALUE line per changed field.
function flakCard(bundle){
 const spec=el('input',{type:'hidden','data-d':'flak'});
 const status=el('p',{class:'hint'},'Load a ship bundle to see its flak field.');
 const body=el('div');
 const num=v=>Number(Number(v).toPrecision(6)).toString();
 let info=null,inputs={};
 const current=key=>{const f=inputs[key];return f.type==='checkbox'?f.checked:f.valueAsNumber};
 const changed=()=>Object.keys(inputs).filter(key=>{const v=current(key),o=info.values[key];
   return typeof o==='boolean'?v!==o:Number.isFinite(v)&&Math.abs(v-o)>1e-6*Math.max(1,Math.abs(o))});
 const readout=el('div',{class:'rate-setting'});
 const diagram=el('div',{class:'flak-diagram'});
 const SVG='http://www.w3.org/2000/svg';
 const svg=(tag,attrs={},...kids)=>{const n=document.createElementNS(SVG,tag);for(const [k,v] of Object.entries(attrs))if(v!=null)n.setAttribute(k,v);n.append(...kids.filter(Boolean));return n};
 // The game's flak zone in world units (ship root scale s): a capsule of world radius r around the segment from
 // s·(d,0,-L/2) to s·(d,0,L/2), clipped by the root-local ellipsoid (s·r sideways and vertically, s·(L/2+r) fore-aft).
 function zoneShape(){
   const s=info.hull?.scale||1,d=current('FlakRange.Min'),L=current('FlakAreaLength'),r=current('FlakAreaRadius');
   const half=s*L/2,er=s*r,ez=s*(L/2+r);
   const inside=(u,v)=>(u/er)**2+(v/ez)**2<=1&&Math.hypot(u,Math.max(0,Math.abs(v)-half))<=r;
   const reach=Math.max(er,ez,r+half)*1.01,pts=[];
   for(let i=0;i<96;i++){const a=i/96*2*Math.PI,cu=Math.cos(a),cv=Math.sin(a);let lo=0,hi=reach;
     for(let k=0;k<30;k++){const m=(lo+hi)/2;if(inside(m*cu,m*cv))lo=m;else hi=m}pts.push([lo*cu,lo*cv])}
   return {s,center:s*d,width:Math.min(r,er),length:Math.min(half+r,ez),pts};
 }
 let dragLock=null;
 function niceStep(span){const raw=span/6,p=10**Math.floor(Math.log10(raw));return [1,2,5,10].map(m=>m*p).find(v=>v>=raw)}
 function drawDiagram(){
   if(!info)return;
   const z=zoneShape(),h=info.hull,s=z.s;
   const hc=h?h.center.map(v=>v*s):[0,0,0],he=h?h.extent.map(v=>v*s):[20,8,50];
   const views=[{name:'Top view (bow to the right; port above, starboard below)',vert:'x',flip:true,sides:[-1,1],hullV:[hc[0],he[0]]},
                {name:'Side view, from the flak side (the zone is beside the ship, not above it)',vert:'y',flip:false,sides:[0],hullV:[hc[1],he[1]]}];
   const W=dragLock?dragLock.W:Math.max(he[2]+Math.abs(hc[2]),z.length)*1.15;
   const sizes={};
   const out=[];
   for(const view of views){
     const Vmax=dragLock?dragLock[view.vert]:view.vert==='x'?Math.max(he[0]+Math.abs(hc[0]),z.center+z.width)*1.12:Math.max(he[1]+Math.abs(hc[1]),z.width)*1.25;
     sizes[view.vert]=Vmax;
     const step=niceStep(Math.max(W,Vmax)*2),pad=step*0.4;
     const g=svg('svg',{viewBox:`${-W-pad} ${-Vmax-pad} ${2*(W+pad)} ${2*(Vmax+pad)}`,class:'flak-svg',role:'img','aria-label':view.name});
     const Y=v=>view.flip?v:-v;                       // svg y grows downward; top view: +x (starboard) down
     for(let t=-Math.floor(W/step)*step;t<=W;t+=step)g.append(svg('line',{x1:t,x2:t,y1:-Vmax,y2:Vmax,class:'grid'+(t===0?' axis':'')}));
     for(let t=-Math.floor(Vmax/step)*step;t<=Vmax;t+=step)g.append(svg('line',{x1:-W,x2:W,y1:t,y2:t,class:'grid'+(t===0?' axis':'')}));
     g.append(svg('text',{x:-W,y:Vmax+pad*0.8,class:'lbl'},`grid ${step} units`+(s!==1?` · ship scale ×${s}`:'')));
     // hull: box with a tapered bow (+z)
     const [vc,ve]=view.hullV,z0=hc[2]-he[2],z1=hc[2]+he[2],bow=z1-he[2]*0.25;
     g.append(svg('polygon',{class:'hull',points:[[z0,vc-ve],[bow,vc-ve],[z1,vc-ve*0.25],[z1,vc+ve*0.25],[bow,vc+ve],[z0,vc+ve]].map(([a,b])=>a+','+Y(b)).join(' ')}));
     g.append(svg('text',{x:hc[2],y:Y(vc)+(view.flip?0:0),class:'lbl mid'},h?h.mesh:'ship (size unknown)'));
     for(const side of view.sides){
       const cx=side*z.center;
       g.append(svg('polygon',{class:'zone'+(side<0?' alt':''),points:z.pts.map(([u,v])=>v+','+Y(cx+u)).join(' ')}));
       g.append(svg('line',{x1:-z.length,x2:z.length,y1:Y(cx),y2:Y(cx),class:'zone-axis'}));
       if(view.sides.length>1)g.append(svg('text',{x:0,y:Y(cx)+(side<0?-1:1)*(z.width+step*0.25),class:'lbl mid'},side<0?'port':'starboard'));
     }
     if(view.vert==='x'){
       // handles on the starboard zone: centre (distance), bow end (length), outer edge (radius)
       const handles=[{key:'d',x:0,y:z.center,tip:'drag sideways: distance from the ship'},
                      {key:'L',x:z.length,y:z.center,tip:'drag fore-aft: zone length'},
                      {key:'r',x:0,y:z.center+z.width,tip:'drag sideways: zone radius'}];
       for(const hd of handles){
         const c=svg('circle',{cx:hd.x,cy:Y(hd.y),r:Math.max(W,Vmax)*0.03,class:'handle'},svg('title',{},hd.tip));
         c.addEventListener('pointerdown',ev=>{ev.preventDefault();try{c.setPointerCapture(ev.pointerId)}catch(_){}dragLock={W,...sizes};
           const move=e=>{const top=diagram.querySelector('svg');if(!top)return;
             const m=top.getScreenCTM().inverse(),pt=new DOMPoint(e.clientX,e.clientY).matrixTransform(m);
             const z=zoneShape();
             const zz=Math.max(0,pt.x),xx=pt.y,r=current('FlakAreaRadius');
             if(hd.key==='d')setField('FlakRange.Min',Math.max(0,xx/s));
             else if(hd.key==='r')setField('FlakAreaRadius',Math.max(1,(xx-z.center)/Math.min(1,s)));
             else setField('FlakAreaLength',Math.max(1,s>=1?2*(zz-r)/s:2*(zz/s-r)));};
           const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);dragLock=null;drawDiagram()};
           window.addEventListener('pointermove',move);window.addEventListener('pointerup',up)});
         g.append(c);
       }
     }
     out.push(el('figure',{},g,el('figcaption',{class:'hint'},view.name)));
   }
   diagram.replaceChildren(...out);
 }
 function setField(key,value){const f=inputs[key];f.value=num(Math.round(value*10)/10);sync()}
 function sync(){
   const keys=changed();
   spec.value=keys.map(key=>key+'='+(typeof info.values[key]==='boolean'?current(key):num(current(key)))).join('\n');
   for(const [key,f] of Object.entries(inputs))f.classList.toggle('chg',keys.includes(key));
   const hull=current('BaseDamagePerTick.HullDamage'),armour=current('BaseDamagePerTick.ArmourDamage');
   const lo=info.armory?info.armory.Min/100:1,hi=info.armory?info.armory.Max/100:1;
   const len=current('FlakAreaLength'),rad=current('FlakAreaRadius'),dist=current('FlakRange.Min');
   readout.replaceChildren(
     el('strong',{},`Per ship in the zone, per turn (120 ticks): ${num(hull*120*hi)} hull, ${num(armour*120*hi)} armour at full Armory health`),
     el('p',{class:'hint'},info.armory?`Armory at 0 health: ${num(hull*120*lo)} hull, ${num(armour*120*lo)} armour (Armory FlakDamageRange ${num(info.armory.Min)}% → ${num(info.armory.Max)}%; edit it under Ship subsystems → Armoury). Crew flak bonus (stat 202) multiplies on top; an offline Armory stops flak.`:'This ship has no Armory: the game warns "has flak but no armory" and flak does nothing.'),
     (()=>{const z=zoneShape();return el('p',{class:'hint'},`Zone (world units${z.s!==1?', ship scale ×'+num(z.s)+': distance and length scale with the ship, the radius does not':''}): centred ${num(z.center)} from the centre line on the chosen side, covering ${num(Math.max(0,z.center-z.width))}–${num(z.center+z.width)} out from it, ${num(z.length)} fore and aft and ${num(z.width)} up and down.`)})());
   drawDiagram();
   if(typeof updPend==='function')updPend();
 }
 bundle.setFlak=data=>{
   info=data||null;inputs={};spec.value='';
   if(!info){status.textContent='This ship has no flak field. Only ships built with one can be edited (stock: eight Colonial ships, none Cylon).';body.replaceChildren();return}
   status.textContent='';
   const grid=el('div',{class:'grid'},...info.fields.map(field=>{
     const value=info.values[field.key];
     const input=field.kind==='bool'?el('input',{type:'checkbox',checked:value}):
       el('input',{type:'number',step:field.kind==='int'?'1':'any',min:field.min??undefined,value:num(value)});
     input.addEventListener('input',sync);input.addEventListener('change',sync);
     inputs[field.key]=input;
     return field.kind==='bool'?el('label',{class:'f chk',title:'--flak '+field.key+'=true|false'},input,field.label,el('span',{class:'orig'},field.help)):
       el('label',{class:'f',title:'--flak '+field.key+'=VALUE'},field.label,input,el('span',{class:'orig'},field.help));
   }));
   const stock=el('details',{class:'ability-options'},el('summary',{},'Stock ships with flak'),
     el('table',{class:'alloc agreement-table'},el('tr',{},...['Ship','Hull/tick','Armour/tick','Distance','Length','Radius','Bursts/tick','Armory %'].map(h=>el('th',{},h))),
       ...info.stock.map(r=>el('tr',{},...[r.ship,num(r.hull),num(r.armour),num(r.distance),num(r.length),num(r.radius),num(r.bursts),r.armory.join('–')].map(v=>el('td',{},v))))));
   body.replaceChildren(readout,diagram,grid,stock);sync();
 };
 return el('div',{class:'card'},el('h3',{},'Flak field'),
   el('p',{class:'hint'},'Flak fills a zone beside the ship, to port or starboard as chosen in battle. Every battle tick, each ship inside takes the per-tick damage (scaled by the Armory and the crew flak bonus) and each missile inside has a 30% chance to be shot down. The game code has no team check, so friendly ships inside are hit too. Saved with Save ship. See docs/game/flak.md.'),
   status,body,spec);
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
 if(options.turnaround_repair_bay||options.turnaround_repair_actions){
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
async function loadDll(){const box=$('#tab-dll');if(!state.info.ready){box.replaceChildren(el('div',{class:'card'},el('p',{},'Set the paths in the Settings tab first.')));return}const fld=(id,l,h)=>el('label',{class:'f'},l,el('input',{type:'number',id,step:'any'}),el('span',{class:'orig'},h));
 if(!box.dataset.ok){box.dataset.ok=1;box.replaceChildren(el('div',{class:'card'},el('h3',{},'Current DLL'),el('pre',{id:'dchk'},'checking...')),
  el('div',{class:'card'},el('h3',{},'Patch'),el('p',{class:'hint'},'Commander max rank and command-tree nodes are set on the Commanders tab. Custom-module and commander DLL code is rebuilt automatically when you save a bundle or the commander tree.'),el('div',{class:'grid'},fld('d_repair_actions','Repair action shortcuts','Unique, automatically indexed shortcuts (1–16)'),el('label',{class:'f chk',title:'Existing saves pick up a raised MunitionSlots count in battle'},el('input',{type:'checkbox',id:'d_mun'}),'Munition load fix'),el('label',{class:'f chk',title:'Patches continue from the edited work-tree DLL unless you choose otherwise'},el('input',{type:'checkbox',id:'d_fresh',onchange:loadDll}),'Start from the original DLL (drops earlier patches)')),
   el('div',{class:'row',style:'margin-top:10px'},el('button',{class:'pri',id:'dgo',onclick:patchDll},'Patch DLL'),el('span',{id:'dmsg',class:'hint'}))),
  skirmishDllCard(), mechanicsDllCard(),
  el('div',{class:'card'},el('h3',{},'Revert'),el('div',{class:'grid'},...[['r_mun','Munition load fix'],['r_repair_key','Indexed repair action shortcuts']].map(([id,l])=>el('label',{class:'f chk'},el('input',{type:'checkbox',id}),l))),
   el('div',{class:'row',style:'margin-top:10px'},el('button',{class:'sec',id:'rgo',onclick:revertDll},'Revert ticked patches'),el('span',{id:'rmsg',class:'hint'}))),el('pre',{id:'dout',hidden:true}))}
 try{const r=await api('/api/dll/check'+($('#d_fresh').checked?'?fresh=1':''));$('#dchk').textContent=r.output+'\n(source: '+r.source+')'}catch(e){$('#dchk').textContent=e.message}}
function mechanicsDllCard(){
 const refit=el('input',{type:'checkbox'}),drones=el('input',{type:'checkbox'}),magazine=el('input',{type:'checkbox'});
 const status=el('span',{class:'hint'}),save=el('button',{class:'pri',disabled:true},'Save mechanics');
 const update=r=>{refit.checked=r.campaign_refit;drones.checked=r.drone_self_targeting;magazine.checked=r.magazine_loadout_display};
 api('/api/dll/mechanics',{}).then(r=>{update(r);save.disabled=false}).catch(e=>{status.textContent=e.message});
 save.onclick=async()=>{save.disabled=true;try{
   update(await api('/api/dll/mechanics/save',{campaign_refit:refit.checked,drone_self_targeting:drones.checked,magazine_loadout_display:magazine.checked}));
   status.textContent='Saved. Restart the game after installing the settings.';
 }catch(e){status.textContent=e.message}finally{save.disabled=false}};
 return el('div',{class:'card'},el('h3',{},'Mechanics'),
   el('label',{class:'f chk'},refit,'Campaign Mk II refit'),
   el('label',{class:'f chk'},drones,'Allow drones to target their own ship'),
   el('label',{class:'f chk'},magazine,'Show expanded magazine capacity in the munition picker'),
   el('div',{class:'row'},save,status));
}
function skirmishDllCard(){
 const ships=el('input',{type:'number',min:1,max:99,step:1,placeholder:'level data'});
 const caps=el('input',{type:'text',placeholder:'32000, 64000'});
 const campaign=el('input',{type:'number',min:1,max:99,step:1,placeholder:'level data'});
 const msg=el('span',{class:'hint'}),note=el('p',{class:'hint'});
 const load=async()=>{try{const r=await api('/api/dll/skirmish',{});ships.value=r.ships??'';caps.value=r.fleet_caps?(r.fleet_caps.join(', ')||'none'):r.default_caps.join(', ');campaign.value=r.campaign_ships??'';
   note.textContent=`Level data: ${r.level_ships??'?'} ships per skirmish fleet, ${r.level_campaign_ships??'?'} capital ships per campaign fleet (Ship database tools → Fleet limits); a value here overrides it. Saved to ${r.file}.`}catch(e){note.textContent=e.message}};
 const save=async(reset)=>{msg.className='hint';msg.textContent='saving…';
   try{const r=await api('/api/dll/skirmish/save',reset?{ships:'',fleet_caps:null,campaign_ships:''}:{ships:ships.value,fleet_caps:caps.value,campaign_ships:campaign.value});msg.className='msg-ok';msg.textContent=r.log.join(' | ');await load();loadDll()}
   catch(e){msg.className='msg-bad';msg.textContent=e.message}};
 load();
 return el('div',{class:'card'},el('h3',{},'Fleet limits'),
  el('p',{class:'hint'},'Read by the custom-module DLL (rebuilt when you save a ship with custom modules) from StreamingAssets/deadlock-fleet.txt, so the work tree must be installed for them to apply. Both players of a multiplayer game need the same settings. Saved skirmish fleets with more ships than the limit are deleted when the game loads them.'),
  el('div',{class:'grid'},
   el('label',{class:'f',title:'dll patch --skirmish-ships N'},'Ships per skirmish fleet',ships,el('span',{class:'orig'},'1–99; blank uses the level data (stock 10)')),
   el('label',{class:'f',title:'dll patch --fleet-caps A,B'},'Extra fleet caps',caps,el('span',{class:'orig'},'Added to the PvP and co-op cap choices; the largest is also the fleet save limit. "none" for none.')),
   el('label',{class:'f',title:'dll patch --campaign-ships N'},'Capital ships per campaign fleet',campaign,el('span',{class:'orig'},'Ships a campaign fleet takes into battle, besides its one support ship (stock 7). Blank uses the level data. The single support slot is built into the game and cannot be raised here.'))),
  note,el('div',{class:'row'},el('button',{class:'pri',onclick:()=>save(false)},'Save fleet limits'),el('button',{class:'sec',title:'dll revert --skirmish-ships --fleet-caps --campaign-ships',onclick:()=>save(true)},'Reset to defaults'),msg));
}
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
 back.append(dlg);back.addEventListener('mousedown',e=>{if(e.target===back)done(null)});document.body.append(back);go(start||state.info.home)})}async function revertDll(){const m=$('#rmsg');if(!$('#r_mun').checked&&!$('#r_repair_key').checked){m.className='msg-bad';m.textContent='Tick the patches to revert. Commander rank and nodes are reverted on the Commanders tab.';return}$('#rgo').disabled=true;try{const r=await api('/api/dll/revert',{munition_load:$('#r_mun').checked,repair_bay_shortcuts:$('#r_repair_key').checked});$('#dout').hidden=false;$('#dout').textContent=r.output;m.textContent=r.ok?'written to '+r.dst:'failed';m.className=r.ok?'msg-ok':'msg-bad';loadDll()}catch(e){m.textContent=e.message;m.className='msg-bad'}$('#rgo').disabled=false}

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
     kind==='weapons'?el('button',{type:'button',class:'sec',onclick:()=>{if(!busy)drawForm(!clone)}},clone?'Cancel':'Clone weapon'):
     el('button',{type:'button',class:'sec',title:'shipdb addmunition',onclick:()=>{if(!busy)copyMunitionForm(row)}},'Copy as a new munition…')));
   if(kind==='munitions'&&!clone)form.append(combatSection(row));
 }
 function combatSection(row){
   const box=el('div',{style:'border-top:1px solid var(--line);margin-top:12px;padding-top:8px'},el('h3',{},'Combat (the missile prefab)'),el('p',{class:'hint'},'Loading…'));
   api('/api/munitions/combat',{id:row.id}).then(r=>{
     const inputs={};
     const grid=el('div',{class:'grid'},...r.fields.map(f=>{const v=r.stats[f.key];
       const i=f.kind==='bool'?el('input',{type:'checkbox',checked:!!v}):el('input',{type:'number',step:f.kind==='int'?'1':'any',min:f.min,max:f.max,value:v});
       inputs[f.key]=[i,f,v];return el('label',{class:f.kind==='bool'?'f chk':'f'},f.kind==='bool'?i:f.label,f.kind==='bool'?' '+f.label:i)}));
     const msg=el('span',{class:'hint'});
     const go=el('button',{type:'button',class:'pri',title:'shipdb setmunitionstats',onclick:async()=>{
       const values={};for(const [k,[i,f,v]] of Object.entries(inputs)){const n=f.kind==='bool'?i.checked:Number(i.value);if(f.kind==='bool'?n!==!!v:Math.abs(n-v)>1e-6)values[k]=n}
       if(!Object.keys(values).length){msg.className='hint';msg.textContent='No changes.';return}
       go.disabled=true;msg.className='hint';msg.textContent='Saving…';
       try{const s=await api('/api/munitions/combat/save',{id:row.id,values});msg.className='msg-ok';msg.textContent=s.log.join(' | ')+(s.backup?' (backup: '+s.backup+')':'');
         for(const [k,val] of Object.entries(values))inputs[k][2]=val}
       catch(e){msg.className='msg-bad';msg.textContent=e.message}finally{go.disabled=false}}},'Save combat stats');
     box.replaceChildren(el('h3',{},'Combat (the missile prefab)'),
       el('p',{class:'hint'},'Hits use the damage on the munition’s missile prefab in sharedassets9.assets; the “Listed” values in the munition table are display copies and follow these. Range is both the firing range and how far the missile flies. Copy a stock munition first to give a variant its own numbers.'),
       grid,el('div',{class:'row'},go,msg));
   }).catch(e=>box.replaceChildren(el('h3',{},'Combat'),el('p',{class:'msg-bad'},e.message)));
   return box;
 }
 function copyMunitionForm(row){
   const name=el('input',{type:'text',required:true,value:row.name+' Mk II',maxlength:256});
   const display=el('input',{type:'text',placeholder:'default: the donor’s'});
   const heading=el('input',{type:'text',placeholder:'default: the donor’s'});
   const description=el('textarea',{rows:3,placeholder:'default: the donor’s'});
   const research=el('input',{type:'checkbox',checked:true});
   const msg=el('span',{class:'hint'});
   const go=el('button',{type:'button',class:'pri',onclick:async()=>{
     if(!name.value.trim()){msg.className='msg-bad';msg.textContent='Give the new munition a name.';return}
     go.disabled=true;busy=true;msg.className='hint';msg.textContent='Creating…';
     try{const r=await api('/api/munitions/create',{donor:row.id,name:name.value.trim(),displayName:display.value.trim(),heading:heading.value.trim(),description:description.value.trim(),copyResearch:research.checked});
       busy=false;await load();status.className='msg-ok';status.textContent='Created '+r.name+' (munition '+r.id+'). '+r.log.join(' | ')+(r.backup?' (backup: '+r.backup+')':'');
       const created=model?.rows.find(x=>x.id===r.id);if(created){selected=created;drawList();drawForm()}}
     catch(e){msg.className='msg-bad';msg.textContent=e.message;go.disabled=false;busy=false}}},'Create munition');
   form.replaceChildren(el('h3',{},'New munition, a copy of '+row.name),
     el('div',{class:'grid'},el('label',{class:'f'},'Name (munition table)',name),el('label',{class:'f'},'Displayed name',display),el('label',{class:'f'},'Blueprint heading',heading),
       el('label',{class:'f',style:'grid-column:span 2'},'Blueprint description',description),
       el('label',{class:'f chk'},research,'Researchable like the donor (same campaigns, cost and mission)')),
     el('p',{class:'hint'},'Copies the donor’s missile prefab (sharedassets9.assets) with the new munition id, its munition and icon table rows in every level file, its MissileFactory entry, and registers the id in the work DLL. Edit the new munition’s stats here afterwards; squadrons of the same faction can carry it (Squadrons → Munitions).'),
     el('div',{class:'row'},go,el('button',{type:'button',class:'sec',onclick:()=>drawForm()},'Cancel'),msg));
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
  row('s_backups','Backups folder','Copies of files the editor replaces. Leave empty for a work-backups folder beside the work folder. It must be outside the work folder, which is merged into the game.',I.backups,false),
  row('s_work','Work folder','Keeps your edits in a merge-ready tree: BSG_Data/ level files, StreamingAssets/ ship bundles, and Managed/Assembly-CSharp.dll. Decrypted scratch files stay in a hidden tool folder.',I.work,false),
  el('div',{class:'row'},el('button',{class:'pri',id:'s_save',onclick:saveSettings},'Save and load'),el('span',{id:'s_msg',class:I.error?'msg-bad':'hint'},I.error||'')))}
async function saveSettings(){const m=$('#s_msg');$('#s_save').disabled=true;m.className='hint';m.textContent='loading the level data (about 15 seconds)...';
 try{state.info=await api('/api/config',{levels:$('#s_levels').value,dll:$('#s_dll').value,bundles:$('#s_bundles').value,work:$('#s_work').value,backups:$('#s_backups').value});bundleForm=undefined;localisationEditor=undefined;delete $('#tab-armaments').dataset.ready;
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
  el('div',{class:'row'},el('label',{class:'chk'},scale,' Scale loss thresholds when the hull pool changes'),save,status),
  el('div',{class:'row'},el('button',{class:'sec',disabled:!data.prefabs.length,title:data.abilityError||'shipdb addsquadron',onclick:()=>showSquadronTypeForm()},'Copy and add a squadron type…')));
 const formSlot=el('div',{});
 function changes(name){return pending[name]||(pending[name]={})}
 function showSquadronTypeForm(){
  const donor=el('select',{},...data.prefabs.map(n=>el('option',{value:n},n)));
  const prefab=el('input',{type:'text'});
  const suggest=()=>{prefab.placeholder='e.g. '+donor.value.replace(/Squadron$/,'')+'Mk2Squadron'};donor.onchange=suggest;suggest();
  const table=el('input',{type:'text',placeholder:'default: the prefab name without “Squadron”'});
  const display=el('input',{type:'text',placeholder:'default: the donor’s'});
  const heading=el('input',{type:'text',placeholder:'default: the donor’s'});
  const description=el('textarea',{rows:3,placeholder:'default: the donor’s'});
  const research=el('input',{type:'checkbox',checked:true});
  const msg=el('span',{class:'hint'});
  const go=el('button',{class:'pri',onclick:async()=>{go.disabled=true;msg.className='hint';msg.textContent='Creating…';
   try{const r=await api('/api/squadrons/create',{donor:donor.value,prefabName:prefab.value.trim(),tableName:table.value.trim(),displayName:display.value.trim(),heading:heading.value.trim(),description:description.value.trim(),copyResearch:research.checked});
    await loadSquadrons('Created '+r.name+' (squadron type '+r.id+'). '+r.log.join(' | ')+(r.backup?' (backup: '+r.backup+')':''))}
   catch(e){msg.className='msg-bad';msg.textContent=e.message;go.disabled=false}}},'Create squadron type');
  formSlot.replaceChildren(el('div',{class:'card'},el('h3',{},'New squadron type'),
   el('div',{class:'grid'},el('label',{class:'f'},'Copy prefab',donor),el('label',{class:'f'},'New prefab name (ends in Squadron)',prefab),el('label',{class:'f'},'Squadron table name',table),
    el('label',{class:'f'},'Info-panel name',display),el('label',{class:'f'},'Blueprint heading',heading),el('label',{class:'f',style:'grid-column:span 2'},'Blueprint description',description),
    el('label',{class:'f chk',title:'shipdb setsquadronresearch'},research,'Researchable in the same campaigns as the donor (cost and mission copied, never unlocked at start)')),
   el('p',{class:'hint'},'Writes a copy of the prefab to sharedassets9.assets, a copy of the donor’s squadron table entry to every level file that has one, a SquadronFactory entry in level9, new text for the names you fill in, and a squadron enum member in the work DLL. The new type is offered in skirmish (when the donor is) on the carriers the donor may use; change that in Carrier allocations. Campaigns only offer squadron types the campaign has unlocked.'),
   el('div',{class:'row'},go,el('button',{class:'sec',onclick:()=>formSlot.replaceChildren()},'Cancel'),msg)));
 }
 function abilitiesCard(q){
  if(!q.abilities)return el('div',{class:'card'},el('h3',{},'Abilities'),el('p',{class:'hint'},data.abilityError?'Ability editing is unavailable: '+data.abilityError:'This prefab has no Actions menu.'));
  const c=changes(q.name);const ab=c.abilities||{add:[],remove:[]};
  const tidy=()=>{if(!ab.add.length&&!ab.remove.length)delete c.abilities;else c.abilities=ab;drawList();drawDetail()};
  const cat=data.abilityCatalogue;
  const rows=q.abilities.map(a=>{const gone=ab.remove.includes(a.key);
   const spec=(data.abilitySettingSpecs||{})[a.key],now=(q.abilitySettings||{})[a.key];
   const inputs=spec&&now&&!gone?spec.map(f=>{const pend=((c.abilitySettings||{})[a.key]||{})[f.field];const cur=pend!==undefined?pend:now[f.field];
     const i=f.kind==='bool'?el('input',{type:'checkbox',checked:!!cur}):el('input',{type:'number',step:f.kind==='int'?'1':'any',min:f.min,max:f.max,value:cur,style:'width:6em'});
     i.onchange=()=>{const v=f.kind==='bool'?i.checked:Number(i.value);const s=c.abilitySettings||(c.abilitySettings={});const k=s[a.key]||(s[a.key]={});
       if(f.kind==='bool'?v===!!now[f.field]:Math.abs(v-now[f.field])<1e-6)delete k[f.field];else k[f.field]=v;
       if(!Object.keys(k).length)delete s[a.key];if(!Object.keys(s).length)delete c.abilitySettings;drawList()};
     if(pend!==undefined)i.classList.add('chg');
     return f.kind==='bool'?el('label',{class:'f chk',title:f.field},i,f.label):el('label',{class:'f',title:f.field},f.label,i)}):[];
   const toggle=el('button',{class:'sec',style:'margin-left:auto',title:'shipdb setsquadronability … remove '+a.key,
    onclick:e=>{e.preventDefault();if(gone)ab.remove=ab.remove.filter(k=>k!==a.key);else ab.remove.push(a.key);tidy()}},gone?'Keep':'Remove');
   return el('details',{class:'ability-options',open:inputs.length>0},
    el('summary',{class:'sq'},el('span',{style:gone?'text-decoration:line-through':''},a.label),
     el('span',{class:'hint',style:'font-weight:normal'},(a.menu==='main'?'main menu':'Abilities menu')+', slot '+a.slot+(gone?' · removed on save':'')),toggle),
    inputs.length?el('div',{class:'grid'},inputs):el('p',{class:'hint'},gone?'Removed when you save.':spec?'':'This ability has no editable settings.'))});
  const adds=ab.add.map(([k,d,set],i)=>el('div',{class:'row'},el('span',{},'+ '+cat[k].label),el('span',{class:'hint'},cat[k].builtin?'built in'+(set?' · battery '+set.battery+', cooldown '+set.cooldown+(set.active?', active at battle start':''):''):'copied from '+d),el('button',{class:'sec',onclick:()=>{ab.add.splice(i,1);tidy()}},'Undo')));
  const have=new Set([...q.abilities.filter(a=>!ab.remove.includes(a.key)).map(a=>a.key),...ab.add.map(([k])=>k)]);
  const options=Object.entries(cat).filter(([k,v])=>!have.has(k)&&(v.builtin||v.donors.some(n=>n!==q.name)));
  const pick=el('select',{},...options.map(([k,v])=>el('option',{value:k},v.label)));
  const donor=el('select',{});
  const battery=el('input',{type:'number',min:1,step:'any',value:100,style:'width:6em',title:'battery size (the Orion has 100)'});
  const cooldown=el('input',{type:'number',min:0,step:1,value:1,style:'width:4em',title:'turns before stealth can be used again'});
  const active=el('input',{type:'checkbox',title:'stealthed when the battle starts'});
  const stealthOpts=el('span',{class:'row'},el('label',{},'battery ',battery),el('label',{},'cooldown ',cooldown),el('label',{class:'chk'},active,' active at battle start'));
  const fill=()=>{const v=cat[pick.value];const built=!!(v&&v.builtin);stealthOpts.hidden=!built;donor.hidden=built;
   donor.replaceChildren(...(v&&!built?v.donors.filter(n=>n!==q.name):[]).map(n=>el('option',{value:n},n.replace(/Squadron$/,''))))};
  pick.onchange=fill;fill();
  const add=el('button',{class:'sec',disabled:!options.length,title:'shipdb setsquadronability … add KEY --donor SQUADRON',onclick:()=>{
   if(cat[pick.value].builtin)ab.add.push([pick.value,'',{battery:Number(battery.value),cooldown:Number(cooldown.value),active:active.checked}]);
   else ab.add.push([pick.value,donor.value]);tidy()}},'Add ability');
  return el('div',{class:'card'},el('h3',{},'Abilities'),
   el('p',{class:'hint'},'Abilities are action buttons on the squadron prefab. An added ability is copied, with its helper component (e.g. DisarmAgent, Jammer), from a squadron that has it into this squadron’s Abilities submenu, which is created when missing. Removing the last ability of a submenu removes the submenu. The squadron table’s info-panel ability list (and, for Fire munitions, its munition slots) follows. Saved with Save squadron changes.'),
   ...(rows.length?rows:[el('p',{class:'hint'},'No abilities.')]),...adds,
   el('div',{class:'row'},el('label',{},'Ability ',pick),donor,stealthOpts,add),
   el('p',{class:'hint'},'Stealth is built in rather than copied: it adds a StealthModule and a Stealth button. Squadrons have no Tech Bay, so it needs the custom-module DLL’s stealth fallback (included in every custom-module build); the battery drains 0.01 and recharges 0.2 per battle tick, the Orion’s Tech Bay at full health. There is no shimmer effect.'),
   munitionSection(q,ab,have));
 }
 function baysCard(q){
  const like=el('select',{},el('option',{value:''},'(guess from the name)'),...data.prefabs.filter(n=>n!==q.name).map(n=>el('option',{value:n},n.replace(/Squadron$/,''))));
  const msg=el('span',{class:'hint'});
  const go=el('button',{class:'sec',title:'shipdb setsquadronbays',onclick:async()=>{go.disabled=true;msg.className='hint';msg.textContent='Updating carrier bundles…';
   try{const r=await api('/api/squadrons/bays',{name:q.name,like:like.value});msg.className='msg-ok';msg.textContent=r.log.join(' | ')+(r.backup?' (backup: '+r.backup+')':'')}
   catch(e){msg.className='msg-bad';msg.textContent=e.message}finally{go.disabled=false}}},'Add to launch bays');
  return el('div',{class:'card'},el('h3',{},'Launch bays'),
   el('p',{class:'hint'},'A carrier launches a squadron only from a bay whose launch list includes its type. This adds the type to every bay that takes the chosen squadron, on each carrier allowed to carry it (same faction, squadron slots, not excluded), and writes those ship bundles to the work tree. New squadron types get this when they are created.'),
   el('div',{class:'row'},el('label',{},'Launch from the same bays as ',like),go,msg));
 }
 function researchCard(q){
  if(q.research==null||!Object.keys(data.campaigns||{}).length)return el('div',{class:'card'},el('h3',{},'Research'),el('p',{class:'hint'},'No research table found in the level data.'));
  const c=changes(q.name);const r=c.research||{};
  const tidy=()=>{if(Object.keys(r).length)c.research=r;else delete c.research;drawList()};
  const rows=Object.entries(data.campaigns).map(([camp,label])=>{
   const orig=q.research[camp]||null;const cur=camp in r?r[camp]:orig;
   const val=k=>cur?cur[k]:(k==='cost'?600:k==='mission'?-1:k==='label'?'':false);
   const on=el('input',{type:'checkbox',checked:!!cur});
   const cost=el('input',{type:'number',min:0,step:1,value:val('cost'),style:'width:6em',disabled:!cur});
   const mission=el('input',{type:'number',min:-1,step:1,value:val('mission'),style:'width:5em',disabled:!cur,title:'story mission that must be completed first; -1 = none'});
   const lab=el('input',{type:'text',value:val('label'),style:'width:5em',maxlength:32,disabled:!cur});
   const unl=el('input',{type:'checkbox',checked:!!val('unlocked'),disabled:!cur,title:'owned from the start (new campaigns only)'});
   const update=()=>{
    if(!on.checked){if(orig)r[camp]=null;else delete r[camp]}
    else{const v={cost:Number(cost.value),mission:Number(mission.value),label:lab.value,unlocked:unl.checked};
     if(orig&&orig.cost===v.cost&&orig.mission===v.mission&&orig.label===v.label&&orig.unlocked===v.unlocked)delete r[camp];else r[camp]=v}
    for(const i of [cost,mission,lab,unl])i.disabled=!on.checked;tidy()};
   for(const i of [on,cost,mission,lab,unl])i.onchange=update;
   return el('tr',{},el('td',{},el('label',{class:'chk'},on,' '+label)),el('td',{},cost),el('td',{},mission),el('td',{},lab),el('td',{style:'text-align:center'},unl));
  });
  return el('div',{class:'card'},el('h3',{},'Research'),
   el('p',{class:'hint'},'Which campaigns research this squadron type (the research tree in level5 and level85), its cost in research points, the story mission that must be completed first (-1 = none) and the short label on its research node. Campaigns offer a squadron type in carrier slots only once it is researched. Added entries show in existing campaigns; “Unlocked at start” only applies to a new campaign. Saved with Save squadron changes.'),
   el('table',{class:'alloc'},el('thead',{},el('tr',{},...['Campaign','Cost (RP)','Mission','Label','Unlocked at start'].map(h=>el('th',{style:'text-align:left;font-weight:normal'},h)))),el('tbody',{},...rows)));
 }
 function munitionSection(q,ab,have){
  if(q.munitions==null)return el('p',{class:'hint'},'Munitions: this prefab has no squadron table entry.');
  const c=changes(q.name);const list=c.munitions?[...c.munitions]:[...q.munitions];
  const types=Object.fromEntries(data.munitionTypes.map(m=>[m.id,m]));
  const set=v=>{if(v.join()===q.munitions.join())delete c.munitions;else c.munitions=v;drawList();drawDetail()};
  const rows=list.map((m,i)=>el('div',{class:'row'},el('span',{},'Slot '+(i+1)+': '+(types[m]?.name||'munition '+m)),
   el('button',{class:'sec',disabled:i===0,onclick:()=>{const v=[...list];[v[i-1],v[i]]=[v[i],v[i-1]];set(v)}},'↑'),
   el('button',{class:'sec',onclick:()=>{set(list.filter((_,k)=>k!==i))}},'Remove')));
  const pick=el('select',{},...data.munitionTypes.filter(m=>m.faction===q.faction&&!list.includes(m.id)).map(m=>el('option',{value:m.id},m.name+(m.dlc>=0?' (DLC '+m.dlc+')':''))));
  const add=el('button',{class:'sec',disabled:!pick.options.length||list.length>=data.maxMunitions,title:'shipdb setsquadronmunitions TYPE ID,ID',onclick:()=>set([...list,Number(pick.value)])},'Add munition');
  const firing=have.has('fire_missile');
  return el('div',{style:'border-top:1px solid var(--line);margin-top:10px;padding-top:8px'},el('h3',{},'Munitions'),
   el('p',{class:'hint'},'The munition types the squadron spawns with (its squadron table entry “'+q.tableName+'”), one per slot. Each munition gets its own Fire munitions button (button 1 fires munition 1 …); saving adds or removes buttons to match, and adds the Fire munitions ability when the squadron has none. Removing the Fire munitions ability keeps this list. Only munitions of the squadron’s own faction are offered; the game swaps others for its default. If another squadron type uses the same fighter with a different number of munitions, saving gives this squadron its own copy of the fighter (ship-database row, factory entries, a bundle copy and a ShipType enum member), so their slot counts stay separate.'+(list.length&&!firing?' This squadron has no Fire munitions ability, so it cannot fire them: add it above.':'')),
   ...(rows.length?rows:[el('p',{class:'hint'},'No munitions.')]),
   el('div',{class:'row'},el('label',{},'Munition ',pick),add));
 }
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
   abilitiesCard(q),researchCard(q),baysCard(q),
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
 const views=el('div',{class:'row'},el('button',{class:'pri'},'Squadron prefabs'),el('button',{class:'sec',onclick:()=>loadAllocations()},'Carrier allocations'),el('button',{class:'sec',onclick:()=>loadJamming()},'Jamming effects'));
 box.replaceChildren(views,intro,formSlot,el('div',{class:'row',style:'align-items:flex-start;gap:16px'},list,detail));
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
 const views=el('div',{class:'row'},el('button',{class:'sec',onclick:()=>loadSquadrons()},'Squadron prefabs'),el('button',{class:'pri'},'Carrier allocations'),el('button',{class:'sec',onclick:()=>loadJamming()},'Jamming effects'));
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
async function loadJamming(message){
 const box=$('#tab-squadrons');box.replaceChildren(el('p',{class:'hint'},'Loading jamming effects…'));
 let data;try{data=await api('/api/jamming',{})}catch(e){box.replaceChildren(el('p',{class:'msg-bad'},e.message));return}
 const labels={speed:'Speed',evade:'Evasion',accuracy:'Accuracy'};
 const views=el('div',{class:'row'},el('button',{class:'sec',onclick:()=>loadSquadrons()},'Squadron prefabs'),el('button',{class:'sec',onclick:()=>loadAllocations()},'Carrier allocations'),el('button',{class:'pri'},'Jamming effects'));
 const status=el('span',{class:message?'msg-ok':'hint'},message||'');
 const glob=Object.fromEntries(data.keys.map(k=>[k,el('input',{type:'number',step:'any',value:data.global[k],style:'width:7em'})]));
 const rows=data.overrides.map(o=>({...o,values:{...o.values}}));
 const body=el('tbody',{});
 const name=r=>(r.kind==='squadron'?data.squadrons:data.ships).find(x=>x.id===r.id)?.name||('#'+r.id);
 function draw(){
  body.replaceChildren(...rows.map((r,i)=>el('tr',{},el('td',{},r.kind==='squadron'?'Squadron':'Ship'),el('td',{},name(r)+' ('+r.id+')'),
   ...data.keys.map(k=>{const inp=el('input',{type:'number',step:'any',value:r.values[k]??'',placeholder:'global '+data.global[k],style:'width:7em'});
     inp.onchange=()=>{if(inp.value==='')delete r.values[k];else r.values[k]=Number(inp.value)};return el('td',{},inp)}),
   el('td',{},el('button',{class:'sec',onclick:()=>{rows.splice(i,1);draw()}},'Remove')))));
 }
 const kind=el('select',{},el('option',{value:'squadron'},'Squadron type'),el('option',{value:'ship'},'Capital ship'));
 const who=el('select',{});
 const fill=()=>who.replaceChildren(...(kind.value==='squadron'?data.squadrons:data.ships).map(x=>el('option',{value:x.id},x.name+' ('+x.id+')')));
 kind.onchange=fill;fill();
 const add=el('button',{class:'sec',onclick:()=>{const id=Number(who.value);if(rows.some(r=>r.kind===kind.value&&r.id===id))return;rows.push({kind:kind.value,id,values:{}});draw()}},'Add jammer');
 const save=el('button',{class:'pri',onclick:async()=>{save.disabled=true;status.className='hint';status.textContent='Saving…';
  try{const r=await api('/api/jamming/save',{global:Object.fromEntries(data.keys.map(k=>[k,Number(glob[k].value)])),overrides:rows});
   await loadJamming('Saved. '+r.log.join(' | ')+(r.backup?' (backup: '+r.backup+')':''))}catch(e){status.className='msg-bad';status.textContent=e.message;save.disabled=false}}},'Save jamming effects');
 draw();
 box.replaceChildren(views,
  el('div',{class:'card'},el('h2',{},'Jamming effects'),
   el('p',{class:'hint'},'A jammer (the Wardriver, or a squadron or ship you gave Jamming) flags squadrons and ships in range as jammed. A jammed ship gets these penalties: speed (added to its maximum speed), evasion and accuracy (percentage points). Range and missile-jam chance are set per squadron in its Abilities card.'),
   el('div',{class:'row'},...data.keys.map(k=>el('label',{},'Global '+labels[k]+' ',glob[k]))),
   el('p',{class:'hint'},'Global = every jammer (the game’s DataTables.JammingPenalty, level5, level85, level9).')),
  el('div',{class:'card'},el('h3',{},'Per jammer'),
   el('p',{class:'hint'},'A squadron type, or a capital ship given Jamming (Ships tab → Abilities → Jamming), listed here jams with its own penalties; empty fields use the global value. '+(data.dllHasHook?'The work DLL has the CustomJamming hook.':'Needs the custom-module DLL with CustomJamming: the work DLL does not have it yet. Rebuild it (Save ship on a ship with custom modules) before playing.')),
   el('table',{class:'alloc'},el('thead',{},el('tr',{},...['Kind','Jammer',labels.speed,labels.evade,labels.accuracy,''].map(h=>el('th',{style:'text-align:left;font-weight:normal'},h)))),body),
   el('div',{class:'row'},kind,who,add)),
  el('div',{class:'row'},save,status));
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
   const target=el('select',{},[...data.abilities.filter(a=>!a.custom&&a.id>=3&&a.id<=20).map(a=>el('option',{value:a.id},a.effect+' — '+a.name)),el('option',{value:30},'Tylium per turn')]);
   const mode=el('select',{},el('option',{value:'flat'},'Flat amount'),el('option',{value:'percentage'},'Percentage'));
   const bonus=el('input',{type:'number',step:'any',value:0});
   const flagship=el('input',{type:'checkbox'});
   const syncEconomyFields=()=>{
    const economy=Number(target.value)===30;
    mode.disabled=flagship.disabled=economy;
    bonus.step=economy?'1':'any';
    bonus.min=economy?'0':'';
    if(economy){mode.value='flat';flagship.checked=false}
   };
   target.addEventListener('change',syncEconomyFields);
   syncEconomyFields();
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
    if(!bonus.reportValidity()||!Number.isFinite(bonus.valueAsNumber))return;
    const economy=targetId===30;
    const effect=economy?'Tylium per turn':data.abilities.find(a=>a.id===targetId).effect;
    const ability={id,name:name.value.trim(),effect:economy?effect:effect+' · '+kind,custom:true,target:targetId,mode:kind,unlockRank:1,
      titleText:name.value.trim(),descriptionText:desc.value.trim()||(economy?'Earns '+bonus.value+' Tylium each campaign turn while commanding a fleet.':'Adds '+bonus.value+' '+kind+' bonus to '+effect+'.'),
      titleId:0,descriptionId:0,displayType:kind==='percentage'?1:0,flagship:isFlagship,bonus:bonus.valueAsNumber};
    newAbilities.push({...ability});data.abilities.push(ability);abilityById[id]=ability;
    allocations[Number(subsystem.value)].push(id);drawTrees();drawAbilities();
    message.className='msg-ok';message.textContent='Added #'+id+' to '+subsystem.options[subsystem.selectedIndex].text+'. Save to write the new ability and build the work-tree DLL.';
    name.value='';bonus.value='0';desc.value='';
   }},'Add ability to selected subsystem');
   const field=(label,input)=>el('label',{class:'f'},label,input);
   return el('div',{class:'card'},el('h3',{},'Create a commander ability'),el('p',{class:'hint'},'Choose one of the game’s stat calculation paths. Flat bonuses add a value; percentage bonuses multiply the calculated value. A custom ability is assigned a unique effect ID and can then be placed in the tree.'),
    el('div',{class:'grid'},field('Ability name',name),field('Effect',target),field('Modifier',mode),field('Bonus amount',bonus),field('Subsystem branch',subsystem),field('Description',desc),el('label',{class:'f chk'},flagship,'Flagship only')),
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
