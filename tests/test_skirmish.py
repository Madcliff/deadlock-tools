"""Skirmish fleet size (shipdb skirmishinfo / setskirmishinfo: SkirmishDataTables.maxShips) on the vanilla levels.

Needs tests/regression_inputs.json with "dll" and "levels"; skipped otherwise.
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
INPUTS_FILE = os.path.join(HERE, "regression_inputs.json")
INPUTS = json.load(open(INPUTS_FILE)) if os.path.isfile(INPUTS_FILE) else {}
READY = all(INPUTS.get(k) and os.path.exists(INPUTS[k]) for k in ("dll", "levels"))


@unittest.skipIf(not READY, "no tests/regression_inputs.json with dll and levels")
class SkirmishFleetSize(unittest.TestCase):
    def run_tool(self, levels, out, *args):
        env = dict(os.environ, PYTHONPATH=ROOT)
        return subprocess.run([sys.executable, "-m", "deadlock_tools", "shipdb", *args, "--levels", levels, "--dll", INPUTS["dll"],
                               "-o", out, "--fresh"], capture_output=True, text=True, cwd=ROOT, env=env, timeout=900)

    def test_read_set_and_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            levels = os.path.join(tmp, "levels")
            os.makedirs(levels)
            for name in ("level6", "level9", "level5"):            # level5 has no skirmish table
                shutil.copy(os.path.join(INPUTS["levels"], name), levels)
            r = self.run_tool(levels, os.path.join(tmp, "out"), "skirmishinfo")
            self.assertIn("level6: maxShips 10, maxShipsConsole 8 (15 skirmish maps)", r.stdout, r.stdout + r.stderr)
            r = self.run_tool(levels, os.path.join(tmp, "out"), "setskirmishinfo", "maxShips", "20")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("SkirmishDataTables.maxShips: 10 -> 20", r.stdout)
            r = self.run_tool(os.path.join(tmp, "out", "levels"), os.path.join(tmp, "check"), "skirmishinfo")
            self.assertIn("level9: maxShips 20", r.stdout, r.stdout + r.stderr)
            before = open(os.path.join(levels, "level6"), "rb").read()
            after = open(os.path.join(tmp, "out", "levels", "level6"), "rb").read()
            self.assertEqual(len(before), len(after))
            self.assertEqual(sum(a != b for a, b in zip(before, after)), 1)        # 10 -> 20 is one byte
            self.assertFalse(os.path.exists(os.path.join(tmp, "out", "levels", "level5")))
            r = self.run_tool(levels, os.path.join(tmp, "bad"), "setskirmishinfo", "maxShips", "0")
            self.assertNotEqual(r.returncode, 0)
            r = self.run_tool(levels, os.path.join(tmp, "bad"), "setskirmishinfo", "bogus", "5")
            self.assertNotEqual(r.returncode, 0)
