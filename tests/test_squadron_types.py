"""New squadron types, squadron abilities and squadron munitions, on the vanilla game files.

Needs tests/regression_inputs.json with "dll" (unpatched Assembly-CSharp.dll) and "levels" (the vanilla level folder),
plus "managed" (the game's Managed folder, for type trees) and "game_data" (a folder with globalgamemanagers.assets,
where the MonoScripts are), and the optional UnityPy asset support on the path. Skipped otherwise.

    python -m unittest tests.test_squadron_types -v
"""
import json, os, shutil, tempfile, types, unittest

from deadlock_tools import shipdb as sd, squadrons as sq, squadron_table as st

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS_FILE = os.path.join(HERE, "regression_inputs.json")
INPUTS = json.load(open(INPUTS_FILE)) if os.path.isfile(INPUTS_FILE) else {}
LEVEL_FILES = ("level5", "level6", "level7", "level8", "level85", "level9")


def missing():
    for key in ("dll", "levels", "managed", "game_data"):
        if not INPUTS.get(key) or not os.path.exists(INPUTS[key]):
            return "regression_inputs.json has no usable %r" % key
    if not os.path.isfile(os.path.join(INPUTS["game_data"], "globalgamemanagers.assets")):
        return "game_data has no globalgamemanagers.assets"
    try:
        from deadlock_tools.bundled_assets import load_asset_tools
        load_asset_tools()
    except ImportError:
        return "the optional UnityPy asset support is not installed"
    return None


SKIP = missing()


def objects(data):
    return {pid: data[off:off + size] for pid, off, size, _c, _m in sd.read_objects(data)}


@unittest.skipIf(SKIP, SKIP or "")
class SquadronTypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from deadlock_tools import squadron_types as T
        cls.T = T
        cls.S = sd.Schema(INPUTS["dll"])
        cls.asset_path = os.path.join(INPUTS["levels"], T.ASSET_FILE)
        cls.assets = open(cls.asset_path, "rb").read()
        cls.levels = {n: open(os.path.join(INPUTS["levels"], n), "rb").read() for n in LEVEL_FILES}

    def prefabs(self, data=None):
        return self.T.Prefabs(data or self.assets, INPUTS["managed"], INPUTS["game_data"])

    def test_stock_abilities(self):
        P = self.prefabs()
        got = {(a["key"], a["menu"], a["slot"]) for a in P.abilities("ColonialRaptorSquadron")}
        self.assertEqual(got, {("fire_missile", "main", 6), ("board", "abilities", 2), ("disarm", "abilities", 1),
                               ("boost_firewalls", "abilities", 3)})
        self.assertEqual(P.abilities("ColonialViperMkVIISquadron"), [])
        self.assertEqual(P.catalogue()["hack"], ["CylonWardriverSquadron"])
        self.assertEqual(P.root("Raptor")[0], "ColonialRaptorSquadron")       # short names pick the exact squadron

    def test_add_then_remove_restores_every_object(self):
        P = self.prefabs()
        P.add_ability("ColonialViperMkVIISquadron", "jamming")                # creates the Abilities submenu, copies Jammer
        P.add_ability("ColonialViperMkVIISquadron", "chaff", "ColonialSweeperSquadron")
        edited = P.save()
        Q = self.prefabs(edited)
        self.assertEqual([(a["key"], a["menu"]) for a in Q.abilities("ColonialViperMkVIISquadron")],
                         [("jamming", "abilities"), ("chaff", "abilities")])
        self.assertIsNotNone(Q.find_component(Q.roots["ColonialViperMkVIISquadron"], "Jammer"))
        Q.remove_ability("ColonialViperMkVIISquadron", "jamming")
        Q.remove_ability("ColonialViperMkVIISquadron", "chaff")              # last one: the submenu goes too
        back = Q.save()
        self.assertEqual(objects(back), objects(self.assets))
        R = self.prefabs(back)
        self.assertIsNone(R.menus("ColonialViperMkVIISquadron")[1])

    def test_removed_ids_are_never_reused(self):
        P = self.prefabs()
        P.add_ability("ColonialViperMkVIISquadron", "jamming")                # newest objects: the highest ids
        top = max(P.objects)
        P.remove_ability("ColonialViperMkVIISquadron", "jamming")             # removes them again
        P.add_ability("ColonialViperMkVIISquadron", "chaff")
        self.assertGreater(min(P.added), top)
        Q = self.prefabs(P.save())
        self.assertEqual([a["key"] for a in Q.abilities("ColonialViperMkVIISquadron")], ["chaff"])

    def test_stealth(self):
        P = self.prefabs()
        types, scripts = len(P.asset.types), len(P.asset.script_types)
        P.add_ability("ColonialViperMkVIISquadron", "stealth", settings={"battery": 40, "cooldown": 2})
        data = P.save()
        Q = self.prefabs(data)
        self.assertEqual((len(Q.asset.types), len(Q.asset.script_types)), (types + 2, scripts + 2))
        self.assertEqual(Q.stealth_settings("ColonialViperMkVIISquadron"), {"battery": 40.0, "cooldown": 2, "active": False})
        root = Q.roots["ColonialViperMkVIISquadron"]
        module = Q.find_component(root, "StealthModule")
        fields = Q.read(module)                                               # reads back exactly (check_read)
        for effect in ("stealthEffect", "changeStealthEffect"):
            go = fields[effect]["m_PathID"]
            self.assertIn(go, Q.children(root))
            self.assertEqual(Q.read(go)["m_IsActive"], 0)
        typ = Q.objects[module].serialized_type
        from deadlock_tools.weapons import _md4
        self.assertEqual(typ.script_id, _md4(b"StealthModuleAssembly-CSharp.dll"))
        self.assertEqual(typ.old_type_hash.hex(), "3ec6f51aed5e015479579e274f2d2aee")    # MonoScript m_PropertiesHash
        self.assertEqual([(a["key"], a["menu"]) for a in Q.abilities("ColonialViperMkVIISquadron")], [("stealth", "abilities")])
        old = objects(self.assets)
        self.assertEqual({p: b for p, b in objects(data).items() if p in old and p not in Q.tree(root)},
                         {p: b for p, b in old.items() if p not in Q.tree(root)})
        Q.remove_ability("ColonialViperMkVIISquadron", "stealth")
        self.assertEqual(objects(Q.save()), old)                               # module, action, effects and menu gone

    def test_remove_keeps_shared_helpers(self):
        P = self.prefabs()
        P.remove_ability("CylonWardriverSquadron", "jamming")
        root = P.roots["CylonWardriverSquadron"]
        self.assertIsNone(P.find_component(root, "Jammer"))                   # only Jamming used it
        P.remove_ability("CylonWardriverSquadron", "disarm")
        self.assertIsNotNone(P.find_component(root, "DisarmAgent"))           # stock squadrons keep it without the action
        self.assertEqual([a["key"] for a in P.abilities("CylonWardriverSquadron")], ["hack"])

    def test_full_main_menu_moves_an_ability_into_a_new_submenu(self):
        P = self.prefabs()
        line = P.add_ability("ColonialAssaultRaptorSquadron", "disarm")   # main slots 0-7 taken (Fire munitions 6, Paint 7)
        self.assertIn("moved Paint target from main slot 7", line)
        Q = self.prefabs(P.save())
        actions, sub, opener = Q.menus("ColonialAssaultRaptorSquadron")
        self.assertEqual(Q.read(opener)["radialMenuPosition"], 7)
        self.assertEqual(sorted((a["key"], a["menu"], a["slot"]) for a in Q.abilities("ColonialAssaultRaptorSquadron")),
                         [("disarm", "abilities", 2), ("fire_missile", "main", 6), ("paint_target", "abilities", 1)])
        Q.remove_ability("ColonialAssaultRaptorSquadron", "disarm")              # the submenu keeps Paint target
        self.assertIsNotNone(Q.menus("ColonialAssaultRaptorSquadron")[1])

    def test_create_type(self):
        T, S = self.T, self.S
        P = self.prefabs()
        type_id = T.next_type_id(S, P, self.levels)
        self.assertEqual(type_id, 115)
        changed, _log = T.create_type(S, P, self.levels, "ColonialRaptorSquadron", "ColonialRaptorMk2Squadron",
                                      "raptor mk2", type_id, {"PointValue": 55})
        self.assertEqual(set(changed), set(LEVEL_FILES))
        data = P.save()
        Q = self.prefabs(data)
        root = Q.roots["ColonialRaptorMk2Squadron"]
        self.assertEqual(Q.squadron_type("ColonialRaptorMk2Squadron"), 115)
        donor_tree = set(Q.tree(Q.roots["ColonialRaptorSquadron"]))
        new_tree = Q.tree(root)
        self.assertEqual(len(new_tree), len(donor_tree))
        for pid in new_tree:                                                   # the copy never points into the donor
            self.assertFalse(set(T._pointers(Q.read(pid))) & donor_tree, Q.kind(pid))
        self.assertEqual([a["key"] for a in Q.abilities("ColonialRaptorMk2Squadron")],
                         [a["key"] for a in Q.abilities("ColonialRaptorSquadron")])
        # every original object is unchanged; the Reader-based squadron editor reads the copy
        old = objects(self.assets)
        self.assertEqual({p: b for p, b in objects(data).items() if p in old}, old)
        self.assertIn("ColonialRaptorMk2Squadron", [q["name"] for q in sq.load(S, data)])
        for name in LEVEL_FILES:
            res = st.find(S, changed[name])[3]
            new, donor = st.entry(res, 115), st.entry(res, 2)
            self.assertEqual(new["name"], "raptor mk2")
            self.assertEqual(new["PointValue"], 55)
            self.assertEqual({k: v for k, v in new.items() if k not in ("_off", "name", "id", "PointValue")},
                             {k: v for k, v in donor.items() if k not in ("_off", "name", "id", "PointValue")})
        _b, _o, factory = sd.parse_factory(types.SimpleNamespace(S=S), changed["level9"], "SquadronFactory")
        entry = next(e for e in factory["objects"] if e["id"] == 115)
        self.assertEqual(entry["prefab"][1], (6, root))
        with self.assertRaises(ValueError):
            Q.clone_squadron("ColonialRaptorSquadron", "ColonialRaptorMk2Squadron", 116)   # name taken

    def test_new_type_gets_launch_bays(self):
        T, S = self.T, self.S
        bundle = INPUTS.get("mercury_enc")
        if not bundle or not os.path.isfile(bundle):
            self.skipTest("regression_inputs.json has no 'mercury_enc'")
        levels = {n: open(os.path.join(INPUTS["levels"], n), "rb").read()
                  for n in os.listdir(INPUTS["levels"]) if n.startswith("level") and not n.endswith(".resS")}
        P = self.prefabs()
        changed, _log = T.create_type(S, P, levels, "AssaultRaptor", "ColonialHeavyAssaultRaptorSquadron", "heavy", 115)
        levels.update(changed)
        self.assertIn((29, "Colonial Mercury", "colonialmercury"), T.launch_carriers(S, levels, 115))
        self.assertEqual(T.guess_like(S, levels, P, "ColonialHeavyAssaultRaptorSquadron"), 4)
        jobs, _log = T.plan_launch_bays(S, levels, 115, 4, lambda name: bundle if name == "colonialmercury" else None)
        (asset, src, spec), = jobs
        from deadlock_tools import bays
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, asset)
            bays.apply(src, out, spec)
            lists = [b["compat"] for b in bays.read_layout(out)["bays"]]
            self.assertTrue(lists and all((115 in c) == (4 in c) for c in lists))   # exactly the bays that take type 4
            self.assertIsNone(T.bay_spec(out, [4], 115))                          # nothing left to do

    def test_abilities_follow_in_the_table(self):
        T, S = self.T, self.S
        P = self.prefabs()
        changed, _log = T.edit_abilities(S, P, self.levels, "ColonialViperMkVIISquadron",
                                         add=[("fire_missile", "ColonialAssaultRaptorSquadron")])
        for name in LEVEL_FILES:
            viper = st.entry(st.find(S, changed[name])[3], 113)
            self.assertEqual(viper["AbilityIDs"], [232])                       # "Fire Munitions"
            self.assertEqual(viper["MunitionSlots"], [20])                     # the donor's munition
        fighter = next(s for s in T._ship_table(S, changed["level5"])[2]["objects"] if s["id"] == 31)
        self.assertEqual(fighter["MunitionSlots"], 1)
        Q = self.prefabs(P.save())
        self.assertTrue(any(Q.kind(p) == "MissileLauncher" for p in Q.tree(Q.roots["ColonialViperMkVIISquadron"])))

    def test_one_fire_button_per_munition(self):
        T, S = self.T, self.S
        P = self.prefabs()
        changed, _log = T.set_munitions(S, self.levels, "assault raptor", [20, 4])
        log = P.sync_fire_buttons("ColonialAssaultRaptorSquadron", 2)
        self.assertTrue(any("added 1 Fire munitions button" in line for line in log))
        Q = self.prefabs(P.save())
        menus = [Q.read(Q.read(b)["m_GameObject"]["m_PathID"])["m_Name"] for b in Q.fire_buttons("ColonialAssaultRaptorSquadron")]
        self.assertEqual(menus, ["Actions", "Abilities"])                     # stock button first: it fires munition 1
        Q.sync_fire_buttons("ColonialAssaultRaptorSquadron", 1)
        self.assertEqual(len(Q.fire_buttons("ColonialAssaultRaptorSquadron")), 1)
        self.assertEqual([a["key"] for a in Q.abilities("ColonialAssaultRaptorSquadron")], ["fire_missile", "paint_target"])
        # munitions but no button: the ability (and a launcher) is added
        line = Q.sync_fire_buttons("ColonialViperMkVIISquadron", 1)
        self.assertEqual(len(Q.fire_buttons("ColonialViperMkVIISquadron")), 1)
        self.assertTrue(any("MissileLauncher" in l for l in line))

    def test_ability_settings(self):
        P = self.prefabs()
        self.assertEqual(P.ability_settings("ColonialSweeperSquadron")["chaff"]["maxStock"], 10)
        line = P.set_ability_settings("ColonialSweeperSquadron", "chaff", {"maxStock": 25, "releaseInterval": "60"})
        self.assertIn("maxStock 10 -> 25", line)
        self.assertIsNone(P.set_ability_settings("ColonialSweeperSquadron", "chaff", {"maxStock": 25}))   # no change
        for bad in ({"maxStock": 2.5}, {"maxStock": -1}, {"nope": 1}):
            with self.assertRaises(ValueError):
                P.set_ability_settings("ColonialSweeperSquadron", "chaff", bad)
        with self.assertRaises(ValueError):
            P.set_ability_settings("CylonWardriverSquadron", "hack", {"HackStrength": 2})
        with self.assertRaises(ValueError):
            P.set_ability_settings("ColonialViperMkVIISquadron", "chaff", {"maxStock": 5})       # no chaff there
        Q = self.prefabs(P.save())
        self.assertEqual(Q.ability_settings("ColonialSweeperSquadron")["chaff"],
                         {"maxStock": 25, "releaseInterval": 60.0, "minRangeFromPreviousClouds": 50.0})

    def test_munitions(self):
        T, S = self.T, self.S
        changed, log = T.set_munitions(S, self.levels, "raptor", [12, 20])
        res = st.find(S, changed["level5"])[3]
        self.assertEqual(st.entry(res, 2)["MunitionSlots"], [12, 20])
        self.assertTrue(any(line.startswith("WARNING") for line in log))      # single raptor shares fighter 9
        fighter = next(s for s in T._ship_table(S, changed["level5"])[2]["objects"] if s["id"] == 9)
        self.assertEqual(fighter["MunitionSlots"], 1)                          # left alone while the types disagree
        changed, log = T.set_munitions(S, self.levels, 113, [0, 12])
        fighter = next(s for s in T._ship_table(S, changed["level9"])[2]["objects"] if s["id"] == 31)
        self.assertEqual(fighter["MunitionSlots"], 2)
        with self.assertRaises(ValueError):
            T.set_munitions(S, self.levels, "raptor", [1])                     # a Cylon munition
        same, log = T.set_munitions(S, self.levels, "raptor", [12])
        self.assertEqual(same, {})

    def test_munitions_copy_a_shared_fighter(self):
        T, S = self.T, self.S
        bundle = INPUTS.get("raptor_enc")
        if not bundle or not os.path.isfile(bundle):
            self.skipTest("regression_inputs.json has no 'raptor_enc' (the vanilla colonialraptor bundle)")
        levels = {n: open(os.path.join(INPUTS["levels"], n), "rb").read()
                  for n in os.listdir(INPUTS["levels"]) if n.startswith("level") and not n.endswith(".resS")}
        P = self.prefabs()
        changed, _log = T.create_type(S, P, levels, "Raptor", "ColonialRaptorMk2Squadron", "raptor mk2", 115)
        levels.update(changed)
        self.assertTrue(T.needs_own_fighter(S, levels, 115, [12, 20]))
        self.assertFalse(T.needs_own_fighter(S, levels, 115, [20]))          # same count as the Raptor: no copy
        out, bundles, enum, _log = T.munitions_with_fighter(S, P, levels, "RaptorMk2", [12, 20],
                                                            lambda name: open(bundle, "rb").read())
        levels.update(out)
        ship_id = max(enum)
        self.assertEqual(enum, {ship_id: "CustomShip%d" % ship_id})
        self.assertEqual(P.fighter("ColonialRaptorMk2Squadron"), ship_id)
        self.assertEqual(P.fighter("ColonialRaptorSquadron"), 9)
        for name, data in levels.items():
            found = T._ship_table(S, data)
            if not found:
                continue
            rows = {s["id"]: s for s in found[2]["objects"]}
            self.assertEqual(rows[9]["MunitionSlots"], 1, name)
            self.assertEqual(rows[ship_id]["MunitionSlots"], 2, name)
            table = st.find(S, data)
            if table:
                self.assertEqual(st.entry(table[3], 115)["ShipType"], ship_id)
                self.assertEqual(st.entry(table[3], 2)["ShipType"], 9)
        _b, _o, factory = sd.parse_factory(types.SimpleNamespace(S=S), levels["level9"], "ShipFactory")
        new_bundle = next(e for e in factory["objects"] if e["id"] == ship_id)["assetName"]
        self.assertEqual(list(bundles), [new_bundle])
        from deadlock_tools import weapons as W
        self.assertNotEqual(W.parse_bundle(bundles[new_bundle])["nodes"][0][3], W.parse_bundle(open(bundle, "rb").read())["nodes"][0][3])
        again, bundles2, enum2, _log = T.munitions_with_fighter(S, P, levels, "RaptorMk2", [12, 20, 0], lambda n: b"")
        self.assertEqual((bundles2, enum2), ({}, {}))                          # its own fighter now: no second copy

    @unittest.skipIf(not shutil.which("mcs") or not shutil.which("mono"), "needs mono and mcs")
    def test_squadron_enum_member(self):
        from deadlock_tools import ship_types as stypes
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "Assembly-CSharp.dll")
            r = stypes.add(INPUTS["managed"], INPUTS["dll"], out, {115: "ColonialRaptorMk2Squadron"}, tmp, squadron=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(stypes.members(out, squadron=True)[115], "ColonialRaptorMk2Squadron")
            self.assertEqual(stypes.members(out), stypes.members(INPUTS["dll"]))  # ShipType untouched


if __name__ == "__main__":
    unittest.main()
