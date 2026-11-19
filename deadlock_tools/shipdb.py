#!/usr/bin/env python3
"""
BSG Deadlock ship database tool.

Reads the ship / munition / blueprint tables out of the game's level files using the field layout from
Assembly-CSharp.dll (names come from the DLL, nothing is guessed) and edits them. Originals are never touched:
results go to out/levels/ under the game's file names.

ARGUMENTS
  python -m deadlock_tools shipdb <command> [command arguments] [--levels PATH] [--dll PATH] [-o DIR] [--fresh]
  --levels and --dll default to the paths saved in deadlock.json ('config'), so they are usually left out.
  --levels takes the game's level folder (normal), a single level file, or a comma-separated list. With a folder, every
  level file that holds the table is patched (the game ships ~10 identical copies of the ship database). Read-only
  ship commands (list / show / vet) use the first level file that has the ship table.
  (The old form '<command> <levels> <dll> ...', in either order, is still accepted.)

COMMANDERS (ranks / fleet-size traits; DataTables in the level files)
  python -m deadlock_tools shipdb commanders                         (show ranks, XP, leadership interval, trait counts)
  python -m deadlock_tools shipdb fixspecs      [--revert-specs]     (repair the base-game Tactician (subsystem 0) captain-skill list, see below)
  python -m deadlock_tools shipdb setcommanders --add 10 --every-level   (ranks 11-20, a fleet-size trait at every rank; continues from your earlier edits in out/levels like the other commands, --fresh starts over;
        then patch the DLL: python -m deadlock_tools dll patch --max-rank 20 --command-nodes 10
        (the skill tree's node count is hard-coded in the DLL: without --command-nodes the Command column stays at 4 nodes; 10 = one per rank 1-10). -o sets the output folder)

SHIPS
  python -m deadlock_tools shipdb list     
  python -m deadlock_tools shipdb show     "viper mk1"
  python -m deadlock_tools shipdb vet      ["viper mk1"]                 (veterancy ranks)
  python -m deadlock_tools shipdb set      "Colonial Mercury" MunitionSlots 4
  python -m deadlock_tools shipdb set      "Colonial Mercury" FighterSlots 5       (carrier slots; Mercury default 3 fighter + 1 support)
  python -m deadlock_tools shipdb set      "Colonial Mercury" UtilityFighterSlots 1
        Carrier/munition slot counts also need the ship model changed: run weapons with the same numbers
        (--fighter-slots 5 --utility-slots 1 --munition-slots 4). The Fighters radial menu holds at most 8 launch buttons in total (--split-squadrons in weapons lifts that per submenu).
  python -m deadlock_tools shipdb setvet   "viper mk1" 4 SquadronRepair 1.5
  python -m deadlock_tools shipdb addvet   "Colonial Mercury" 2 FlakDamage 0.1   (ADD an effect to a rank; creates the rank if missing; existing effect = overwritten)
  python -m deadlock_tools shipdb removevet "Colonial Mercury" 2 FlakDamage     (REMOVE an effect from a rank; use "all" instead of the name to clear the rank)
  python -m deadlock_tools shipdb copyvet  "Colonial Mercury" "Jupiter Mk2"   (copy ALL ranks, even empty)
  python -m deadlock_tools shipdb setxp    2 10                          (XP needed for rank 2)

MUNITIONS
  python -m deadlock_tools shipdb munitions                              (list types: capacity, salvo size, supply cost ...)
  python -m deadlock_tools shipdb setmunition "colonial guided" MaxMissiles 80 [StandardSalvoSize 6 ...]
        Editable: StandardSalvoSize MaxMissiles HasUnlimitedMunitions SupplyCostPerMunition SurvivalCostPerMunition PointValue

BLUEPRINTS / RESEARCH
  python -m deadlock_tools shipdb blueprints                             (list research unlocks per campaign)
  python -m deadlock_tools shipdb addblueprint "Colonial Mercury" [--cost 4000] [--like "Jupiter Mk2"]
                                         [--campaigns 2,3,4,5] [--mission N] [--label Mc] [--list capital] [--unlocked] [--keep-build-stats]
        Adds the ship to the research lists AND makes it buildable (Buildable/BuildTime/BuildCost/RushCost copied from --like).
        Needs a NEW campaign: an existing save keeps its own research state.

SQUADRONS (fighter hull strength, in the game's sharedassets9.assets - not in the level files)
  python -m deadlock_tools shipdb squadrons                (list: fighters, hull pool, hull per fighter, loss thresholds)
  python -m deadlock_tools shipdb setsquadron  "Viper Mk VII" HullFactor 2
        HullFactor F = pool and every fighter-loss threshold x F;  MaxHullPoints N = set the pool;  FighterHull N = hull per fighter.
        Name is part of the squadron name ("assault", "raptor", or "all"). A folder works too (files without squadrons are skipped).

FLEET LIMITS (campaign)
  python -m deadlock_tools shipdb fleetinfo                              (show MaxShips = ships per fleet group, StartingFleetPointCap ...)
  python -m deadlock_tools shipdb setfleetinfo MaxShips 10 [StartingFleetPointCap 6000]

FLEET MAP ICONS
  python -m deadlock_tools shipdb fleeticons                             (list the war-room icon tables)
  python -m deadlock_tools shipdb fleeticon    "Colonial Mercury,Colonial Valkyrie" "Jupiter Mk2"
        Copies a ship's fleet icon to other ships in every scene that has the table. (--factory all is not recommended.)

COMBINING EDITS
  Just run the commands one after another: each continues from the earlier results in out/levels/
  (use --fresh to start over, -o to pick another output folder). Finally back up the game's level files and copy
  out/levels/* over them.
"""
import struct, glob, sys, argparse, os

if __package__ in (None, ""): sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deadlock_tools import config

TABLES = {  # table id -> (name, columns)  column kinds: 'u2','u4','S'(string),'G'(guid),'B'(blob), ('T',[tables]) coded, ('I',table) index
    0x00: ("Module", ["u2", "S", "G", "G", "G"]),
    0x01: ("TypeRef", [("C", "ResolutionScope"), "S", "S"]),
    0x02: ("TypeDef", ["u4", "S", "S", ("C", "TypeDefOrRef"), ("I", 0x04), ("I", 0x06)]),
    0x03: ("FieldPtr", [("I", 0x04)]),
    0x04: ("Field", ["u2", "S", "B"]),
    0x05: ("MethodPtr", [("I", 0x06)]),
    0x06: ("MethodDef", ["u4", "u2", "u2", "S", "B", ("I", 0x08)]),
    0x07: ("ParamPtr", [("I", 0x08)]),
    0x08: ("Param", ["u2", "u2", "S"]),
    0x09: ("InterfaceImpl", [("I", 0x02), ("C", "TypeDefOrRef")]),
    0x0A: ("MemberRef", [("C", "MemberRefParent"), "S", "B"]),
    0x0B: ("Constant", ["u2", ("C", "HasConstant"), "B"]),
    0x0C: ("CustomAttribute", [("C", "HasCustomAttribute"), ("C", "CustomAttributeType"), "B"]),
    0x0D: ("FieldMarshal", [("C", "HasFieldMarshal"), "B"]),
    0x0E: ("DeclSecurity", ["u2", ("C", "HasDeclSecurity"), "B"]),
    0x0F: ("ClassLayout", ["u2", "u4", ("I", 0x02)]),
    0x10: ("FieldLayout", ["u4", ("I", 0x04)]),
    0x11: ("StandAloneSig", ["B"]),
    0x12: ("EventMap", [("I", 0x02), ("I", 0x14)]),
    0x13: ("EventPtr", [("I", 0x14)]),
    0x14: ("Event", ["u2", "S", ("C", "TypeDefOrRef")]),
    0x15: ("PropertyMap", [("I", 0x02), ("I", 0x17)]),
    0x16: ("PropertyPtr", [("I", 0x17)]),
    0x17: ("Property", ["u2", "S", "B"]),
    0x18: ("MethodSemantics", ["u2", ("I", 0x06), ("C", "HasSemantics")]),
    0x19: ("MethodImpl", [("I", 0x02), ("C", "MethodDefOrRef"), ("C", "MethodDefOrRef")]),
    0x1A: ("ModuleRef", ["S"]),
    0x1B: ("TypeSpec", ["B"]),
    0x1C: ("ImplMap", ["u2", ("C", "MemberForwarded"), "S", ("I", 0x1A)]),
    0x1D: ("FieldRVA", ["u4", ("I", 0x04)]),
    0x1E: ("EncLog", ["u4", "u4"]),
    0x1F: ("EncMap", ["u4"]),
    0x20: ("Assembly", ["u4", "u2", "u2", "u2", "u2", "u4", "B", "S", "S"]),
    0x21: ("AssemblyProcessor", ["u4"]),
    0x22: ("AssemblyOS", ["u4", "u4", "u4"]),
    0x23: ("AssemblyRef", ["u2", "u2", "u2", "u2", "u4", "B", "S", "S", "B"]),
    0x24: ("AssemblyRefProcessor", ["u4", ("I", 0x23)]),
    0x25: ("AssemblyRefOS", ["u4", "u4", "u4", ("I", 0x23)]),
    0x26: ("File", ["u4", "S", "B"]),
    0x27: ("ExportedType", ["u4", "u4", "S", "S", ("C", "Implementation")]),
    0x28: ("ManifestResource", ["u4", "u4", "S", ("C", "Implementation")]),
    0x29: ("NestedClass", [("I", 0x02), ("I", 0x02)]),
    0x2A: ("GenericParam", ["u2", "u2", ("C", "TypeOrMethodDef"), "S"]),
    0x2B: ("MethodSpec", [("C", "MethodDefOrRef"), "B"]),
    0x2C: ("GenericParamConstraint", [("I", 0x2A), ("C", "TypeDefOrRef")]),
}
CODED = {
    "TypeDefOrRef": [0x02, 0x01, 0x1B], "HasConstant": [0x04, 0x08, 0x17],
    "HasCustomAttribute": [0x06, 0x04, 0x01, 0x02, 0x08, 0x09, 0x0A, 0x00, 0x0E, 0x17, 0x14, 0x11, 0x1A, 0x1B, 0x20, 0x23, 0x26, 0x27, 0x28, 0x2A, 0x2C, 0x2B],
    "HasFieldMarshal": [0x04, 0x08], "HasDeclSecurity": [0x02, 0x06, 0x20],
    "MemberRefParent": [0x02, 0x01, 0x1A, 0x06, 0x1B], "HasSemantics": [0x14, 0x17],
    "MethodDefOrRef": [0x06, 0x0A], "MemberForwarded": [0x04, 0x06],
    "Implementation": [0x26, 0x23, 0x27], "CustomAttributeType": [None, None, 0x06, 0x0A, None],
    "ResolutionScope": [0x00, 0x1A, 0x23, 0x01], "TypeOrMethodDef": [0x02, 0x06],
}
TAGBITS = {k: max(1, (len(v) - 1).bit_length()) for k, v in CODED.items()}


class Meta:
    def __init__(self, path):
        d = open(path, "rb").read()
        self.d = d
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        nsec = struct.unpack_from("<H", d, pe + 6)[0]
        optsz = struct.unpack_from("<H", d, pe + 20)[0]
        opt = pe + 24
        magic = struct.unpack_from("<H", d, opt)[0]
        ddoff = opt + (96 if magic == 0x10B else 112)
        clr_rva = struct.unpack_from("<I", d, ddoff + 14 * 8)[0]
        secs = []
        so = opt + optsz
        for i in range(nsec):
            vs, va, rs, rp = struct.unpack_from("<IIII", d, so + i * 40 + 8)
            secs.append((va, vs, rp, rs))
        self.secs = secs
        self.r2o = lambda rva: next(rp + rva - va for va, vs, rp, rs in secs if va <= rva < va + max(vs, rs))
        cli = self.r2o(clr_rva)
        mrva, msize = struct.unpack_from("<II", d, cli + 8)
        mo = self.r2o(mrva)
        assert d[mo:mo + 4] == b"BSJB"
        vlen = struct.unpack_from("<I", d, mo + 12)[0]
        p = mo + 16 + vlen + 2
        ns = struct.unpack_from("<H", d, p)[0]; p += 2
        self.streams = {}
        for _ in range(ns):
            off, sz = struct.unpack_from("<II", d, p); p += 8
            e = d.index(b"\0", p); name = d[p:e].decode(); p = (e + 4) & ~3
            self.streams[name] = (mo + off, sz)
        so_, ss = self.streams["#Strings"]; self.strings = d[so_:so_ + ss]
        self._parse_tables()

    def s(self, i):
        e = self.strings.index(b"\0", i)
        return self.strings[i:e].decode("utf-8", "replace")

    def _parse_tables(self):
        d = self.d
        to, ts = self.streams.get("#~") or self.streams["#-"]
        heap = d[to + 6]
        self.wS = 4 if heap & 1 else 2; self.wG = 4 if heap & 2 else 2; self.wB = 4 if heap & 4 else 2
        valid = struct.unpack_from("<Q", d, to + 8)[0]
        p = to + 24
        self.rows = {}
        for t in range(64):
            if valid >> t & 1:
                self.rows[t] = struct.unpack_from("<I", d, p)[0]; p += 4
        if heap & 0x40: p += 4

        def idxw(t): return 4 if self.rows.get(t, 0) > 0xFFFF else 2
        def cw(name):
            tabs = CODED[name]; mx = max(self.rows.get(t, 0) for t in tabs if t is not None)
            return 4 if mx >= (1 << (16 - TAGBITS[name])) else 2
        self.cols = {}
        for t in self.rows:
            if t not in TABLES: raise ValueError("unknown table %x" % t)
            cols = []
            for c in TABLES[t][1]:
                if c == "u2": cols.append(("n", 2))
                elif c == "u4": cols.append(("n", 4))
                elif c == "S": cols.append(("S", self.wS))
                elif c == "G": cols.append(("G", self.wG))
                elif c == "B": cols.append(("B", self.wB))
                elif c[0] == "I": cols.append(("I", idxw(c[1])))
                else: cols.append(("C", cw(c[1]), c[1]))
            self.cols[t] = cols
        self.tab = {}
        for t in sorted(self.rows):
            sizes = [c[1] for c in self.cols[t]]; rs = sum(sizes)
            rows = []
            for r in range(self.rows[t]):
                vals = []; q = p
                for sz in sizes:
                    vals.append(int.from_bytes(d[q:q + sz], "little")); q += sz
                rows.append(vals); p += rs
            self.tab[t] = rows

    def typedefs(self):
        """yield (name, namespace, [field names], [(method name)])"""
        td = self.tab[0x02]; fld = self.tab.get(0x04, []); md = self.tab.get(0x06, [])
        for i, row in enumerate(td):
            f0 = row[4] - 1; m0 = row[5] - 1
            if i + 1 < len(td): f1 = td[i + 1][4] - 1; m1 = td[i + 1][5] - 1
            else: f1 = len(fld); m1 = len(md)
            yield (self.s(row[1]), self.s(row[2]), [(self.s(fld[k][1]), fld[k][0], fld[k][2]) for k in range(f0, f1)],
                   [self.s(md[k][3]) for k in range(m0, m1)], row[0])




class Schema:
    def __init__(self, dll):
        m = self.m = Meta(dll)
        self.td = m.tab[2]; self.fl = m.tab[4]; self.tr = m.tab[1]
        self.names = [m.s(r[1]) for r in self.td]
        bo, _ = m.streams["#Blob"]; self.bo = bo
        # field -> owner and constant
        self.owner = {}
        for i, r in enumerate(self.td):
            f0 = r[4] - 1; f1 = (self.td[i + 1][4] - 1) if i + 1 < len(self.td) else len(self.fl)
            for k in range(f0, f1): self.owner[k] = i
        # fields with [SerializeField]
        ca = m.tab[0x0C]; mr = m.tab.get(0x0A, [])
        self.serfield = set()
        for parent, ctor, _v in ca:
            if parent & 0x1F == 4:                     # HasCustomAttribute: Field = tag 1 ... computed below
                pass
        # HasCustomAttribute has 5 tag bits (22 tables); Field tag = 1
        for parent, ctor, _v in ca:
            if (parent & 0x1F) == 1:
                tag = ctor & 7; idx = ctor >> 3
                if tag == 3 and idx - 1 < len(mr):     # MemberRef
                    parentref = mr[idx - 1][0]
                    if (parentref & 7) == 1:           # TypeRef
                        if m.s(self.tr[(parentref >> 3) - 1][1]) == "SerializeField":
                            self.serfield.add((parent >> 5) - 1)

    def blob(self, i):
        d = self.m.d; p = self.bo + i; b = d[p]
        if b & 0x80 == 0: n = b; p += 1
        elif b & 0xC0 == 0x80: n = ((b & 0x3f) << 8) | d[p + 1]; p += 2
        else: n = ((b & 0x1f) << 24) | (d[p + 1] << 16) | (d[p + 2] << 8) | d[p + 3]; p += 4
        return d[p:p + n]

    def typedef_index(self, name):
        return self.names.index(name)

    def slice_arg(self, sig, i):
        """return (arg_bytes, next_i) for one type starting at sig[i]"""
        r = Reader(self, b""); _, j = r.skip(sig, i)
        return bytes(sig[i:j]), j

    def fields(self, ti, ctx=()):
        """Serialized instance fields of typedef ti incl. base classes (base first). Returns [(name, sig, ctx)]."""
        r = self.td[ti]
        base = []
        ext = r[3]
        if ext:
            tag = ext & 3; idx = ext >> 2
            if tag == 0: base = self.fields(idx - 1, ())
            elif tag == 2:                              # TypeSpec: generic base class
                sig = self.blob(self.m.tab[0x1B][idx - 1][0])
                if sig[0] == 0x15:
                    rr = Reader(self, b"")
                    t2, i2, j = rr.token(sig, 2)
                    n = sig[j]; j += 1; args = []
                    for _ in range(n):
                        arg, j = self.slice_arg(sig, j)
                        if arg[0] == 0x13: arg = ctx[arg[1]]
                        args.append(arg)
                    if t2 == 0: base = self.fields(i2 - 1, tuple(args))
        f0 = r[4] - 1; f1 = (self.td[ti + 1][4] - 1) if ti + 1 < len(self.td) else len(self.fl)
        out = []
        for k in range(f0, f1):
            flags, name, sig = self.fl[k]
            if flags & 0x10 or flags & 0x40 or flags & 0x80: continue   # static / literal / NotSerialized
            access = flags & 7
            if access != 6 and k not in self.serfield: continue
            out.append((self.m.s(name), self.blob(sig), ctx))
        return base + out

    def base_name(self, ti):
        ext = self.td[ti][3]
        if not ext: return None
        tag = ext & 3; idx = ext >> 2
        if tag == 0: return self.names[idx - 1]
        if tag == 1: return self.m.s(self.tr[idx - 1][1])
        return None


STRUCTS = {"FloatRange": 8, "Vector2": 8, "Vector3": 12, "Vector4": 16, "Quaternion": 16, "Color": 16, "Color32": 4, "Rect": 16, "Bounds": 24, "LayerMask": 4, "Vector2Int": 8, "Vector3Int": 12, "IntRange": 8}


class Reader:
    def __init__(self, schema, buf, pos=0):
        self.s = schema; self.b = buf; self.p = pos; self.ctx = ()

    def align(self): self.p = (self.p + 3) & ~3

    def prim(self, code):
        b = self.b; p = self.p
        fm = {0x02: ("<B", 1), 0x03: ("<H", 2), 0x04: ("<b", 1), 0x05: ("<B", 1), 0x06: ("<h", 2), 0x07: ("<H", 2),
              0x08: ("<i", 4), 0x09: ("<I", 4), 0x0a: ("<q", 8), 0x0b: ("<Q", 8), 0x0c: ("<f", 4), 0x0d: ("<d", 8)}[code]
        v = struct.unpack_from(fm[0], b, p)[0]; self.p += fm[1]
        if fm[1] < 4: self.align()
        if code == 0x02: v = bool(v)
        return v

    def string(self):
        n = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4
        v = self.b[self.p:self.p + n].decode("utf-8", "replace"); self.p += n; self.align(); return v

    def token(self, sig, i):
        """decode compressed TypeDefOrRef at sig[i]; return (kind, index, next_i)"""
        b = sig[i]
        if b & 0x80 == 0: v = b; i += 1
        elif b & 0xC0 == 0x80: v = ((b & 0x3f) << 8) | sig[i + 1]; i += 2
        else: v = ((b & 0x1f) << 24) | (sig[i + 1] << 16) | (sig[i + 2] << 8) | sig[i + 3]; i += 4
        return (v & 3), (v >> 2), i

    def typ(self, sig, i):
        """read a value of the type encoded at sig[i]; returns (value, next_i)"""
        c = sig[i]; i += 1
        if c in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d): return self.prim(c), i
        if c == 0x0e: return self.string(), i
        if c == 0x13:                                    # VAR (generic parameter)
            arg = self.ctx[sig[i]]; i += 1
            v, _ = self.typ(arg, 0); return v, i
        if c == 0x1d:                                    # SZARRAY
            n = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4
            assert 0 <= n < 10_000_000, "bad array length %d at %d" % (n, self.p - 4)
            start = i; out = []
            for _ in range(n):
                v, j = self.typ(sig, start); out.append(v)
            if n == 0: _, j = self.skip(sig, start)
            self.align() if False else None
            return out, j
        if c == 0x15:                                    # GENERICINST
            kind = sig[i]; i += 1
            tag, idx, i = self.token(sig, i)
            n = sig[i]; i += 1
            start = i
            n_el = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4
            assert 0 <= n_el < 10_000_000, "bad list length %d at %d" % (n_el, self.p - 4)
            out = []
            for _ in range(n_el):
                v, j = self.typ(sig, start); out.append(v)
            if n_el == 0: _, j = self.skip(sig, start)
            # skip remaining generic args
            for _ in range(n - 1): _, j = self.skip(sig, j)
            if n_el == 0: pass
            return out, j
        if c in (0x11, 0x12):
            tag, idx, i = self.token(sig, i)
            return self.named(c, tag, idx), i
        raise ValueError("unsupported sig element 0x%x" % c)

    def skip(self, sig, i):
        """advance i past a type without reading data"""
        c = sig[i]; i += 1
        if c in (0x11, 0x12): _, _, i = self.token(sig, i); return None, i
        if c == 0x13: return None, i + 1
        if c == 0x1d: return self.skip(sig, i)
        if c == 0x15:
            i += 1; _, _, i = self.token(sig, i); n = sig[i]; i += 1
            for _ in range(n): _, i = self.skip(sig, i)
            return None, i
        return None, i

    def root(self, ti):
        """walk base chain to the first external TypeRef; return 'Namespace.Name'"""
        s = self.s
        while True:
            ext = s.td[ti][3]
            if not ext: return "?"
            tag = ext & 3; idx = ext >> 2
            if tag == 0: ti = idx - 1; continue
            if tag == 1:
                r = s.tr[idx - 1]; return s.m.s(r[2]) + "." + s.m.s(r[1])
            if tag == 2:                                 # generic base: follow its definition
                sig = s.blob(s.m.tab[0x1B][idx - 1][0])
                if sig[0] == 0x15:
                    t2, i2, _ = self.token(sig, 2)
                    if t2 == 0: ti = i2 - 1; continue
                    if t2 == 1:
                        r = s.tr[i2 - 1]; return s.m.s(r[2]) + "." + s.m.s(r[1])
            return "?"

    def named(self, c, tag, idx):
        s = self.s
        if tag == 0:                                     # TypeDef in this assembly
            ti = idx - 1; root = self.root(ti)
            if root == "System.Enum": return self.prim(0x08)
            if root in ("System.ValueType", "System.Object"): return self.obj(ti)
            return ("PPtr", self.pptr())                 # derives from a UnityEngine/other Object type
        if tag == 1:                                     # TypeRef
            r = s.tr[idx - 1]; name = s.m.s(r[1]); ns = s.m.s(r[2])
            if name in STRUCTS:
                n = STRUCTS[name]
                v = struct.unpack_from("<%df" % (n // 4), self.b, self.p) if name not in ("Color32", "LayerMask", "Vector2Int", "Vector3Int") else self.b[self.p:self.p + n].hex()
                self.p += n; return v
            if ns == "System" and name == "Object": return self.obj_unknown()
            if c == 0x11: raise ValueError("unknown valuetype " + ns + "." + name)
            return ("PPtr", self.pptr())
        raise ValueError("typespec")

    def obj_unknown(self): raise ValueError("System.Object field")

    def pptr(self):
        f, pid = struct.unpack_from("<iq", self.b, self.p); self.p += 12; return (f, pid)

    def obj(self, ti, ctx=()):
        out = {}; offs = {}
        for name, sig, c in self.s.fields(ti, ctx):
            saved = self.ctx; self.ctx = c; start = self.p
            v, _ = self.typ(sig, 1)
            self.ctx = saved
            out[name] = v; offs[name] = (start, sig[1])
        out["_off"] = offs
        return out


# ----------------------------------------------------------------------------- CLI
def read_objects(d):
    meta, fsize, ver, doff = struct.unpack(">IIII", d[:16]); o = 20
    j = d.index(b"\0", o); o = j + 1 + 4
    tt = d[o]; o += 1
    if tt: raise SystemExit("this serialized file has a type tree; this tool expects the stripped (no type tree) form like level0")
    nt = struct.unpack("<i", d[o:o + 4])[0]; o += 4
    types = []
    for _ in range(nt):
        cid = struct.unpack("<i", d[o:o + 4])[0]; o += 4 + 1 + 2
        if cid == 114: o += 16
        o += 16; types.append(cid)
    n = struct.unpack("<i", d[o:o + 4])[0]; o += 4
    out = []
    for _ in range(n):
        o = (o + 3) & ~3
        pid, off, size, ti = struct.unpack("<qIIi", d[o:o + 20])
        out.append((pid, doff + off, size, types[ti], o)); o += 20
    return out


def enum_names(S, ti):
    m = S.m; const = {}
    for r in m.tab[0x0B]:
        if (r[1] & 3) == 0:
            b = S.blob(r[2]); const[(r[1] >> 2) - 1] = int.from_bytes(b, "little", signed=True)
    f0 = S.td[ti][4] - 1; f1 = (S.td[ti + 1][4] - 1) if ti + 1 < len(S.td) else len(S.fl)
    return {const[k]: m.s(S.fl[k][1]) for k in range(f0, f1) if k in const}


class Db:
    def __init__(self, dll, lvl, schema=None):
        self.S = S = schema or Schema(dll)
        self.buf = bytearray(open(lvl, "rb").read())
        ti = S.typedef_index("ShipDataTable")
        self.res = None
        for pid, abs_off, size, cid, _epos in read_objects(bytes(self.buf)):
            if cid == 114 and size > 5000:
                r = Reader(S, bytes(self.buf[abs_off:abs_off + size]), 32)
                try: res = r.obj(ti)
                except Exception: continue
                if r.p == size and len(res["objects"]) > 0:
                    self.res, self.base, self.pid, self.size = res, abs_off, pid, size; break
        if self.res is None: raise SystemExit("could not locate the ShipDataTable MonoBehaviour in this file")
        self.ships = self.res["objects"]
        # enum name tables: Effect enum from ShipVeterancyRankEffectInfo.Effect; Faction/ShipClass from ShipDataTableObject
        def enum_of(cls, field):
            ti2 = S.typedef_index(cls)
            for n, sig, c in S.fields(ti2):
                if n == field:
                    t, ix, _ = Reader(S, b"").token(sig, 2); return enum_names(S, ix - 1)
        self.effect = enum_of("ShipVeterancyRankEffectInfo", "Effect")
        self.faction = enum_of("ShipDataTableObject", "Faction")
        self.sclass = enum_of("ShipDataTableObject", "ShipClass")
        self.effect_by_name = {v.lower(): k for k, v in self.effect.items()}

    def find(self, name):
        hits = [s for s in self.ships if s["name"].lower() == name.lower()]
        if not hits: raise SystemExit("no ship named %r. Try: list" % name)
        return hits[0]

    def eff(self, k): return self.effect.get(k, str(k))

    def write_int(self, rel, v): struct.pack_into("<i", self.buf, self.base + rel, int(v))
    def write_float(self, rel, v): struct.pack_into("<f", self.buf, self.base + rel, float(v))

    def veterancy_span(self, ship):
        """(start, end) of a ship's CrewVeterancyRankEffects bytes, relative to the MonoBehaviour start."""
        i = next(k for k, x in enumerate(self.ships) if x is ship)
        start = ship["_off"]["CrewVeterancyRankEffects"][0]
        end = self.ships[i + 1]["_off"]["name"][0] if i + 1 < len(self.ships) else self.res["_off"]["CrewVeterancyRankInfo"][0]
        return start, end

    def copy_vet(self, target, source):
        """Replace target's veterancy list with a copy of source's. Resizes the database object and shifts later objects."""
        ts, te = self.veterancy_span(target); ss, se = self.veterancy_span(source)
        mono = bytes(self.buf[self.base:self.base + self.size])
        new_mono = mono[:ts] + mono[ss:se] + mono[te:]
        delta = len(new_mono) - len(mono)
        old_end = self.base + self.size
        new_end = self.base + len(new_mono)
        shift = (((new_end + 7) & ~7) - ((old_end + 7) & ~7))          # keep later objects 8-byte aligned
        buf = bytes(self.buf)
        meta, fsize, ver, doff = struct.unpack(">IIII", buf[:16])
        table = bytearray(buf[:doff])
        for pid, abs_off, size, cid, epos in read_objects(buf):
            if pid == self.pid:
                struct.pack_into("<I", table, epos + 12, len(new_mono))              # size field
            elif abs_off >= old_end:
                struct.pack_into("<I", table, epos + 8, abs_off - doff + shift)      # offset field
        struct.pack_into(">I", table, 4, fsize + shift)                             # total file size
        pad = b"\0" * (((new_end + 7) & ~7) - new_end)
        tail = buf[(old_end + 7) & ~7:] if shift or True else b""
        gap = buf[old_end:(old_end + 7) & ~7]                                       # original padding (discarded, re-created)
        self.buf = bytearray(bytes(table) + buf[doff:self.base] + new_mono + pad + tail)
        return delta, shift

    def save(self, path):
        open(path, "wb").write(self.buf); print("wrote", path)



def load_factory(db, scene_path, factory):
    """find the <factory> MonoBehaviour in a scene file (by its GameObject's name) and parse it."""
    d = bytes(open(scene_path, "rb").read()); S = db.S
    ti = S.typedef_index(factory)
    try:
        if struct.unpack(">I", d[4:8])[0] != len(d): raise LookupError("not a serialized file")
        table = read_objects(d)
    except LookupError: raise
    except Exception: raise LookupError("not a readable serialized file")
    go_names = {}
    for pid, off, size, cid, _e in table:
        if cid == 1:
            b = d[off:off + size]; n = struct.unpack_from("<i", b, 0)[0]; o = 4 + 12 * n + 4
            L = struct.unpack_from("<i", b, o)[0]; go_names[pid] = b[o + 4:o + 4 + L].decode("utf-8", "replace")
    for pid, off, size, cid, _e in table:
        if cid != 114: continue
        b = d[off:off + size]
        if go_names.get(struct.unpack_from("<iq", b, 0)[1]) != factory: continue
        n = struct.unpack_from("<i", b, 28)[0]; q = 32 + ((n + 3) & ~3)
        r = Reader(S, b, q)
        try: res = r.obj(ti)
        except Exception: continue
        if r.p == size: return bytearray(d), off, res
    raise LookupError("no %s in %s" % (factory, scene_path))


def chain_source(a, f_, outdir):
    """If this file was already patched into outdir by an earlier command, continue from THAT copy so edits stack
    (otherwise every command would start again from the original and overwrite the previous one). --fresh disables this."""
    cand = os.path.join(outdir, os.path.basename(f_))
    if not getattr(a, "fresh", False) and os.path.exists(cand) and os.path.abspath(cand) != os.path.abspath(f_):
        print("   (continuing from your earlier edits in %s; use --fresh to start from the original instead)" % cand)
        return cand
    return f_


def load_missiles(S, buf):
    """find the MissileDataTable MonoBehaviour (munition types) in a scene file. Returns (abs_offset, parsed) or raises LookupError."""
    ti = S.typedef_index("MissileDataTable")
    try:
        if struct.unpack(">I", buf[4:8])[0] != len(buf): raise LookupError("not a serialized file")
        table = read_objects(buf)
    except LookupError: raise
    except Exception: raise LookupError("not a readable serialized file")
    for pid, off, size, cid, _e in table:
        if cid != 114 or not 500 < size < 100000: continue
        b = buf[off:off + size]
        n = struct.unpack_from("<i", b, 32)[0]
        if not 1 <= n <= 500: continue
        r = Reader(S, b, 32)
        try: res = r.obj(S.typedef_index("MissileDataTable"))
        except Exception: continue
        if r.p == size and res.get("objects") and "MaxMissiles" in res["objects"][0]: return off, res
    raise LookupError("no munition table in this file")


MUNITION_EDITABLE = ("StandardSalvoSize", "MaxMissiles", "HasUnlimitedMunitions", "SupplyCostPerMunition", "SurvivalCostPerMunition", "PointValue")


def munitions_cmd(a):
    paths = []
    rest = list(a.args)
    while rest and os.path.exists(rest[0]): paths.append(rest.pop(0))
    files = []
    for pth in paths:
        if os.path.isdir(pth): files += [os.path.join(r_, f_) for r_, _d, fs in os.walk(pth) for f_ in sorted(fs)]
        else: files.append(pth)
    if not files: raise SystemExit("give the level files or the folder that contains them")
    S = Schema(a.dll)
    if a.cmd == "setmunition":
        if len(rest) < 3 or (len(rest) - 1) % 2: raise SystemExit('usage: shipdb setmunition "munition name" Field value [Field value ...]')
        mname_, pairs = rest[0], [(rest[i], rest[i + 1]) for i in range(1, len(rest), 2)]
        for fld, _v in pairs:
            if fld not in MUNITION_EDITABLE: raise SystemExit("cannot edit %r. Editable: %s" % (fld, " ".join(MUNITION_EDITABLE)))
        outdir = a.out; os.makedirs(outdir, exist_ok=True)
    shown = False; patched = []; skipped = []
    for f_ in files:
        srcf = chain_source(a, f_, outdir) if a.cmd == "setmunition" else f_
        buf = bytearray(open(srcf, "rb").read())
        try: off, res = load_missiles(S, bytes(buf))
        except LookupError: skipped.append(os.path.basename(f_)); continue
        if a.cmd == "munitions":
            if shown: print("(%s has the same table layout; not repeated)" % os.path.basename(f_)); continue
            shown = True
            print("munition table in %s: %d types\n" % (os.path.basename(f_), len(res["objects"])))
            print("%-30s %3s %6s %6s %5s %7s %7s %5s" % ("name", "id", "salvo", "max", "unlim", "supply", "survive", "pts"))
            for m in res["objects"]:
                print("%-30s %3s %6s %6s %5s %7.2f %7.2f %5s" % (m["name"], m["id"], m["StandardSalvoSize"], m["MaxMissiles"], m["HasUnlimitedMunitions"], m["SupplyCostPerMunition"], m["SurvivalCostPerMunition"], m["PointValue"]))
            continue
        hits = [m for m in res["objects"] if m["name"].lower() == mname_.lower()]
        if not hits: raise SystemExit("no munition named %r. Try: munitions" % mname_)
        m = hits[0]
        for fld, val in pairs:
            pos, code = m["_off"][fld]; pos += off
            old = m[fld]
            if code == 0x0c: struct.pack_into("<f", buf, pos, float(val))
            elif code == 0x08: struct.pack_into("<i", buf, pos, int(val))
            elif code == 0x02: struct.pack_into("<B", buf, pos, 1 if val.lower() in ("1", "true", "yes") else 0)
            else: raise SystemExit("field %r is not a simple number/bool" % fld)
            print("[ok] %-8s %s.%s: %s -> %s" % (os.path.basename(f_), m["name"], fld, old, val))
        outp = os.path.join(outdir, os.path.basename(f_)); open(outp, "wb").write(buf)
        off2, res2 = load_missiles(S, bytes(buf))                      # re-read the result to prove it is still valid
        chk = next(x for x in res2["objects"] if x["name"] == m["name"])
        for fld, val in pairs:
            assert abs(float(chk[fld]) - float(val if val.lower() not in ("true", "false", "yes") else (val.lower() != "false"))) < 1e-4 or isinstance(chk[fld], bool), fld
        patched.append(os.path.basename(f_))
    if a.cmd == "setmunition":
        print("\npatched %d file(s): %s" % (len(patched), ", ".join(patched) or "none"))
        print("no munition table (left alone): %s" % (", ".join(skipped) or "none"))
        print("Patched copies are in %s/ (same file names). Back up the originals, then copy them over." % outdir)
    elif not shown: print("no munition table found in the given files")


# ----------------------------------------------------------------------------- blueprints (war-room research unlocks)
def resize_object(buf, pid, new_mono):
    """replace the bytes of object <pid> with new_mono (any length); fixes the object table, later offsets and the file size."""
    meta, fsize, ver, doff = struct.unpack(">IIII", buf[:16])
    objs = read_objects(buf)
    tgt = next(o for o in objs if o[0] == pid); base, size = tgt[1], tgt[2]
    old_end, new_end = base + size, base + len(new_mono)
    shift = ((new_end + 7) & ~7) - ((old_end + 7) & ~7)
    table = bytearray(buf[:doff])
    for p_, abs_off, sz, cid, epos in objs:
        if p_ == pid: struct.pack_into("<I", table, epos + 12, len(new_mono))
        elif abs_off >= old_end: struct.pack_into("<I", table, epos + 8, abs_off - doff + shift)
    struct.pack_into(">I", table, 4, fsize + shift)
    pad = b"\0" * (((new_end + 7) & ~7) - new_end)
    return bytes(table) + buf[doff:base] + new_mono + pad + buf[(old_end + 7) & ~7:]


def load_wardata(S, buf):
    """find the WarRoomDataTables MonoBehaviour (holds the blueprint/research unlock lists). Returns (pid, abs_off, size, parsed)."""
    ti = S.typedef_index("WarRoomDataTables")
    try:
        if struct.unpack(">I", buf[4:8])[0] != len(buf): raise LookupError("not a serialized file")
        table = read_objects(buf)
    except LookupError: raise
    except Exception: raise LookupError("not a readable serialized file")
    for pid, off, size, cid, _e in table:
        if cid != 114 or size < 3000: continue
        r = Reader(S, buf[off:off + size], 32)
        try: res = r.obj(ti)
        except Exception: continue
        if r.p == size and res.get("BlueprintInfo"): return pid, off, size, res
    raise LookupError("no blueprint table in this file")


UNLOCK_KEYS = {"battlestar": "BattlestarShipUnlocks", "capital": "CapitalShipUnlocks"}
UNLOCK_FIELDS = ("Campaign", "CapitalShipUnlocks", "MunitionUnlocks", "SquadronUnlocks", "BattlestarShipUnlocks")


def pack_unlock(ship_id, mission, cost, label, unlocked, flag2):
    lab = label.encode("utf-8")
    return (struct.pack("<iii", ship_id, mission, cost) + struct.pack("<i", len(lab)) + lab + b"\0" * ((4 - len(lab) % 4) % 4)
            + bytes([1 if unlocked else 0]) + b"\0\0\0" + bytes([1 if flag2 else 0]) + b"\0\0\0")


def list_span(res, ci, key):
    """(count_pos, first_elem_pos, end_pos) of one unlock list, relative to the MonoBehaviour start"""
    items = res["BlueprintInfo"]; it = items[ci]
    count_pos = it["_off"][key][0]
    k = UNLOCK_FIELDS.index(key)
    if k + 1 < len(UNLOCK_FIELDS): end = it["_off"][UNLOCK_FIELDS[k + 1]][0]
    elif ci + 1 < len(items): end = items[ci + 1]["_off"]["Campaign"][0]
    else: end = res["_off"]["UnitRewards"][0]
    return count_pos, count_pos + 4, end


def check_unlock_layout(mono, res):
    """prove pack_unlock reproduces the game's own entries byte for byte (otherwise refuse to edit)"""
    n = 0
    for ci, it in enumerate(res["BlueprintInfo"]):
        for key in ("CapitalShipUnlocks", "BattlestarShipUnlocks"):
            cpos, first, end = list_span(res, ci, key); els = it[key]
            for j, u in enumerate(els):
                st = u["_off"]["ODOMECMDGKJ"][0]
                en = els[j + 1]["_off"]["ODOMECMDGKJ"][0] if j + 1 < len(els) else end
                if mono[st:en] != pack_unlock(u["ODOMECMDGKJ"], u["GEIBBHBCKAM"], u["GKIMOHIEFNI"], u["NPFHKIHKKIC"], u["HJGFMHOLHPG"], u["COPOFKMOPIH"]):
                    raise SystemExit("blueprint entry layout differs from what this tool expects (campaign %d %s #%d); not editing" % (ci, key, j))
                n += 1
    return n


def blueprint_cmd(a):
    paths = []; rest = list(a.args)
    while rest and os.path.exists(rest[0]): paths.append(rest.pop(0))
    files = []
    for pth in paths:
        if os.path.isdir(pth): files += [os.path.join(r_, f_) for r_, _d, fs in os.walk(pth) for f_ in sorted(fs)]
        else: files.append(pth)
    if not files: raise SystemExit("give the level files or the folder that contains them")
    S = Schema(a.dll)
    outdir = a.out
    if a.cmd == "addblueprint":
        if len(rest) != 1: raise SystemExit('usage: shipdb addblueprint "Colonial Mercury" [--cost RP] [--like "Jupiter Mk2"] [--campaigns 0,1,2] [--list battlestar|capital] [--unlocked] [--label Mc]')
        os.makedirs(outdir, exist_ok=True); ship_name = rest[0]
    shown = False; done = []; skipped = []
    for f_ in files:
        src = chain_source(a, f_, outdir) if a.cmd == "addblueprint" else f_
        buf = open(src, "rb").read()
        try: pid, off, size, res = load_wardata(S, buf)
        except LookupError: 
            if a.cmd == "addblueprint":
                # files without the table still get the build-stat edit below
                pass
            else: skipped.append(os.path.basename(f_)); continue
            res = None
        names = {}
        try: db = Db(a.dll, src, S); names = {x["id"]: x["name"] for x in db.ships}
        except (SystemExit, Exception): db = None
        if a.cmd == "blueprints":
            if shown: print("(%s has the same table; not repeated)" % os.path.basename(f_)); continue
            shown = True
            print("blueprint unlocks in %s (RP cost, mission requirement, starts unlocked):" % os.path.basename(f_))
            for it in res["BlueprintInfo"]:
                print("\n campaign %s" % it["Campaign"])
                for key in ("CapitalShipUnlocks", "BattlestarShipUnlocks"):
                    print("   %s:" % key)
                    for u in it[key]:
                        print("     %-24s cost %-5s mission %-3s label %-4s%s" % (names.get(u["ODOMECMDGKJ"], u["ODOMECMDGKJ"]), u["GKIMOHIEFNI"], u["GEIBBHBCKAM"], u["NPFHKIHKKIC"], "  [unlocked at start]" if u["HJGFMHOLHPG"] else ""))
            continue
        # ---- addblueprint ----
        tag = os.path.basename(f_); changed = False
        if db is not None and not a.keep_build_stats:
            tgt = db.find(ship_name); like = db.find(a.like)
            for fld in ("Buildable", "BuildTime", "BuildCost", "RushCost"):
                pos, code = tgt["_off"][fld]
                v = like[fld]
                if code == 0x02: struct.pack_into("<B", db.buf, db.base + pos, 1 if v else 0)
                else: struct.pack_into("<i", db.buf, db.base + pos, int(v))
            print("[ok] %-8s %s: Buildable/BuildTime/BuildCost/RushCost copied from %s (%s, %s, %s, %s)" % (tag, tgt["name"], like["name"], like["Buildable"], like["BuildTime"], like["BuildCost"], like["RushCost"]))
            buf = bytes(db.buf); changed = True
            try: pid, off, size, res = load_wardata(S, buf)
            except LookupError: res = None
        if res is not None and db is not None:
            mono = buf[off:off + size]; check_unlock_layout(mono, res)
            sid = db.find(ship_name)["id"]; key = UNLOCK_KEYS[a.list]
            want = [int(x) for x in a.campaigns.split(",")] if a.campaigns else [it["Campaign"] for it in res["BlueprintInfo"]]
            like_cost = {}
            for it in res["BlueprintInfo"]:
                for u in it[key]:
                    if names.get(u["ODOMECMDGKJ"], "").lower() == a.like.lower(): like_cost[it["Campaign"]] = u["GKIMOHIEFNI"]
            fallback = next(iter(like_cost.values()), 3000)
            jobs = []
            for ci, it in enumerate(res["BlueprintInfo"]):
                if it["Campaign"] not in want: continue
                if any(u["ODOMECMDGKJ"] == sid for u in it[key]):
                    print("[skip] %-8s campaign %s already lists %s" % (tag, it["Campaign"], ship_name)); continue
                cpos, first, end = list_span(res, ci, key)
                jobs.append((end, cpos, it["Campaign"], len(it[key])))
            mono = bytearray(mono)
            for end, cpos, camp, cnt in sorted(jobs, reverse=True):
                cost = a.cost if a.cost is not None else like_cost.get(camp, fallback)
                el = pack_unlock(sid, a.mission, cost, a.label, a.unlocked, False)
                struct.pack_into("<i", mono, cpos, cnt + 1)
                mono[end:end] = el
                print("[ok] %-8s campaign %s: %s added to %s (cost %d RP, mission %d%s)" % (tag, camp, ship_name, key, cost, a.mission, ", unlocked at start" if a.unlocked else ""))
                changed = True
            if jobs: buf = resize_object(buf, pid, bytes(mono))
        if changed:
            outp = os.path.join(outdir, os.path.basename(f_)); open(outp, "wb").write(buf)
            # prove the result is still valid
            Db(a.dll, outp, S)
            try:
                p2, o2, s2, r2 = load_wardata(S, open(outp, "rb").read())
                ok = sum(1 for it in r2["BlueprintInfo"] for u in it[UNLOCK_KEYS[a.list]] if u["ODOMECMDGKJ"] == Db(a.dll, outp, S).find(ship_name)["id"])
                print("     verified %s: re-read OK, %s listed in %d campaign(s)" % (tag, ship_name, ok))
            except LookupError: print("     verified %s: ship database re-read OK" % tag)
            done.append(tag)
    if a.cmd == "addblueprint":
        print("\npatched %d file(s): %s" % (len(done), ", ".join(done) or "none"))
        print("Patched copies are in %s/ (same file names). Back up the originals, then copy them over." % outdir)
        print("NOTE: blueprint unlocks are read when a campaign starts - use a NEW campaign (an existing save keeps its own research state).")
    elif not shown: print("no blueprint table found in the given files")


def fleetinfo_cmd(a):
    """fleetinfo / setfleetinfo: the campaign fleet limits (WarRoomDataTables.FleetInfo: MaxShips, StartingFleetPointCap, JumpCooldownCostPerTurn)"""
    paths = []; rest = list(a.args)
    while rest and os.path.exists(rest[0]): paths.append(rest.pop(0))
    files = []
    for pth in paths:
        if os.path.isdir(pth): files += [os.path.join(r_, f_) for r_, _d, fs in os.walk(pth) for f_ in sorted(fs)]
        else: files.append(pth)
    if not files: raise SystemExit("give the level files or the folder that contains them")
    S = Schema(a.dll); outdir = a.out
    edits = []
    if a.cmd == "setfleetinfo":
        if not rest or len(rest) % 2: raise SystemExit("usage: shipdb setfleetinfo MaxShips 10 [StartingFleetPointCap 6000 ...]")
        edits = [(rest[i], int(rest[i + 1])) for i in range(0, len(rest), 2)]
        os.makedirs(outdir, exist_ok=True)
    done = []
    for f_ in files:
        src = chain_source(a, f_, outdir) if edits else f_
        buf = bytearray(open(src, "rb").read())
        try: pid, off, size, res = load_wardata(S, bytes(buf))
        except LookupError: continue
        fi = res["FleetInfo"]; tag = os.path.basename(f_)
        if not edits:
            print("%s: %s" % (tag, {k: v for k, v in fi.items() if k != "_off"})); continue
        for fld, val in edits:
            if fld not in fi["_off"] or fld == "_off": raise SystemExit("no FleetInfo field %r (fields: %s)" % (fld, ", ".join(k for k in fi if k != "_off")))
            pos, code = fi["_off"][fld]
            struct.pack_into("<i", buf, off + pos, val)
            print("[ok] %-8s FleetInfo.%s: %s -> %s" % (tag, fld, fi[fld], val))
        outp = os.path.join(outdir, tag); open(outp, "wb").write(bytes(buf))
        chk = load_wardata(S, bytes(buf))[3]["FleetInfo"]
        assert all(chk[f] == v for f, v in edits), "verification failed"
        done.append(tag)
    if edits:
        print("\npatched %d file(s): %s\nPatched copies are in %s/ (same file names). Back up the originals, then copy them over." % (len(done), ", ".join(done) or "none", outdir))
        print("NOTE: the limit is read from the game data when needed (not stored in saves); the fleet screens may only have a fixed number of ship slots, so test larger values.")


def vet_lines(db, s):
    out = []
    for rk in s["CrewVeterancyRankEffects"]:
        effs = ", ".join("%s %+g" % (db.eff(e["Effect"]), e["BonusAmount"]) for e in rk["Effects"])
        out.append("   rank %d: %s" % (rk["Rank"], effs or "-"))
    return out


def squadron_list(buf, S):
    """every squadron prefab in a sharedassets file: dicts with name, FighterSquadron + ShipInfo (offset, parsed) or []"""
    table = read_objects(buf)
    byp = {p_: (o, sz, c) for p_, o, sz, c, _ in table}
    tiF, tiS = S.typedef_index("FighterSquadron"), S.typedef_index("ShipInfo")
    out = []
    for pid, (o, sz, c) in byp.items():
        if c != 1: continue
        try:
            n = struct.unpack_from("<i", buf, o)[0]; q = o + 4 + 12 * n + 4
            l = struct.unpack_from("<i", buf, q)[0]; name = buf[q + 4:q + 4 + l].decode("latin1")
        except Exception: continue
        if not name.endswith("Squadron"): continue
        fs = si = None
        for i in range(n):
            f, cp = struct.unpack_from("<iq", buf, o + 4 + 12 * i)
            if cp not in byp or byp[cp][2] != 114: continue
            co, cs, _c = byp[cp]
            try:
                r = Reader(S, buf[co:co + cs], 32); res = r.obj(tiF)
                mc = res.get("MaxShipCount")
                if r.p == cs and isinstance(mc, int) and 1 <= mc <= 60 and isinstance(res.get("ShipLoss"), list) and len(res["ShipLoss"]) == mc - 1:
                    fs = (co, res); continue
            except Exception: pass
            try:
                r = Reader(S, buf[co:co + cs], 32); res = r.obj(tiS)
                if r.p == cs and isinstance(res.get("MaxHullPoints"), float): si = (co, res)
            except Exception: pass
        if fs and si: out.append(dict(name=name, fs=fs, si=si))
    return sorted(out, key=lambda x: x["name"])


def squadron_cmd(a):
    """squadrons <dll> <assets|folder>            list;   setsquadron <dll> <assets|folder> "name" HullFactor 2 | MaxHullPoints N | FighterHull N"""
    rest = list(a.args)
    if a.level0.lower().endswith(".dll"): dll, rest = a.level0, [a.dll] + rest
    else: dll, rest = a.dll, [a.level0] + rest
    paths = []
    while rest and os.path.exists(rest[0]): paths.append(rest.pop(0))
    files = []
    for pth in paths:
        if os.path.isdir(pth): files += [os.path.join(r_, f_) for r_, _d, fs in os.walk(pth) for f_ in sorted(fs)]
        else: files += pth.split(",") if not os.path.exists(pth) else [pth]
    if not files: raise SystemExit("give sharedassets9.assets (or the game's Data folder)")
    S = Schema(dll); outdir = a.out
    key = lambda t: t.lower().replace(" ", "").replace("_", "")
    edits = []
    if a.cmd == "setsquadron":
        if len(rest) < 3 or len(rest) % 2 == 0:
            raise SystemExit('usage: shipdb setsquadron "Viper Mk VII" HullFactor 2   (or MaxHullPoints 120 / FighterHull 12; name "all" = every squadron)')
        sel = rest[0]; edits = [(rest[i], float(rest[i + 1])) for i in range(1, len(rest), 2)]
        for f_, _v in edits:
            if f_ not in ("HullFactor", "MaxHullPoints", "FighterHull"): raise SystemExit("editable: HullFactor (x pool and thresholds), MaxHullPoints (pool), FighterHull (hull per fighter at full strength)")
        os.makedirs(outdir, exist_ok=True)
    done = []
    for f_ in files:
        src = chain_source(a, f_, outdir) if edits else f_
        try:
            raw = open(src, "rb").read()
            if len(raw) < 20 or struct.unpack(">I", raw[4:8])[0] != len(raw): continue
            sq = squadron_list(raw, S)
        except Exception: continue
        if not sq: continue
        tag = os.path.basename(f_)
        if not edits:
            print("== %s\n%-30s%9s%11s%13s   ShipLoss thresholds" % (tag, "squadron", "fighters", "hull pool", "per fighter"))
            for q in sq:
                mc = q["fs"][1]["MaxShipCount"]; hp = q["si"][1]["MaxHullPoints"]
                print("%-30s%9d%11g%13.2f   %s" % (q["name"], mc, hp, hp / mc, [round(x, 2) for x in q["fs"][1]["ShipLoss"]]))
            continue
        hit = [q for q in sq if key(sel) == "all" or key(sel) in key(q["name"])]
        if not hit: raise SystemExit("no squadron matches %r. Squadrons: %s" % (sel, ", ".join(q["name"] for q in sq)))
        buf = bytearray(raw); print("== " + tag)
        for q in hit:
            co, res = q["fs"]; so, sres = q["si"]; old = sres["MaxHullPoints"]; mc = res["MaxShipCount"]; f = 1.0
            for fld, v in edits:
                if v <= 0: raise SystemExit("values must be positive")
                f *= v if fld == "HullFactor" else (v / old if fld == "MaxHullPoints" else v * mc / old)
                old = old if fld == "HullFactor" else old
            o_hp = sres["_off"]["MaxHullPoints"][0]; struct.pack_into("<f", buf, so + o_hp, sres["MaxHullPoints"] * f)
            base = co + res["_off"]["ShipLoss"][0] + 4
            for i, v in enumerate(res["ShipLoss"]): struct.pack_into("<f", buf, base + 4 * i, v * f)
            print("[ok] %-30s hull pool %g -> %g (x%g), per fighter %.2f -> %.2f, %d thresholds scaled" % (q["name"], sres["MaxHullPoints"], sres["MaxHullPoints"] * f, f, sres["MaxHullPoints"] / mc, sres["MaxHullPoints"] * f / mc, len(res["ShipLoss"])))
        chk = {q["name"]: q for q in squadron_list(bytes(buf), S)}
        for q in hit: assert abs(chk[q["name"]]["si"][1]["MaxHullPoints"] - q["si"][1]["MaxHullPoints"] * 0 - chk[q["name"]]["si"][1]["MaxHullPoints"]) < 1e-9 and q["name"] in chk, "verification failed"
        open(os.path.join(outdir, tag), "wb").write(bytes(buf)); done.append(tag)
    if edits:
        print("\npatched %d file(s): %s\nPatched copies are in %s/ (same file names). Back up the originals, then copy them over." % (len(done), ", ".join(done) or "none", outdir))
        print("NOTE: level data shows its own hull number for the UI only; battles use these prefab values.")
    elif not files or True:
        pass



# ---------------------------------------------------------------- commanders (ranks / fleet-size traits) ----------------
def find_tables(S, buf, ti):
    for pid, off, size, cid, _ in read_objects(buf):
        if cid != 114 or size < 100: continue
        r = Reader(S, buf[off:off + size], 32)
        try: res = r.obj(ti)
        except BaseException: continue
        if r.p == size and len(res.get("RankXPTable", [])) > 0: return pid, off, size, res
    return None

def files_of(arg):
    out = []
    for p in arg.split(","):
        out += sorted(glob.glob(os.path.join(p, "*"))) if os.path.isdir(p) else [p]
    return [f for f in out if os.path.isfile(f)]

def show_commanders(res):
    xp = [(x["Rank"], x["XPRequired"]) for x in res["RankXPTable"]]
    print("  ranks:", xp)
    print("  leadership interval per difficulty:", [(x["Difficulty"], x["Interval"]) for x in res["LeadershipIntervalPerDifficulty"]])
    o = res["OfficerSkills"]; print("  fleet-size traits (CommandSkills):", len(o["CommandSkills"]), " +%d fleet points each" % o["FleetPointsPerCommandSkill"],
          "| FleetSkills:", len(o["FleetSkills"]), "| CaptainSkills:", len(o["CaptainSkills"]))

def extend_commanders(mono, res, add, growth, every):
    m = bytearray(mono)
    xp = [(x["Rank"], x["XPRequired"]) for x in res["RankXPTable"]]
    last_step = xp[-1][1] - xp[-2][1]; new = []; cur = xp[-1][1]; step = last_step
    for i in range(add):
        step = int(round(step * growth / 50.0)) * 50 or 50; cur += step; new.append((xp[-1][0] + 1 + i, cur))
    # --- do the later edits first so earlier offsets stay valid ---
    o_off = res["_off"]
    edits = []                                                                           # (position, delete_len, insert_bytes)
    if every:
        iv_off = res["_off"]["LeadershipIntervalPerDifficulty"][0]
        for e in res["LeadershipIntervalPerDifficulty"]: edits.append((e["_off"]["Interval"][0], 4, struct.pack("<i", 1)))
        cs = res["OfficerSkills"]["CommandSkills"]; cs_off = res["OfficerSkills"]["_off"]["CommandSkills"][0]
        want = xp[-1][0] + add - 1                                                       # rank 1 has no promotion -> one trait per promotion
        icons = [bytes(mono[e["_off"]["Icon"][0]:e["_off"]["Icon"][0] + 12]) for e in cs]
        extra = b"".join(icons[(len(cs) + i) % len(icons)] for i in range(want - len(cs))) if want > len(cs) else b""
        edits.append((cs_off, 4, struct.pack("<i", max(want, len(cs)))))
        if extra: edits.append((cs_off + 4 + 12 * len(cs), 0, extra))
    x_off = res["_off"]["RankXPTable"][0]
    edits.append((x_off, 4, struct.pack("<i", len(xp) + add)))
    edits.append((x_off + 4 + 8 * len(xp), 0, b"".join(struct.pack("<ii", r, x) for r, x in new)))
    for pos, dl, ins in sorted(edits, key=lambda e: -e[0]): m[pos:pos + dl] = ins
    return bytes(m), new


def fix_specialisations(buf, off, res, f, outdir, revert=False):
    """The captain-skill choices of the 7 fleet specialisations are listed per subsystem k as skills [3k, 3k+1, 3k+2]. The base game ships
    subsystem 0 (the Tactician line: Charismatic / Decisive Leader / Indomitable) as [2, 10, 1]: Charismatic can never be picked and
    skill 10 (Custodian, the Greasemonkey line's) is offered a second time, which breaks promotions. Same size, so patched in place."""
    out = bytearray(buf); fixed = 0
    for e in res["CommanderSpecialisations"]:
        k = e["SubSystem"]; want = [3 * k, 3 * k + 1, 3 * k + 2]; have = list(e["Specialisations"])
        if revert: want = [2, 10, 1] if k == 0 else have                                   # the game's shipped value for subsystem 0
        print("  subsystem %d: %s%s" % (k, have, "" if have == want else "  -> %s" % want))
        if have != want:
            assert len(have) == 3
            p = off + e["_off"]["Specialisations"][0] + 4                                  # skip the array's element count
            for j, v in enumerate(want): struct.pack_into("<i", out, p + 4 * j, v)
            fixed += 1
    if not fixed: print("  nothing to fix in", os.path.basename(f)); return
    os.makedirs(outdir, exist_ok=True); open(os.path.join(outdir, os.path.basename(f)), "wb").write(out)
    print("  fixed %d subsystem(s) -> %s" % (fixed, os.path.join(outdir, os.path.basename(f))))


def commanders_cmd(a):
    S = Schema(a.dll); ti = S.typedef_index("DataTables"); n = 0
    outdir = a.out                                     # same folder as the ship edits, so they stack
    for f in files_of(a.level0):
        buf = open(chain_source(a, f, outdir), "rb").read()
        try: hit = find_tables(S, buf, ti)
        except BaseException: hit = None
        if not hit: continue
        pid, off, size, res = hit; n += 1; print(os.path.basename(f), "- DataTables object", pid)
        if a.cmd == "commanders": show_commanders(res); continue
        if a.cmd == "fixspecs": fix_specialisations(buf, off, res, f, outdir, a.revert_specs); continue
        new_mono, new = extend_commanders(buf[off:off + size], res, a.add, a.xp_growth, a.every_level)
        out = resize_object(bytearray(buf), pid, new_mono)
        os.makedirs(outdir, exist_ok=True); open(os.path.join(outdir, os.path.basename(f)), "wb").write(out)
        print("  new ranks:", new); show_commanders(find_tables(S, out, ti)[3])
    if not n: print("no commander DataTables object found in %s" % a.level0)
    if a.cmd == "fixspecs" and n: print("Copy the level files from %s/ over the originals (back them up first)." % outdir)
    if a.cmd == "setcommanders" and n:
        newmax = len(res["RankXPTable"]) + a.add
        cmdn = " --command-nodes 10" if a.every_level else ""   # one Command node per rank 1-10 (up to 19 possible, see deadlock_dll.py)
        print("Copy the level files from %s/ over the originals (back them up first), then patch the DLL (rank cap%s):\n    python -m deadlock_tools dll patch --max-rank %d%s" % (outdir, " + number of Command-column nodes" if cmdn else "", newmax, cmdn))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "vet", "show", "set", "setvet", "setxp", "copyvet", "fleeticons", "fleeticon", "munitions", "setmunition", "blueprints", "addblueprint", "addvet", "removevet", "fleetinfo", "setfleetinfo", "commanders", "setcommanders", "fixspecs", "squadrons", "setsquadron"])
    ap.prog = "deadlock shipdb"
    ap.add_argument("args", nargs="*", help="the command's own arguments (ship name, field, value ...)")
    config.add_path_args(ap, "levels", "dll", "out")
    ap.add_argument("--cost", type=int, help="addblueprint: research point cost (default: same as --like ship in that campaign, else 3000)")
    ap.add_argument("--like", default="Jupiter Mk2", help="addblueprint: reference ship whose build stats/RP cost are copied (default Jupiter Mk2)")
    ap.add_argument("--list", default="battlestar", choices=["battlestar", "capital"], help="addblueprint: which blueprint list (default battlestar)")
    ap.add_argument("--campaigns", help="addblueprint: comma list of campaign numbers (default all)")
    ap.add_argument("--mission", type=int, default=-1, help="addblueprint: story mission required (-1 = none, default)")
    ap.add_argument("--label", default="Mc", help="addblueprint: short label on the research node (default Mc)")
    ap.add_argument("--unlocked", action="store_true", help="addblueprint: available from the start without researching")
    ap.add_argument("--keep-build-stats", action="store_true", help="addblueprint: do not copy Buildable/BuildTime/BuildCost/RushCost from --like")
    ap.add_argument("--add", type=int, default=10, help="setcommanders: how many ranks to add (default 10)")
    ap.add_argument("--revert-specs", action="store_true", help="fixspecs: put the game's original specialisation lists back (undo the fix)")
    ap.add_argument("--xp-growth", type=float, default=1.2, help="setcommanders: each new rank costs this much more XP than the step before (default 1.2)")
    ap.add_argument("--every-level", action="store_true", help="setcommanders: leadership interval 1 and one fleet-size trait per rank")
    ap.add_argument("--fresh", action="store_true", help="ignore earlier edits already in the output folder and start again from the original files")
    ap.add_argument("--factory", default="FleetModelFactory", help="FleetModelFactory (war-room fleet icon, default) or ProjectionFactory")
    a = ap.parse_args()
    _normalise(a)
    if a.cmd in ("commanders", "setcommanders", "fixspecs"):
        commanders_cmd(a); return
    if a.cmd in ("squadrons", "setsquadron"):
        squadron_cmd(a); return
    if a.cmd in ("fleetinfo", "setfleetinfo"):
        a.args = [a.level0] + a.args
        fleetinfo_cmd(a); return
    if a.cmd in ("blueprints", "addblueprint"):
        a.args = [a.level0] + a.args
        blueprint_cmd(a); return
    if a.cmd in ("munitions", "setmunition"):
        a.args = [a.level0] + a.args
        munitions_cmd(a); return
    # Batch mode: edit commands accept a folder (or comma-separated files) and patch EVERY file that holds a ship database
    if a.cmd in ("set", "setvet", "setxp", "copyvet", "addvet", "removevet") and (os.path.isdir(a.level0) or "," in a.level0):
        files = []
        for part in a.level0.split(","):
            if os.path.isdir(part): files += [os.path.join(r_, f_) for r_, _d, fs in os.walk(part) for f_ in sorted(fs)]
            elif part: files.append(part)
        outdir = a.out; os.makedirs(outdir, exist_ok=True)
        done, skipped = [], []
        files = [f_ for f_ in files if os.path.isfile(f_)]
        for f_ in files:
            src = chain_source(a, f_, outdir)
            try: Db(a.dll, src)
            except (SystemExit, Exception): skipped.append(os.path.basename(f_)); continue
            print("== " + os.path.basename(f_))
            run(a, src, os.path.join(outdir, os.path.basename(f_))); done.append(os.path.basename(f_))
        print("\nedited %d file(s): %s" % (len(done), ", ".join(done) or "none"))
        print("no ship database (left alone): %s" % (", ".join(skipped) or "none"))
        print("Patched copies are in %s/ (same file names). Back up the originals, then copy them over." % outdir)
        return
    if a.cmd in EDIT_CMDS:                                   # one level file: edit it, continuing from earlier edits
        os.makedirs(a.out, exist_ok=True)
        run(a, chain_source(a, a.level0, a.out), os.path.join(a.out, os.path.basename(a.level0)))
    else:
        level0 = a.level0 if a.cmd in ("fleeticons", "fleeticon") else ship_table_file(a)
        if a.cmd in ("list", "show", "vet") and not a.fresh:          # show the current state: your edits in out/levels/ if there are any (like the GUI)
            cand = os.path.join(a.out, os.path.basename(level0))
            if os.path.isfile(cand) and not config.same_file(cand, level0): print("(showing your edited copy %s; --fresh shows the original)" % cand); level0 = cand
        run(a, level0, os.path.join(a.out, os.path.basename(level0 or "")))


EDIT_CMDS = ("set", "setvet", "setxp", "copyvet", "addvet", "removevet")


def _is_path(p):
    return os.path.exists(p) or ("," in p and all(os.path.exists(x) for x in p.split(",") if x))


def _normalise(a):
    """Fill a.level0 / a.dll / a.args / a.out the same way for every command.
    New form:  <cmd> [args] [--levels L] [--dll D]   (defaults from deadlock.json)
    Old form:  <cmd> <levels> <dll> [args]  or  <cmd> <dll> <levels> [args]  - leading paths are taken as levels / DLL."""
    pos, lv, dll = list(a.args), [], None
    while pos and _is_path(pos[0]):
        p_ = pos.pop(0)
        if dll is None and p_.lower().endswith(".dll") and os.path.isfile(p_): dll = p_
        else: lv.append(p_)
    a.dll = config.setting("dll", a.dll or dll)
    if a.levels: a.level0, a.args = config.setting("levels", a.levels), lv + pos
    elif lv: a.level0, a.args = lv[0], lv[1:] + pos
    else: a.level0, a.args = config.setting("levels"), pos
    a.out = config.out_dir("levels", a.out, create=False)
    if config.same_file(a.out, a.level0 if os.path.isdir(a.level0) else os.path.dirname(os.path.abspath(a.level0))):
        raise SystemExit("the output folder %s is the level folder itself - choose another output folder" % a.out)


def ship_table_file(a):
    """the level file read by list / show / vet: a.level0 itself, or the first file in the folder that has the ship table"""
    if os.path.isfile(a.level0): return a.level0
    S = Schema(a.dll)
    for f_ in files_of(a.level0):
        try: Db(a.dll, f_, S); return f_
        except (SystemExit, Exception): pass
    raise SystemExit("no level file with the ship table in %s" % a.level0)


def run(a, level0, out):
    db = Db(a.dll, level0) if level0 else None

    if a.cmd == "list":
        print("%-26s %-9s %5s %5s %5s %6s %6s  %-9s %s" % ("ship", "buildable", "cost", "time", "rush", "hull", "fleetpt", "faction", "class"))
        for s in db.ships:
            print("%-26s %-9s %5d %5d %5d %6g %6d   %-9s %s" % (s["name"], s["Buildable"], s["BuildCost"], s["BuildTime"], s["RushCost"], s["MaxHullPoints"], s["FleetPointValue"], db.faction.get(s["Faction"], s["Faction"]), db.sclass.get(s["ShipClass"], s["ShipClass"])))
    elif a.cmd == "vet":
        print("XP needed per rank:", {r["Rank"]: r["XPRequired"] for r in db.res["CrewVeterancyRankInfo"]})
        print("XP awards: survive battle %s, damage an opponent %s, confirmed kill %s, auto-resolve %s" % (db.res["surviveBattleXP"], db.res["damageAtLeastOneOpponentXP"], db.res["atLeastOneConfirmedKillXP"], db.res["autoResolveXP"]))
        for s in ([db.find(a.args[0])] if a.args else db.ships):
            print(s["name"]); print("\n".join(vet_lines(db, s)) or "   (none)")
    elif a.cmd == "show":
        s = db.find(a.args[0])
        for k, v in s.items():
            if k in ("_off", "AttachmentInfo", "CrewVeterancyRankEffects", "ArmamentInfoList"): continue
            print("%-30s %s" % (k, v))
        print("%-30s %s" % ("Advantages", [db.eff(x) for x in s["AdvantageList"]]))
        print("%-30s %s" % ("Penalties", [db.eff(x) for x in s["PenaltyList"]]))
        print("Veterancy:"); print("\n".join(vet_lines(db, s)))
    elif a.cmd == "set":
        s = db.find(a.args[0]); field, val = a.args[1], a.args[2]
        if field not in s["_off"]: raise SystemExit("no field %r; see: show" % field)
        off, code = s["_off"][field]
        if code == 0x0c: db.write_float(off, val)
        elif code == 0x08 or code == 0x11: db.write_int(off, val)
        elif code == 0x02: struct.pack_into("<B", db.buf, db.base + off, 1 if val.lower() in ("1", "true", "yes") else 0)
        else: raise SystemExit("field %r is not a simple number/bool" % field)
        print("%s.%s: %s -> %s" % (s["name"], field, s[field], val)); db.save(out)
    elif a.cmd == "setvet":
        s = db.find(a.args[0]); rank = int(a.args[1]); eff = a.args[2].lower(); val = a.args[3]
        for rk in s["CrewVeterancyRankEffects"]:
            if rk["Rank"] == rank:
                for e in rk["Effects"]:
                    if db.eff(e["Effect"]).lower() == eff:
                        db.write_float(e["_off"]["BonusAmount"][0], val)
                        print("%s rank %d %s: %g -> %s" % (s["name"], rank, db.eff(e["Effect"]), e["BonusAmount"], val)); db.save(out); return
                raise SystemExit("rank %d of %s has no %s effect (adding effects would change the file size; not supported)" % (rank, s["name"], a.args[2]))
        raise SystemExit("%s has no rank %d entry" % (s["name"], rank))
    elif a.cmd == "addvet":
        # addvet <ship> <rank> <Effect> <value>  - add an effect to a rank (creates the rank entry if the ship lacks it); an existing effect is overwritten
        if len(a.args) != 4: raise SystemExit('usage: shipdb addvet "Colonial Mercury" 2 FlakDamage 0.1')
        s = db.find(a.args[0]); rank = int(a.args[1]); name = a.args[2].lower(); val = float(a.args[3])
        if name not in db.effect_by_name: raise SystemExit("unknown effect %r. Effects: %s" % (a.args[2], ", ".join(db.effect.values())))
        eid = db.effect_by_name[name]
        valid = [r["Rank"] for r in db.res["CrewVeterancyRankInfo"]]
        if rank not in valid: raise SystemExit("rank %d does not exist in this game (ranks: %s)" % (rank, valid))
        ranks = [(r["Rank"], [[e["Effect"], e["BonusAmount"]] for e in r["Effects"]]) for r in s["CrewVeterancyRankEffects"]]
        r_ = next((x for x in ranks if x[0] == rank), None)
        if r_ is None:
            r_ = (rank, []); ranks.append(r_); ranks.sort(key=lambda x: x[0])
        old = next((e for e in r_[1] if e[0] == eid), None)
        if old: print("%s rank %d %s: %g -> %g (already present, overwritten)" % (s["name"], rank, db.eff(eid), old[1], val)); old[1] = val
        else: r_[1].append([eid, val]); print("%s rank %d: added %s %g" % (s["name"], rank, db.eff(eid), val))
        blob = struct.pack("<i", len(ranks))
        for rk, effs in ranks:
            blob += struct.pack("<ii", rk, len(effs)) + b"".join(struct.pack("<if", e_, v_) for e_, v_ in effs)
        ts, te = db.veterancy_span(s)
        mono = bytes(db.buf[db.base:db.base + db.size])
        db.buf = bytearray(resize_object(bytes(db.buf), db.pid, mono[:ts] + blob + mono[te:]))
        db.save(out)
        chk = Db(a.dll, out); got = chk.find(s["name"])
        have = {r["Rank"]: {chk.eff(e["Effect"]): round(e["BonusAmount"], 4) for e in r["Effects"]} for r in got["CrewVeterancyRankEffects"]}
        assert have[rank][db.eff(eid)] == round(val, 4), "verification failed"
        print("verified:", have)
    elif a.cmd == "removevet":
        # removevet <ship> <rank> <Effect|all>  - remove one effect from a rank (a rank left with no effects is removed too)
        if len(a.args) != 3: raise SystemExit('usage: shipdb removevet "Colonial Mercury" 2 FlakDamage   (or "all" to clear the rank)')
        s = db.find(a.args[0]); rank = int(a.args[1]); name = a.args[2].lower()
        ranks = [(r["Rank"], [[e["Effect"], e["BonusAmount"]] for e in r["Effects"]]) for r in s["CrewVeterancyRankEffects"]]
        r_ = next((x for x in ranks if x[0] == rank), None)
        if r_ is None: raise SystemExit("%s has no rank %d entry" % (s["name"], rank))
        if name == "all": print("%s rank %d: removed all %d effect(s)" % (s["name"], rank, len(r_[1]))); r_[1].clear()
        else:
            if name not in db.effect_by_name: raise SystemExit("unknown effect %r. Effects: %s" % (a.args[2], ", ".join(db.effect.values())))
            eid = db.effect_by_name[name]; hit = next((e for e in r_[1] if e[0] == eid), None)
            if hit is None: raise SystemExit("rank %d of %s has no %s effect (it has: %s)" % (rank, s["name"], a.args[2], ", ".join(db.eff(e[0]) for e in r_[1]) or "none"))
            r_[1].remove(hit); print("%s rank %d: removed %s (was %g)" % (s["name"], rank, db.eff(eid), hit[1]))
        ranks = [x for x in ranks if x[1]]
        blob = struct.pack("<i", len(ranks))
        for rk, effs in ranks:
            blob += struct.pack("<ii", rk, len(effs)) + b"".join(struct.pack("<if", e_, v_) for e_, v_ in effs)
        ts, te = db.veterancy_span(s)
        mono = bytes(db.buf[db.base:db.base + db.size])
        db.buf = bytearray(resize_object(bytes(db.buf), db.pid, mono[:ts] + blob + mono[te:]))
        db.save(out)
        chk = Db(a.dll, out); got = chk.find(s["name"])
        print("verified:", {r["Rank"]: {chk.eff(e["Effect"]): round(e["BonusAmount"], 4) for e in r["Effects"]} for r in got["CrewVeterancyRankEffects"]} or "no veterancy left")
    elif a.cmd == "copyvet":
        tgt, src = db.find(a.args[0]), db.find(a.args[1])
        delta, shift = db.copy_vet(tgt, src)
        print("%s now uses the veterancy ranks of %s (database grew by %d bytes, later objects shifted by %d)" % (tgt["name"], src["name"], delta, shift))
        db.save(out)
        chk = Db(a.dll, out)                       # re-parse the result to prove it is still valid
        print("verified: re-read OK, %d ships" % len(chk.ships)); print("\n".join(vet_lines(chk, chk.find(tgt["name"]))))
    elif a.cmd in ("fleeticons", "fleeticon"):
        # args: <scene files / folders ...> ["Target ship[,Other target]" "Source ship"]   (ship names are read from the scene files themselves)
        paths = []
        rest = list(a.args)
        while rest and os.path.exists(rest[0]): paths.append(rest.pop(0))
        if level0 and os.path.exists(level0) and not paths: paths.append(level0)
        files = []
        for pth in paths:
            if os.path.isdir(pth): files += [os.path.join(r_, f_) for r_, _d, fs in os.walk(pth) for f_ in sorted(fs)]
            else: files.append(pth)
        factories = ["FleetModelFactory", "ProjectionFactory"] if a.factory.lower() == "all" else [a.factory]
        schema = Schema(a.dll)
        if a.cmd == "fleeticon":
            if len(rest) != 2: raise SystemExit('usage: shipdb fleeticon "Target ship[,Other target]" "Source ship"')
            names_t = [x.strip() for x in rest[0].split(",")]; name_s = rest[1]
            outdir = a.out; os.makedirs(outdir, exist_ok=True)
        patched = []; skipped = []
        for f_orig in files:
            f_ = chain_source(a, f_orig, outdir) if a.cmd == "fleeticon" else f_orig
            try: fdb = Db(a.dll, f_, schema)                 # this scene's own ship database
            except (SystemExit, Exception): skipped.append(os.path.basename(f_)); continue
            ids = {sh["id"]: sh["name"] for sh in fdb.ships}
            buf = None
            for fac_name in factories:
                try: buf_, base, fac = load_factory(fdb, f_ if buf is None else os.path.join(outdir, os.path.basename(f_)), fac_name)
                except LookupError: continue
                if a.cmd == "fleeticons":
                    print("\n%s in %s: %d entries" % (fac_name, f_, len(fac["objects"])))
                    for e in fac["objects"]:
                        print("  id %-4s %-24s ship: %-26s prefab file %s path %s" % (e["id"], e["name"], ids.get(e["id"], "?"), e["prefab"][1][0], e["prefab"][1][1]))
                    buf = buf_; continue
                src = fdb.find(name_s); by = {e["id"]: e for e in fac["objects"]}
                if src["id"] not in by: print("[skip] %s: %s has no %s entry" % (f_, src["name"], fac_name)); continue
                for tn in names_t:
                    tgt = fdb.find(tn)
                    if tgt["id"] not in by: print("[skip] %s: %s has no %s entry (adding one needs a resize; not supported)" % (f_, tgt["name"], fac_name)); continue
                    pos = base + by[tgt["id"]]["_off"]["prefab"][0]
                    old = struct.unpack_from("<iq", buf_, pos)
                    struct.pack_into("<iq", buf_, pos, *by[src["id"]]["prefab"][1])
                    print("[ok]   %s  %-18s %-24s prefab %s -> %s" % (os.path.basename(f_), fac_name, tgt["name"], old, by[src["id"]]["prefab"][1]))
                open(os.path.join(outdir, os.path.basename(f_)), "wb").write(buf_); buf = buf_
            if buf is not None: patched.append(os.path.basename(f_))
        if a.cmd == "fleeticon":
            print("\npatched %d file(s): %s" % (len(patched), ", ".join(patched) or "none"))
            print("not a war-room scene (left alone): %s" % (", ".join(skipped + [x for x in map(os.path.basename, files) if x not in patched and x not in skipped]) or "none"))
            print("Patched copies are in %s/ (same file names). Back up the originals, then copy them over." % outdir)
    elif a.cmd == "setxp":
        rank, xp = int(a.args[0]), int(a.args[1])
        for r in db.res["CrewVeterancyRankInfo"]:
            if r["Rank"] == rank:
                db.write_int(r["_off"]["XPRequired"][0], xp); print("rank %d XPRequired: %d -> %d" % (rank, r["XPRequired"], xp)); db.save(out); return
        raise SystemExit("no rank %d" % rank)


if __name__ == "__main__":
    main()
