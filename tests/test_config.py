"""Unit tests for the shared settings / output rules (no game files needed).  Run:  python -m unittest"""
import json, os, tempfile, unittest
from unittest import mock

from deadlock_tools import config


def put(path, data):
    with open(path, "wb") as fh: fh.write(data)


def get(path):
    with open(path, "rb") as fh: return fh.read()


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); d = self.tmp.name
        self.patches = [mock.patch.object(config, "PROJECT_DIR", d), mock.patch.object(config, "SETTINGS_FILE", os.path.join(d, "deadlock.json")),
                        mock.patch.object(config, "OLD_SETTINGS_FILE", os.path.join(d, "old.json"))]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def test_defaults_and_relative_paths(self):
        self.assertEqual(config.load()["out"], "out")
        self.assertEqual(config.out_dir("levels", create=False), os.path.join(self.tmp.name, "out", "levels"))

    def test_saved_settings_and_override(self):
        config.save({"dll": "/game/Assembly-CSharp.dll", "out": "results"})
        self.assertEqual(config.setting("dll"), "/game/Assembly-CSharp.dll")
        self.assertEqual(config.out_root(), os.path.join(self.tmp.name, "results"))
        self.assertEqual(config.setting("dll", "/other.dll"), "/other.dll")

    def test_old_gui_settings_are_used_until_deadlock_json_exists(self):
        put(config.OLD_SETTINGS_FILE, json.dumps({"levels": "/old/levels", "out": "/old/out"}).encode())
        self.assertEqual(config.load()["levels"], "/old/levels")
        self.assertEqual(config.load()["out"], "out")              # the old 'out' was the level folder itself: not carried over
        config.save({"levels": "/new/levels"})
        self.assertEqual(config.load()["levels"], "/new/levels")

    def test_missing_setting_explains_how_to_fix(self):
        with self.assertRaises(SystemExit) as e: config.setting("dll")
        self.assertIn("--dll", str(e.exception))

    def test_latest_prefers_earlier_output_unless_fresh(self):
        orig = os.path.join(self.tmp.name, "level0"); put(orig, b"a")
        self.assertEqual(config.latest(orig, "levels", quiet=True), orig)
        done = os.path.join(config.out_dir("levels"), "level0"); put(done, b"b")
        self.assertEqual(config.latest(orig, "levels", quiet=True), done)
        self.assertEqual(config.latest(orig, "levels", fresh=True, quiet=True), orig)

    def test_write_refuses_to_overwrite_original(self):
        orig = os.path.join(self.tmp.name, "x"); put(orig, b"a")
        with self.assertRaises(SystemExit): config.write(orig, b"b", orig)
        self.assertEqual(get(orig), b"a")


if __name__ == "__main__":
    unittest.main()
