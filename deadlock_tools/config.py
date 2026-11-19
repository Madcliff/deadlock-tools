"""Shared settings and file-location rules for every Deadlock tool.

Settings live in ONE file, deadlock.json in the project folder (next to this package), and are used by the command-line
tools and the GUI alike:

    levels   folder with the game's level files (level0, level1, ... sharedassets9.assets)
    dll      the game's Assembly-CSharp.dll
    bundles  folder with ship bundles (encrypted game files or decrypted .dec)
    out      output folder (default: out/ in the project folder)
    work     scratch folder for decrypted bundles (default: work/ in the project folder)

Relative paths in deadlock.json are relative to the project folder. Every tool also takes --levels / --dll / --bundles /
--out on the command line, which win over the file.

Output layout - every result keeps the game's own file name, so a folder can be copied straight over the game's files:

    out/levels/    level0 ... sharedassets9.assets
    out/dll/       Assembly-CSharp.dll
    out/bundles/   colonialmercury ... (encrypted, ready for the game)
    work/decrypted/  .dec files made along the way

Edits stack: a tool starts from the file already in out/ when there is one (use --fresh to start again from the
original). The originals are never written to.
"""
import json
import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(PACKAGE_DIR)
SETTINGS_FILE = os.path.join(PROJECT_DIR, "deadlock.json")
OLD_SETTINGS_FILE = os.path.expanduser("~/.deadlock_ships.json")      # the GUI's settings before the reorganisation

KEYS = ("levels", "dll", "bundles", "out", "work")
DEFAULTS = {"levels": "", "dll": "", "bundles": "", "out": "out", "work": "work"}
KINDS = ("levels", "dll", "bundles")                                  # sub-folders of the output folder
HELP = {
    "levels": "folder with the game's level files",
    "dll": "the game's Assembly-CSharp.dll",
    "bundles": "folder with ship bundles",
    "out": "output folder",
    "work": "scratch folder for decrypted bundles",
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
        cfg.update({k: str(v).strip() for k, v in data.items() if k in KEYS and v})
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
    return setting("out", override)


def out_dir(kind, override=None, create=True):
    """out/<kind>, e.g. out/levels. override = a different output folder (its <kind> sub-folder is used)."""
    if kind not in KINDS:
        raise ValueError(kind)
    d = os.path.join(out_root(override), kind)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def work_dir(name="decrypted", create=True):
    d = os.path.join(setting("work"), name)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def latest(original, kind, override=None, fresh=False, quiet=False):
    """The file to start an edit from: the copy already in out/<kind> (earlier edits) unless fresh, else the original."""
    cand = os.path.join(out_root(override), kind, os.path.basename(original))
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
    return path


def add_path_args(ap, *keys):
    """Add the standard --levels / --dll / --bundles / --out options to an argparse parser."""
    for k in keys:
        if k == "out":
            ap.add_argument("-o", "--out", metavar="DIR", help="output folder (default: 'out' setting, results go into its levels/ dll/ bundles/ sub-folders)")
        else:
            ap.add_argument("--" + k, metavar="PATH", help="%s (default: '%s' in deadlock.json)" % (HELP[k], k))
    return ap


def config_main(argv=None):
    """python -m deadlock_tools config [--levels P] [--dll P] [--bundles P] [--out P] [--work P]   (no option = show)"""
    import argparse
    ap = argparse.ArgumentParser(prog="deadlock config", description="Show or change the saved paths (deadlock.json).")
    for k in KEYS:
        ap.add_argument("--" + k, metavar="PATH", help=HELP[k])
    a = ap.parse_args(argv)
    cfg = load()
    changes = {k: getattr(a, k) for k in KEYS if getattr(a, k) is not None}
    if changes:
        for k, v in changes.items():
            cfg[k] = os.path.abspath(os.path.expanduser(v)) if v else DEFAULTS[k]
        save(cfg)
        print("saved", SETTINGS_FILE)
    elif not os.path.isfile(SETTINGS_FILE):
        print("(no %s yet%s)" % (SETTINGS_FILE, ", values below come from " + OLD_SETTINGS_FILE if os.path.isfile(OLD_SETTINGS_FILE) else ""))
    for k in KEYS:
        v = cfg.get(k, "")
        full = resolve(v)
        flag = "" if not full else ("" if os.path.exists(full) else "   (missing)" if k not in ("out", "work") else "   (created when needed)")
        print("  %-8s %s%s" % (k, full or "(not set)", flag))
    return 0


if __name__ == "__main__":
    sys.exit(config_main())
