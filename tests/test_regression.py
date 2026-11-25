"""Byte-for-byte regression tests against outputs recorded from the tools before the reorganisation.

They need game files, so they are skipped unless tests/regression_inputs.json exists (copy
tests/regression_inputs.example.json and fill in your paths). Each input is checked against the hash it had when the
expected outputs were recorded; a different input file skips that test instead of failing it.
"levels" must be the vanilla level folder (GOG 1.5.113, identical to Steam's; BSG/Battlestar.Galactica.Deadlock.zip has
the originals): every level file is checked, so installing edited levels into it skips the shipdb tests (keep a clean
copy for the tests). The bundles are the vanilla ones too: mercury_enc is the game file, mercury_dec its decryption.

    python -m unittest tests.test_regression -v
"""
import hashlib, json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
INPUTS_FILE = os.path.join(HERE, "regression_inputs.json")
INPUT_SHA1 = {   # the input files the expected hashes were made from
    "dll": "f036ae751809d34e257b7bbc325c6f7f8251b49e",            # unpatched Assembly-CSharp.dll
    "level0": "c4ed9359355b5a5f39a1872ca66467a8ff74603c",         # <levels>/level0 .. level97 (vanilla GOG 1.5.113 = Steam)
    "mercury_dec": "7691029324507a1b1a416ccea180444be6d48796",    # decrypted vanilla colonialmercury.dec
    "orion_dec": "0c2d95780c6478a2f15b3439c25d9ac7ca6fbf7d",      # decrypted colonialorion.dec
    "mercury_enc": "3d3fa51f2b719e9aafec13121a7cf7c50fa78ae2",    # encrypted vanilla colonialmercury (game file)
}
LEVEL_SHA1 = {   # every level file the shipdb tests read; "level0" in need() checks all of them (vanilla GOG 1.5.113 = Steam)
    "level0": "c4ed9359355b5a5f39a1872ca66467a8ff74603c", "level1": "1305ba1cfce58f689c974cafec33c0eeb0612b27",
    "level3": "1bc6f987db51de9d4d25c75246385b1d2a2514fa", "level5": "17c189e4130683e15c5e453d17a92270ff2f9881",
    "level6": "375f01eeeecdec8ed89f800089d07280568d2e10", "level7": "fd9526ca283a0308a63d12f001ab0151a777270b",
    "level8": "55b2a32dd803c081523e29a91af22bf77614a5bc", "level85": "0833e12ab08d59d6147a00c524dddc3c718dc5cc",
    "level9": "7694966687618c543a85d44965f34d32b5eb2c10", "level97": "de9b19fcd31c4e16b410fcefb69c779f2ae86635",
}
SET_LEVELS = {"level0": "577c611cb6101fc7fa17b5e80c4955e5754999d7", "level1": "ad734e393e5dbec33307cf1fb40da0253c05298e", "level3": "b1289e651559d8b2c0a7c024ba76ece0e8a7260a",
              "level5": "a17a3e4304e6eaa2940841ce9e3fa67fa27d0427", "level6": "15ff9964b7e2e58339a1738c5c73a65c44232236", "level7": "c5ddeaecb1506de11adaf012b000b97050216986",
              "level8": "890a436a353ae41b27c619e5ec3a0edc28f6a206", "level85": "58a84d82e321c666ea395303bd13958fed7a1d88", "level9": "dca72937cd1c86127cec019671e9c3d195a4bc52",
              "level97": "25c872ef3710f8bb9bc9d0f742a896f13af10054"}
CMD_LEVELS = {"level5": "a6c9e0c4143e9355d2df8fe33341654a81c95916", "level85": "cda79dc76d156b9b51d3f8af34858e92978cce2e", "level9": "f714b8da41eb284211a759c6178497ec2d92e779"}


def sha1(path):
    with open(path, "rb") as fh: return hashlib.sha1(fh.read()).hexdigest()


def load_inputs():
    if not os.path.isfile(INPUTS_FILE): return None
    with open(INPUTS_FILE) as fh: return json.load(fh)


INPUTS = load_inputs()


@unittest.skipIf(INPUTS is None, "no tests/regression_inputs.json")
class Regression(unittest.TestCase):
    def need(self, *keys):
        for k in keys:
            # "level0" stands for the whole levels folder: a modded level elsewhere changes the outputs too
            checks = [(os.path.join(INPUTS["levels"], name), h, "levels/" + name) for name, h in LEVEL_SHA1.items()] \
                if k == "level0" else [(INPUTS.get(k), INPUT_SHA1[k], k)]
            for path, expected, label in checks:
                if not path or not os.path.isfile(path) or sha1(path) != expected:
                    self.skipTest("input %s is missing or not the recorded file" % label)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        env = dict(os.environ, PYTHONPATH=ROOT)
        r = subprocess.run([sys.executable, "-m", "deadlock_tools", *args], capture_output=True, text=True, cwd=ROOT, env=env, timeout=900)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def assertHash(self, path, expected):
        self.assertTrue(os.path.isfile(path), path); self.assertEqual(sha1(path), expected, path)

    def test_dll_all_patches_in_one_run_and_stacked(self):
        self.need("dll")
        # The legacy armour-regen / squadron-revive patches were removed, so the old recorded
        # four-patch hash no longer applies: one run and stacked runs must agree instead.
        self.run_tool("dll", "patch", INPUTS["dll"], "--max-rank", "20", "--command-nodes", "10", "-o", self.out)
        single = sha1(os.path.join(self.out, "dll", os.path.basename(INPUTS["dll"])))
        self.assertNotEqual(single, INPUT_SHA1["dll"])
        stacked = os.path.join(self.out, "stacked")
        for opt in (["--max-rank", "20"], ["--command-nodes", "10"]):
            self.run_tool("dll", "patch", INPUTS["dll"], *opt, "-o", stacked)
        self.assertHash(os.path.join(stacked, "dll", os.path.basename(INPUTS["dll"])), single)
        self.run_tool("dll", "revert", INPUTS["dll"], "-o", stacked)
        self.assertHash(os.path.join(stacked, "dll", os.path.basename(INPUTS["dll"])), INPUT_SHA1["dll"])

    def test_dll_munition_load(self):
        self.need("dll")
        self.run_tool("dll", "patch", INPUTS["dll"], "--munition-load", "-o", self.out)
        self.assertHash(os.path.join(self.out, "dll", os.path.basename(INPUTS["dll"])), "4547e6ad4fde670ad487dc4a0d714296971d6131")

    def test_xor_round_trip(self):
        self.need("mercury_enc", "mercury_dec")
        # vanilla game file -> the vanilla .dec input, and back to the game file byte for byte
        self.run_tool("xor", "decrypt", INPUTS["mercury_enc"], "-o", self.out)
        self.assertHash(os.path.join(self.out, "decrypted", os.path.basename(INPUTS["mercury_enc"]) + ".dec"), INPUT_SHA1["mercury_dec"])
        self.run_tool("xor", "encrypt", INPUTS["mercury_dec"], "-o", self.out)
        self.assertHash(os.path.join(self.out, "bundles", "colonialmercury"), INPUT_SHA1["mercury_enc"])

    def test_shipdb_reads(self):
        self.need("dll", "level0")
        p = ["--levels", INPUTS["levels"], "--dll", INPUTS["dll"], "-o", self.out]       # empty output folder: read the originals
        for args, expected in ((["list"], "243d68d94ffff5d54f6a4800de69bc0610bc53aa"), (["show", "viper mk1"], "66c8c43e07174ed6fd0d42c8268ba83cf445a826"),
                               (["munitions"], "0dd12d52e83fb0c2e9539151068dd0d6ba2e8b81"), (["fleetinfo"], "493b541542187bb41a437f84a72a46026cd51502")):
            self.assertEqual(hashlib.sha1(self.run_tool("shipdb", *args, *p).encode()).hexdigest(), expected, args)

    def test_shipdb_edits_stack(self):
        self.need("dll", "level0")
        p = ["--levels", INPUTS["levels"], "--dll", INPUTS["dll"], "-o", self.out]
        self.run_tool("shipdb", "set", "Colonial Mercury", "MunitionSlots", "4", *p)
        self.run_tool("shipdb", "setxp", "4", "100", *p)
        for name, h in SET_LEVELS.items(): self.assertHash(os.path.join(self.out, "levels", name), h)

    def test_editor_reads_staged_database_values(self):
        self.need("dll", "level0")
        from deadlock_tools.editor import Project

        project = Project(INPUTS["levels"], INPUTS["dll"], self.out, "")
        edits = {"name": "colonial guided", "settings": "MaxMissiles 123"}
        result = project.run_tool("setmunition", edits)
        self.assertTrue(result["ok"], result["output"])
        for fresh in (False, True):
            result = project.run_tool("munitions", {"name": edits["name"]}, fresh=fresh)
            self.assertTrue(result["ok"], result["output"])
            self.assertEqual("MaxMissiles 123" in result["output"], not fresh)
        result = project.run_tool("fleeticons", {"factory": "all"})
        self.assertTrue(result["ok"], result["output"])

    def test_shipdb_setcommanders(self):
        self.need("dll", "level0")
        self.run_tool("shipdb", "setcommanders", "--add", "10", "--every-level", "--levels", INPUTS["levels"], "--dll", INPUTS["dll"], "-o", self.out)
        for name, h in CMD_LEVELS.items(): self.assertHash(os.path.join(self.out, "levels", name), h)

    def test_align_launch_bays_and_move_icon(self):
        from deadlock_tools.weapons import load_support_donor

        self.need("mercury_dec")
        self.run_tool("weapons", INPUTS["mercury_dec"], "-o", self.out, "--no-encrypt",
                      "--fighter-slots", "5", "--utility-slots", "1", "--align-launch-bays",
                      "--bay-icon", "2=10,20,30", "--bay-icon", "5=-10,20,30")
        bundle = os.path.join(self.out, "bundles", "colonialmercury.dec")
        file, components = load_support_donor(bundle)
        bays = {fields["SlotNo"]: fields for fields in (file.read(obj)[0] for obj in components["LaunchPoint"])
                if fields["CanLaunch"]}
        self.assertEqual(set(bays), set(range(6)))
        for slot in range(5):
            self.assertIn(113, bays[slot]["LaunchCompatibility"])
            self.assertNotIn(2, bays[slot]["LaunchCompatibility"])
        self.assertIn(2, bays[5]["LaunchCompatibility"])
        for slot, x in ((2, 10), (5, -10)):
            icon = file.by_pid[bays[slot]["iconPosition"]["m_PathID"]]
            self.assertEqual(file.read(icon)[0]["m_LocalPosition"], {"x": x, "y": 20, "z": 30})

    def test_cloned_bundle_gets_its_own_cab_names(self):
        # Unity will not load two bundles that share a CAB name; the game hung loading a copied Mercury
        import re
        from deadlock_tools import weapons as W
        self.need("mercury_dec")
        raw = open(INPUTS["mercury_dec"], "rb").read()
        donor = W.parse_bundle(raw)
        old = {re.match(r"CAB-[0-9a-f]{32}", n[3]).group(0) for n in donor["nodes"]}
        out = W.rename_bundle_asset(raw, "colonialmercurymk2", 999)
        self.assertEqual(out, W.rename_bundle_asset(raw, "colonialmercurymk2", 999))   # reproducible
        clone = W.parse_bundle(out)
        names = [n[3] for n in clone["nodes"]]
        self.assertEqual(len(names), len(donor["nodes"]))
        for name in names: self.assertNotIn(name.split(".")[0], old)
        self.assertFalse(any(o.encode() in clone["data"] for o in old), "donor CAB name left inside the clone")
        cab = names[0]; self.assertIn(cab + ".resS", names)
        off, size, _f, _n = clone["nodes"][0]
        serialized = W.SerFile(clone["data"][off:off + size])
        streamed = {serialized.read(o)[0]["m_StreamData"]["path"] for o in serialized.objs if o.classid in (28, 43)} - {""}
        self.assertEqual(streamed, {"archive:/%s/%s.resS" % (cab, cab)})
        other = W.parse_bundle(W.rename_bundle_asset(raw, "colonialmercurymk3", 1000))
        self.assertNotEqual(other["nodes"][0][3], cab)

    def test_support_abilities_preserve_prefab_links(self):
        from pathlib import Path
        from deadlock_tools.editor import Project
        from deadlock_tools.weapons import SUPPORT_ABILITIES, load_support_donor

        self.need("mercury_dec")
        mercury = Path(INPUTS["mercury_dec"])
        donor_folder = mercury.parent
        donor_paths = {name: donor_folder / ("colonial" + name + ".dec")
                       for name in ("defender", "celestra")}
        if not all(path.is_file() for path in donor_paths.values()):
            # the donors are picked automatically from the bundle folder (--bundles below), no --*-donor flags
            self.skipTest("colonialdefender.dec and colonialcelestra.dec must be alongside the Mercury fixture")

        from types import SimpleNamespace
        fields = {entry["dest"] for entry in Project.bundle_form(SimpleNamespace(bundle_files=list))}
        self.assertTrue(SUPPORT_ABILITIES.keys() <= fields)
        initial = os.path.join(self.out, "initial")
        self.run_tool("weapons", str(mercury), "-o", initial, "--no-encrypt",
                      "--mini-jump", "--ability-submenu")
        source = os.path.join(initial, "bundles", "colonialmercury.dec")
        self.run_tool("weapons", source, "--bundles", str(donor_folder), "-o", self.out,
                      "--no-encrypt", "--ability-submenu", "--fighter-slots", "5",
                      "--utility-slots", "1", "--split-squadrons",
                      *("--" + ability.replace("_", "-") for ability in SUPPORT_ABILITIES))
        result = os.path.join(self.out, "bundles", "colonialmercury.dec")
        prefab, components = load_support_donor(result)
        celestra, donor_components = load_support_donor(str(donor_paths["celestra"]))

        def owner(obj):
            ref = prefab.read(obj)[0]["m_GameObject"]
            self.assertEqual(ref["m_FileID"], 0)
            go = prefab.read(prefab.by_pid[ref["m_PathID"]])[0]
            self.assertIn(obj.pid, [entry["component"]["m_PathID"] for entry in go["m_Component"]])
            return go["m_Name"]

        for ability, (module, action, _) in SUPPORT_ABILITIES.items():
            self.assertEqual([owner(obj) for obj in components[module]], ["ColonialMercury"])
            expected_menu = {
                "turnaround_repair_bay": ["Fighters"],
                "targeted_resupply": ["RestockMunitions", "RestockMunitions"],
            }.get(ability, ["Abilities"])
            self.assertEqual([owner(obj) for obj in components[action]], expected_menu)
        self.assertEqual([owner(obj) for obj in components["MiniJumpAction"]], ["Abilities"])
        abilities = [obj for obj in prefab.objs
                     if obj.classid == 1 and prefab.read(obj)[0]["m_Name"] == "Abilities"]
        self.assertEqual(len(abilities), 1)
        resupply = [prefab.read(obj)[0] for obj in components["ResupplyMunitionAction"]]
        self.assertEqual([obj["radialMenuPosition"] for obj in resupply], [2, 3])
        restock_go = resupply[0]["m_GameObject"]
        submenu, = [obj for obj in components["SubMenuAction"]
                    if prefab.read(obj)[0]["SubMenu"] == restock_go]
        self.assertEqual(owner(submenu), "Abilities")
        plating = prefab.read(components["DeployPlatingModule"][0])[0]
        donor_plating = celestra.read(donor_components["DeployPlatingModule"][0])[0]
        self.assertEqual(plating["droneTemplate"], donor_plating["droneTemplate"])
        bundle, = [obj for obj in prefab.objs if obj.classid == 142]
        metadata = prefab.read(bundle)[0]
        container, = [entry["second"] for entry in metadata["m_Container"]]
        start = container["preloadIndex"]
        self.assertIn(plating["droneTemplate"],
                      metadata["m_PreloadTable"][start:start + container["preloadSize"]])

        self.assertTrue(any(fields["CanDock"] and 0 in fields["LaunchCompatibility"]
                            for fields in (prefab.read(obj)[0] for obj in components["LaunchPoint"])))

    def test_weapons_everything(self):
        self.need("mercury_dec", "orion_dec")
        out = self.run_tool("weapons", INPUTS["mercury_dec"], "--list-mounts")
        self.assertEqual(hashlib.sha1(out.encode()).hexdigest(), "b851f19c648b7d7a40d7f44f48e1f3a2db608453")
        self.run_tool("weapons", INPUTS["mercury_dec"], "-o", self.out, "--clone", "fore=3", "--layout", "free", "--scale", "fore=0.7", "--clone-scale", "0.7",
                      "--fighter-slots", "5", "--utility-slots", "1", "--munition-slots", "4", "--hull-regen", "0.5", "--munition-factory",
                      "--mini-jump", "--augury", "--stealth", INPUTS["orion_dec"], "--ability-submenu")
        self.assertHash(os.path.join(self.out, "bundles", "colonialmercury"), "7bfb3342b1c11de220014c5e2fdaaacd25e808ac")   # from the vanilla Mercury (recorded 2026-11-25)


if __name__ == "__main__":
    unittest.main()
