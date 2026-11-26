"""weapons --swap-weapon: change every turret of one weapon type to another (vanilla Mercury .dec; skipped without it)."""
import collections, os, subprocess, sys, tempfile, unittest

from deadlock_tools import weapons as w

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MERCURY = os.path.join(os.path.dirname(ROOT), "decrypted", "colonialmercury.dec")


class Parse(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(w.parse_swap_weapon("4=121"), ("all", 4, 121))
        self.assertEqual(w.parse_swap_weapon("fore:4=121"), ("fore", 4, 121))
        self.assertEqual(w.parse_swap_weapon("Port:*=3"), ("port", None, 3))
        for bad in ("4", "fore:x=1", "4=-1", "=5"):
            with self.assertRaises(ValueError, msg=bad):
                w.parse_swap_weapon(bad)


@unittest.skipUnless(os.path.isfile(MERCURY), "no vanilla colonialmercury.dec")
class Swap(unittest.TestCase):
    def run_weapons(self, *args):
        return subprocess.run([sys.executable, "-m", "deadlock_tools", "weapons", MERCURY, *args],
                              cwd=ROOT, capture_output=True, text=True)

    def test_swap_by_quadrant_and_refuse_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            r = self.run_weapons(out, "--no-encrypt", "--swap-weapon", "fore:4=121", "--swap-weapon", "rear:*=3")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            got = collections.Counter((w.MOUNT_QUADRANTS[m["quadrant"]], m["weapon"]) for m in w.bundle_mount_layout(out)["mounts"])
            self.assertEqual(got[("fore", 121)], 10)
            self.assertEqual(got[("rear", 3)], 2)
            self.assertEqual(got[("port", 106)], 12)                      # untouched
            bad = self.run_weapons(os.path.join(tmp, "bad"), "--swap-weapon", "dorsal:4=121")
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("matches no turret", bad.stdout + bad.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "bad")))


if __name__ == "__main__":
    unittest.main()
