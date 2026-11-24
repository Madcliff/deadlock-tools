"""Squadron prefab and allocation editors: every edit reads back, reverses byte-for-byte, and touches only its objects.

Needs the game's BSG_Data (set DEADLOCK_GAME_DATA, or the levels path in deadlock.json); skipped otherwise.
"""
import os, unittest

from deadlock_tools import config, shipdb as sd, squadrons as sq, squadron_table as st


def game_data():
    path = os.environ.get("DEADLOCK_GAME_DATA") or config.setting("levels", required=False)
    return path if path and os.path.isfile(os.path.join(path, "sharedassets9.assets")) and os.path.isfile(os.path.join(path, "level5")) else None


GAME = game_data()


def objects(data):
    return {pid: data[off:off + size] for pid, off, size, _c, _m in sd.read_objects(data)}


@unittest.skipIf(GAME is None, "no game BSG_Data with sharedassets9.assets and level5")
class SquadronEditors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.S = sd.Schema(os.path.join(GAME, "Managed", "Assembly-CSharp.dll"))
        with open(os.path.join(GAME, "sharedassets9.assets"), "rb") as fh: cls.assets = fh.read()
        with open(os.path.join(GAME, "level5"), "rb") as fh: cls.level = fh.read()

    def test_all_squadrons_found_with_core_components(self):
        squadrons = sq.load(self.S, self.assets)
        self.assertGreaterEqual(len(squadrons), 16)
        for q in squadrons:
            self.assertIn("FighterSquadron", q["components"]); self.assertIn("ShipInfo", q["components"])

    def test_fighter_count_round_trip_every_squadron(self):
        for q in sq.load(self.S, self.assets):
            fs = q["components"]["FighterSquadron"][3]
            grown, _ = sq.apply(self.S, self.assets, q["name"], {"MaxShipCount": min(sq.MAX_FIGHTERS, fs["MaxShipCount"] + 3)})
            back, _ = sq.apply(self.S, grown, q["name"], {"MaxShipCount": fs["MaxShipCount"], "ShipLoss": fs["ShipLoss"]})
            self.assertEqual(back, self.assets, q["name"])

    def test_scalar_edit_changes_only_the_component(self):
        name = sq.load(self.S, self.assets)[0]["name"]
        out, log = sq.apply(self.S, self.assets, name, {"MaxVelocity": 9.5, "RetreatDistance": 77})
        self.assertEqual(len(out), len(self.assets))
        before, after = objects(self.assets), objects(out)
        self.assertLessEqual(len([p for p in before if before[p] != after[p]]), 2)
        d = sq.describe(sq.find(sq.load(self.S, out), name))
        values = {f["field"]: f["value"] for f in d["fields"]}
        self.assertAlmostEqual(values["MaxVelocity"], 9.5, places=4); self.assertAlmostEqual(values["RetreatDistance"], 77, places=4)

    def test_invalid_values_rejected(self):
        name = sq.load(self.S, self.assets)[0]["name"]
        for bad in ({"MaxShipCount": sq.MAX_FIGHTERS + 1}, {"MaxHullPoints": -1}, {"NoSuchField": 1}):
            with self.assertRaises(ValueError): sq.apply(self.S, self.assets, name, bad)

    def test_allocation_round_trip(self):
        res = st.find(self.S, self.level)[3]
        first = res["objects"][0]
        changed, log = st.apply(self.S, self.level, {first["id"]: {"shipExclusionList": first["shipExclusionList"] + [9999], "PointValue": 1234}})
        self.assertTrue(log)
        res2 = st.find(self.S, changed)[3]
        self.assertIn(9999, st.entry(res2, first["id"])["shipExclusionList"])
        back, _ = st.apply(self.S, changed, {first["id"]: {"shipExclusionList": first["shipExclusionList"], "PointValue": first["PointValue"]}})
        self.assertEqual(back, self.level)

    def test_allowed_follows_exclusions_and_utility(self):
        res = st.find(self.S, self.level)[3]
        for e in res["objects"]:
            for ship in e["shipExclusionList"]:
                self.assertNotIn(e["id"], st.allowed(res, ship, e["Faction"]))
        utility = st.allowed(res, 29, 0, utility_slot=True)
        self.assertTrue(all(st.entry(res, i)["IsUtility"] for i in utility))


if __name__ == "__main__":
    unittest.main()
