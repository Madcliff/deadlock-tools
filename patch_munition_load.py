#!/usr/bin/env python3
"""
patch_munition_load.py - make existing saves pick up a ship's NEW munition slot count in battle.

PROBLEM
    After raising MunitionSlots in the level files, the campaign screens show the new count (they read the
    ship database) but battles still show the old count, because each fleet ship in your SAVE stores its own
    munition list (ActiveMunitions) with the old length. The game only rebuilds that list when it is EMPTY.

WHAT THIS DOES
    Patches one 17-byte check in Assembly-CSharp.dll (FleetShip save-file validation) so the list is also
    rebuilt when it is SHORTER than the ship's MunitionSlots. Ships whose list is already long enough are
    untouched. Affected ships get default munitions again (re-pick them in the war room).

USAGE
    python patch_munition_load.py "path/to/Assembly-CSharp.dll"               -> writes Assembly-CSharp.dll.patched
    python patch_munition_load.py "path/to/Assembly-CSharp.dll" -o out.dll
    python patch_munition_load.py "path/to/Assembly-CSharp.dll" --revert      -> restores the original check

    Then: back up the game's Assembly-CSharp.dll, replace it with the .patched file (same name) and LOAD your
    save (the check runs when a save is loaded). Needs the level files already patched with MunitionSlots.

NOTES
    * Only a ship whose saved list is null would now crash on load; the game's own constructor never leaves it
      null, so this should not happen with normal saves.
    * Game updates replace the DLL: run this again afterwards.
"""
import argparse, sys

ORIG    = bytes.fromhex("027b1b020004" "2c09" "027b1b020004" "8e" "2d39")      # null/empty check
PATCHED = bytes.fromhex("00" "027b1b020004" "8e" "69" "06" "7bab300004" "2f39") # len >= MunitionSlots ? skip : rebuild
assert len(ORIG) == len(PATCHED) == 17


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dll"); ap.add_argument("-o", "--out"); ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    d = open(a.dll, "rb").read()
    old, new = (PATCHED, ORIG) if a.revert else (ORIG, PATCHED)
    n = d.count(old)
    if n != 1:
        if d.count(new) == 1: sys.exit("already %s: nothing to do" % ("original" if a.revert else "patched"))
        sys.exit("expected exactly 1 match of the check in this DLL, found %d. This DLL version is different; not patching." % n)
    i = d.index(old)
    out = d[:i] + new + d[i + len(old):]
    dst = a.out or a.dll + (".reverted" if a.revert else ".patched")
    open(dst, "wb").write(out)
    print("patched 17 bytes at file offset 0x%x -> %s" % (i, dst))
    print("Back up the game's original Assembly-CSharp.dll, replace it with this file (same name), then load your save.")


if __name__ == "__main__":
    main()
