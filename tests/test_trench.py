"""Trench layout of the Mercury MK2 bow guns (deadlock_tools.trench).

Needs a Mercury MK2 bundle with cloned fore turrets (the work tree's work/BSG_Data/StreamingAssets/colonialmercurymk2, or
DEADLOCK_MK2_BUNDLE); the bundle tests are skipped otherwise. Writes only to a temporary folder.
"""
import os, shutil, tempfile, unittest

from deadlock_tools import trench, weapons as w

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MK2 = os.environ.get("DEADLOCK_MK2_BUNDLE") or os.path.join(ROOT, "work", "BSG_Data", "StreamingAssets", "colonialmercurymk2")


class RowSpots(unittest.TestCase):
    def test_centre_run_maximises_count_then_matches_stock(self):
        xs = trench._row_xs([(0, 12.5)], 3.2, [])
        self.assertEqual(len(xs), 4)                                  # 8 spots: +-x for 4 x values, no centre spot
        self.assertTrue(all(b - a >= 3.2 - 1e-9 for a, b in zip(xs, xs[1:])))
        self.assertGreaterEqual(2 * xs[0], 3.2 - 1e-9)                # the centre pair is a gap apart too
        self.assertLessEqual(xs[-1], 12.5)
        matched = trench._row_xs([(0, 12.5)], 3.2, [8.8])
        self.assertTrue(any(abs(x - 8.8) < 1e-6 for x in matched))   # same count, so the phase goes through the stock turret

    def test_centre_spot_when_it_fits_more(self):
        xs = trench._row_xs([(0, 13.0)], 3.2, [])
        self.assertEqual(xs[0], 0.0)                                  # 0, +-3.2 ... +-12.8 = 9 spots beat 8
        self.assertEqual(len(xs), 5)

    def test_side_run_is_centred(self):
        self.assertEqual(trench._row_xs([(13.5, 14.5)], 3.2, []), [14.0])


@unittest.skipUnless(os.path.isfile(MK2), "no Mercury MK2 bundle")
class MercuryTrench(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="trench-")
        cls.src = os.path.join(cls.tmp, "colonialmercurymk2")
        shutil.copyfile(MK2, cls.src)
        cls.fore = [m for m in w.bundle_mount_layout(cls.src)["mounts"] if m["quadrant"] == 2]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_layout_keeps_every_turret_and_passes_the_checks(self):
        dst = os.path.join(self.tmp, "out")
        report = trench.trench_layout(self.src, dst)
        self.assertEqual(report["turrets"], len(self.fore))
        self.assertEqual(report["removed"], [])
        self.assertGreaterEqual(report["closest"], 4.0 - 1e-3)
        self.assertEqual(report["mesh_scales"], [1.0])
        self.assertLessEqual(report["max_off_surface"], 0.3)
        with open(self.src, "rb") as a, open(dst, "rb") as b:
            self.assertEqual(a.read(7) == b"UnityFS", b.read(7) == b"UnityFS")     # encrypted like the source
        again = os.path.join(self.tmp, "again")
        trench.trench_layout(dst, again)                                           # running it twice changes nothing
        pos = lambda path: {m["name"]: m["position"] for m in w.bundle_mount_layout(path)["mounts"]}
        first, second = pos(dst), pos(again)
        self.assertEqual(set(first), set(second))
        for name, p in first.items():                  # x/z exact; y may settle by a hair (median height offset)
            self.assertAlmostEqual(p["x"], second[name]["x"], places=4, msg=name)
            self.assertAlmostEqual(p["z"], second[name]["z"], places=4, msg=name)
            self.assertAlmostEqual(p["y"], second[name]["y"], delta=0.01, msg=name)

    def test_short_trench_removes_extra_clones_only(self):
        dst = os.path.join(self.tmp, "short")
        report = trench.trench_layout(self.src, dst, floor_rows=1)
        self.assertTrue(report["removed"])
        self.assertTrue(all(trench.CLONE.search(n) for n in report["removed"]))
        names = {m["name"] for m in w.bundle_mount_layout(dst)["mounts"]}
        self.assertFalse(names & set(report["removed"]))
        self.assertEqual(report["turrets"] + len(report["removed"]), len(self.fore))
        trench.check(dst)


if __name__ == "__main__":
    unittest.main()
