#!/usr/bin/env python3
"""Build dist/deadlock.pyz: the whole toolkit as ONE file, to share or keep next to the game.

    python scripts/build_pyz.py
    python deadlock.pyz gui          (or any other tool: python deadlock.pyz shipdb list ...)

Settings (deadlock.json) and the editable work tree are kept next to the .pyz file.

The UI-asset dependencies (UnityPy, TypeTreeGeneratorAPI and everything they need) are ALWAYS bundled:
by default they are installed fresh with pip for this Python and platform; --ui-assets DIR uses an existing
install instead (a folder made with: pip install --target DIR "UnityPy>=1.25,<2" "TypeTreeGeneratorAPI>=0.0.10").
The build fails rather than produce a .pyz without them. The bundled native files only work on the same
Python version and platform the .pyz was built with.
"""
import argparse
import subprocess
import hashlib
import json
from pathlib import Path
import os, shutil, sys, tempfile, zipapp
import sysconfig
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


UI_REQUIREMENTS = ["UnityPy>=1.25,<2", "TypeTreeGeneratorAPI>=0.0.10"]


def install_ui_assets(target):
    """pip-install the UI-asset dependencies into target for the running Python and platform."""
    command = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
               "--no-warn-script-location", "--target", str(target), *UI_REQUIREMENTS]
    result = subprocess.run(command)
    if result.returncode:
        raise SystemExit("could not install the UI-asset dependencies with pip (offline?). "
                         "Install them once with:  pip install --target vendor %s  and rerun with --ui-assets vendor"
                         % " ".join('"%s"' % r for r in UI_REQUIREMENTS))


def check_ui_assets(source):
    """the dependency folder must import cleanly on its own, or the .pyz would fail at run time"""
    probe = "import sys; sys.path.insert(0, sys.argv[1]); import UnityPy; from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator; print(UnityPy.__version__)"
    result = subprocess.run([sys.executable, "-I", "-c", probe, str(source)], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("the UI-asset dependencies in %s do not import with this Python:\n%s" % (source, result.stderr[-2000:]))
    return result.stdout.strip()


def bundle_ui_assets(source, target):
    if not (source / "UnityPy").is_dir() or not (source / "TypeTreeGeneratorAPI").is_dir():
        raise ValueError("--ui-assets must point to a directory with the installed UI dependencies")
    payload = target / "ui-assets.zip"
    with ZipFile(payload, "w", compression=ZIP_DEFLATED) as vendor:
        for file in sorted(source.rglob("*")):
            relative = file.relative_to(source)
            if file.is_file() and "__pycache__" not in relative.parts and relative.parts[0] != "bin":
                vendor.write(file, relative)
    manifest = {"python": sys.implementation.cache_tag, "platform": sysconfig.get_platform(),
                "sha256": hashlib.sha256(payload.read_bytes()).hexdigest()}
    (target / "ui-assets.json").write_text(json.dumps(manifest), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui-assets", type=Path, metavar="DIRECTORY",
                        help="use UI dependencies already installed in DIRECTORY (default: install them fresh with pip)")
    args = parser.parse_args()
    dist = os.path.join(ROOT, "dist"); os.makedirs(dist, exist_ok=True)
    target = os.path.join(dist, "deadlock.pyz")
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(os.path.join(ROOT, "deadlock_tools"), os.path.join(tmp, "deadlock_tools"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        os.makedirs(os.path.join(tmp, "scripts"))
        for name in ("build_custom_modules.sh", "build_commander_modifiers.sh",
                     "build_commander_ability_levels.sh", "add_ship_type_enum.sh",
                     "add_ship_type_enum.cs", "merge_custom_modules.cs"):
            shutil.copyfile(os.path.join(ROOT, "scripts", name), os.path.join(tmp, "scripts", name))
        shutil.copytree(os.path.join(ROOT, "dll_modules"), os.path.join(tmp, "dll_modules"),
                        ignore=shutil.ignore_patterns("*.md"))
        shutil.copyfile(os.path.join(ROOT, "LICENSE"), os.path.join(tmp, "LICENSE"))
        source = args.ui_assets
        if source is None:
            source = Path(tmp) / ".ui-assets-install"
            print("installing the UI-asset dependencies for bundling:", ", ".join(UI_REQUIREMENTS))
            install_ui_assets(source)
        version = check_ui_assets(source)
        bundle_ui_assets(source, Path(tmp))
        if source.parent == Path(tmp):
            shutil.rmtree(source)
        print("bundled UnityPy", version, "for", sys.implementation.cache_tag, sysconfig.get_platform())
        zipapp.create_archive(tmp, target, interpreter="/usr/bin/env python3", main="deadlock_tools.__main__:main", compressed=True)
    print("wrote", target, "(%d KB)" % (os.path.getsize(target) // 1024))


if __name__ == "__main__":
    sys.exit(main())
