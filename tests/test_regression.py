"""Byte-for-byte regression tests against outputs recorded from the tools before the reorganisation.

They need game files, so they are skipped unless tests/regression_inputs.json exists (copy
tests/regression_inputs.example.json and fill in your paths). Each input is checked against the hash it had when the
expected outputs were recorded; a different input file skips that test instead of failing it.

    python -m unittest tests.test_regression -v
"""
import hashlib, json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
INPUTS_FILE = os.path.join(HERE, "regression_inputs.json")
INPUT_SHA1 = {   # the input files the expected hashes were made from
    "dll": "f036ae751809d34e257b7bbc325c6f7f8251b49e",            # unpatched Assembly-CSharp.dll
    "level0": "ee0a41809d2ed1958c24d62b2634a6e5f501769f",         # <levels>/level0
    "mercury_dec": "0d15aa1ca98fde9e80bde2bd53547d94f3ab79a2",    # decrypted colonialmercury.dec
    "orion_dec": "0c2d95780c6478a2f15b3439c25d9ac7ca6fbf7d",      # decrypted colonialorion.dec
    "mercury_enc": "a11060eaa9200e202619772e413155ade96e630b",    # encrypted colonialmercury (game file)
}
SET_LEVELS = {"level0": "86b9870f9ef6df8379ce01207a44bd524791863c", "level1": "137df9bf81ff14ab3a0bdfd920b8c2a3f1826ee1", "level3": "df25e4cc27f30aab4a496471747f4d90c86f874c",
              "level5": "06e67d22bac85f2ff418c9b4d59e98a23284ed32", "level6": "b3b37161adf77f2bec048e89880e2c5fc5067c77", "level7": "ec4f97265f5067cfb5bf9ff3cda8c4e74de6b954",
              "level8": "970edce93414624c2f50b35a464cd76d8c7887fc", "level85": "2a479b34e2df93194d1bec1f508dd6d14ce6f126", "level9": "e986a2b9dfc770b87678f2bf5a8a108e46920706",
              "level97": "f6f286efddf61df8eeefb855e283d5b53acc5937"}
CMD_LEVELS = {"level5": "835219def95d67826be35876b4e5d756cb6c14f9", "level85": "78e30f486046edd5ada9fc8097f1b0ecdc771953", "level9": "72bf904cc3615af8fba6566be2faf08274d9cb18"}


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
            path = os.path.join(INPUTS["levels"], "level0") if k == "level0" else INPUTS.get(k)
            if not path or not os.path.isfile(path) or sha1(path) != INPUT_SHA1[k]:
                self.skipTest("input %s is missing or not the recorded file" % k)

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
        self.run_tool("dll", "patch", INPUTS["dll"], "--armour-factor", "0.1", "--revive-turns", "0", "--max-rank", "20", "--command-nodes", "10", "-o", self.out)
        self.assertHash(os.path.join(self.out, "dll", os.path.basename(INPUTS["dll"])), "52190e7e9d4ee6251c7fc6edfc986ec702944d43")
        stacked = os.path.join(self.out, "stacked")
        for opt in (["--armour-factor", "0.1"], ["--revive-turns", "0"], ["--max-rank", "20"], ["--command-nodes", "10"]):
            self.run_tool("dll", "patch", INPUTS["dll"], *opt, "-o", stacked)
        self.assertHash(os.path.join(stacked, "dll", os.path.basename(INPUTS["dll"])), "52190e7e9d4ee6251c7fc6edfc986ec702944d43")
        self.run_tool("dll", "revert", INPUTS["dll"], "-o", stacked)
        self.assertHash(os.path.join(stacked, "dll", os.path.basename(INPUTS["dll"])), INPUT_SHA1["dll"])

    def test_dll_munition_load(self):
        self.need("dll")
        self.run_tool("dll", "patch", INPUTS["dll"], "--munition-load", "-o", self.out)
        self.assertHash(os.path.join(self.out, "dll", os.path.basename(INPUTS["dll"])), "4547e6ad4fde670ad487dc4a0d714296971d6131")

    def test_xor_round_trip(self):
        self.need("mercury_enc", "mercury_dec")
        self.run_tool("xor", "decrypt", INPUTS["mercury_enc"], "--work", self.out)
        self.assertHash(os.path.join(self.out, "decrypted", os.path.basename(INPUTS["mercury_enc"]) + ".dec"), "18e27ea2fd67b9b478a89992b15f2f1b624a013c")
        self.run_tool("xor", "encrypt", INPUTS["mercury_dec"], "-o", self.out)
        self.assertHash(os.path.join(self.out, "bundles", "colonialmercury"), "66a80bb4bd895d0d47163b047b84c1c7465b7d68")

    def test_shipdb_reads(self):
        self.need("dll", "level0")
        p = ["--levels", INPUTS["levels"], "--dll", INPUTS["dll"], "-o", self.out]       # empty output folder: read the originals
        for args, expected in ((["list"], "96eaa83fb1b78608e75a5fd009649b9bbf98dfe8"), (["show", "viper mk1"], "66c8c43e07174ed6fd0d42c8268ba83cf445a826"),
                               (["munitions"], "175c3b34dcae132e11aeea4430445906854475fd"), (["fleetinfo"], "493b541542187bb41a437f84a72a46026cd51502")):
            self.assertEqual(hashlib.sha1(self.run_tool("shipdb", *args, *p).encode()).hexdigest(), expected, args)

    def test_shipdb_edits_stack(self):
        self.need("dll", "level0")
        p = ["--levels", INPUTS["levels"], "--dll", INPUTS["dll"], "-o", self.out]
        self.run_tool("shipdb", "set", "Colonial Mercury", "MunitionSlots", "4", *p)
        self.run_tool("shipdb", "setxp", "4", "100", *p)
        for name, h in SET_LEVELS.items(): self.assertHash(os.path.join(self.out, "levels", name), h)

    def test_shipdb_setcommanders(self):
        self.need("dll", "level0")
        self.run_tool("shipdb", "setcommanders", "--add", "10", "--every-level", "--levels", INPUTS["levels"], "--dll", INPUTS["dll"], "-o", self.out)
        for name, h in CMD_LEVELS.items(): self.assertHash(os.path.join(self.out, "levels", name), h)

    def test_weapons_everything(self):
        self.need("mercury_dec", "orion_dec")
        out = self.run_tool("weapons", INPUTS["mercury_dec"], "--list-mounts")
        self.assertEqual(hashlib.sha1(out.encode()).hexdigest(), "b851f19c648b7d7a40d7f44f48e1f3a2db608453")
        self.run_tool("weapons", INPUTS["mercury_dec"], "-o", self.out, "--clone", "fore=3", "--layout", "free", "--scale", "fore=0.7", "--clone-scale", "0.7",
                      "--fighter-slots", "5", "--utility-slots", "1", "--munition-slots", "4", "--hull-regen", "0.5", "--munition-factory", "--squadron-revive",
                      "--armour-regen", "--mini-jump", "--augury", "--stealth", INPUTS["orion_dec"], "--ability-submenu")
        self.assertHash(os.path.join(self.out, "bundles", "colonialmercury"), "5c3d1c82c69b4653b9e67b2ad1fbf13cd43f21b2")


if __name__ == "__main__":
    unittest.main()
