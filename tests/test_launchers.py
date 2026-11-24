"""Launch bays, munition slots and munition launchers (deadlock_tools.bays): edit, remove, re-read, never dangle.

Uses the work-tree Mercury MK2 bundle when present; skipped otherwise."""
import os, tempfile, unittest

from deadlock_tools import bays

BUNDLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work", "BSG_Data", "StreamingAssets", "colonialmercurymk2")


@unittest.skipUnless(os.path.isfile(BUNDLE), "no work-tree colonialmercurymk2 bundle")
class Launchers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = bays.read_layout(BUNDLE)
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def out(self, name):
        return os.path.join(self.tmp.name, name)

    def test_read(self):
        L = self.layout
        self.assertTrue(L["bays"] and L["launchers"] and L["launchButtons"] and L["munitionButtons"])
        self.assertEqual(len(L["launchButtons"]), len(L["slots"]))

    def test_remove_slot_renumbers_and_drops_one_button(self):
        top = max(self.layout["slots"])
        r = bays.apply(BUNDLE, self.out("slot"), {"removeSlots": [top]})
        after = bays.read_layout(self.out("slot"))
        self.assertEqual(r["removedSlots"], [top])
        self.assertEqual(len(after["launchButtons"]), len(self.layout["launchButtons"]) - 1)
        self.assertEqual(after["slots"], [s for s in self.layout["slots"] if s != top])

    def test_remove_middle_slot_renumbers_bays_above(self):
        k = sorted(self.layout["slots"])[1]
        bays.apply(BUNDLE, self.out("mid"), {"removeSlots": [k]})
        after = bays.read_layout(self.out("mid"))
        self.assertEqual(after["slots"], list(range(len(self.layout["slots"]) - 1)))

    def test_remove_munition_button(self):
        last = self.layout["munitionButtons"][-1]["id"]
        bays.apply(BUNDLE, self.out("mun"), {"removeMunitions": [last]})
        self.assertEqual(len(bays.read_layout(self.out("mun"))["munitionButtons"]), len(self.layout["munitionButtons"]) - 1)

    def test_launcher_edit_and_removal(self):
        first, second = self.layout["launchers"][0], self.layout["launchers"][-1]
        points = first["launchPoints"] + [{"x": 1.0, "y": 2.0, "z": 3.0}]
        bays.apply(BUNDLE, self.out("ml"), {"launchers": [{"id": first["id"], "launchPoints": points, "position": {"x": 0, "y": 0, "z": 1}}],
                                            "removeLaunchers": [second["id"]]})
        after = {l["name"]: l for l in bays.read_layout(self.out("ml"))["launchers"]}
        self.assertNotIn(second["name"], after)
        self.assertEqual(len(after[first["name"]]["launchPoints"]), len(points))

    def test_cannot_remove_every_launcher(self):
        with self.assertRaises(ValueError):
            bays.apply(BUNDLE, self.out("none"), {"removeLaunchers": [l["id"] for l in self.layout["launchers"]]})

    def test_weapons_bay_layout_cli(self):
        """weapons --bay-layout (what Save ship runs): revision check, edits applied after the other options."""
        import json, subprocess, sys
        from deadlock_tools import weapons as w
        spec = {"revision": self.layout["revision"], "removeSlots": [max(self.layout["slots"])],
                "bays": [{"id": self.layout["bays"][0]["id"], "dockRange": 7.5}]}
        path = self.out("spec.json")
        with open(path, "w") as stream:
            json.dump(spec, stream)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        run = lambda *args: subprocess.run([sys.executable, "-m", "deadlock_tools", "weapons", BUNDLE, *args],
                                           cwd=root, capture_output=True, text=True)
        result = run(self.out("cli"), "--no-encrypt", "--bay-layout", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = bays.read_layout(self.out("cli"))
        self.assertEqual(len(after["launchButtons"]), len(self.layout["launchButtons"]) - 1)
        self.assertEqual(next(b for b in after["bays"] if b["id"] == spec["bays"][0]["id"])["dockRange"], 7.5)
        listing = run("--list-bays")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn(self.layout["bays"][0]["id"], listing.stdout)
        spec["revision"] = "0" * 64                                  # stale layout: refused, nothing written
        with open(path, "w") as stream:
            json.dump(spec, stream)
        stale = run(self.out("stale"), "--no-encrypt", "--bay-layout", path)
        self.assertNotEqual(stale.returncode, 0)
        self.assertFalse(os.path.exists(self.out("stale")))

    def test_realign_mirrors_pairs_without_overlaps(self):
        rows, _notes = bays.realign_spec(self.layout)
        bays.apply(BUNDLE, self.out("realign"), {"bays": rows})
        after = bays.read_layout(self.out("realign"))
        moved = {b["id"]: b for b in after["bays"] if b["id"] in {row["id"] for row in rows}}
        spots = [tuple(round(b["position"][a], 3) for a in "xyz") for b in after["bays"]]
        self.assertEqual(len(spots), len(set(spots)))                          # no two bays on one spot
        by_slot = sorted(moved.values(), key=lambda b: b["slot"])
        for port, starboard in zip(by_slot[0::2], by_slot[1::2]):              # consecutive slots mirror each other
            self.assertLess(port["position"]["x"], 0); self.assertGreater(starboard["position"]["x"], 0)
            self.assertAlmostEqual(port["position"]["x"], -starboard["position"]["x"], 4)
            self.assertAlmostEqual(port["position"]["z"], starboard["position"]["z"], 4)
            self.assertTrue(starboard["name"].startswith("Starboard") and port["name"].startswith("Port"))
        self.assertEqual(after["slots"], self.layout["slots"])                  # nothing removed or renumbered


if __name__ == "__main__":
    unittest.main()
