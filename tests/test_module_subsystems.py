"""Module subsystems: parse/describe, and add / edit / remove round trips on a copy of the MK2 bundle.

The bundle tests use the work tree's colonialmercurymk2 (or DEADLOCK_MK2_BUNDLE) and are skipped without it."""
import os, shutil, tempfile, unittest

from deadlock_tools import module_subsystems as ms, subsystem_modifiers as sm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MK2 = os.environ.get("DEADLOCK_MK2_BUNDLE") or os.path.join(ROOT, "work", "BSG_Data", "StreamingAssets", "colonialmercurymk2")


class Spec(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(ms.parse_spec("0:1:50:100"), {"module": 0, "source": 1, "min": 50.0, "max": 100.0})
        self.assertEqual(ms.parse_spec("hull_regeneration:Engineering:50:100"), ms.parse_spec("0:1:50:100"))
        self.assertEqual(ms.parse_spec("CustomArmourRepairModule:tech bay:0:25")["module"], 2)
        self.assertEqual(ms.parse_spec("munition factory:armoury:25:100")["source"], 2)
        for bad in ("0:1:50", "warp:1:0:1", "0:galley:0:1", "0:1:x:1", "0:1:0:nan"):
            with self.assertRaises(ValueError, msg=bad):
                ms.parse_spec(bad)

    def test_describe(self):
        self.assertIn("Hull regeneration from Engineering", ms.describe({"module": 0, "source": 1, "min": 50, "max": 100}))


@unittest.skipUnless(os.path.isfile(MK2), "no work-tree colonialmercurymk2 bundle")
class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="module-subsystems-")
        self.src = os.path.join(self.tmp, "src")
        ms.apply(MK2, self.src, [])                       # start without the component, whatever the work tree has

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_edit_remove(self):
        out = os.path.join(self.tmp, "out")
        two = [ms.parse_spec("0:1:50:100"), ms.parse_spec("0:6:0:25")]
        self.assertEqual(ms.apply(self.src, out, two)["entries"], two)
        changed = [ms.parse_spec("1:2:25:100")]
        self.assertEqual(ms.apply(out, out, changed)["entries"], changed)          # in place, replaces the list
        self.assertEqual(ms.apply(out, out, []), {"present": False, "entries": []})
        with open(MK2, "rb") as a, open(out, "rb") as b:
            self.assertEqual(a.read(7) == b"UnityFS", b.read(7) == b"UnityFS")       # encrypted like the source

    def test_lives_beside_the_crew_modifiers(self):
        out = os.path.join(self.tmp, "both")
        sm.apply(self.src, out, [sm.parse_spec("102:4:0:0.5")])
        ms.apply(out, out, [ms.parse_spec("0:1:50:100")])
        self.assertEqual(len(sm.read(out)["entries"]), 1)
        self.assertEqual(len(ms.read(out)["entries"]), 1)


if __name__ == "__main__":
    unittest.main()
