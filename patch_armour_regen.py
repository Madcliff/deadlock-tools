#!/usr/bin/env python3
"""
patch_armour_regen.py - make flagged ships regenerate their ARMOUR in battle, in step with the Regenerative Hull Resin module.

WHAT IT DOES
    Patches Assembly-CSharp.dll so the Regenerative Hull Resin module's tick (every tick, same rate as its hull repair,
    already scaled by the ship's Engineering subsystem) also restores the ship's normal armour:
        every hull quadrant:  armour = Min(armour + hullRepairRate * FACTOR, that quadrant's maximum)
    Only quadrants below their maximum change, so it works exactly "when the armour is damaged".
    The hull still repairs as before.

THE FLAG (which ships regenerate armour)
    The flag is the SIGN of the module's hullPerTick value:
        positive  (e.g.  0.2)  -> normal module: hull repair only, as in the unpatched game
        negative  (e.g. -0.2)  -> hull repair of 0.2 AND armour regeneration
    So every existing ship (positive values) is unchanged; only ships you give a negative value regenerate armour.
    Set it with deadlock_weapons.py:   --hull-regen 0.2 --armour-regen     (adds the module with -0.2)
                                       --armour-regen                      (on a ship that already has the module)

USAGE
    python patch_armour_regen.py "path/to/Assembly-CSharp.dll"                 -> writes Assembly-CSharp.dll.patched
    python patch_armour_regen.py "path/to/Assembly-CSharp.dll" --factor 5      armour per tick = hull rate x 5 (default 1)
    python patch_armour_regen.py "path/to/Assembly-CSharp.dll" -o out.dll
    python patch_armour_regen.py "path/to/Assembly-CSharp.dll" --revert        -> restores the original methods
    (re-running with a different --factor on an already patched DLL just changes the factor)

    FACTOR is applied to EACH of the 6 quadrants every tick. The Mercury has 1000-1800 armour per quadrant, so use a
    large hull-regen value or factor if you want it to be noticeable. Back up the game's DLL first.
NOTES
    Untested in the game. Changes two methods in place (file size unchanged): RegenerativeHullResinModule's tick and an
    unused decoy method in ArmourInfo that now holds the armour code. The script refuses a DLL it does not recognise.
    The armour bar may only visibly update on the next hit if the HUD does not poll armour.
"""
import argparse, struct, sys

TICK_OFF = 1679060
TICK_ORIG = bytes.fromhex("133004004800000010000011027bb91d00046f683b00062d012a027bba1d0004027bb81d00046f8d5f00060a027bb91d00046f6c3b0006027bb91d00047b16130004340f027bb91d00040617166f3e5c0006262a")
TICK_NEW = bytes.fromhex("133004004200000010000011027bb91d00046f683b00062d012a027bb91d00046f3e3b0006027bba1d0004027bb81d00046f8d5f00061616283b2f00060a027bb91d00040617166f3e5c0006262a000000000000")
H_OFF = 879476
H_ORIG = bytes.fromhex("13300300b800000025000011040a027b6a0f0004069822008043443644027b6a0f0004069803361d052c11027b6a0f0004068f36000001254e0359562200807e4410012b1c03027b6a0f00040698591001052c0d027b6a0f000406220000ea43a00322008038443306220020c7442a027b680f0004069822000005443644027b680f0004069803361d052c11027b680f0004068f36000001254e035956220000664410012b1c03027b680f00040698591001052c0d027b680f0004062200c0f744a0032a")
H_NEW = bytes.fromhex("133006005e00000025000011032200000000345403651001022c4d027b680f00042c45027b690f00042c3d160a06027b680f00048e692f30027b680f000406027b680f0004069803220000803f5a582200000000027b690f00040698289d05000aa00617580a2bc5032a000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000")
FACTOR_OFF = 73      # offset of the factor float inside the helper
assert len(TICK_ORIG) == len(TICK_NEW) and len(H_ORIG) == len(H_NEW)


def helper(factor):
    b = bytearray(H_NEW); struct.pack_into("<f", b, FACTOR_OFF, factor); return bytes(b)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dll"); ap.add_argument("-o", "--out"); ap.add_argument("--revert", action="store_true")
    ap.add_argument("--check", action="store_true", help="only report whether this DLL has the armour regeneration patch (and its factor)")
    ap.add_argument("--factor", type=float, default=1.0, help="armour restored per tick = hull repair rate x factor (default 1)")
    a = ap.parse_args()
    d = bytearray(open(a.dll, "rb").read())
    cur_tick = bytes(d[TICK_OFF:TICK_OFF + len(TICK_ORIG)]); cur_h = bytes(d[H_OFF:H_OFF + len(H_ORIG)])
    masked = lambda x: x[:FACTOR_OFF] + x[FACTOR_OFF + 4:]
    patched = cur_tick == TICK_NEW and masked(cur_h) == masked(H_NEW)
    original = cur_tick == TICK_ORIG and cur_h == H_ORIG
    if not (patched or original):
        sys.exit("this DLL is not the expected version (the two methods are not at the expected place); not changing it")
    if a.check:
        print("armour regeneration patch: %s" % ("INSTALLED, factor %g" % struct.unpack_from("<f", cur_h, FACTOR_OFF)[0] if patched else "not installed (original code)"))
        return
    if a.revert:
        if original: sys.exit("already original: nothing to do")
        t, h = TICK_ORIG, H_ORIG
    else:
        t, h = TICK_NEW, helper(a.factor)
    d[TICK_OFF:TICK_OFF + len(t)] = t; d[H_OFF:H_OFF + len(h)] = h
    dst = a.out or a.dll + (".reverted" if a.revert else ".patched")
    open(dst, "wb").write(d)
    print(("restored original methods" if a.revert else "armour regeneration installed (factor %g)" % a.factor), "->", dst)
    if not a.revert: print("Back up the game's original Assembly-CSharp.dll, then replace it with this file (same name).")


if __name__ == "__main__":
    main()
