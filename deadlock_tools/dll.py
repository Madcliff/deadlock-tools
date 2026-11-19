#!/usr/bin/env python3
"""
deadlock_dll.py - every Assembly-CSharp.dll patch for the Deadlock tools, in one place.

  python deadlock_dll.py patch  Assembly-CSharp.dll [--armour-factor F] [--revive-turns N] [--max-rank N] [--command-nodes N [--command-step S] [--command-first-rank R]] [-o out.dll]
  python deadlock_dll.py check  Assembly-CSharp.dll
  python deadlock_dll.py revert Assembly-CSharp.dll [--armour] [--revive] [--max-rank] [--command-nodes] [-o out.dll]     (no option = revert all four)

PATCHES (each is only applied when its option is given; re-running on a patched DLL just changes the values)
  --armour-factor F   armour regeneration for ships built with deadlock_weapons.py --armour-regen (armour per tick = hull rate x F)
  --revive-turns N    squadron revive for ships built with --squadron-revive (N turns; 0 = rebuild at the next opportunity)
  --max-rank N        commander max rank (game default 10), for deadlock_shipdb.py setcommanders
  --command-nodes N   number of nodes in the Command (fleet-size) column of the commander skill tree. Game default: 4 nodes, available at
                      commander rank 1/3/5/7. Node i becomes available at rank R + i*S, with --command-first-rank R (default 1) and
                      --command-step S (default 1 = one node per rank: N=10 gives ranks 1-10). The node count is hard-coded in the
                      DLL, so setcommanders alone cannot change it.
Default output: <dll>.patched (the original is never touched). Back up the game's DLL, then replace it with the result.
The single-patch tools remain available as: python deadlock_dll.py armour ... / revive ... (their own options: --factor/--turns/--check/--revert).
"""
import argparse, os, re, shutil, struct, sys, tempfile

# ---- the individual patches (each also available on its own: 'armour' / 'revive') ----
def _dll_armour(argv=None):
    """
ARMOUR REGENERATION - make flagged ships regenerate their ARMOUR in battle, in step with the Regenerative Hull Resin module.

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
    python deadlock_dll.py armour Assembly-CSharp.dll                 -> writes Assembly-CSharp.dll.patched
    python deadlock_dll.py armour Assembly-CSharp.dll --factor 5      armour per tick = hull rate x 5 (default 1)
    python deadlock_dll.py armour Assembly-CSharp.dll -o out.dll
    python deadlock_dll.py armour Assembly-CSharp.dll --revert        -> restores the original methods
    (re-running with a different --factor on an already patched DLL just changes the factor)

    FACTOR is applied to EACH of the 6 quadrants every tick. The Mercury has 1000-1800 armour per quadrant, so use a
    large hull-regen value or factor if you want it to be noticeable. Back up the game's DLL first.
NOTES
    Changes two methods in place (file size unchanged): RegenerativeHullResinModule's tick and an
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
        ap = argparse.ArgumentParser(description=_dll_armour.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        ap.add_argument("dll"); ap.add_argument("-o", "--out"); ap.add_argument("--revert", action="store_true")
        ap.add_argument("--check", action="store_true", help="only report whether this DLL has the armour regeneration patch (and its factor)")
        ap.add_argument("--factor", type=float, default=1.0, help="armour restored per tick = hull repair rate x factor (default 1)")
        a = ap.parse_args(argv)
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
    main()


def _dll_revive(argv=None):
    """
SQUADRON REVIVE - ships with a Munition Factory module rebuild their DESTROYED squadrons.

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
    python deadlock_dll.py revive Assembly-CSharp.dll               -> writes Assembly-CSharp.dll.patched
    python deadlock_dll.py revive Assembly-CSharp.dll --turns 60    delay before the revive (default 0 = at once; 8 = the nuke time)
    python deadlock_dll.py revive Assembly-CSharp.dll -o out.dll
    python deadlock_dll.py revive Assembly-CSharp.dll --revert      -> restores the original methods
    (re-running with a different --turns on an already patched DLL just changes the time)
    TURNS x 120 is counted in module ticks; at full rate (1.0) a destroyed squadron takes TURNS x 120 ticks.

NOTES
    Changes three places in place (file size unchanged): MunitionFactoryModule.OnGameEvent, an unused
    decoy method in the same class that now holds the revive code, and one byte in ShipInfo.ReviveSquadron.
    The progress is stored in the squadron's (unused) LastRoundYaw field. The script refuses a DLL it does not recognise.
    Works together with the armour patch (different methods). Back up the game's DLL first.
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
        ap = argparse.ArgumentParser(description=_dll_revive.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        ap.add_argument("dll"); ap.add_argument("-o", "--out"); ap.add_argument("--revert", action="store_true")
        ap.add_argument("--check", action="store_true", help="only report whether this DLL has the squadron revive patch (and the rebuild time)")
        ap.add_argument("--turns", type=float, default=0.0, help="delay before a destroyed squadron is revived, in turns of 120 ticks at full resupply rate (default 0 = at once; the normal squadron repair then does the restoring. 8 = the time a nuke takes)")
        a = ap.parse_args(argv)
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
    main()



MAXRANK_PATS = [re.compile(rb"\x02\x7b(....)\x1f(.)\x2f\x12\x02\x7b(....)", re.S), re.compile(rb"\x02\x7b(....)\x1f(.)\xfe\x04\x16\xfe\x01\x2a", re.S)]


def maxrank_sites(d):
    hits = [(m_.start(2), m_.group(2)[0]) for p_ in MAXRANK_PATS for m_ in p_.finditer(d) if m_.group(2)[0] in range(5, 120)]
    return hits if len(hits) == 2 else None


def set_max_rank(src, value, dst):
    d = bytearray(open(src, "rb").read()); hits = maxrank_sites(d)
    if not hits: raise SystemExit("max-rank patch: expected 2 sites - this DLL build differs, nothing written")
    assert 5 <= value <= 100
    for pos, _ in hits: d[pos] = value
    open(dst, "wb").write(d); print("commander max rank %d -> %d -> %s" % (hits[0][1], value, dst))


# ---- Command-column node count (OPHEFGLELFC.KDNKKEKBOMG = int[4]{2,4,6,8}: the ranks at which a command point is earned) ----
# Two methods read that array: the tree builder (node count + each node's unlock rank) and the point counter. Both lookups are
# replaced, in place and at identical byte length, by arithmetic: unlock rank(i) = 2 + i*step, node count = N.
def _cmd_T(d):
    arr = rb"\x8d....\x25\xd0....\x28....\x80"   # newarr T; dup; ldtoken blob; call InitializeArray; stsfld
    ms = list(re.finditer(rb"\x1f\x0a" + arr + rb"....\x19" + arr + rb"....\x1a" + arr + rb"(....)", d, re.S))   # sizes 10, 3, 4 in a row
    return ms[0].group(1) if len(ms) == 1 else None


def _cmd_sites(d):
    """-> (T, [(pos, length, kind, value or None), ...]) with exactly 4 sites, or None if this DLL build differs."""
    T = _cmd_T(d)
    if not T: return None
    t = re.escape(T); sites = []
    specs = [  # kind, original, patched
        ("tree-elem",  rb"\x7e" + t + rb"\x11\x06\x94(?=\x6f)",       rb"\x11\x06\x1f(.)\x5a(?:\x18\x58\x00|\x1f(.)\x58)(?=\x6f)"),
        ("tree-bound", rb"\x11\x06\x7e" + t + rb"\x8e\x69(?=\x32)",    rb"\x11\x06\x1f(.)\x00{5}(?=\x32)"),
        ("cnt-elem",   rb"(?<=\x2b\x14)\x7e" + t + rb"\x08\x94(?=\x0d)", rb"(?<=\x2b\x14)\x08\x1f(.)\x5a(?:\x18\x58\x00|\x1f(.)\x58)(?=\x0d)"),
        ("cnt-bound",  rb"\x08\x7e" + t + rb"\x8e\x69(?=\x32\xe2)",   rb"\x08\x1f(.)\x00{5}(?=\x32\xe2)"),
    ]
    for kind, orig, pat in specs:
        o = list(re.finditer(orig, d, re.S)); p_ = list(re.finditer(pat, d, re.S))
        if len(o) + len(p_) != 1: return None
        sites.append((o[0].start(), len(o[0].group()), kind, None) if o else (p_[0].start(), len(p_[0].group()), kind, (p_[0].group(1)[0], (p_[0].group(2) or b"\x02")[0]) if kind.endswith("elem") else p_[0].group(1)[0]))
    return T, sites


def _cmd_bytes(kind, T, n, step, original, base=2):
    if original:
        return {"tree-elem": b"\x7e" + T + b"\x11\x06\x94", "tree-bound": b"\x11\x06\x7e" + T + b"\x8e\x69",
                "cnt-elem": b"\x7e" + T + b"\x08\x94", "cnt-bound": b"\x08\x7e" + T + b"\x8e\x69"}[kind]
    return {"tree-elem": bytes([0x11, 0x06, 0x1f, step, 0x5a, 0x1f, base, 0x58]), "tree-bound": bytes([0x11, 0x06, 0x1f, n]) + b"\0" * 5,
            "cnt-elem": bytes([0x08, 0x1f, step, 0x5a, 0x1f, base, 0x58]), "cnt-bound": bytes([0x08, 0x1f, n]) + b"\0" * 5}[kind]


def set_command_nodes(src, n, step, dst, first=1):
    """n = node count, step = ranks between nodes, first = rank of the first node; n=None restores the original {2,4,6,8} (= ranks 1/3/5/7).
    The game stores each node as 'rank + 1' (it unlocks when commander rank + 1 >= value), hence base = first + 1."""
    d = bytearray(open(src, "rb").read()); r = _cmd_sites(d)
    if not r: raise SystemExit("command-nodes patch: expected 4 sites - this DLL build differs, nothing written")
    T, sites = r
    if n is not None and not (1 <= n <= 60 and 1 <= step <= 20 and 1 <= first <= 60 and first + 1 + (n - 1) * step <= 100): raise SystemExit("--command-nodes must be 1-60, --command-step 1-20, --command-first-rank 1-60")
    for pos, ln, kind, _ in sites:
        new = _cmd_bytes(kind, T, n, step, n is None, first + 1); assert len(new) == ln, kind; d[pos:pos + ln] = new
    open(dst, "wb").write(d)
    print("command skill nodes -> %s -> %s" % ("original (4 nodes, at commander rank 1/3/5/7)" if n is None else "%d nodes, at commander rank %s" % (n, ", ".join(str(first + i * step) for i in range(min(n, 10))) + (" ... %d" % (first + (n - 1) * step) if n > 10 else "")), dst))


def check(dll):
    d = open(dll, "rb").read(); h = maxrank_sites(d)
    print("commander max rank: %s" % (h[0][1] if h else "unknown (DLL build not recognised)"))
    r = _cmd_sites(d)
    if not r: print("command skill nodes: unknown (DLL build not recognised)")
    else:
        v = {k: x for _, _, k, x in r[1]}
        if v["tree-bound"] is None: print("command skill nodes: original (4 nodes, at commander rank 1/3/5/7)")
        else:
            n_, (st_, b_) = v["tree-bound"], v["tree-elem"]
            print("command skill nodes: %d, one every %d rank(s) from rank %d (last at rank %d)" % (n_, st_, b_ - 1, b_ - 1 + (n_ - 1) * st_))
    for fn in (_dll_armour, _dll_revive):
        try: fn([dll, "--check"])
        except SystemExit as e:
            if e.code not in (0, None): print(e)


def main():
    if sys.argv[1:2] == ["armour"]: _dll_armour(sys.argv[2:]); return
    if sys.argv[1:2] == ["revive"]: _dll_revive(sys.argv[2:]); return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["patch", "check", "revert"]); ap.add_argument("dll"); ap.add_argument("-o", "--out")
    ap.add_argument("--armour-factor", type=float); ap.add_argument("--revive-turns", type=float); ap.add_argument("--max-rank", type=int)
    ap.add_argument("--command-nodes", type=int, nargs="?", const=0, help="patch: node count; revert: give the flag alone"); ap.add_argument("--command-step", type=int, default=1); ap.add_argument("--command-first-rank", type=int, default=1)
    ap.add_argument("--armour", action="store_true"); ap.add_argument("--revive", action="store_true")
    a = ap.parse_args()
    if a.cmd == "check": check(a.dll); return
    out = a.out or a.dll + (".patched" if a.cmd == "patch" else ".reverted"); cur = a.dll; tmp = tempfile.mkdtemp()
    def step(fn, args, name):
        nonlocal cur
        nxt = os.path.join(tmp, name)
        try: fn([cur] + args + ["-o", nxt])
        except SystemExit as e:
            if "nothing to do" not in str(e.code): raise      # e.g. reverting a patch that is not installed: skip it
            print(e.code); return
        cur = nxt
    if a.cmd == "patch":
        if a.armour_factor is None and a.revive_turns is None and a.max_rank is None and not a.command_nodes: ap.error("give at least one of --armour-factor / --revive-turns / --max-rank / --command-nodes N")
        if a.armour_factor is not None: step(_dll_armour, ["--factor", str(a.armour_factor)], "a.dll")
        if a.revive_turns is not None: step(_dll_revive, ["--turns", str(a.revive_turns)], "b.dll")
        if a.max_rank is not None: nxt = os.path.join(tmp, "c.dll"); set_max_rank(cur, a.max_rank, nxt); cur = nxt
        if a.command_nodes: nxt = os.path.join(tmp, "d.dll"); set_command_nodes(cur, a.command_nodes, a.command_step, nxt, a.command_first_rank); cur = nxt
    else:
        every = not (a.armour or a.revive or a.max_rank is not None or a.command_nodes is not None)
        if a.armour or every: step(_dll_armour, ["--revert"], "a.dll")
        if a.revive or every: step(_dll_revive, ["--revert"], "b.dll")
        if a.max_rank is not None or every: nxt = os.path.join(tmp, "c.dll"); set_max_rank(cur, 10 if every else a.max_rank, nxt); cur = nxt
        if a.command_nodes is not None or every: nxt = os.path.join(tmp, "d.dll"); set_command_nodes(cur, None, 1, nxt); cur = nxt
    shutil.copyfile(cur, out); shutil.rmtree(tmp, ignore_errors=True); print("done ->", out)


if __name__ == "__main__":
    main()
