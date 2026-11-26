"""Squadron research entries (WarRoomDataTables.BlueprintInfo[*].SquadronUnlocks) on the vanilla level files.

Needs tests/regression_inputs.json with "dll" and "levels" (vanilla); skipped otherwise.

    python -m unittest tests.test_squadron_research -v
"""
import json, os, unittest

from deadlock_tools import shipdb as sd, squadron_research as R

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS_FILE = os.path.join(HERE, "regression_inputs.json")
INPUTS = json.load(open(INPUTS_FILE)) if os.path.isfile(INPUTS_FILE) else {}
READY = all(INPUTS.get(k) and os.path.exists(INPUTS[k]) for k in ("dll", "levels"))


@unittest.skipIf(not READY, "no tests/regression_inputs.json with dll and levels")
class SquadronResearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.S = sd.Schema(INPUTS["dll"])
        cls.levels = {}
        for n in ("level5", "level85", "level9"):
            with open(os.path.join(INPUTS["levels"], n), "rb") as fh:
                cls.levels[n] = fh.read()

    def test_stock_entries(self):
        raptor = R.entries_for(self.S, self.levels["level5"], 2)
        self.assertEqual(sorted(raptor), [0, 2, 3, 4, 5])                    # not researched in Quorum
        self.assertTrue(raptor[0]["unlocked"])
        self.assertEqual(R.entries_for(self.S, self.levels["level5"], 5)[3]["mission"], 39)   # Taipan after mission 39
        self.assertEqual(R.entries_for(self.S, self.levels["level5"], 113), {})               # Viper Mk VII: skirmish only
        self.assertIsNone(R.read(self.S, self.levels["level9"]))

    def test_add_update_remove_round_trip(self):
        S, levels = self.S, dict(self.levels)
        out, log = R.apply_all(S, levels, 113, {0: {"cost": 900, "mission": 10, "label": "V7"}, 5: {}})
        self.assertEqual(sorted(out), ["level5", "level85"])
        levels.update(out)
        for name in ("level5", "level85"):
            got = R.entries_for(S, levels[name], 113)
            self.assertEqual((got[0]["cost"], got[0]["mission"], got[0]["label"]), (900, 10, "V7"))
            self.assertEqual((got[5]["cost"], got[5]["mission"], got[5]["label"], got[5]["unlocked"]), (600, -1, "", False))
        out, _log = R.apply_all(S, levels, 113, {0: {"cost": 1200, "unlocked": True}})
        levels.update(out)
        got = R.entries_for(S, levels["level5"], 113)[0]
        self.assertEqual((got["cost"], got["mission"], got["label"], got["unlocked"]), (1200, 10, "V7", True))
        before = R.read(S, self.levels["level5"])
        after = R.read(S, levels["level5"])
        for c in before:                                                      # nothing else moved
            self.assertEqual([e for e in after[c] if e["id"] != 113], before[c])
        out, _log = R.apply_all(S, levels, 113, {0: None, 5: None})
        levels.update(out)
        self.assertEqual(levels["level5"], self.levels["level5"])
        self.assertEqual(levels["level85"], self.levels["level85"])

    def test_copy_entries(self):
        out, _log = R.copy_entries(self.S, self.levels, 4, 116)
        got = R.entries_for(self.S, out["level5"], 116)
        donor = R.entries_for(self.S, self.levels["level5"], 4)
        self.assertEqual(sorted(got), sorted(donor))
        for c in got:
            self.assertEqual((got[c]["cost"], got[c]["mission"], got[c]["unlocked"]), (donor[c]["cost"], donor[c]["mission"], False))

    def test_refusals(self):
        with self.assertRaises(ValueError):
            R.apply_all(self.S, self.levels, 2, {0: {"label": "x" * 40}})
        with self.assertRaises(ValueError):
            R.apply_all(self.S, self.levels, 2, {9: {"cost": 1}})            # no such campaign
        with self.assertRaises(ValueError):
            R.apply_all(self.S, self.levels, 2, {0: {"cost": -5}})


if __name__ == "__main__":
    unittest.main()
