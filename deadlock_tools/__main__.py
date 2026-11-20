"""python -m deadlock_tools <tool> [options]      (run a tool with -h for its own help)

tools:
  config    show or save the source folders, DLL file, and work folder
  xor       decrypt / encrypt ship bundles
  weapons   edit a ship bundle: turrets, carrier/munition slots, systems, abilities
  shipdb    read and edit the ship / munition / research / commander tables in the level files
  dll       patch Assembly-CSharp.dll (armour regen, squadron revive, max rank, command nodes, munition load)
  gui       the graphical editor in your web browser (all of the above as forms)

Results continue in the configured work folder's BSG_Data tree; edits stack until you pass --fresh.
"""
import importlib
import sys

TOOLS = {
    "config": ("deadlock_tools.config", "config_main"),
    "xor": ("deadlock_tools.bundle_xor", "main"),
    "weapons": ("deadlock_tools.weapons", "main"),
    "shipdb": ("deadlock_tools.shipdb", "main"),
    "dll": ("deadlock_tools.dll", "main"),
    "gui": ("deadlock_tools.editor", "main"),
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    name = argv.pop(0)
    if name not in TOOLS:
        print("unknown tool %r\n\n%s" % (name, __doc__.strip()), file=sys.stderr)
        return 2
    mod, func = TOOLS[name]
    sys.argv = ["deadlock " + name] + argv
    return getattr(importlib.import_module(mod), func)() or 0


if __name__ == "__main__":
    sys.exit(main())
