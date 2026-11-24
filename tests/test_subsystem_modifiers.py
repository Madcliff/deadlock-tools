"""Subsystem crew modifiers: add / edit / remove round trips on a copy of a ship bundle.

Uses DEADLOCK_SUBSYSTEM_TEST_BUNDLE, else regression_inputs.json "mercurymk2", else the work tree's
work/BSG_Data/StreamingAssets/colonialmercurymk2; skipped when none exists. Never writes to the source.
"""
import json
import math
import os
import shutil
import tempfile
import unittest

from deadlock_tools import subsystem_modifiers as sm
from deadlock_tools.weapons import CREW_MODIFIER_FIELDS, SerFile, parse_bundle, xor_bytes

HERE = os.path.dirname(os.path.abspath(__file__))


def _bundle_path():
    candidates = [os.environ.get("DEADLOCK_SUBSYSTEM_TEST_BUNDLE")]
    inputs = os.path.join(HERE, "regression_inputs.json")
    if os.path.isfile(inputs):
        with open(inputs) as handle:
            candidates.append(json.load(handle).get("mercurymk2"))
    candidates.append(os.path.join(HERE, "..", "work", "BSG_Data", "StreamingAssets", "colonialmercurymk2"))
    return next((os.path.abspath(p) for p in candidates if p and os.path.isfile(p)), None)


BUNDLE = _bundle_path()


def serialized(path):
    with open(path, "rb") as handle:
        bundle = parse_bundle(handle.read())
    offset, size, _flags, _name = bundle["nodes"][0]
    return bundle, SerFile(bundle["data"][offset:offset + size])


class StatTableTest(unittest.TestCase):
    def test_stat_names_cover_the_editor_list(self):
        for stat, name in CREW_MODIFIER_FIELDS.items():
            self.assertEqual(sm.STATS[stat]["name"], name)

    def test_stock_sources_follow_the_hundreds(self):
        self.assertEqual(sm.stock_source(1), 0)
        self.assertEqual(sm.stock_source(605), 6)
        self.assertTrue(sm.EFFECTIVE_STATS <= set(sm.STATS))
        self.assertIn(1, sm.EFFECTIVE_STATS)
        self.assertNotIn(0, sm.EFFECTIVE_STATS)


@unittest.skipIf(BUNDLE is None, "no ship bundle available for the subsystem-modifier round trip")
class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="subsystem-modifiers-")
        self.src = os.path.join(self.dir, "ship")
        shutil.copyfile(BUNDLE, self.src)
        # Start from a bundle without the component, whatever the work tree holds.
        if sm.read(self.src)["present"]:
            sm.apply(self.src, self.src, [])
        with open(self.src, "rb") as handle:
            self.src_bytes = handle.read()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_add_edit_remove(self):
        entries = [{"stat": 1, "source": 1, "min": 0.0, "max": 2.5},
                   {"stat": 202, "source": 3, "min": 0.25, "max": 0.5}]
        added = sm.apply(self.src, self.path("a"), entries)
        self.assertEqual(added, {"present": True, "entries": entries})
        with open(self.src, "rb") as handle:
            self.assertEqual(handle.read(), self.src_bytes, "source bundle must not change")

        before_bundle, before = serialized(self.src)
        after_bundle, after = serialized(self.path("a"))
        self.assertEqual(before_bundle["nodes"][1][3], after_bundle["nodes"][1][3])
        res_before = before_bundle["data"][before_bundle["nodes"][1][0]:][:before_bundle["nodes"][1][1]]
        res_after = after_bundle["data"][after_bundle["nodes"][1][0]:][:after_bundle["nodes"][1][1]]
        self.assertEqual(res_before, res_after)
        go_pid = sm._ship_object(before)
        bundle_pid = next(o.pid for o in before.objs if o.classid == 142)
        new = set(after.by_pid) - set(before.by_pid)
        script_existed = sm.CLASS_NAME in sm._scripts(before).values()
        self.assertEqual(len(new), 1 if script_existed else 2)      # component (+ MonoScript)
        for o in before.objs:
            if o.pid not in (go_pid, bundle_pid):
                self.assertEqual(before.raw(o), after.raw(after.by_pid[o.pid]), o.pid)
        component = sm._components(after, go_pid)[0]
        self.assertIn({"component": {"m_FileID": 0, "m_PathID": component.pid}},
                      after.read(after.by_pid[go_pid])[0]["m_Component"])
        preload = after.read(after.by_pid[bundle_pid])[0]
        self.assertEqual({ref["m_PathID"] for ref in preload["m_PreloadTable"][-len(new):]}, new)
        self.assertEqual(preload["m_Container"][0]["second"]["preloadSize"], len(preload["m_PreloadTable"]))

        edited = sm.apply(self.path("a"), self.path("b"), [{"stat": 100, "source": 6, "min": -0.5, "max": 1}])
        self.assertEqual(edited["entries"], [{"stat": 100, "source": 6, "min": -0.5, "max": 1.0}])
        _bundle, b = serialized(self.path("b"))
        self.assertEqual(set(b.by_pid), set(after.by_pid))

        removed = sm.apply(self.path("b"), self.path("c"), [])
        self.assertEqual(removed, {"present": False, "entries": []})
        _bundle, c = serialized(self.path("c"))
        self.assertEqual(set(c.by_pid), set(before.by_pid) | (new - {component.pid}))
        self.assertEqual(c.read(c.by_pid[go_pid])[0], before.read(before.by_pid[go_pid])[0])
        c_preload = c.read(c.by_pid[bundle_pid])[0]
        self.assertEqual(c_preload["m_Container"][0]["second"]["preloadSize"], len(c_preload["m_PreloadTable"]))

        # Adding again reuses the MonoScript already in the bundle.
        again = sm.apply(self.path("c"), self.path("d"), entries)
        self.assertEqual(again["entries"], entries)
        _bundle, d = serialized(self.path("d"))
        self.assertEqual(len(set(d.by_pid) - set(c.by_pid)), 1)

    def test_encryption_is_preserved(self):
        encrypted = not self.src_bytes.startswith(b"UnityFS")
        sm.apply(self.src, self.path("same"), [{"stat": 2, "source": 0, "min": 0, "max": 0.1}])
        with open(self.path("same"), "rb") as handle:
            self.assertEqual(not handle.read(7).startswith(b"UnityFS"), encrypted)
        plain = self.path("plain")
        with open(plain, "wb") as handle:
            handle.write(self.src_bytes if not encrypted else xor_bytes(self.src_bytes))
        sm.apply(plain, self.path("plain-out"), [{"stat": 2, "source": 0, "min": 0, "max": 0.1}])
        with open(self.path("plain-out"), "rb") as handle:
            self.assertTrue(handle.read(7).startswith(b"UnityFS"))
        self.assertTrue(sm.read(self.path("plain-out"))["present"])

    def test_in_place_and_invalid_entries(self):
        target = self.path("inplace")
        shutil.copyfile(self.src, target)
        sm.apply(target, target, [{"stat": 501, "source": 2, "min": 0.1, "max": 0.2}])
        self.assertEqual(len(sm.read(target)["entries"]), 1)
        for bad in ({"stat": 999, "source": 0, "min": 0, "max": 1},
                    {"stat": 1, "source": 9, "min": 0, "max": 1},
                    {"stat": 1, "source": 0, "min": math.nan, "max": 1}):
            with self.assertRaises(ValueError):
                sm.apply(self.src, self.path("bad"), [bad])
        self.assertFalse(os.path.exists(self.path("bad")))


class ParseSpec(unittest.TestCase):
    """weapons --subsystem-modifier values; no bundle needed."""

    def test_names_numbers_and_stock_source(self):
        self.assertEqual(sm.parse_spec("102:4:0:0.5"), {"stat": 102, "source": 4, "min": 0.0, "max": 0.5})
        self.assertEqual(sm.parse_spec("squadron_repair:Hangar:0:0.5"), sm.parse_spec("102:4:0:0.5"))
        self.assertEqual(sm.parse_spec("Marine strength:tech bay:1:2")["source"], 6)
        self.assertEqual(sm.parse_spec("201::1:2")["source"], 2)          # empty source = the stat's own subsystem
        self.assertEqual(sm.parse_spec("605:TechLab:-1:3")["min"], -1.0)

    def test_rejects_bad_specs(self):
        for bad in ("102:4:0", "bogus:1:0:1", "102:galley:0:1", "102:4:x:1", "102:4:0:inf"):
            with self.assertRaises(ValueError, msg=bad):
                sm.parse_spec(bad)

    def test_describe(self):
        line = sm.describe({"stat": 102, "source": 4, "min": 0.0, "max": 0.5})
        self.assertIn("Squadron repair", line); self.assertIn("Hangar", line); self.assertIn("percent, live", line)
        self.assertIn("no stock effect", sm.describe({"stat": 0, "source": 0, "min": 0, "max": 1}))


if __name__ == "__main__":
    unittest.main()
