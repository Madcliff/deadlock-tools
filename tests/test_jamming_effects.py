"""Jamming effects: the global DataTables.JammingPenalty and the per-jammer overrides file.

Needs tests/regression_inputs.json with "dll" and "levels" (vanilla); skipped otherwise.
"""
import json, os, tempfile, unittest

from deadlock_tools import jamming as J, shipdb as sd

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS_FILE = os.path.join(HERE, "regression_inputs.json")
INPUTS = json.load(open(INPUTS_FILE)) if os.path.isfile(INPUTS_FILE) else {}
READY = all(INPUTS.get(k) and os.path.exists(INPUTS[k]) for k in ("dll", "levels"))


@unittest.skipIf(not READY, "no tests/regression_inputs.json with dll and levels")
class JammingEffects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.S = sd.Schema(INPUTS["dll"])
        cls.levels = {n: open(os.path.join(INPUTS["levels"], n), "rb").read() for n in ("level5", "level85", "level9", "level6")}

    def test_global_penalty(self):
        self.assertEqual(J.global_penalty(self.S, self.levels), {"speed": -1.2000000476837158, "evade": -15.0, "accuracy": 0.0})
        changed, log = J.set_global(self.S, self.levels, {"evade": -20, "accuracy": -5})
        self.assertEqual(sorted(changed), ["level5", "level85", "level9"])      # level6 has no DataTables
        for name, data in changed.items():
            self.assertEqual(J.global_penalty(self.S, {name: data}), {"speed": -1.2000000476837158, "evade": -20.0, "accuracy": -5.0})
            self.assertEqual(len(data), len(self.levels[name]))                  # floats rewritten in place
        back, _log = J.set_global(self.S, dict(self.levels, **changed), {"evade": -15, "accuracy": 0})
        self.assertEqual({n: back[n] for n in back}, {n: self.levels[n] for n in back})
        with self.assertRaises(ValueError):
            J.set_global(self.S, self.levels, {"nope": 1})

    def test_overrides_file(self):
        overrides, log = J.set_override({}, "squadron", 103, {"evade": -25, "accuracy": "-10"})
        overrides, log = J.set_override(overrides, "ship", 206, {"speed": -2})
        overrides, log = J.set_override(overrides, "squadron", 103, {"accuracy": None})
        self.assertIn("accuracy back to global", log[0])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, J.FILE)
            open(path, "wb").write(J.render(overrides))
            self.assertEqual(J.read_overrides(path), {("squadron", 103): {"evade": -25.0}, ("ship", 206): {"speed": -2.0}})
            text = open(path).read()
            self.assertIn("squadron 103 evade=-25", text)
        empty, _log = J.set_override(overrides, "ship", 206, {"speed": None})
        self.assertNotIn(("ship", 206), empty)
        with self.assertRaises(ValueError):
            J.set_override({}, "fleet", 1, {"evade": 1})


if __name__ == "__main__":
    unittest.main()


MANAGED = INPUTS.get("managed")
CECIL = "/usr/lib/mono/gac/Mono.Cecil/0.11.1.0__0738eb9f132ed756/Mono.Cecil.dll"
CAN_BUILD = bool(MANAGED and os.path.isdir(MANAGED) and os.path.isfile(CECIL)
                 and all(__import__("shutil").which(t) for t in ("mcs", "mono")))


@unittest.skipIf(not CAN_BUILD, "needs \"managed\" (vanilla Managed folder) in regression_inputs.json, mcs, mono and Mono.Cecil")
class JammingDllHooks(unittest.TestCase):
    def test_capital_ships_take_jam_orders(self):
        # stock Jam / JamCancel handlers only act on IsProxy ships (squadrons); the merge swaps that check for
        # CustomJamming.CanCommandJam in both, so a capital ship's Jammer really switches on and off
        import re, subprocess
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(["bash", os.path.join(HERE, "..", "scripts", "build_custom_modules.sh"), MANAGED,
                                  os.path.join(MANAGED, "Assembly-CSharp.dll"), os.path.join(tmp, "out.dll")],
                                 capture_output=True, text=True, check=True).stdout
        found = re.search(r"take jam orders \((\d+) check", out)
        self.assertTrue(found, out)
        self.assertGreaterEqual(int(found.group(1)), 2)
        self.assertIn("16 penalty read(s)", out)
        # the merge refuses a DLL that still references the temporary CustomModules assembly (0.6.0 shipped one:
        # an array initializer in CustomJamming crashed fighters attacking jammed ships)
        self.assertNotIn("still references", out)
        self.assertIn("Fit ability buttons to the screen in 2 ShipInfoPanel method(s)", out)
        self.assertIn("Added the extra fleet-cap choices", out)
        self.assertIn("Routed skirmish ships-per-fleet through CustomSkirmishFleetCap (3 read(s))", out)
        self.assertRegex(out, r"Routed campaign capital ships-per-fleet through CustomSkirmishFleetCap \(\d+ read\(s\)\)")
