"""New munition types (munition_types.create_munition) on the vanilla game files.

Needs tests/regression_inputs.json with "dll", "levels", "managed" and "game_data" (as tests/test_squadron_types.py)
and the optional UnityPy asset support; skipped otherwise.

    python -m unittest tests.test_munition_types -v
"""
import os, types, unittest

from deadlock_tools import shipdb as sd
from tests.test_squadron_types import INPUTS, SKIP, objects, LEVEL_FILES


@unittest.skipIf(SKIP, SKIP or "")
class MunitionTypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from deadlock_tools import munition_types as M, squadron_types as T
        cls.M, cls.T = M, T
        cls.S = sd.Schema(INPUTS["dll"])
        cls.assets = open(os.path.join(INPUTS["levels"], M.ASSET_FILE), "rb").read()
        cls.levels = {n: open(os.path.join(INPUTS["levels"], n), "rb").read() for n in LEVEL_FILES}

    def test_copy_a_capital_munition(self):
        M, S = self.M, self.S
        from deadlock_tools import squadron_research as R
        P = self.T.Prefabs(self.assets, INPUTS["managed"], INPUTS["game_data"])
        munition_id = M.next_munition_id(S, self.levels)
        self.assertEqual(munition_id, 30)
        changed, _log = M.create_munition(S, P, self.levels, "colonial torpedo", "Heavy Torpedo", munition_id,
                                          {"descriptionID": 115})
        self.assertEqual(set(changed), set(LEVEL_FILES))
        donor = M.entry(S, self.levels, 2)
        for name in LEVEL_FILES:
            table = M.missile_table(S, changed[name])[3]["objects"]
            new = next(e for e in table if e["id"] == munition_id)
            self.assertEqual(new["name"], "Heavy Torpedo")
            self.assertEqual(new["descriptionID"], 115)
            self.assertEqual({k: v for k, v in M._plain(new).items() if k != "descriptionID"},
                             {k: v for k, v in M._plain(donor).items() if k != "descriptionID"})
            ids = [e["id"] for e in table]
            image = M.image_table(S, changed[name], ids)[3]["objects"]
            self.assertEqual(next(e for e in image if e["id"] == munition_id)["Icon"],
                             next(e for e in image if e["id"] == 2)["Icon"])
        _b, _o, factory = sd.parse_factory(types.SimpleNamespace(S=S), changed["level9"], "MissileFactory")
        root = next(e for e in factory["objects"] if e["id"] == munition_id)["prefab"][1][1]
        data = P.save()
        Q = self.T.Prefabs(data, INPUTS["managed"], INPUTS["game_data"])
        missiles = [p for p in Q.tree(root) if Q.kind(p) == "Missile"]
        self.assertEqual([Q.read(p)["missileType"] for p in missiles], [munition_id])
        old = objects(self.assets)
        self.assertEqual({p: b for p, b in objects(data).items() if p in old}, old)
        self.assertEqual(sorted(R.entries_for(S, changed["level5"], munition_id, R.MUNITION_KEY)),
                         sorted(R.entries_for(S, self.levels["level5"], 2, R.MUNITION_KEY)))
        with self.assertRaises(ValueError):
            M.entry(S, self.levels, "no such munition")

    def test_combat_stats(self):
        M, S = self.M, self.S
        P = self.T.Prefabs(self.assets, INPUTS["managed"], INPUTS["game_data"])
        self.assertEqual(M.combat_stats(S, P, self.levels, 12)["hull"], 4.0)
        self.assertNotIn("accuracy", M.combat_stats(S, P, self.levels, 12))      # unguided
        changed, log = M.set_combat_stats(S, P, self.levels, 12, {"hull": 8, "blast": 30, "speed": 70, "range": 5000})
        self.assertEqual(set(changed), set(LEVEL_FILES))
        with self.assertRaises(ValueError):
            M.set_combat_stats(S, P, self.levels, 12, {"accuracy": 0.9})
        with self.assertRaises(ValueError):
            M.set_combat_stats(S, P, self.levels, 12, {"hull": -1})
        Q = self.T.Prefabs(P.save(), INPUTS["managed"], INPUTS["game_data"])
        stats = M.combat_stats(S, Q, dict(self.levels, **changed), 12)
        self.assertEqual((stats["hull"], stats["blast"], stats["speed"], stats["range"]), (8.0, 30.0, 70.0, 5000.0))
        row = M.entry(S, {"level5": changed["level5"]}, 12)                      # display copies follow
        self.assertEqual((row["DisplayDamage"]["HullDamage"], row["DisplayDamage"]["Range"], row["MaxVelocity"], row["Range"]),
                         (8.0, 30.0, 70.0, 5000.0))


if __name__ == "__main__":
    unittest.main()
