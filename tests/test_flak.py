"""Flak field editing (weapons --flak FIELD=VALUE) on a temporary copy of the vanilla Mercury.

Needs tests/regression_inputs.json with "mercury_dec"; the bundle tests are skipped otherwise.

    python -m unittest tests.test_flak -v
"""
import json, os, subprocess, sys, tempfile, unittest

from deadlock_tools import flak as FL, weapons as W

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
INPUTS_FILE = os.path.join(HERE, "regression_inputs.json")
INPUTS = json.load(open(INPUTS_FILE)) if os.path.isfile(INPUTS_FILE) else {}
READY = INPUTS.get("mercury_dec") and os.path.isfile(INPUTS["mercury_dec"])


def prefab(path):
    b = W.read_bundle(path)
    off, size, *_ = b["nodes"][0]
    return W.SerFile(b["data"][off:off + size])


class Parse(unittest.TestCase):
    def test_values_and_refusals(self):
        self.assertEqual(FL.parse(["FlakRange.Min=120", "BaseDamagePerTick.SubsystemSpreadCount=2",
                                   "BaseDamagePerTick.AppliesIrradiatedStatus=on"]),
                         {"FlakRange.Min": 120.0, "BaseDamagePerTick.SubsystemSpreadCount": 2,
                          "BaseDamagePerTick.AppliesIrradiatedStatus": True})
        for bad in ("Bogus=1", "FlakRange.Min", "FlakAreaRadius=0", "FlakAreaLength=-5", "BaseDamagePerTick.HullDamage=-1",
                    "BaseDamagePerTick.SubsystemSpreadCount=1.5", "BaseDamagePerTick.AppliesIrradiatedStatus=maybe",
                    "FlakRange.Min=nan"):
            with self.assertRaises(ValueError, msg=bad):
                FL.parse([bad])

    def test_every_field_has_a_label(self):
        self.assertEqual(set(FL.LABELS), set(FL.FIELDS))


@unittest.skipIf(not READY, "no tests/regression_inputs.json with mercury_dec")
class FlakBundle(unittest.TestCase):
    def run_tool(self, *args):
        env = dict(os.environ, PYTHONPATH=ROOT)
        r = subprocess.run([sys.executable, "-m", "deadlock_tools", "weapons", *args], capture_output=True, text=True,
                           cwd=ROOT, env=env, timeout=900)
        return r

    def test_stock_values(self):
        info = FL.read(prefab(INPUTS["mercury_dec"]))
        self.assertEqual(info["values"]["FlakRange.Min"], 100.0)
        self.assertEqual(info["values"]["FlakAreaLength"], 110.0)
        self.assertEqual(info["values"]["BaseDamagePerTick.HullDamage"], 1.0)
        self.assertEqual(info["armory"], {"Min": 20.0, "Max": 100.0})

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_tool(INPUTS["mercury_dec"], "-o", tmp, "--no-encrypt", "--flak", "FlakRange.Min=150",
                              "--flak", "BaseDamagePerTick.HullDamage=2.5", "--flak", "FlakAreaRadius=60",
                              "--flak", "BaseDamagePerTick.AppliesIrradiatedStatus=true")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("flak FlakRange.Max raised to 151", r.stdout)
            out = os.path.join(tmp, "bundles", os.path.basename(INPUTS["mercury_dec"]))
            before, after = prefab(INPUTS["mercury_dec"]), prefab(out)
            values = FL.read(after)["values"]
            self.assertEqual((values["FlakRange.Min"], values["FlakRange.Max"], values["FlakAreaRadius"]), (150.0, 151.0, 60.0))
            self.assertEqual(values["BaseDamagePerTick.HullDamage"], 2.5)
            self.assertTrue(values["BaseDamagePerTick.AppliesIrradiatedStatus"])
            self.assertEqual(len(before.objs), len(after.objs))
            stock = FL.read(before)["values"]
            for key in ("FlakAreaLength", "FlakExplosionsPerTick", "BaseDamagePerTick.ArmourDamage"):
                self.assertEqual(values[key], stock[key])
            # running the same edit again changes nothing more
            again = os.path.join(tmp, "again")
            r = self.run_tool(out, "-o", again, "--no-encrypt", "--flak", "FlakRange.Min=150")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("flak FlakRange", r.stdout)
            # inspect shows the values; a Max below Min is refused
            r = self.run_tool(out, "--inspect")
            self.assertIn("FlakRange.Min", r.stdout)
            r = self.run_tool(out, "-o", os.path.join(tmp, "bad"), "--no-encrypt", "--flak", "FlakRange.Max=10")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("must be at least FlakRange.Min", r.stdout + r.stderr)

    def test_stats_feed_the_card(self):
        stats = W.bundle_ship_stats(INPUTS["mercury_dec"])
        self.assertEqual([f["key"] for f in stats["flak"]["fields"]], list(FL.FIELDS))
        self.assertEqual(len(stats["flak"]["stock"]), len(FL.STOCK))
        hull = stats["flak"]["hull"]                     # Mercury hull mesh: about 68 wide, 174 long, unscaled
        self.assertEqual(hull["scale"], 1.0)
        self.assertAlmostEqual(hull["extent"][0], 34.0, delta=1)
        self.assertAlmostEqual(hull["extent"][2], 87.2, delta=1)
