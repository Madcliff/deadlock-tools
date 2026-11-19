#!/usr/bin/env python3
"""
patch_squadron_revive.py - ships with a Munition Factory module rebuild their DESTROYED squadrons.

WHAT IT DOES
    Patches Assembly-CSharp.dll so that, in every battle, a FLAGGED ship that has the Munition Factory module (the passive
    munition-resupply module of the Cylon Argos) rebuilds squadrons that were DESTROYED (the empty carrier slots):
        a destroyed squadron (no hull left) is put back in its carrier slot, docked and ready to relaunch, with ONE fighter,
        and the ship's NORMAL squadron repair (the repair crew's docked-squadron repair) is started on it, so the
        numbers climb back by the game's own rules rather than from nothing to full at once.
        --turns N adds an optional delay first: N x 120 progress, counted at the module's resupply rate (Armory
        MunitionResupplyRange / 100, so it is slower when the Armory is damaged). Default 0 = revive at once.
    Squadrons that still have hull (only damaged) are NOT touched (use the normal docking repair for those).

THE FLAG (which ships do this)
    The flag is a ship setting that only the editor used (ShipInfo "ShowExclusionInEditor", 0 or 1). Ships are 0 unless you
    set it, so every ship (including the Argos) behaves exactly as before; only ships you flag rebuild squadrons.
    Set it with deadlock_weapons.py:   --munition-factory --squadron-revive    (adds the module AND the flag)
                                       --squadron-revive                        (flag a ship that already has the module)

    It works in all battle types: the game's own "revive squadron" routine was restricted to Survival battles; that
    restriction is removed.  Ships without the flag are unaffected, enemy ships included.

USAGE
    python patch_squadron_revive.py "path/to/Assembly-CSharp.dll"               -> writes Assembly-CSharp.dll.patched
    python patch_squadron_revive.py "path/to/Assembly-CSharp.dll" --turns 60    delay before the revive (default 0 = at once; 8 = the nuke time)
    python patch_squadron_revive.py "path/to/Assembly-CSharp.dll" -o out.dll
    python patch_squadron_revive.py "path/to/Assembly-CSharp.dll" --revert      -> restores the original methods
    (re-running with a different --turns on an already patched DLL just changes the time)
    TURNS x 120 is counted in module ticks; at full rate (1.0) a destroyed squadron takes TURNS x 120 ticks.

NOTES
    Untested in the game. Changes three places in place (file size unchanged): MunitionFactoryModule.OnGameEvent, an unused
    decoy method in the same class that now holds the revive code, and one byte in ShipInfo.ReviveSquadron.
    The progress is stored in the squadron's (unused) LastRoundYaw field. The script refuses a DLL it does not recognise.
    Works together with patch_armour_regen.py (different methods). Back up the game's DLL first.
"""
import argparse, struct, sys

OGE_OFF = 1587076
OGE_ORIG = bytes.fromhex("133003004b0000000000000002030428441501060214280800000a2d0e02284700000a14280800000a2c012a031f142e1f031f1f2e06031f452e082a0228e74f00062a0204742916000228f34f00062a0228f94f00062a")
OGE_NEW = bytes.fromhex("13300300420000000000000002030428441501060214280800000a2d0f031f142e25031f1f2e06031f452e0e2a0228e74f00060228e34f00062a0204742916000228f34f00062a0228f94f00062a000000000000000000")
H_OFF = 1586868
H_ORIG = bytes.fromhex("13300600c3000000f8040011027b591b00046f5a5d00060a062c04068e2d012a027b5b1b00046f050a000a068e692e300272e09503701e8d140000012517027b5b1b00046f050a000a8c2d000001a22517068e698c2d000001a228dd1701062a160b2b5c027b5b1b0004076f060a000a7b531b00042d450607a3f10400020c087bc6200004087bc52000042f2f02727bca00701d8d140000012516078c2d000001a22516027b591b00046f3600000aa228bb170106020728eb4f00060717580b07027b5b1b00046f050a000a32962a")
H_NEW = bytes.fromhex("13300600b2000000740300112b012a027b591b00040a067b1a1300042cf0066f683b00062ce8027b5a1b00042ce0160b07067be82000046f4b06000a2fd0067be8200004076f4906000a0c086f683b00062d6208257be7200004027b5a1b00046f405f0006587de7200004087be72000042200006145373d0822000000007de7200004086f1b5d0006087bf12000047b801400048e2c1e08087bf12000047b8014000416986f733b0006087bf12000046fc13f00060717580b3872ffffff0000000000000000000000000000000000")
TIMER_OFF = 114     # offset of the "ticks needed" float inside the helper
REV_OFF = 1828632         # ShipInfo.ReviveSquadron: BattleType == Survival check (ldc.i4.6 -> dup)
REV_ORIG, REV_NEW = 0x1c, 0x25
assert len(OGE_ORIG) == len(OGE_NEW) and len(H_ORIG) == len(H_NEW)


def helper(turns):
    b = bytearray(H_NEW); struct.pack_into("<f", b, TIMER_OFF, turns * 120.0); return bytes(b)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dll"); ap.add_argument("-o", "--out"); ap.add_argument("--revert", action="store_true")
    ap.add_argument("--check", action="store_true", help="only report whether this DLL has the squadron revive patch (and the rebuild time)")
    ap.add_argument("--turns", type=float, default=0.0, help="delay before a destroyed squadron is revived, in turns of 120 ticks at full resupply rate (default 0 = at once; the normal squadron repair then does the restoring. 8 = the time a nuke takes)")
    a = ap.parse_args()
    if a.turns < 0: sys.exit("--turns cannot be negative")
    d = bytearray(open(a.dll, "rb").read())
    cur_o = bytes(d[OGE_OFF:OGE_OFF + len(OGE_ORIG)]); cur_h = bytes(d[H_OFF:H_OFF + len(H_ORIG)]); cur_r = d[REV_OFF]
    masked = lambda x: x[:TIMER_OFF] + x[TIMER_OFF + 4:]
    patched = cur_o == OGE_NEW and masked(cur_h) == masked(H_NEW) and cur_r == REV_NEW
    original = cur_o == OGE_ORIG and cur_h == H_ORIG and cur_r == REV_ORIG
    if not (patched or original):
        sys.exit("this DLL is not the expected version (the methods are not at the expected place); not changing it")
    if a.check:
        print("squadron revive patch: %s" % ("INSTALLED, %g turns at full rate" % (struct.unpack_from("<f", cur_h, TIMER_OFF)[0] / 120) if patched else "not installed (original code)"))
        return
    if a.revert:
        if original: sys.exit("already original: nothing to do")
        o, h, r = OGE_ORIG, H_ORIG, REV_ORIG
    else:
        o, h, r = OGE_NEW, helper(a.turns), REV_NEW
    d[OGE_OFF:OGE_OFF + len(o)] = o; d[H_OFF:H_OFF + len(h)] = h; d[REV_OFF] = r
    dst = a.out or a.dll + (".reverted" if a.revert else ".patched")
    open(dst, "wb").write(d)
    print(("restored original methods" if a.revert else "squadron revive installed (%g turns = %g ticks at full rate)" % (a.turns, a.turns * 120)), "->", dst)
    if not a.revert: print("Back up the game's original Assembly-CSharp.dll, then replace it with this file (same name).")


if __name__ == "__main__":
    main()
