"""Fleet-limit DLL options (deadlock-fleet.txt read by the custom-module DLL's CustomSkirmishFleetCap):
dll patch / revert --skirmish-ships, --fleet-caps, --campaign-ships. No game files needed.
"""
import os, subprocess, sys, tempfile, unittest

from deadlock_tools import skirmish as SK

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)


class SettingsFile(unittest.TestCase):
    def test_update_render_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, SK.FILE)
            settings, log = SK.update(path, ships=20, fleet_caps=SK.parse_caps("64000, 32000"), campaign_ships=14)
            self.assertEqual(SK.read(path), {"ships": 20, "fleet_caps": (32000, 64000), "campaign_ships": 14})
            self.assertEqual(len(log), 3)
            self.assertEqual(SK.update(path, ships=20)[1], [])                      # no change, no log
            SK.update(path, fleet_caps=())                                          # 'none': no extra caps
            self.assertEqual(SK.read(path)["fleet_caps"], ())
            SK.update(path, clear_ships=True, clear_caps=True, clear_campaign=True)
            self.assertFalse(os.path.exists(path))                                 # empty settings: file removed

    def test_old_file_name_is_read_and_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, SK.OLD_FILE), "w").write("ships 18\n")
            path = os.path.join(tmp, SK.FILE)
            self.assertEqual(SK.read(path)["ships"], 18)
            SK.update(path, campaign_ships=9)
            self.assertFalse(os.path.exists(os.path.join(tmp, SK.OLD_FILE)))
            self.assertEqual(SK.read(path), {"ships": 18, "fleet_caps": None, "campaign_ships": 9})

    def test_refusals(self):
        for bad in ("5", "abc", "2000000"):
            with self.assertRaises(ValueError):
                SK.parse_caps(bad)
        with self.assertRaises(ValueError):
            SK.check_ships(0)
        with self.assertRaises(ValueError):
            SK.check_ships(100)
        self.assertEqual(SK.parse_caps("none"), ())

    def test_describe(self):
        self.assertEqual(SK.describe({"ships": None, "fleet_caps": None, "campaign_ships": None}),
                         ["skirmish ships per fleet: from the level data", "extra fleet caps: default (32000, 64000)",
                          "campaign capital ships per fleet: from the level data"])


class DllCommand(unittest.TestCase):
    def run_dll(self, *args):
        env = dict(os.environ, PYTHONPATH=ROOT)
        return subprocess.run([sys.executable, "-m", "deadlock_tools", "dll", *args], capture_output=True, text=True,
                              cwd=ROOT, env=env, timeout=300)

    def test_patch_and_revert(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_dll("patch", "-o", tmp, "--skirmish-ships", "20", "--fleet-caps", "32000,64000", "--campaign-ships", "14")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            path = os.path.join(tmp, "bundles", SK.FILE)
            self.assertEqual(SK.read(path), {"ships": 20, "fleet_caps": (32000, 64000), "campaign_ships": 14})
            self.assertFalse(os.path.exists(os.path.join(tmp, "dll")))             # settings only: the DLL is not touched
            r = self.run_dll("revert", "-o", tmp, "--campaign-ships")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIsNone(SK.read(path)["campaign_ships"])
            r = self.run_dll("patch", "-o", tmp, "--skirmish-ships", "500")
            self.assertNotEqual(r.returncode, 0)
