#!/usr/bin/env python3
"""Build dist/deadlock.pyz: the whole toolkit as ONE file, to share or keep next to the game.

    python scripts/build_pyz.py
    python deadlock.pyz gui          (or any other tool: python deadlock.pyz shipdb list ...)

Settings (deadlock.json) and the editable work tree are kept next to the .pyz file.
"""
import os, shutil, sys, tempfile, zipapp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    dist = os.path.join(ROOT, "dist"); os.makedirs(dist, exist_ok=True)
    target = os.path.join(dist, "deadlock.pyz")
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(os.path.join(ROOT, "deadlock_tools"), os.path.join(tmp, "deadlock_tools"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(os.path.join(ROOT, "LICENSE"), os.path.join(tmp, "LICENSE"))
        zipapp.create_archive(tmp, target, interpreter="/usr/bin/env python3", main="deadlock_tools.__main__:main", compressed=True)
    print("wrote", target, "(%d KB)" % (os.path.getsize(target) // 1024))


if __name__ == "__main__":
    sys.exit(main())
