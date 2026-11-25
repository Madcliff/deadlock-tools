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
        if not any(trench.CLONE.search(m["name"]) for m in cls.fore):
            # the work-tree MK2 may have stock bow guns only (reset_mounts); clone them as the MK2 was built (2 each)
            import subprocess, sys
            cloned = os.path.join(cls.tmp, "cloned")
            subprocess.run([sys.executable, "-m", "deadlock_tools", "weapons", cls.src, cloned, "--clone", "fore=2",
                            "--layout", "free"], cwd=ROOT, check=True, capture_output=True)
            shutil.move(cloned, cls.src)
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

VANILLA = os.path.join(os.path.dirname(ROOT), "decrypted", "colonialmercury.dec")


@unittest.skipUnless(os.path.isfile(MK2) and os.path.isfile(VANILLA), "needs the MK2 work bundle and the vanilla Mercury .dec")
class ResetMounts(unittest.TestCase):
    def test_trench_layout_then_reset_gives_vanilla_mounts(self):
        import tempfile
        from deadlock_tools import trench
        def local(path):
            _b, prefab, _r = trench._open(path); value = lambda pid: prefab.read(prefab.by_pid[pid])[0]
            return {m["name"]: (m["quad"], tuple(round(x, 4) for x in m["pos"]), round(m["scale"], 4),
                                value(m["mount"].pid)["WeaponType"]) for m in trench._mounts(prefab, value)}
        with tempfile.TemporaryDirectory() as tmp:
            laid = os.path.join(tmp, "laid"); back = os.path.join(tmp, "back")
            trench.reset_mounts(MK2, VANILLA, back)                       # start from vanilla mounts
            report = trench.reset_mounts(back, VANILLA, laid)            # idempotent: nothing to change or remove
            self.assertEqual((report["changed"], report["removed"]), (0, []))
            self.assertEqual(local(back), local(VANILLA))


if __name__ == "__main__":
    unittest.main()
