"""core.workspace: one transaction for every save - backup, atomic replace, all-or-nothing."""
import os, tempfile, unittest
from unittest import mock

from deadlock_tools.core.workspace import Workspace


class TransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = self.tmp.name
        self.orig = os.path.join(root, "game"); os.makedirs(os.path.join(self.orig, "Managed"))
        for path, data in ((os.path.join(self.orig, "level0"), b"original"), (os.path.join(self.orig, "Managed", "Assembly-CSharp.dll"), b"dll")):
            with open(path, "wb") as fh: fh.write(data)
        self.ws = Workspace(self.orig, os.path.join(self.orig, "Managed", "Assembly-CSharp.dll"), os.path.join(root, "work"))

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, path):
        with open(path, "rb") as fh: return fh.read()

    def test_source_prefers_work_copy(self):
        self.assertEqual(self.ws.source("levels", "level0"), os.path.join(self.orig, "level0"))
        with self.ws.transaction("t") as tx:
            tx.stage(self.ws.target("levels", "level0"), b"edit1")
        self.assertEqual(self.ws.source("levels", "level0"), self.ws.target("levels", "level0"))
        self.assertEqual(self.ws.source("levels", "level0", fresh=True), os.path.join(self.orig, "level0"))

    def test_commit_backs_up_what_it_replaces(self):
        target = self.ws.target("levels", "level0")
        with self.ws.transaction("first") as tx: tx.stage(target, b"edit1")
        with self.ws.transaction("second") as tx: tx.stage(target, b"edit2")
        self.assertEqual(self.read(target), b"edit2")
        self.assertTrue(tx.backup and os.path.isfile(os.path.join(tx.backup, "level0")))
        self.assertEqual(self.read(os.path.join(tx.backup, "level0")), b"edit1")
        self.assertEqual(self.read(os.path.join(self.orig, "level0")), b"original")

    def test_failed_verify_writes_nothing(self):
        a, b = self.ws.target("levels", "level0"), self.ws.target("levels", "level1")
        def bad(_data): raise ValueError("nope")
        with self.assertRaises(ValueError):
            with self.ws.transaction("t") as tx:
                tx.stage(a, b"x"); tx.stage(b, b"y", verify=bad)
        self.assertFalse(os.path.exists(a) or os.path.exists(b))

    def test_rollback_when_a_replace_fails(self):
        a, b = self.ws.target("levels", "level0"), self.ws.target("levels", "level1")
        with self.ws.transaction("seed") as tx: tx.stage(a, b"old-a"); tx.stage(b, b"old-b")
        real = os.replace; calls = []
        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 2: raise OSError("disk full")
            return real(src, dst)
        with mock.patch("os.replace", flaky):
            with self.assertRaises(OSError):
                with self.ws.transaction("t") as tx: tx.stage(a, b"new-a"); tx.stage(b, b"new-b")
        self.assertEqual(self.read(a), b"old-a"); self.assertEqual(self.read(b), b"old-b")
        self.assertFalse([f for f in os.listdir(os.path.dirname(a)) if f.endswith("-tmp")])

    def test_exception_in_block_writes_nothing(self):
        target = self.ws.target("levels", "level0")
        with self.assertRaises(RuntimeError):
            with self.ws.transaction("t") as tx:
                tx.stage(target, b"x"); raise RuntimeError("abort")
        self.assertFalse(os.path.exists(target))

    def test_refuses_to_overwrite_original(self):
        with self.assertRaises(ValueError):
            with self.ws.transaction("t") as tx:
                original = os.path.join(self.orig, "level0")
                tx.stage(original, b"x", original=original)
        self.assertEqual(self.read(os.path.join(self.orig, "level0")), b"original")


if __name__ == "__main__":
    unittest.main()
