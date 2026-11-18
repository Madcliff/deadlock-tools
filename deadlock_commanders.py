#!/usr/bin/env python3
"""Raise the hard-coded commander max rank (10) in Assembly-CSharp.dll.
  python patch_commander_maxrank.py Assembly-CSharp.dll --max 20 [-o out.dll]     patch
  python patch_commander_maxrank.py Assembly-CSharp.dll --check                   show current value
  python patch_commander_maxrank.py Assembly-CSharp.dll --max 10 -o orig.dll      revert
Two places compare CommanderData.Rank with the constant 10: 'is max rank' and 'XP needed for the next rank'."""
import sys, argparse, struct
ap = argparse.ArgumentParser(); ap.add_argument("dll"); ap.add_argument("--max", type=int); ap.add_argument("-o"); ap.add_argument("--check", action="store_true")
a = ap.parse_args(); d = bytearray(open(a.dll, "rb").read())
# ldarg.0; ldfld CommanderData.Rank; ldc.i4.s N; (bge.s ... | clt ...)    -- the Rank field token differs per build, so find it through the methods' shape
import re
pats = [re.compile(rb"\x02\x7b(....)\x1f(.)\x2f\x12\x02\x7b(....)", re.S),     # XP for next rank: if (rank >= N) ...
        re.compile(rb"\x02\x7b(....)\x1f(.)\xfe\x04\x16\xfe\x01\x2a", re.S)]   # IsMaxRank: return !(rank < N)
hits = []
for p in pats:
    for m in p.finditer(d):
        if m.group(2)[0] in range(5, 120): hits.append((m.start(2), m.group(2)[0]))
vals = sorted({v for _, v in hits}); print("found %d site(s), current max rank constant(s): %s" % (len(hits), vals))
if len(hits) != 2: sys.exit("expected exactly 2 sites (got %d) - this DLL build differs, nothing changed" % len(hits))
if a.check or a.max is None: sys.exit(0)
assert 5 <= a.max <= 100
for pos, _ in hits: d[pos] = a.max
out = a.o or a.dll + ".maxrank"; open(out, "wb").write(d); print("wrote", out, "max rank =", a.max)
