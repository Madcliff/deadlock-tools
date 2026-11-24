#!/usr/bin/env python3
"""
dll - every Assembly-CSharp.dll patch for the Deadlock tools, in one place.

  python -m deadlock_tools dll patch  [DLL] [--max-rank N] [--munition-load] [--repair-bay-key]
                                            [--command-nodes N [--command-step S] [--command-first-rank R]] [--fresh]
  python -m deadlock_tools dll check  [DLL]
  python -m deadlock_tools dll revert [DLL] [--max-rank] [--command-nodes] [--munition-load] [--repair-bay-shortcuts]
                                            (no option = revert all patches)

DLL defaults to the 'dll' path in deadlock.json. The result is written to the work tree at BSG_Data/Managed/Assembly-CSharp.dll
(the original is never touched) and later runs continue from that file, so patches given in separate runs add up; --fresh
starts again from the original. 'check' with no DLL reports on the work-tree copy if it exists, else on the original.

PATCHES (each is only applied when its option is given; re-running on a patched DLL just changes the values)
  --max-rank N        commander max rank (game default 10), for 'shipdb setcommanders'
  --command-nodes N   number of nodes in the Command (fleet-size) column of the commander skill tree. Game default: 4 nodes, available at
                      commander rank 1/3/5/7. Node i becomes available at rank R + i*S, with --command-first-rank R (default 1) and
                      --command-step S (default 1 = one node per rank: N=10 gives ranks 1-10). The node count is hard-coded in the
                      DLL, so setcommanders alone cannot change it.
  --munition-load     let existing saves pick up a raised MunitionSlots count in battle (see 'dll munition -h')
  --repair-bay-actions N  assign indexed shortcuts and key mappings to repair actions (1-16)
Back up the game's DLL, then merge the work-tree copy into BSG_Data/Managed/.
The munition patch can also be run on its own: dll munition ... (options --check/--revert).
"""
import argparse, os, re, shutil, struct, sys, tempfile

if __package__ in (None, ""): sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deadlock_tools import config

# ---- the individual patches (munition load and repair-bay shortcuts can also run on their own) ----
def _sub_args(ap):
    """options shared by the single-patch commands (munition / repair-bay shortcuts)"""
    config.add_path_args(ap, "out")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="start from the original DLL, not the work-tree copy")
    ap.add_argument("--dst", help=argparse.SUPPRESS)      # internal: exact output file (used by 'patch' to chain steps)


def _sub_input(a):
    """The DLL to work on, continued from the work-tree copy unless --fresh."""
    a.dll = a.original = config.setting("dll", a.dll)
    if not a.dst:
        a.dll = config.latest(a.dll, "dll", a.out, a.fresh, quiet=a.check)


def _dll_munition(argv=None):
    """
MUNITION LOAD - make existing saves pick up a ship's NEW munition slot count in battle.

PROBLEM
    After raising MunitionSlots in the level files, the campaign screens show the new count (they read the
    ship database) but battles still show the old count, because each fleet ship in your SAVE stores its own
    munition list (ActiveMunitions) with the old length. The game only rebuilds that list when it is EMPTY.

WHAT IT DOES
    Patches one 17-byte check in Assembly-CSharp.dll (FleetShip save-file validation) so the list is also
    rebuilt when it is SHORTER than the ship's MunitionSlots. Ships whose list is already long enough are
    untouched. Affected ships get default munitions again (re-pick them in the war room).

USAGE
    python -m deadlock_tools dll munition [Assembly-CSharp.dll]            -> writes the work-tree DLL copy
    python -m deadlock_tools dll munition [Assembly-CSharp.dll] --revert   -> restores the original check
    (or as part of: dll patch --munition-load)

    Then: back up the game's Assembly-CSharp.dll, replace it with the result and LOAD your save (the check runs when a
    save is loaded). Needs the level files already patched with MunitionSlots.

NOTES
    * Only a ship whose saved list is null would now crash on load; the game's own constructor never leaves it
      null, so this should not happen with normal saves.
    * Game updates replace the DLL: run this again afterwards.
"""
    ORIG    = bytes.fromhex("027b1b020004" "2c09" "027b1b020004" "8e" "2d39")      # null/empty check
    PATCHED = bytes.fromhex("00" "027b1b020004" "8e" "69" "06" "7bab300004" "2f39") # len >= MunitionSlots ? skip : rebuild
    assert len(ORIG) == len(PATCHED) == 17
    ap = argparse.ArgumentParser(description=_dll_munition.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dll", nargs="?"); _sub_args(ap)
    ap.add_argument("--check", action="store_true", help="only report whether this DLL has the munition load patch")
    a = ap.parse_args(argv); _sub_input(a)
    d = open(a.dll, "rb").read()
    n_orig, n_new = d.count(ORIG), d.count(PATCHED)
    if n_orig + n_new != 1:
        sys.exit("expected exactly 1 match of the check in this DLL, found %d. This DLL version is different; not patching." % (n_orig + n_new))
    if a.check:
        print("munition load patch: %s" % ("INSTALLED" if n_new else "not installed (original code)")); return
    old, new = (PATCHED, ORIG) if a.revert else (ORIG, PATCHED)
    if d.count(old) != 1: sys.exit("already %s: nothing to do" % ("original" if a.revert else "patched"))
    i = d.index(old)
    dst = a.dst or os.path.join(config.out_dir("dll", a.out), os.path.basename(a.dll))
    config.write(dst, d[:i] + new + d[i + len(old):], a.original)
    print("%s the munition load check (17 bytes at file offset 0x%x) -> %s" % ("restored" if a.revert else "patched", i, dst))


CUSTOM_BUILD_MARKERS = (b"CustomRepairBaySlots", b"CustomArmourRepairModule", b"CustomSquadronReplacementModule",
                        b"CustomCommanderStatModifiers", b"CustomSubsystemModifiers")


def is_custom_build(data):
    """True for a DLL rewritten by the custom-module build (Cecil): fixed-offset patches do not apply to it."""
    return any(marker in data for marker in CUSTOM_BUILD_MARKERS)


def _method_il(path, type_name, method_name):
    """IL bytes of a method body, found through the metadata (works on Cecil-rewritten DLLs)."""
    from deadlock_tools.shipdb import Meta
    meta = Meta(path); types = meta.tab[0x02]; methods = meta.tab.get(0x06, [])
    for i, row in enumerate(types):
        if meta.s(row[1]) != type_name: continue
        last = types[i + 1][5] - 1 if i + 1 < len(types) else len(methods)
        for k in range(row[5] - 1, last):
            if meta.s(methods[k][3]) == method_name and methods[k][0]:
                at = meta.r2o(methods[k][0]); head = meta.d[at]
                if head & 3 == 2:                                   # tiny header: size in the top 6 bits
                    return meta.d[at + 1:at + 1 + (head >> 2)]
                size = struct.unpack_from("<I", meta.d, at + 4)[0]  # fat header (12 bytes)
                return meta.d[at + 12:at + 12 + size]
    return None


# get_ShortcutKey returning 60 + (RadialMenuPosition - 4): the binary patch below or the custom-module build's rewrite
INDEXED_SHORTCUT_IL = re.compile(rb"\x02\x28...\x06(?:\x1a|\x1f\x04)\x59\x1f\x3c\x58\x2a\Z", re.S)


def custom_build_shortcuts(path):
    """'indexed' / 'original' / None for a custom-module build's TurnaroundRepairBayAction.get_ShortcutKey"""
    il = _method_il(path, "TurnaroundRepairBayAction", "get_ShortcutKey")
    if il is None: return None
    if INDEXED_SHORTCUT_IL.match(il): return "indexed"
    return "original" if il == bytes.fromhex("1f2f2a") else None


def _dll_repair_bay_key(argv=None):
    """Give each turnaround repair action a unique indexed battle shortcut."""
    # Exact Assembly-CSharp build signature: TurnaroundRepairBayAction.get_ShortcutKey.
    RVA_FIELD = 0x5DB85E
    BODY_OFFSET = 0xA6AD0
    ORIGINAL_RVA = 0x0A88D0
    PATCHED_RVA = 0x00937910
    ORIGINAL_BODY = bytes.fromhex("0e 1f 2f 2a")  # SpecialAttack3
    # Keep these out of the generic ability stack. The correct launch controls
    # live in the dedicated RepairBaySlotButton area beside squadron/munition
    # slots; the existing panel code currently serializes only one such widget.
    PANEL_VISIBLE_OFFSET = 0xA6761  # get_VisibleInShipInfoPanel body (after tiny header)
    PANEL_HIDDEN = 0x16             # ldc.i4.0
    # This is an unused zero-filled run near the end of the managed .text section.
    # Keep it clear of the ArmourInfo helper body that the removed armour-regeneration patch used in older DLLs.
    CAVE_OFFSET = 0x935B10
    CAVE_SIZE = 14
    # Return RepairBayShortcutBase + (RadialMenuPosition - first repair position).
    HELPER = bytes.fromhex("36 02 28 9e 23 00 06 1f 04 59 1f 3c 58 2a")
    assert len(HELPER) == CAVE_SIZE

    ap = argparse.ArgumentParser(description=_dll_repair_bay_key.__doc__)
    ap.add_argument("dll", nargs="?"); _sub_args(ap)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--actions", type=int, default=1, help="number of indexed repair actions (1-16)")
    a = ap.parse_args(argv); _sub_input(a)
    if not 1 <= a.actions <= 16:
        sys.exit("--actions must be between 1 and 16")
    data = bytearray(open(a.dll, "rb").read())
    if is_custom_build(data):
        # The custom-module build rewrites get_ShortcutKey itself (scripts/merge_custom_modules.cs); Cecil also moves
        # and shares method bodies, so the fixed-offset patch below must never touch such a DLL.
        state = custom_build_shortcuts(a.dll)
        if a.check:
            print("indexed repair-bay shortcut patch: %s" % {
                "indexed": "built into the custom-module DLL",
                "original": "not installed (custom-module DLL without repair-bay support)"}.get(state, "unknown (custom-module DLL)"))
            return
        if a.revert:
            sys.exit("this custom-module DLL has the indexed repair shortcuts built in, so they cannot be reverted in place. "
                     "They go away when the custom-module DLL is rebuilt for ships without turnaround repair actions "
                     "(Save ship), or start from the original DLL (--fresh), which drops every custom module")
        if state == "indexed":
            sys.exit("already patched: nothing to do (the custom-module DLL provides the indexed shortcuts)")
        sys.exit("this custom-module DLL has no indexed repair shortcuts; rebuild it with the custom modules instead of patching it in place")
    rva = struct.unpack_from("<I", data, RVA_FIELD)[0]
    cave = bytes(data[CAVE_OFFSET:CAVE_OFFSET + CAVE_SIZE])
    shortcut_original = (rva == ORIGINAL_RVA and
                         bytes(data[BODY_OFFSET:BODY_OFFSET + 4]) == ORIGINAL_BODY and
                         cave == bytes(CAVE_SIZE))
    shortcut_patched = rva == PATCHED_RVA and cave == HELPER
    panel_byte = data[PANEL_VISIBLE_OFFSET]
    if not (shortcut_original or shortcut_patched) or panel_byte not in (PANEL_HIDDEN, 0x17):
        sys.exit("this DLL is not the expected build or the repair shortcut region was modified; refusing to change it")
    # Also recognize the earlier, misplaced UI experiment so this patch can
    # repair it without losing the independent indexed-shortcut change.
    misplaced_visible = panel_byte == 0x17
    patched = shortcut_patched and not misplaced_visible
    if a.check:
        print("indexed repair-bay shortcut patch: %s" % ("INSTALLED" if patched else "not installed"))
        return
    if (a.revert and not shortcut_patched and not misplaced_visible) or (
            not a.revert and shortcut_patched and not misplaced_visible):
        sys.exit("already %s: nothing to do" % ("original" if a.revert else "patched"))
    if a.revert:
        if shortcut_patched:
            struct.pack_into("<I", data, RVA_FIELD, ORIGINAL_RVA)
            data[CAVE_OFFSET:CAVE_OFFSET + CAVE_SIZE] = bytes(CAVE_SIZE)
        data[PANEL_VISIBLE_OFFSET] = PANEL_HIDDEN
        action = "restored the shared repair shortcut and kept repair actions out of the ability stack"
    else:
        if shortcut_original:
            struct.pack_into("<I", data, RVA_FIELD, PATCHED_RVA)
            data[CAVE_OFFSET:CAVE_OFFSET + CAVE_SIZE] = HELPER
        data[PANEL_VISIBLE_OFFSET] = PANEL_HIDDEN
        action = "enabled indexed repair shortcuts (IDs 60 onward); panel slots remain dedicated UI work"
    dst = a.dst or os.path.join(config.out_dir("dll", a.out), os.path.basename(a.dll))
    config.write(dst, bytes(data), a.original)
    print(action, "->", dst)


MAXRANK_PATS = [re.compile(rb"\x02\x7b(....)\x1f(.)\x2f\x12\x02\x7b(....)", re.S), re.compile(rb"\x02\x7b(....)\x1f(.)\xfe\x04\x16\xfe\x01\x2a", re.S)]


def maxrank_sites(d):
    hits = [(m_.start(2), m_.group(2)[0]) for p_ in MAXRANK_PATS for m_ in p_.finditer(d) if m_.group(2)[0] in range(5, 120)]
    return hits if len(hits) == 2 else None


def set_max_rank(src, value, dst):
    d = bytearray(open(src, "rb").read()); hits = maxrank_sites(d)
    if not hits: raise SystemExit("max-rank patch: expected 2 sites - this DLL build differs, nothing written")
    assert 5 <= value <= 100
    for pos, _ in hits: d[pos] = value
    with open(dst, "wb") as f:
        f.write(d)
        f.flush()
        if hasattr(os, 'fsync'):
            os.fsync(f.fileno())
    print("commander max rank %d -> %d -> %s" % (hits[0][1], value, dst))


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
    with open(dst, "wb") as f:
        f.write(d)
        f.flush()
        if hasattr(os, 'fsync'):
            os.fsync(f.fileno())
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
    for fn in (_dll_munition, _dll_repair_bay_key):
        try: fn([dll, "--check", "--dst", "-"])
        except SystemExit as e:
            if e.code not in (0, None): print(e)


def _managed_dir(levels, dll):
    """the game's Managed/ folder: <levels>/Managed, else the folder holding the configured DLL"""
    inside = os.path.join(levels, "Managed")
    return inside if os.path.isdir(inside) else os.path.dirname(os.path.abspath(dll))


def main():
    sub = {"munition": _dll_munition}
    if sys.argv[1:2] and sys.argv[1] in sub: sub[sys.argv[1]](sys.argv[2:]); return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["patch", "check", "revert"]); ap.add_argument("dll", nargs="?", metavar="DLL", help="default: 'dll' in deadlock.json")
    ap.add_argument("--dll", dest="dll_opt", metavar="PATH", help="same as giving DLL")
    config.add_path_args(ap, "out", "levels")
    ap.add_argument("--fresh", action="store_true", help="start from the original DLL, not the work-tree copy (earlier patches are dropped)")
    ap.add_argument("--max-rank", type=int, nargs="?", const=10, help="patch: new max rank; revert: give the flag alone")
    ap.add_argument("--command-nodes", type=int, nargs="?", const=0, help="patch: node count; revert: give the flag alone"); ap.add_argument("--command-step", type=int, default=1); ap.add_argument("--command-first-rank", type=int, default=1)
    ap.add_argument("--munition-load", action="store_true", help="patch/revert the munition load check")
    ap.add_argument("--repair-bay-actions", type=int, help="assign indexed shortcuts and key mappings to repair actions (1-16)")
    ap.add_argument("--repair-bay-shortcuts", action="store_true", help="revert indexed repair-bay shortcut support and its staged key mappings")
    a = ap.parse_args(); a.dll = a.dll or a.dll_opt
    original = config.setting("dll", a.dll)
    if a.cmd == "check":
        src = original if a.dll else config.latest(original, "dll", a.out, quiet=True)
        print("checking", src); check(src); return
    cur = config.latest(original, "dll", a.out, a.fresh)
    out = os.path.join(config.out_dir("dll", a.out), os.path.basename(original))
    if os.path.exists(out) and config.same_file(out, original): raise SystemExit("refusing to overwrite the original %s - choose another output folder" % original)
    tmp = tempfile.mkdtemp()
    def step(fn, args, name):
        nonlocal cur
        nxt = os.path.join(tmp, name)
        try: fn([cur] + args + ["--fresh", "--dst", nxt])
        except SystemExit as e:
            if "nothing to do" not in str(e.code): raise      # e.g. reverting a patch that is not installed: skip it
            print(e.code); return
        cur = nxt
    if a.cmd == "patch":
        if a.max_rank is None and not a.command_nodes and not a.munition_load and a.repair_bay_actions is None:
            ap.error("give at least one patch option")
        if a.repair_bay_actions is not None and not 1 <= a.repair_bay_actions <= 16:
            ap.error("--repair-bay-actions must be between 1 and 16")
        if a.max_rank is not None: nxt = os.path.join(tmp, "c.dll"); set_max_rank(cur, a.max_rank, nxt); cur = nxt
        if a.command_nodes: nxt = os.path.join(tmp, "d.dll"); set_command_nodes(cur, a.command_nodes, a.command_step, nxt, a.command_first_rank); cur = nxt
        if a.munition_load: step(_dll_munition, [], "e.dll")
        if a.repair_bay_actions is not None:
            step(_dll_repair_bay_key, ["--actions", str(a.repair_bay_actions)], "f.dll")
            from deadlock_tools.repair_shortcuts import update_key_assets
            update_key_assets(config.setting("levels", a.levels), config.out_dir("levels", a.out), a.repair_bay_actions, _managed_dir(config.setting("levels", a.levels), original))
    else:
        every = not (a.max_rank is not None or a.command_nodes is not None or a.munition_load or a.repair_bay_actions is not None or a.repair_bay_shortcuts)
        if a.max_rank is not None or every: nxt = os.path.join(tmp, "c.dll"); set_max_rank(cur, 10, nxt); cur = nxt
        if a.command_nodes is not None or every: nxt = os.path.join(tmp, "d.dll"); set_command_nodes(cur, None, 1, nxt); cur = nxt
        if a.munition_load or every: step(_dll_munition, ["--revert"], "e.dll")
        with open(cur, "rb") as stream:
            current = stream.read()
        repair_patch_was_installed = len(current) > 0x5DB862 and struct.unpack_from("<I", current, 0x5DB85E)[0] == 0x00937910
        if every and is_custom_build(current):
            print("indexed repair shortcuts: built into this custom-module DLL, left as they are")
            every = False
        if a.repair_bay_shortcuts or every:
            step(_dll_repair_bay_key, ["--revert"], "f.dll")
            if a.repair_bay_shortcuts or repair_patch_was_installed:
                from deadlock_tools.repair_shortcuts import update_key_assets
                update_key_assets(config.setting("levels", a.levels), config.out_dir("levels", a.out), 0, _managed_dir(config.setting("levels", a.levels), original))
    if config.same_file(cur, out): print("nothing changed ->", out)
    else: shutil.copyfile(cur, out); print("done ->", out)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
