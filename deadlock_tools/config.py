"""Shared settings and file-location rules for every Deadlock tool.

Settings live in ONE file, deadlock.json in the project folder (next to this package), and are used by the command-line
tools and the GUI alike:

    levels   folder with the game's level files (level0, level1, ... sharedassets9.assets)
    dll      the game's Assembly-CSharp.dll
    bundles  folder with ship bundles (encrypted game files or decrypted .dec)
    work     editable work folder (default: work/ in the project folder)

Relative paths in deadlock.json are relative to the project folder. Every tool also takes --levels / --dll / --bundles
on the command line, which win over the file. Optional command-line -o overrides are temporary per-run destinations.

The work folder mirrors the game's BSG_Data layout:

    work/BSG_Data/                    level0 ... sharedassets9.assets
    work/BSG_Data/Managed/            Assembly-CSharp.dll
    work/BSG_Data/StreamingAssets/    ship bundles (encrypted, ready for the game)
    work/.deadlock-tools/             editor staging and decrypted scratch

The work copy takes priority over the configured source. Edits stack there; --fresh starts again from the configured
original. The originals are never written to.
"""
import json
import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(PACKAGE_DIR)
ZIPAPP = None                                                         # set when running from the single-file deadlock.pyz
if getattr(sys, "frozen", False):
    PROJECT_DIR = os.path.dirname(os.path.abspath(sys.executable))
elif os.path.isfile(PROJECT_DIR):
    ZIPAPP, PROJECT_DIR = PROJECT_DIR, os.path.dirname(PROJECT_DIR)   # settings and work/ then live next to the .pyz
SETTINGS_FILE = os.path.join(PROJECT_DIR, "deadlock.json")
OLD_SETTINGS_FILE = os.path.expanduser("~/.deadlock_ships.json")      # the GUI's settings before the reorganisation

KEYS = ("levels", "dll", "bundles", "work")
DEFAULTS = {"levels": "", "dll": "", "bundles": "", "work": "work"}
WORK_PATHS = {"levels": (), "dll": ("Managed",), "bundles": ("StreamingAssets",)}
KINDS = tuple(WORK_PATHS)
HELP = {
    "levels": "folder with the game's level files",
    "dll": "the game's Assembly-CSharp.dll",
    "bundles": "folder with ship bundles",
    "work": "editable work folder",
}


def load():
    """The settings as a dict with every key present (missing ones get their default)."""
    cfg = dict(DEFAULTS)
    path = SETTINGS_FILE if os.path.isfile(SETTINGS_FILE) else None
    if path is None and os.path.isfile(OLD_SETTINGS_FILE):
        path = OLD_SETTINGS_FILE
    if path:
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            raise SystemExit("cannot read %s: %s" % (path, e))
        keys = KEYS if path == SETTINGS_FILE else ("levels", "dll", "bundles")   # the old file's 'out' meant something else
        cfg.update({k: str(v).strip() for k, v in data.items() if k in keys and v})
        if path == SETTINGS_FILE and data.get("out"):
            cfg["_legacy_out"] = str(data["out"]).strip()
    return cfg


def save(cfg):
    data = {k: (cfg.get(k) or "").strip() for k in KEYS}
    with open(SETTINGS_FILE, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return data


def resolve(path):
    """Absolute path; relative paths are taken from the project folder, ~ is expanded. '' stays ''."""
    if not path:
        return ""
    path = os.path.expanduser(path)
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(PROJECT_DIR, path))


def setting(key, override=None, required=True):
    """A path setting: the command-line value if given (relative to the current folder, as usual on a command line),
    otherwise the one in deadlock.json (relative to the project folder)."""
    if override:
        return os.path.abspath(os.path.expanduser(override))
    value = resolve(load().get(key, ""))
    if not value and required:
        raise SystemExit("no '%s' path set (%s): pass --%s PATH, or save it once with:  python -m deadlock_tools config --%s PATH"
                         % (key, HELP[key], key, key))
    return value


def out_root(override=None):
    return os.path.abspath(os.path.expanduser(override)) if override else setting("work")


def migrate_legacy_out(work_override=None):
    """Move old out/<kind> edits into the merge-ready work tree once, preserving existing work files."""
    cfg = load()
    legacy = cfg.get("_legacy_out")
    if not legacy: return False
    import shutil
    source_root = resolve(legacy)
    work_root = os.path.abspath(os.path.expanduser(work_override)) if work_override else setting("work")
    for kind, parts in WORK_PATHS.items():
        old_dir = os.path.join(source_root, kind)
        new_dir = os.path.join(work_root, "BSG_Data", *parts)
        if not os.path.isdir(old_dir): continue
        os.makedirs(new_dir, exist_ok=True)
        for filename in os.listdir(old_dir):
            source, target = os.path.join(old_dir, filename), os.path.join(new_dir, filename)
            if os.path.isfile(source) and not os.path.exists(target): shutil.copy2(source, target)
    old_scratch = os.path.join(work_root, "decrypted")
    new_scratch = os.path.join(work_root, ".deadlock-tools", "decrypted")
    if os.path.isdir(old_scratch):
        for root, _dirs, files in os.walk(old_scratch):
            relative = os.path.relpath(root, old_scratch)
            target_root = new_scratch if relative == "." else os.path.join(new_scratch, relative)
            os.makedirs(target_root, exist_ok=True)
            for filename in files:
                source, target = os.path.join(root, filename), os.path.join(target_root, filename)
                if not os.path.exists(target): shutil.copy2(source, target)
    save(cfg)
    return True


def out_dir(kind, override=None, create=True):
    """Return a work-tree destination, or <override>/<kind> for a one-off CLI destination."""
    if kind not in WORK_PATHS: raise ValueError(kind)
    if not override: migrate_legacy_out()
    d = os.path.join(out_root(override), kind) if override else os.path.join(setting("work"), "BSG_Data", *WORK_PATHS[kind])
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def work_dir(name="decrypted", create=True):
    d = os.path.join(setting("work"), ".deadlock-tools", name)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def latest(original, kind, override=None, fresh=False, quiet=False):
    """Use the work-tree copy as the continuing source unless --fresh or a one-off -o is given."""
    cand = os.path.join(out_dir(kind, override, create=False), os.path.basename(original))
    if not fresh and os.path.isfile(cand) and not same_file(cand, original):
        if not quiet:
            print("   (continuing from your earlier edits in %s; use --fresh to start from the original instead)" % cand)
        return cand
    return original


def write(path, data, original=None):
    """Write an output file, creating its folder. Refuses to overwrite the game's original file."""
    if original and os.path.exists(path) and same_file(path, original):
        raise SystemExit("refusing to overwrite the original %s - choose another output folder" % original)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        if hasattr(os, 'fsync'):
            os.fsync(fh.fileno())
    return path


def add_path_args(ap, *keys):
    """Add source-path options; keep the internal staging override out of help text."""
    for k in keys:
        if k == "out":
            import argparse
            ap.add_argument("-o", "--out", metavar="DIR", help=argparse.SUPPRESS)
        else:
            ap.add_argument("--" + k, metavar="PATH", help="%s (default: '%s' in deadlock.json)" % (HELP[k], k))
    return ap


def config_main(argv=None):
    """python -m deadlock_tools config [--levels P] [--dll P] [--bundles P] [--work P]   (no option = show)"""
    import argparse
    ap = argparse.ArgumentParser(prog="deadlock config", description="Show or change the saved paths (deadlock.json).")
    for k in KEYS:
        ap.add_argument("--" + k, metavar="PATH", help=HELP[k])
    a = ap.parse_args(argv)
    cfg = load()
    changes = {k: getattr(a, k) for k in KEYS if getattr(a, k) is not None}
    if changes:
        migrate_legacy_out(changes.get("work"))
        cfg = load()
        for k, v in changes.items():
            cfg[k] = os.path.abspath(os.path.expanduser(v)) if v else DEFAULTS[k]
        save(cfg)
        print("saved", SETTINGS_FILE)
    elif not os.path.isfile(SETTINGS_FILE):
        print("(no %s yet%s)" % (SETTINGS_FILE, ", values below come from " + OLD_SETTINGS_FILE if os.path.isfile(OLD_SETTINGS_FILE) else ""))
    for k in KEYS:
        v = cfg.get(k, "")
        full = resolve(v)
        flag = "" if not full else ("" if os.path.exists(full) else "   (missing)" if k != "work" else "   (created when needed)")
        print("  %-8s %s%s" % (k, full or "(not set)", flag))
    return 0


if __name__ == "__main__":
    sys.exit(config_main())
