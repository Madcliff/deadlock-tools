"""dll check / patch / revert on a DLL rebuilt by the custom-module build (Cecil moves and shares method bodies).

Uses the work tree's Assembly-CSharp.dll when it is a custom-module build; skipped otherwise. Writes only to a temp folder."""
import os, subprocess, sys, tempfile, unittest

from deadlock_tools import dll

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DLL = os.path.join(ROOT, "work", "BSG_Data", "Managed", "Assembly-CSharp.dll")


def custom_build():
    if not os.path.isfile(WORK_DLL): return False
    with open(WORK_DLL, "rb") as stream:
        return dll.is_custom_build(stream.read())


@unittest.skipUnless(custom_build(), "no custom-module build in the work tree")
class CustomBuildDll(unittest.TestCase):
    def run_dll(self, *args):
        return subprocess.run([sys.executable, "-m", "deadlock_tools", "dll", *args], cwd=ROOT, capture_output=True, text=True)

    def test_check_reads_the_shortcut_through_metadata(self):
        self.assertEqual(dll.custom_build_shortcuts(WORK_DLL), "indexed")
        result = self.run_dll("check", WORK_DLL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("built into the custom-module DLL", result.stdout)
        self.assertNotIn("refusing", result.stdout)

    def test_patch_and_revert_all_run(self):
        with tempfile.TemporaryDirectory() as out:
            patched = self.run_dll("patch", WORK_DLL, "--munition-load", "-o", out)
            self.assertEqual(patched.returncode, 0, patched.stdout + patched.stderr)
            target = os.path.join(out, "dll", "Assembly-CSharp.dll")
            self.assertIn("munition load patch: INSTALLED", self.run_dll("check", target).stdout)
            reverted = self.run_dll("revert", target, "-o", os.path.join(out, "r"))
            self.assertEqual(reverted.returncode, 0, reverted.stdout + reverted.stderr)
            after = self.run_dll("check", os.path.join(out, "r", "dll", "Assembly-CSharp.dll")).stdout
            self.assertIn("commander max rank: 10", after)
            self.assertIn("munition load patch: not installed", after)
            self.assertIn("built into the custom-module DLL", after)   # left alone, not broken


if __name__ == "__main__":
    unittest.main()
