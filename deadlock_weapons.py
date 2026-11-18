#!/usr/bin/env python3
"""
deadlock_weapons.py - edit a Deadlock ship bundle (built around the Colonial Mercury).

What it does (all optional, combine as needed; run with -h for every option and examples):
    turrets      clone / resize weapon mounts, placed on free hull space with left-right symmetry (--clone, --layout free, --scale, --clone-scale)
    slots        extra munition slots and squadron slots, with launch bays, build-screen icons and optional squadron submenus
    systems      hull-resin regen, armour regen, Munition Factory, squadron revive
    abilities    micro-FTL jump, predictive augury, stealth (donors: Argos, guardian basestar, Orion), optionally grouped in an Abilities submenu

Workflow
    1. python deadlock_bundle_xor.py decrypt <bundle>                      (writes decrypted/<bundle>.dec; this tool needs the decrypted file)
    2. python deadlock_weapons.py <input.dec> <output> [options] --encrypt   (always starts from the .dec, so give every option in ONE command)
    3. rename the output to the original bundle name, back up and replace the game's file.
With no options the output is an unmodified copy. DLL patches (armour regen, squadron revive, commander ranks) are in deadlock_dll.py;
slot counts and other ship data in the level files are edited with deadlock_shipdb.py and must match the numbers used here
(the battle UI shows min(prefab buttons, level-data slots)).
"""
"""Unity serialized file (v17, with type tree) reader/writer + UnityFS bundle container. Dependency free."""
import struct

_COMMON = ["AABB", "AnimationClip", "AnimationCurve", "AnimationState", "Array", "Base", "BitField", "bitset", "bool", "char", "ColorRGBA", "Component", "data", "deque", "double", "dynamic_array", "FastPropertyName", "first", "float", "Font", "GameObject", "Generic Mono", "GradientNEW", "GUID", "GUIStyle", "int", "list", "long long", "map", "Matrix4x4f", "MdFour", "MonoBehaviour", "MonoScript", "m_ByteSize", "m_Curve", "m_EditorClassIdentifier", "m_EditorHideFlags", "m_Enabled", "m_ExtensionPtr", "m_GameObject", "m_Index", "m_IsArray", "m_IsStatic", "m_MetaFlag", "m_Name", "m_ObjectHideFlags", "m_PrefabInternal", "m_PrefabParentObject", "m_Script", "m_StaticEditorFlags", "m_Type", "m_Version", "Object", "pair", "PPtr<Component>", "PPtr<GameObject>", "PPtr<Material>", "PPtr<MonoBehaviour>", "PPtr<MonoScript>", "PPtr<Object>", "PPtr<Prefab>", "PPtr<Sprite>", "PPtr<TextAsset>", "PPtr<Texture>", "PPtr<Texture2D>", "PPtr<Transform>", "Prefab", "Quaternionf", "Rectf", "RectInt", "RectOffset", "second", "set", "short", "size", "SInt16", "SInt32", "SInt64", "SInt8", "staticvector", "string", "TextAsset", "TextMesh", "Texture", "Texture2D", "Transform", "TypelessData", "UInt16", "UInt32", "UInt64", "UInt8", "unsigned int", "unsigned long long", "unsigned short", "vector", "Vector2f", "Vector3f", "Vector4f", "m_ScriptingClassIdentifier", "Gradient", "Type*", "int2_storage", "int3_storage", "BoundsInt", "m_CorrespondingSourceObject", "m_PrefabInstance", "m_PrefabAsset", "FileSize", "Hash128"]
COMMON = {}
_o = 0
for _s in _COMMON:
    COMMON[_o] = _s; _o += len(_s) + 1


def al(n, a=4): return (n + a - 1) & ~(a - 1)


class Node:
    __slots__ = ("level", "type", "name", "size", "index", "flags", "meta", "tflags", "ver")


class SType:
    def __init__(self): self.classid = 0; self.stripped = 0; self.script_idx = -1; self.script_hash = b""; self.hash = b""; self.nodes = []; self.raw = b""


class Obj:
    def __init__(self, pid, off, size, tidx, classid): self.pid, self.off, self.size, self.tidx, self.classid = pid, off, size, tidx, classid


class SerFile:
    def __init__(self, data):
        d = self.d = bytes(data)
        self.meta_size, self.file_size, self.version, self.data_offset = struct.unpack(">IIII", d[:16])
        p = 16
        self.endian = d[p]; self.reserved = d[p + 1:p + 4]; p += 4
        j = d.index(b"\0", p); self.unity = d[p:j].decode(); p = j + 1
        self.platform = struct.unpack_from("<i", d, p)[0]; p += 4
        self.has_tt = d[p]; p += 1
        assert self.has_tt, "this file has no type tree"
        nt = struct.unpack_from("<i", d, p)[0]; p += 4
        self.types = []
        for _ in range(nt):
            t = SType(); start = p
            t.classid = struct.unpack_from("<i", d, p)[0]; p += 4
            t.stripped = d[p]; p += 1
            t.script_idx = struct.unpack_from("<h", d, p)[0]; p += 2
            if t.classid == 114: t.script_hash = d[p:p + 16]; p += 16
            t.hash = d[p:p + 16]; p += 16
            nn, sb = struct.unpack_from("<ii", d, p); p += 8
            nb = d[p:p + nn * 24]; p += nn * 24
            sbuf = d[p:p + sb]; p += sb
            def S(off):
                if off & 0x80000000: return COMMON.get(off & 0x7fffffff, "common%d" % (off & 0x7fffffff))
                e = sbuf.index(b"\0", off); return sbuf[off:e].decode()
            for i in range(nn):
                ver, lvl, tfl, to, no, bs, idx, mf = struct.unpack_from("<HBBIIiiI", nb, i * 24)
                n = Node(); n.ver = ver; n.level = lvl; n.tflags = tfl; n.type = S(to); n.name = S(no); n.size = bs; n.index = idx; n.meta = mf
                t.nodes.append(n)
            t.raw = d[start:p]
            self.types.append(t)
        self.p_objcount = p
        n = struct.unpack_from("<i", d, p)[0]; p += 4
        self.objs = []
        for _ in range(n):
            p = al(p, 4)
            pid, off, size, ti = struct.unpack_from("<qIIi", d, p); p += 20
            self.objs.append(Obj(pid, off, size, ti, self.types[ti].classid))
        self.p_after_objs = p
        self.meta_end_guess = p
        self.suffix = d[p:self.data_offset]            # script types, externals, user info, padding
        self.prefix = d[16:self.p_objcount]            # endian .. type trees
        self.by_pid = {o.pid: o for o in self.objs}

    def raw(self, o): return self.d[self.data_offset + o.off:self.data_offset + o.off + o.size]

    # ----- value (de)serialisation using the type tree -----
    def read(self, o):
        nodes = self.types[o.tidx].nodes; raw = self.raw(o); pos = [0]
        v, _ = self._rd(nodes, 0, raw, pos)
        return v, pos[0]

    def _rd(self, nodes, i, b, pos):
        n = nodes[i]; t = n.type
        # children range
        j = i + 1
        while j < len(nodes) and nodes[j].level > n.level: j += 1
        kids = list(range(i + 1, j))
        direct = [k for k in kids if nodes[k].level == n.level + 1]
        if t in ("SInt8", "UInt8", "char", "bool", "SInt16", "short", "UInt16", "unsigned short", "SInt32", "int", "UInt32", "unsigned int", "SInt64", "long long", "UInt64", "unsigned long long", "float", "double"):
            fmt = {"SInt8": "<b", "UInt8": "<B", "char": "<B", "bool": "<?", "SInt16": "<h", "short": "<h", "UInt16": "<H", "unsigned short": "<H", "SInt32": "<i", "int": "<i", "UInt32": "<I", "unsigned int": "<I", "SInt64": "<q", "long long": "<q", "UInt64": "<Q", "unsigned long long": "<Q", "float": "<f", "double": "<d"}[t]
            v = struct.unpack_from(fmt, b, pos[0])[0]; pos[0] += struct.calcsize(fmt)
        elif t == "string":
            sz = struct.unpack_from("<i", b, pos[0])[0]; pos[0] += 4
            v = bytes(b[pos[0]:pos[0] + sz]).decode("utf-8", "replace"); pos[0] += sz
            if i + 1 < len(nodes) and nodes[i + 1].type == "Array" and nodes[i + 1].meta & 0x4000: pos[0] = al(pos[0])
        elif t == "TypelessData":
            sz = struct.unpack_from("<i", b, pos[0])[0]; pos[0] += 4; v = bytes(b[pos[0]:pos[0] + sz]); pos[0] += sz
            if i + 1 < len(nodes) and nodes[i + 1].type == "Array" and nodes[i + 1].meta & 0x4000: pos[0] = al(pos[0])
        elif direct and nodes[direct[0]].type == "Array" and len(direct) == 1 or t == "Array":
            an = i if t == "Array" else direct[0]
            sz = struct.unpack_from("<i", b, pos[0])[0]; pos[0] += 4
            dnode = an + 2                      # nodes[an+1] = size, nodes[an+2] = data
            v = []
            for _ in range(sz):
                x, _e = self._rd(nodes, dnode, b, pos); v.append(x)
            if nodes[an].meta & 0x4000: pos[0] = al(pos[0])
        else:
            v = {}
            for k in direct:
                v[nodes[k].name] = self._rd(nodes, k, b, pos)[0]
        if n.meta & 0x4000: pos[0] = al(pos[0])
        return v, j

    def write(self, o_or_idx, val):
        tidx = o_or_idx.tidx if isinstance(o_or_idx, Obj) else o_or_idx
        out = bytearray(); self._wr(self.types[tidx].nodes, 0, val, out); return bytes(out)

    def _wr(self, nodes, i, v, out):
        n = nodes[i]; t = n.type
        j = i + 1
        while j < len(nodes) and nodes[j].level > n.level: j += 1
        direct = [k for k in range(i + 1, j) if nodes[k].level == n.level + 1]
        if t in ("SInt8", "UInt8", "char", "bool", "SInt16", "short", "UInt16", "unsigned short", "SInt32", "int", "UInt32", "unsigned int", "SInt64", "long long", "UInt64", "unsigned long long", "float", "double"):
            fmt = {"SInt8": "<b", "UInt8": "<B", "char": "<B", "bool": "<?", "SInt16": "<h", "short": "<h", "UInt16": "<H", "unsigned short": "<H", "SInt32": "<i", "int": "<i", "UInt32": "<I", "unsigned int": "<I", "SInt64": "<q", "long long": "<q", "UInt64": "<Q", "unsigned long long": "<Q", "float": "<f", "double": "<d"}[t]
            out += struct.pack(fmt, v)
        elif t == "string":
            bb = v.encode("utf-8"); out += struct.pack("<i", len(bb)) + bb
            if i + 1 < len(nodes) and nodes[i + 1].type == "Array" and nodes[i + 1].meta & 0x4000: out += b"\0" * (al(len(out)) - len(out))
        elif t == "TypelessData":
            out += struct.pack("<i", len(v)) + v
            if i + 1 < len(nodes) and nodes[i + 1].type == "Array" and nodes[i + 1].meta & 0x4000: out += b"\0" * (al(len(out)) - len(out))
        elif direct and nodes[direct[0]].type == "Array" and len(direct) == 1 or t == "Array":
            an = i if t == "Array" else direct[0]
            out += struct.pack("<i", len(v))
            for x in v: self._wr(nodes, an + 2, x, out)
            if nodes[an].meta & 0x4000: out += b"\0" * (al(len(out)) - len(out))
        else:
            for k in direct: self._wr(nodes, k, v[nodes[k].name], out)
        if n.meta & 0x4000: out += b"\0" * (al(len(out)) - len(out))

    # ----- rebuild the file with replaced / appended object data -----
    def rebuild(self, replaced=None, appended=None):
        """replaced: {pid: bytes}; appended: [(pid, type_index, bytes)]. Returns new file bytes."""
        replaced = replaced or {}; appended = appended or []
        blobs = []
        for o in self.objs:
            blobs.append((o.pid, o.tidx, replaced.get(o.pid, self.raw(o))))
        for pid, ti, b in appended: blobs.append((pid, ti, b))
        blobs.sort(key=lambda t: t[0])   # keep the object table sorted by path id (Unity may binary-search it)
        data = bytearray(); table = []
        for pid, ti, b in blobs:
            data += b"\0" * (al(len(data), 8) - len(data))
            table.append((pid, len(data), len(b), ti)); data += b
        # metadata
        obj_tab = bytearray(struct.pack("<i", len(table)))
        for pid, off, size, ti in table:
            obj_tab += b"\0" * (al(len(obj_tab) + 16 + len(self.prefix), 4) - (len(obj_tab) + 16 + len(self.prefix)))
            obj_tab += struct.pack("<qIIi", pid, off, size, ti)
        meta = bytes(self.prefix) + bytes(obj_tab) + self.suffix[:(getattr(self, 'suffix_len', None) or self._suffix_len())]
        # data_offset: keep original alignment rule (align to 16)
        meta_end = 16 + len(meta)
        data_offset = al(meta_end, 16)
        meta_size = meta_end - 20
        head = struct.pack(">IIII", meta_size, 0, self.version, data_offset)
        blob = head + meta + b"\0" * (data_offset - meta_end) + bytes(data)
        blob = blob[:4] + b"" + blob[4:]
        blob = bytearray(blob); struct.pack_into(">I", blob, 4, len(blob))
        return bytes(blob)

    def _suffix_len(self):
        """length of the post-object metadata (script types, externals, userinfo), using absolute alignment"""
        d = self.d; base = self.p_after_objs; p = base
        n = struct.unpack_from("<i", d, p)[0]; p += 4                      # script types
        for _ in range(n):
            p += 4; p = al(p, 4); p += 8
        n = struct.unpack_from("<i", d, p)[0]; p += 4                      # externals
        for _ in range(n):
            p = d.index(b"\0", p) + 1; p += 16 + 4; p = d.index(b"\0", p) + 1
        p = d.index(b"\0", p) + 1                                          # user information
        return p - base


import lzma as _lzma
def _lz4_block(src, usize):
    out = bytearray(); i = 0; n = len(src)
    while i < n:
        t = src[i]; i += 1
        l = t >> 4
        if l == 15:
            while True:
                b = src[i]; i += 1; l += b
                if b != 255: break
        out += src[i:i+l]; i += l
        if i >= n: break
        off = src[i] | (src[i+1] << 8); i += 2
        m = t & 15
        if m == 15:
            while True:
                b = src[i]; i += 1; m += b
                if b != 255: break
        m += 4; s = len(out) - off
        if off >= m: out += out[s:s+m]
        else:
            for k in range(m): out.append(out[s+k])
    return bytes(out)


def _decompress(raw, usize, mode):
    if mode == 0: return raw
    if mode == 1: return _lzma.LZMADecompressor(format=_lzma.FORMAT_ALONE).decompress(raw[:5] + struct.pack("<Q", usize) + raw[5:])
    if mode in (2, 3): return _lz4_block(raw, usize)
    raise AssertionError("unknown bundle compression %d" % mode)


def read_bundle(path):
    d = open(path, "rb").read()
    o = d.index(b"\0") + 1; ver = struct.unpack(">I", d[o:o + 4])[0]; o += 4
    j = d.index(b"\0", o); pl = d[o:j].decode(); o = j + 1
    j = d.index(b"\0", o); rev = d[o:j].decode(); o = j + 1
    size, cs, us, fl = struct.unpack(">qIII", d[o:o + 20]); hdr_end = o + 20
    comp = fl & 0x3f
    assert ver == 6 and not fl & 0x80, "unsupported UnityFS layout (expected a v6 bundle with the block list at the start)"
    bi = _decompress(d[hdr_end:hdr_end + cs], us, comp)          # block list (LZMA / LZ4 / none)
    p = 16; nb = struct.unpack(">i", bi[p:p + 4])[0]; p += 4
    blocks = []
    for _ in range(nb): blocks.append(struct.unpack(">IIH", bi[p:p + 10])); p += 10
    nn = struct.unpack(">i", bi[p:p + 4])[0]; p += 4
    nodes = []
    for _ in range(nn):
        off, sz, f = struct.unpack(">qqI", bi[p:p + 20]); p += 20
        e = bi.index(b"\0", p); nodes.append((off, sz, f, bi[p:e].decode())); p = e + 1
    data = b""; q = hdr_end + cs
    for usz_, csz_, f_ in blocks:                                # data blocks (LZMA / LZ4 / none) -> one uncompressed buffer
        data += _decompress(d[q:q + csz_], usz_, f_ & 0x3f); q += csz_
    return dict(version=ver, player=pl, revision=rev, flags=fl, hash=bi[:16], nodes=nodes, data=data)


def write_bundle(path, b, node_blobs):
    """node_blobs: list of (flags, name, bytes) in order -> uncompressed single block bundle."""
    data = b""; nodes = []
    for f, name, blob in node_blobs:
        nodes.append((len(data), len(blob), f, name)); data += blob
    bi = bytearray(b["hash"]) + struct.pack(">i", 1) + struct.pack(">IIH", len(data), len(data), 0x40) + struct.pack(">i", len(nodes))
    for off, sz, f, name in nodes: bi += struct.pack(">qqI", off, sz, f) + name.encode() + b"\0"
    hdr = b"UnityFS\0" + struct.pack(">I", b["version"]) + b["player"].encode() + b"\0" + b["revision"].encode() + b"\0"
    total = len(hdr) + 8 + 4 + 4 + 4 + len(bi) + len(data)
    out = hdr + struct.pack(">qIII", total, len(bi), len(bi), 0x40) + bytes(bi) + data
    assert len(out) == total, (len(out), total)
    open(path, "wb").write(out)


# ======================================================================================
#  Ship bundle editor: turrets, slots, ship systems, abilities
# ======================================================================================
import argparse, os, copy, sys, re

def xor_bytes(data, key=bytes.fromhex("0f0f070d0507")):
    n = len(data); ks = (key * (n // len(key) + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(n, "big")


def _noop(): pass

# --- Regenerative Hull Resin module, extracted from the Cylon modern basestar bundle ---
RESIN_TYPE_RAW = bytes.fromhex("720000000035001d1b0e80f34cee85375f2d50c4d456f01d90848de0815399900d3c9feb097de30d0000001e000000010000000701008037000080ffffffff00000000008000000100010034020080760100800c000000010000004100000001000200de00008000000000040000000200000041000000010002002e0300800900000008000000030000004100000001000100a00300805d0100800100000004000000014100000100010068020080ea0100800c000000050000000000000001000200de00008000000000040000000600000001008000010002002e030080090000000800000007000000010080000100010048030080ab010080ffffffff0800000001800800010002013100008031000080ffffffff090000000140080001000300de0000801b030080040000000a0000000100080001000300510000806a000080010000000b0000000100080001000100a100008012000000040000000c000000000000006d5f46696c654944006d5f5061746849440068756c6c5065725469636b00")      # serialized-type record (MonoBehaviour, script hash + type tree)
RESIN_MB_RAW = bytes.fromhex("0000000076ec8f0efc3f7b780100000000000000444537a79317e26100000000cdcc4c3e")    # MonoBehaviour bytes: GO pptr@4, script pptr@20, name len@28, hullPerTick@32
RESIN_MS_RAW = bytes.fromhex("1b000000526567656e6572617469766548756c6c526573696e4d6f64756c6500000000001d90848de0815399900d3c9feb097de31b000000526567656e6572617469766548756c6c526573696e4d6f64756c65000000000013000000417373656d626c792d4353686172702e646c6c00")    # MonoScript object bytes

# --- Munition Factory Module (passive munition resupply), extracted from the Cylon Argos bundle ---
MFM_TYPE_RAW = bytes.fromhex("72000000001c0066e8715df7eaedaf215b36c30773d3be71bb6a6b6c8f052f948db64c7dd3ca4f0c00000012000000010000000701008037000080ffffffff00000000008000000100010034020080760100800c000000010000004100000001000200de00008000000000040000000200000041000000010002002e0300800900000008000000030000004100000001000100a00300805d0100800100000004000000014100000100010068020080ea0100800c000000050000000000000001000200de00008000000000040000000600000001008000010002002e030080090000000800000007000000010080000100010048030080ab010080ffffffff0800000001800800010002013100008031000080ffffffff090000000140080001000300de0000801b030080040000000a0000000100080001000300510000806a000080010000000b000000010008006d5f46696c654944006d5f50617468494400")
MFM_MB_RAW = bytes.fromhex("000000009e886f51bc54da9b0100000000000000d10e3251425e1b0600000000")    # MonoBehaviour bytes: GO pptr@4, script pptr@20, name len@28 (the module has no fields of its own)
MFM_MS_RAW = bytes.fromhex("150000004d756e6974696f6e466163746f72794d6f64756c650000000000000071bb6a6b6c8f052f948db64c7dd3ca4f150000004d756e6974696f6e466163746f72794d6f64756c650000000000000013000000417373656d626c792d4353686172702e646c6c00")    # MonoScript object bytes


# --- Mini-jump (micro-FTL jump), extracted from the Cylon Argos bundle ---
MJA_TYPE_RAW = bytes.fromhex("720000000028003eadf745b1324df5b4926cc95dcb8ceb9c4e7666f707db6eaf403f0385d2c9b21300000085000000010000000701008037000080ffffffff00000000008000000100010034020080760100800c000000010000004100000001000200de00008000000000040000000200000041000000010002002e0300800900000008000000030000004100000001000100a00300805d0100800100000004000000014100000100010068020080ea0100800c000000050000000000000001000200de00008000000000040000000600000001008000010002002e030080090000000800000007000000010080000100010048030080ab010080ffffffff0800000001800800010002013100008031000080ffffffff090000000140080001000300de0000801b030080040000000a0000000100080001000300510000806a000080010000000b0000000100080001000100de00008012000000040000000c0000000000000001000100de0000801c000000040000000d0000000000000001000100a100008029000000040000000e0000000000000001000100a10000803d000000040000000f0000000000000001000100de0000804f00000004000000100000000000000001000100de0000805d00000004000000110000000000000001000100a00300806f0000000100000012000000004100006d5f46696c654944006d5f506174684944006a756d7044656c6179006a756d704475726174696f6e006a756d7052616e6765486f72697a6f6e74616c006a756d7052616e6765566572746963616c00636f6f6c646f776e5475726e730065787472614d697373696c6544656c61790070726576656e744d696e694a756d70416374696f6e00")     # MiniJumpAgent serialized type
MJA_MS_RAW = bytes.fromhex("0d0000004d696e694a756d704167656e74000000000000009c4e7666f707db6eaf403f0385d2c9b20d0000004d696e694a756d704167656e740000000000000013000000417373656d626c792d4353686172702e646c6c00")      # MiniJumpAgent MonoScript
MJA_MB_RAW = bytes.fromhex("000000009e886f51bc54da9b010000000000000071aca08a47b3572f0000000032000000030000000000484300002041050000000500000000000000")      # MiniJumpAgent bytes: jumpDelay int@32, jumpDuration int@36, rangeH float@40, rangeV float@44, cooldownTurns int@48, extraMissileDelay int@52, preventMiniJumpAction bool@56
MJX_TYPE_RAW = bytes.fromhex("72000000000100431278a94b7741fd5622db5734a4dae2cd8245c39c0dc56c73a2e95c6a690efd0e00000030000000010000000701008037000080ffffffff00000000008000000100010034020080760100800c000000010000004100000001000200de00008000000000040000000200000041000000010002002e0300800900000008000000030000004100000001000100a00300805d0100800100000004000000014100000100010068020080ea0100800c000000050000000000000001000200de00008000000000040000000600000001008000010002002e030080090000000800000007000000010080000100010048030080ab010080ffffffff0800000001800800010002013100008031000080ffffffff090000000140080001000300de0000801b030080040000000a0000000100080001000300510000806a000080010000000b0000000100080001000100de00008012000000040000000c0000000000000001000100a003008025000000010000000d000000004100006d5f46696c654944006d5f5061746849440072616469616c4d656e75506f736974696f6e0068696465496e4d656e7500")     # MiniJumpAction serialized type
MJX_MS_RAW = bytes.fromhex("0e0000004d696e694a756d70416374696f6e000000000000cd8245c39c0dc56c73a2e95c6a690efd0e0000004d696e694a756d70416374696f6e00000000000013000000417373656d626c792d4353686172702e646c6c00")      # MiniJumpAction MonoScript
MJX_MB_RAW = bytes.fromhex("000000009c6ab4a76a4412a301000000000000000d2693f49f8d958c000000000700000000000000")      # MiniJumpAction bytes: radialMenuPosition int@32, hideInMenu bool@36


# --- Predictive Augury (Cylon guardian basestar ability): module on the ship + button in the Actions menu ---
PAM_TYPE_RAW = bytes.fromhex("72000000000b004ea53f8056c1bb7390e1a7f88b34f657baef6d209a601dd47d7cdefb4a36283b0d00000020000000010000000701008037000080ffffffff00000000008000000100010034020080760100800c000000010000004100000001000200de00008000000000040000000200000041000000010002002e0300800900000008000000030000004100000001000100a00300805d0100800100000004000000014100000100010068020080ea0100800c000000050000000000000001000200de00008000000000040000000600000001008000010002002e030080090000000800000007000000010080000100010048030080ab010080ffffffff0800000001800800010002013100008031000080ffffffff090000000140080001000300de0000801b030080040000000a0000000100080001000300510000806a000080010000000b0000000100080001000100de00008012000000040000000c000000000000006d5f46696c654944006d5f506174684944006d6178416d6d756e6974696f6e00")     # PredictiveAuguryModule serialized type
PAM_MS_RAW = bytes.fromhex("16000000507265646963746976654175677572794d6f64756c65000000000000baef6d209a601dd47d7cdefb4a36283b16000000507265646963746976654175677572794d6f64756c6500000000000013000000417373656d626c792d4353686172702e646c6c00")      # PredictiveAuguryModule MonoScript
PAM_MB_RAW = bytes.fromhex("000000009888822b985bf66c01000000000000009e1c8027a5c1aec50000000001000000")      # PredictiveAuguryModule bytes: maxAmmunition int@32
PAX_TYPE_RAW = bytes.fromhex("72000000002e0020d95e237857841c2e8ccd0c0c6915edcd8245c39c0dc56c73a2e95c6a690efd0e00000030000000010000000701008037000080ffffffff00000000008000000100010034020080760100800c000000010000004100000001000200de00008000000000040000000200000041000000010002002e0300800900000008000000030000004100000001000100a00300805d0100800100000004000000014100000100010068020080ea0100800c000000050000000000000001000200de00008000000000040000000600000001008000010002002e030080090000000800000007000000010080000100010048030080ab010080ffffffff0800000001800800010002013100008031000080ffffffff090000000140080001000300de0000801b030080040000000a0000000100080001000300510000806a000080010000000b0000000100080001000100de00008012000000040000000c0000000000000001000100a003008025000000010000000d000000004100006d5f46696c654944006d5f5061746849440072616469616c4d656e75506f736974696f6e0068696465496e4d656e7500")     # PredictiveAuguryAction serialized type
PAX_MS_RAW = bytes.fromhex("1600000050726564696374697665417567757279416374696f6e000000000000cd8245c39c0dc56c73a2e95c6a690efd1600000050726564696374697665417567757279416374696f6e00000000000013000000417373656d626c792d4353686172702e646c6c00")      # PredictiveAuguryAction MonoScript
PAX_MB_RAW = bytes.fromhex("000000009604043e4f156e390100000000000000947bbb9e2d909741000000000700000000000000")      # PredictiveAuguryAction bytes: radialMenuPosition int@32, hideInMenu bool@36


def build_hull_query(F, RESS, val):
    """Return ys(x, z) -> sorted list of heights where the ship's hull mesh crosses the vertical line through (x, z), or None if no
    usable hull mesh was found. Pure Python (grid index over the triangles' x/z footprint)."""
    import struct as _st
    import re as _re
    # every mesh on a mesh filter except wrecks; the hull = the biggest one plus the other pieces of the same LOD level (e.g. body + wings)
    cand = []
    for o in F.objs:
        if o.classid != 33: continue
        mf = val(o.pid); mp = mf["m_Mesh"]["m_PathID"]
        if not mp or mp not in F.by_pid: continue
        m = val(mp)
        if "wreck" in m["m_Name"].lower() or not m["m_SubMeshes"]: continue
        cand.append((m["m_VertexData"]["m_VertexCount"], m, mf["m_GameObject"]["m_PathID"]))
    if not cand: return None
    main = max(cand, key=lambda c: c[0]); lod = _re.search(r"lod\d+", main[1]["m_Name"].lower())
    parts = [c for c in cand if lod and lod.group(0) in c[1]["m_Name"].lower() and "col" not in c[1]["m_Name"].lower()] or [main]
    def world(go):
        tp = next((x["component"]["m_PathID"] for x in val(go)["m_Component"] if x["component"]["m_PathID"] in F.by_pid and F.by_pid[x["component"]["m_PathID"]].classid == 4), 0)
        chain = []
        while tp:
            t = val(tp); chain.append(t); tp = t["m_Father"]["m_PathID"]
        M = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]
        for t in reversed(chain):
            q = t["m_LocalRotation"]; x, y, z, w = q["x"], q["y"], q["z"], q["w"]; sc = t["m_LocalScale"]; ps = t["m_LocalPosition"]
            R = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
            L = [[R[i][0] * sc["x"], R[i][1] * sc["y"], R[i][2] * sc["z"], (ps["x"], ps["y"], ps["z"])[i]] for i in range(3)]
            M = [[sum(M[i][k] * L[k][j] for k in range(3)) + (M[i][3] if j == 3 else 0) for j in range(4)] for i in range(3)]
        return M
    def inv(M):
        a, b, c, d, e, f, g, h, i = (M[0][0], M[0][1], M[0][2], M[1][0], M[1][1], M[1][2], M[2][0], M[2][1], M[2][2])
        det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
        if abs(det) < 1e-12: return None
        R = [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det], [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det], [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]
        return [R[r] + [-(R[r][0] * M[0][3] + R[r][1] * M[1][3] + R[r][2] * M[2][3])] for r in range(3)]
    ref = inv(world(main[2]))
    T = []; V_all = []
    for n, m, go in parts:
        sd = m.get("m_StreamData") or {}
        if sd.get("size"): raw = bytes(RESS[sd["offset"]:sd["offset"] + sd["size"]])
        else: raw = bytes(m["m_VertexData"]["m_DataSize"])
        ch = m["m_VertexData"]["m_Channels"][0]
        if not n or not raw or len(raw) % n or ch["format"] != 0 or ch["dimension"] < 3 or ch["stream"] != 0: continue
        st = len(raw) // n; off = ch["offset"]
        rel = None
        if ref is not None:
            W = world(go); rel = [[sum(ref[i][k] * W[k][j] for k in range(3)) + (ref[i][3] if j == 3 else 0) for j in range(4)] for i in range(3)]
        V = [_st.unpack_from("<3f", raw, i * st + off) for i in range(n)]
        if rel is not None: V = [tuple(rel[r][0] * v[0] + rel[r][1] * v[1] + rel[r][2] * v[2] + rel[r][3] for r in range(3)) for v in V]
        V_all += V
        ib = bytes(m["m_IndexBuffer"]); isz = 2 if m["m_IndexFormat"] == 0 else 4; idx = _st.unpack("<%d%s" % (len(ib) // isz, "H" if isz == 2 else "I"), ib)
        for sm in m["m_SubMeshes"]:
            if sm["topology"] != 0: continue
            f = sm["firstByte"] // isz
            for k in range(f, f + sm["indexCount"] - 2, 3): T.append((V[idx[k]], V[idx[k + 1]], V[idx[k + 2]]))
    if not T: return None
    V = V_all
    CELL = 2.0; grid = {}
    for t in T:
        x0 = min(a[0] for a in t); x1 = max(a[0] for a in t); z0 = min(a[2] for a in t); z1 = max(a[2] for a in t)
        for i in range(int(x0 // CELL), int(x1 // CELL) + 1):
            for j in range(int(z0 // CELL), int(z1 // CELL) + 1): grid.setdefault((i, j), []).append(t)
    def ys(x, z):
        out = []
        for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in grid.get((int(x // CELL), int(z // CELL)), ()):
            d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
            if abs(d) < 1e-12: continue
            l1 = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d; l2 = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d; l3 = 1 - l1 - l2
            if l1 >= -1e-9 and l2 >= -1e-9 and l3 >= -1e-9: out.append(l1 * ay + l2 * by + l3 * cy)
        return sorted(out)
    ys.xlo = min(v[0] for v in V); ys.xhi = max(v[0] for v in V); ys.zlo = min(v[2] for v in V); ys.zhi = max(v[2] for v in V)
    return ys


def double_weapons(src, dst, offset=(1.5, 0.0, 0.0), copies=0, munition_slots=None, fighter_slots=None, utility_slots=None, bay_offset=(0.0, 3.0, 4.0), hull_regen=None, armour_regen=False, clones=(), list_mounts=False, clone_scale=None, scales=(), layout="offset", gap=1.5, mirror=True, munition_factory=False, squadron_revive=False, hull_follow=True, min_gap=None, mini_jump=None, augury=None, stealth=None, slot_parts="buttons,bays,icons", ability_submenu=False, stealth_mesh="ship", squadron_groups=None, icon_offset=(12.0, 0.0, -4.0)):
    B = read_bundle(src)
    (O1, S1, F1, N1), (O2, S2, F2, N2) = B["nodes"]
    CAB = B["data"][O1:O1 + S1]; RESS = B["data"][O2:O2 + S2]
    F = SerFile(CAB)
    cache = {}
    def goname_of(p): return val(p)["m_Name"]
    def val(pid):
        if pid not in cache: cache[pid] = F.read(F.by_pid[pid])[0]
        return cache[pid]
    mounts = [o for o in F.objs if o.classid == 114 and "WeaponType" in val(o.pid) and "Quadrant" in val(o.pid) and "InitialAngles" in val(o.pid)]
    print(f"found {len(mounts)} weapon mount points")
    QUADS = ["port", "starboard", "fore", "rear", "dorsal", "ventral"]
    def mount_info(o):
        x = val(o.pid); g = val(x["m_GameObject"]["m_PathID"])
        tp = next(val(y["component"]["m_PathID"]) for y in g["m_Component"] if F.by_pid[y["component"]["m_PathID"]].classid == 4)["m_LocalPosition"]
        q = x["Quadrant"]
        return g["m_Name"], (QUADS[q] if 0 <= q < len(QUADS) else str(q)), x["WeaponType"], tp
    def matches(sel, o):
        name, quad, wtype, _ = mount_info(o); sel = sel.lower()
        if sel == "all": return True
        if sel in ("sides", "broadside"): return quad in ("port", "starboard")
        if sel.startswith("type:"): return str(wtype) == sel[5:]
        if sel.startswith("name:"): return sel[5:] in name.lower()
        assert sel in QUADS, f"unknown selector '{sel}': use all, sides, {', '.join(QUADS)}, type:N or name:TEXT"
        return quad == sel
    if list_mounts:
        print(f"{'mount':<22}{'quadrant':<10}{'type':<6}x      y      z")
        for o in sorted(mounts, key=lambda o: (mount_info(o)[1], mount_info(o)[0])):
            n, q, t, p = mount_info(o); print(f"{n:<22}{q:<10}{t:<6}{p['x']:<7.1f}{p['y']:<7.1f}{p['z']:.1f}")
        by = {}
        for o in mounts: by.setdefault((mount_info(o)[1], mount_info(o)[2]), []).append(o)
        print("\ngroups (quadrant, weapon type): " + "; ".join(f"{q} type {t} x{len(l)}" for (q, t), l in sorted(by.items())))
        sys.exit(0)
    ncopy = {}
    for o in mounts:
        n = copies
        for sel, k in clones:
            if matches(sel, o): n = k
        ncopy[o.pid] = n
    assert mounts or not any(ncopy.values()), "no WeaponMountPoint components found - is this a ship bundle?"
    for sel, k in clones:
        assert any(matches(sel, o) for o in mounts), f"--clone {sel}: no weapon mount matches"
    if any(ncopy.values()):
        print(f"cloning {sum(1 for n in ncopy.values() if n)} of {len(mounts)} mounts, {sum(ncopy.values())} new mounts in total")
    # ---- where each clone goes -------------------------------------------------------------------------------------
    # layout "offset": every clone is moved by --offset (x mirrored for mounts on the port side so the result is left/right symmetric)
    # layout "mid"   : each clone slides along its row towards the next mount (up to --gap units), so clones sit BETWEEN the existing
    #                  turrets on the hull surface (position interpolated, so it follows the hull curve); port/starboard stay symmetric
    import math
    def clone_delta(o, c, n):
        return deltas[o.pid](c, n)
    deltas = {}
    P = {o.pid: tuple(mount_info(o)[3][k] for k in "xyz") for o in mounts}
    if layout == "mid":
        groups = {}
        for o in mounts:
            name, quad, wtype, _ = mount_info(o); x = P[o.pid][0]
            side = (x > 0) - (x < 0) if quad in ("fore", "rear", "dorsal", "ventral") else 0
            groups.setdefault((quad, wtype, side), []).append(o)
        for (quad, wtype, side), lst in groups.items():
            key = (lambda o: (abs(P[o.pid][0]), P[o.pid][2])) if quad in ("fore", "rear") else (lambda o: (P[o.pid][2], abs(P[o.pid][0])))
            lst.sort(key=key)
            for i, o in enumerate(lst):
                if len(lst) == 1: a, b = P[o.pid], (P[o.pid][0], P[o.pid][1], P[o.pid][2] + 1)
                elif i + 1 < len(lst): a, b = P[o.pid], P[lst[i + 1].pid]
                else: a, b = P[lst[i - 1].pid], P[o.pid]
                g = tuple(b[k] - a[k] for k in range(3)); L = math.sqrt(sum(v * v for v in g)) or 1.0
                full = min(L, 2 * gap)                      # the clone slides at most `gap` units at 1 copy
                d = tuple(g[k] / L * full for k in range(3))
                deltas[o.pid] = (lambda d: (lambda c, n: tuple(d[k] * c / (n + 1) for k in range(3))))(d)
    else:
        for o in mounts:
            sgn = -1.0 if (mirror and P[o.pid][0] < 0) else 1.0
            deltas[o.pid] = (lambda sgn: (lambda c, n: (offset[0] * c * sgn, offset[1] * c, offset[2] * c)))(sgn)
    go_type = next(o.tidx for o in F.objs if o.classid == 1)
    tr_type = next(o.tidx for o in F.objs if o.classid == 4)
    ab = next(o for o in F.objs if o.classid == 142)
    used = set(F.by_pid); nxt = [max(abs(p) for p in used) + 1000]
    def newpid():
        while nxt[0] in used: nxt[0] += 1
        used.add(nxt[0]); nxt[0] += 1; return nxt[0] - 1
    replaced, appended, new_pids = {}, [], []
    hq = build_hull_query(F, RESS, val) if (hull_follow and any(ncopy.values())) else None
    if hull_follow and any(ncopy.values()) and hq is None: print("hull follow: no readable hull mesh found - clones are placed without a hull check")
    hf = {"raised": 0, "ok": 0, "off": [], "far": [], "maxlift": 0.0}
    def nearest_surface(x, y, z):
        ys = hq(x, z)
        return min(ys, key=lambda v: abs(v - y)) if ys else None
    free_pos, free_skip = {}, []
    if layout == "free":
        assert hq is not None, "--layout free needs a readable hull mesh in the bundle (none found)"
        # clearance between a clone and any other mount: default = 0.9 x the closest spacing between existing turrets of the same quadrant
        base = min([min(math.dist(P[a.pid], P[b.pid]) for b in mounts if b is not a and mount_info(b)[1] == mount_info(a)[1]) for a in mounts if sum(1 for b in mounts if mount_info(b)[1] == mount_info(a)[1]) > 1] or [3.0])
        surf = {}
        for o in mounts:
            ss_ = nearest_surface(*P[o.pid]); surf[o.pid] = (ss_, P[o.pid][1] - ss_) if ss_ is not None else None
        def run_free(gap_d):
            skips = []
            occupied = [P[o.pid] for o in mounts]
            def valid_at(o, x, z, occ):
                sp = P[o.pid]; sv = surf[o.pid]
                if sv is None: return None
                ys_ = hq(x, z)
                if not ys_: return None
                sf = min(ys_, key=lambda v: abs(v - sv[0])); r = math.hypot(x - sp[0], z - sp[2])
                if abs(sf - sv[0]) > min(0.25 + 0.5 * r, 1.5): return None        # stay on the same hull surface (gentle slopes allowed)
                cand = (x, sf + sv[1], z)
                return None if any(math.dist(cand, q) < gap_d for q in occ) else cand
            cp = {}                                                                           # mirror twin of each mount (same y/z, opposite x), if the ship has one
            for o in mounts:
                sp = P[o.pid]; cp[o.pid] = None
                if abs(sp[0]) > 0.05:
                    for o2 in mounts:
                        q = P[o2.pid]
                        if o2 is not o and abs(q[0] + sp[0]) < 0.3 and abs(q[1] - sp[1]) < 0.3 and abs(q[2] - sp[2]) < 0.3 and mount_info(o2)[1:3] == mount_info(o)[1:3] or (o2 is not o and abs(q[0] + sp[0]) < 0.3 and abs(q[1] - sp[1]) < 0.3 and abs(q[2] - sp[2]) < 0.3 and mount_info(o2)[2] == mount_info(o)[2] and {mount_info(o2)[1], mount_info(o)[1]} == {"port", "starboard"}):
                            cp[o.pid] = o2; break
            def pair_ok(o, cand, occ):                                                        # cand and its mirror must both be free and on the hull
                o2 = cp[o.pid]
                if o2 is None: return True
                mc = (-cand[0], cand[1], cand[2])
                if math.dist(cand, mc) < gap_d: return False
                return valid_at(o2, mc[0], mc[2], occ + [cand]) is not None
            def offsets(centre):
                for ri in range(1, 33):
                    r = 0.25 * ri
                    for k in range(24):
                        a_ = 2 * math.pi * k / 24; dx, dz = r * math.cos(a_), r * math.sin(a_)
                        if centre and abs(dx) > 1e-6: continue                              # centreline turrets move only forward/back, so the ship stays symmetric
                        yield r, dx, dz
            def commit(o, c, cand, placed):
                placed[(o.pid, c)] = cand; occupied.append(cand)
                o2 = cp[o.pid]
                if o2 is not None and (o2.pid, c) not in placed:
                    m_ = (-cand[0], cand[1], cand[2]); placed[(o2.pid, c)] = m_; occupied.append(m_)
            # rows: same quadrant, weapon type and side of the ship. One shared offset per row (regular look); a clone and its mirror twin are validated and placed together
            rows = {}
            for o in mounts:
                x_ = P[o.pid][0]; rows.setdefault((mount_info(o)[1], mount_info(o)[2], (x_ > 0.05) - (x_ < -0.05)), []).append(o)
            maxc = max(ncopy.values()) if ncopy else 0
            placed = {}
            for c in range(1, maxc + 1):
                for key in sorted(rows, key=lambda k: (k[2] < 0, k[0], k[1])):
                    mem = sorted([o for o in rows[key] if ncopy[o.pid] >= c and (o.pid, c) not in placed], key=lambda o: (P[o.pid][2], P[o.pid][0]))
                    if not mem: continue
                    best = None
                    for r, dx, dz in offsets(key[2] == 0):
                        occ2 = list(occupied); got = {}
                        for o in mem:
                            cand = valid_at(o, P[o.pid][0] + dx, P[o.pid][2] + dz, occ2)
                            if cand and pair_ok(o, cand, occ2):
                                got[o.pid] = cand; occ2.append(cand)
                                if cp[o.pid] is not None and (cp[o.pid].pid not in [m.pid for m in mem]): occ2.append((-cand[0], cand[1], cand[2]))
                        if got and (best is None or len(got) > len(best[0]) or (len(got) == len(best[0]) and r < best[1] - 1e-9)): best = (got, r)
                        if best and len(best[0]) == len(mem) and r > best[1] + 1e-9: break
                    if best:
                        for o in mem:
                            if o.pid in best[0] and (o.pid, c) not in placed: commit(o, c, best[0][o.pid], placed)
                    for o in mem:                                                          # whatever the shared offset could not place: nearest free spot
                        if (o.pid, c) in placed: continue
                        res_ = None
                        for ri in range(1, 33):
                            r = 0.25 * ri; bestc = None
                            for k in range(24):
                                a_ = 2 * math.pi * k / 24; dx, dz = r * math.cos(a_), r * math.sin(a_)
                                if key[2] == 0 and abs(dx) > 1e-6: continue
                                cand = valid_at(o, P[o.pid][0] + dx, P[o.pid][2] + dz, occupied)
                                if cand and pair_ok(o, cand, occupied) and (bestc is None or abs(cand[1] - P[o.pid][1]) < abs(bestc[1] - P[o.pid][1])): bestc = cand
                            if bestc: res_ = bestc; break
                        if res_: commit(o, c, res_, placed)
                        else: skips.append(mount_info(o)[0] + ("_x%d" % (c + 1)))
            return placed, skips
        def patterns(placed):                                                             # distinct shared offsets per copy and row (1 each = perfectly regular)
            seen = {}
            for (pid, c), pos in placed.items():
                o = next(m for m in mounts if m.pid == pid); x_ = P[pid][0]
                seen.setdefault((c, mount_info(o)[1], mount_info(o)[2], (x_ > 0.05) - (x_ < -0.05)), set()).add((round(abs(pos[0] - x_) * 2) / 2 if abs(x_) < 0.05 else round((pos[0] - x_) * (1 if x_ >= 0 else -1) * 2) / 2, round((pos[2] - P[pid][2]) * 2) / 2))
            return sum(len(v) for v in seen.values()) - len(seen)
        if min_gap is not None:
            gap_d = min_gap; placed, free_skip = run_free(gap_d)
        else:
            top = min(3.0, max(0.8, 0.9 * base)) * ((1 + (clone_scale if clone_scale is not None else 1.0)) / 2); cands = []
            for f in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5):
                g = max(0.8, top * f); pl, sk = run_free(g); cands.append((len(sk), patterns(pl), -g, g, pl, sk))
                if not sk and patterns(pl) == 0: break
            _, _, _, gap_d, placed, free_skip = min(cands, key=lambda t: t[:3])
        free_pos.update(placed)
        print(f"free layout: clearance {gap_d:.2f} units; placed {len(free_pos)} clone(s)" + (f", NO free spot for {len(free_skip)}: " + ", ".join(free_skip) if free_skip else ""))
    children_add = {}                                   # father transform pid -> [new transform pids]
    for o in mounts:
        for c in range(1, ncopy[o.pid] + 1):
            mb = copy.deepcopy(val(o.pid)); gpid = mb["m_GameObject"]["m_PathID"]
            if clone_scale is not None: mb["MeshScale"] = clone_scale
            go = copy.deepcopy(val(gpid))
            tpid = next(x["component"]["m_PathID"] for x in go["m_Component"] if F.by_pid[x["component"]["m_PathID"]].classid == 4)
            tr = copy.deepcopy(val(tpid))
            ngo, ntr, nmb = newpid(), newpid(), newpid()
            go["m_Name"] = f'{go["m_Name"]}_x{c + 1}'
            for x in go["m_Component"]:
                p = x["component"]["m_PathID"]
                x["component"]["m_PathID"] = ntr if p == tpid else nmb
            tr["m_GameObject"]["m_PathID"] = ngo; tr["m_Children"] = []
            lp = tr["m_LocalPosition"]
            if layout == "free":
                if (o.pid, c) not in free_pos: continue
                lp["x"], lp["y"], lp["z"] = free_pos[(o.pid, c)]
            else:
                dd = clone_delta(o, c, ncopy[o.pid]); lp["x"] += dd[0]; lp["y"] += dd[1]; lp["z"] += dd[2]
            if hq is not None and layout != "free":
                # keep the clone from sinking into the hull: it must sit at least as high above the hull surface as the mount it was copied from
                sp = P[o.pid]; ss = nearest_surface(*sp); sc = nearest_surface(lp["x"], lp["y"], lp["z"])
                if ss is None or sc is None: hf["off"].append(go["m_Name"])
                else:
                    need = (sp[1] - ss) - (lp["y"] - sc)
                    if need <= 0.05: hf["ok"] += 1
                    elif need > 2.5: hf["far"].append((go["m_Name"], round(need, 2)))
                    else: lp["y"] += need; hf["raised"] += 1; hf["maxlift"] = max(hf["maxlift"], need)
            mb["m_GameObject"]["m_PathID"] = ngo
            father = tr["m_Father"]["m_PathID"]
            children_add.setdefault(father, []).append(ntr)
            appended += [(ngo, go_type, F.write(go_type, go)), (ntr, tr_type, F.write(tr_type, tr)), (nmb, o.tidx, F.write(o.tidx, mb))]
            new_pids += [ngo, ntr, nmb]
    if hq is not None and layout != "free":
        print(f"hull follow: {hf['ok']} clone(s) already clear of the hull, {hf['raised']} raised out of it (largest lift {hf['maxlift']:.2f})")
        if hf["off"]: print("  no hull under (left as placed): " + ", ".join(hf["off"]))
        if hf["far"]: print("  would need a lift over 2.5 units, left as placed (check these): " + ", ".join(f"{n} ({d})" for n, d in hf["far"]))
    for sel, sc in scales:
        hit = [o for o in mounts if matches(sel, o)]
        assert hit, f"--scale {sel}: no weapon mount matches"
        for o in hit:
            mbv = copy.deepcopy(val(o.pid)); mbv["MeshScale"] = sc; replaced[o.pid] = F.write(o, mbv)
        print(f"scaled {len(hit)} existing mount(s) matching '{sel}' to {sc:g}")
    # ---- extra munition slots: the battle UI shows min(FireMissileAction components on the prefab, MunitionSlots) ----
    if munition_slots:
        MB = [o for o in F.objs if o.classid == 114 and list(val(o.pid))[4:] == ["radialMenuPosition", "hideInMenu"]]
        by_go = {}
        for o in MB: by_go.setdefault(val(o.pid)["m_GameObject"]["m_PathID"], []).append(o)
        mun = [g for g in by_go if F.by_pid[g].classid == 1 and val(g)["m_Name"] == "Munitions"]
        assert len(mun) == 1, "expected one 'Munitions' menu object on this prefab, found %d" % len(mun)
        mgo = mun[0]
        others = {val(o.pid)["m_Script"]["m_PathID"] for g, l in by_go.items() if g != mgo for o in l}
        fire = [o for o in by_go[mgo] if val(o.pid)["m_Script"]["m_PathID"] not in others]
        print(f"prefab has {len(fire)} munition slot action(s) (FireMissileAction); target {munition_slots}")
        assert fire, "could not identify the FireMissileAction components"
        used = {val(o.pid)["radialMenuPosition"] for o in by_go[mgo]}
        gov = copy.deepcopy(val(mgo)); added = 0
        for _ in range(max(0, munition_slots - len(fire))):
            nmb = newpid(); mbv = copy.deepcopy(val(fire[-1].pid))
            pos = next(k for k in range(0, 8) if k not in used); used.add(pos); mbv["radialMenuPosition"] = pos
            appended.append((nmb, fire[-1].tidx, F.write(fire[-1].tidx, mbv)))
            gov["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": nmb}})
            new_pids.append(nmb); added += 1
            print(f"  + munition slot {len(fire) + added}: radial menu position {pos}")
        if added: replaced[mgo] = F.write(F.by_pid[mgo], gov)
    late_children = {}                                  # father transform pid -> [transform pids to hang under it at the end]
    # ---- extra carrier (fighter / support squadron) slots ----------------------------------------------------------
    # game rules (from the code): slots 0..FighterSlots-1 are fighter slots, the next UtilityFighterSlots are support slots;
    # the prefab needs ONE LaunchFightersAction per slot (UI/launch) and a LaunchPoint bay per SlotNo (falls back to any compatible bay).
    if fighter_slots is not None or utility_slots is not None:
        assert fighter_slots is not None and utility_slots is not None, "give BOTH --fighter-slots and --utility-slots (the final values you set in the level files)"
        total = fighter_slots + utility_slots
        MB2 = [o for o in F.objs if o.classid == 114 and list(val(o.pid))[4:] == ["radialMenuPosition", "hideInMenu"]]
        by_go2 = {}
        for o in MB2: by_go2.setdefault(val(o.pid)["m_GameObject"]["m_PathID"], []).append(o)
        fg = [g for g in by_go2 if F.by_pid[g].classid == 1 and val(g)["m_Name"] == "Fighters"]
        assert len(fg) == 1, "expected one 'Fighters' menu object, found %d" % len(fg)
        fgo = fg[0]
        other = {val(o.pid)["m_Script"]["m_PathID"] for g, l in by_go2.items() if g != fgo for o in l}
        cnt = {}
        for o in by_go2[fgo]:
            sc = val(o.pid)["m_Script"]["m_PathID"]
            if sc not in other: cnt.setdefault(sc, []).append(o)
        ranked = sorted(cnt.values(), key=len, reverse=True)
        assert ranked and (len(ranked) == 1 or len(ranked[0]) > len(ranked[1])), "cannot tell which component is LaunchFightersAction on this prefab"
        launch = ranked[0]
        order = [c["component"]["m_PathID"] for c in val(fgo)["m_Component"]]
        launch.sort(key=lambda o: order.index(o.pid))
        print(f"prefab has {len(launch)} fighter/support launch action(s); target {total} ({fighter_slots} fighter + {utility_slots} support)")
        assert total <= 8 and total - len(launch) <= len({0,1,2,3,4,5,6,7} - {val(o.pid)["radialMenuPosition"] for o in by_go2[fgo]}), \
            "the Fighters radial menu has no room for %d more launch buttons (only %d free positions)" % (total - len(launch), len({0,1,2,3,4,5,6,7} - {val(o.pid)["radialMenuPosition"] for o in by_go2[fgo]}))
        used = {val(o.pid)["radialMenuPosition"] for o in by_go2[fgo]}
        gov = copy.deepcopy(val(fgo)); added = 0; new_launch = {}
        for _ in range(max(0, total - len(launch)) if "buttons" in slot_parts else 0):
            nmb = newpid(); mbv = copy.deepcopy(val(launch[-1].pid)); new_launch[nmb] = (launch[-1].tidx, mbv)
            pos = next(k for k in range(0, 8) if k not in used); used.add(pos); mbv["radialMenuPosition"] = pos
            appended.append((nmb, launch[-1].tidx, F.write(launch[-1].tidx, mbv)))
            gov["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": nmb}})
            new_pids.append(nmb); added += 1
            print(f"  + launch button for carrier slot {len(launch) + added - 1}: radial menu position {pos}")
        if squadron_groups:
            order_all = [o.pid for o in launch] + list(new_launch)                          # slot order = component order on the Fighters object
            assert sum(squadron_groups) == len(order_all), "--squadron-groups must add up to %d launch buttons (got %d)" % (len(order_all), sum(squadron_groups))
            assert all(0 < g_ <= 7 for g_ in squadron_groups) and len(squadron_groups) <= 6, "each group needs 1-7 buttons and there can be at most 6 groups"
            scr_n2 = {m_.pid: val(m_.pid)["m_ClassName"] for m_ in F.objs if m_.classid == 115}
            cs2 = [o for o in F.objs if o.classid == 114 and val(o.pid).get("m_Script") and val(o.pid)["m_Script"]["m_PathID"] in scr_n2]
            ftr_ = next(c["component"]["m_PathID"] for c in val(fgo)["m_Component"] if F.by_pid[c["component"]["m_PathID"]].classid == 4)
            back_ = next(o for c in val(fgo)["m_Component"] for o in [F.by_pid[c["component"]["m_PathID"]]] if o.classid == 114 and scr_n2[val(o.pid)["m_Script"]["m_PathID"]] == "BackAction")
            smb_ = next(o for o in cs2 if scr_n2[val(o.pid)["m_Script"]["m_PathID"]] == "SubMenuAction" and val(o.pid)["SubMenu"]["m_PathID"] == fgo)
            moved = set(order_all); keep_pos = {val(o.pid)["radialMenuPosition"] for o in by_go2[fgo] if o.pid not in moved}
            gov["m_Component"] = [c for c in gov["m_Component"] if c["component"]["m_PathID"] not in moved]
            names = ["Fighters", "Support"] + ["Squadrons%d" % (i + 1) for i in range(2, 6)] if len(squadron_groups) == 2 else ["Squadrons%d" % (i + 1) for i in range(6)]
            at = 0
            for gi, cnt in enumerate(squadron_groups):
                ngo, ntr, nback, nsub = newpid(), newpid(), newpid(), newpid()
                members = order_all[at:at + cnt]; at += cnt
                g2 = copy.deepcopy(val(fgo)); g2["m_Name"] = names[gi]; g2["m_Component"] = [{"component": {"m_FileID": 0, "m_PathID": p_}} for p_ in [ntr, nback] + members]
                t2 = copy.deepcopy(val(ftr_)); t2["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; t2["m_Children"] = []
                b2 = copy.deepcopy(val(back_.pid)); b2["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; b2["radialMenuPosition"] = 0
                spos = next(k for k in range(8) if k not in keep_pos); keep_pos.add(spos)
                s2 = copy.deepcopy(val(smb_.pid)); s2["SubMenu"] = {"m_FileID": 0, "m_PathID": ngo}; s2["radialMenuPosition"] = spos; s2["m_GameObject"] = {"m_FileID": 0, "m_PathID": fgo}
                for mi, p_ in enumerate(members):
                    if p_ in new_launch: ti_, v_ = new_launch[p_]; v_["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; v_["radialMenuPosition"] = 1 + mi
                    else:
                        o_ = F.by_pid[p_]; v_ = copy.deepcopy(val(p_)); v_["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; v_["radialMenuPosition"] = 1 + mi; replaced[p_] = F.write(o_, v_)
                appended.extend([(ngo, F.by_pid[fgo].tidx, F.write(F.by_pid[fgo].tidx, g2)), (ntr, F.by_pid[ftr_].tidx, F.write(F.by_pid[ftr_].tidx, t2)),
                                 (nback, back_.tidx, F.write(back_.tidx, b2)), (nsub, smb_.tidx, F.write(smb_.tidx, s2))])
                new_pids.extend([ngo, ntr, nback, nsub]); gov["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": nsub}})
                late_children.setdefault(val(ftr_)["m_Father"]["m_PathID"], []).append(ntr)
                print(f"  squadron submenu '{names[gi]}': slots {at - cnt}..{at - 1} (button at position {spos} in the Fighters menu)")
            for p_, (ti_, v_) in new_launch.items():                                         # re-write the new launch buttons with their final parent / position
                i_ = next(i for i, t in enumerate(appended) if t[0] == p_); appended[i_] = (p_, ti_, F.write(ti_, v_))
            added = added or 1
        if added: replaced[fgo] = F.write(F.by_pid[fgo], gov)
        # launch bays (LaunchPoint) so every slot number has its own bay
        bays = [o for o in F.objs if o.classid == 114 and "SlotNo" in val(o.pid) and "LaunchPoints" in val(o.pid)]
        have = {val(o.pid)["SlotNo"] for o in bays if val(o.pid)["CanLaunch"]}
        tmpl = [o for o in bays if val(o.pid)["CanLaunch"] and val(o.pid)["CanDock"]] or [o for o in bays if val(o.pid)["CanLaunch"]]
        assert tmpl, "no launch bay to copy"
        tmpl.sort(key=lambda o: goname_of(val(o.pid)["m_GameObject"]["m_PathID"]))
        nb = 0; used_t = {}
        for slot in range(total):
            if slot in have or "bays" not in slot_parts: continue
            src = tmpl[nb % len(tmpl)]; nb += 1
            used_t[src.pid] = used_t.get(src.pid, 0) + 1; k_ = used_t[src.pid]
            sv = copy.deepcopy(val(src.pid)); sg = sv["m_GameObject"]["m_PathID"]
            sgo = copy.deepcopy(val(sg))
            st_pid = next(x["component"]["m_PathID"] for x in sgo["m_Component"] if F.by_pid[x["component"]["m_PathID"]].classid == 4)
            st = copy.deepcopy(val(st_pid))
            ngo, ntr, nmb = newpid(), newpid(), newpid()
            sgo["m_Name"] = "%s_slot%d" % (sgo["m_Name"], slot)
            sgo["m_Component"] = [{"component": {"m_FileID": 0, "m_PathID": ntr}}, {"component": {"m_FileID": 0, "m_PathID": nmb}}]
            st["m_GameObject"]["m_PathID"] = ngo
            lp = st["m_LocalPosition"]; lp["x"] += bay_offset[0] * k_; lp["y"] += bay_offset[1] * k_; lp["z"] += bay_offset[2] * k_
            old_kids = st["m_Children"]; st["m_Children"] = []
            sv["m_GameObject"]["m_PathID"] = ngo; sv["SlotNo"] = slot
            # clone the Icon marker (child transform) so the UI icon sits with the new bay
            ic = sv["iconPosition"]["m_PathID"]
            if ic and "icons" in slot_parts:
                ict = copy.deepcopy(val(ic)); icg = copy.deepcopy(val(ict["m_GameObject"]["m_PathID"]))
                nig, nit = newpid(), newpid()
                icg["m_Component"] = [{"component": {"m_FileID": 0, "m_PathID": nit}}]
                ict["m_GameObject"]["m_PathID"] = nig; ict["m_Father"]["m_PathID"] = ntr; ict["m_Children"] = []
                side_ = -1.0 if st["m_LocalPosition"]["x"] > 0 else 1.0                            # +x offset = towards the centre line, mirrored for port / starboard
                il_ = ict["m_LocalPosition"]; il_["x"] += icon_offset[0] * side_ * k_; il_["y"] += icon_offset[1] * k_; il_["z"] += icon_offset[2] * k_
                st["m_Children"].append({"m_FileID": 0, "m_PathID": nit}); sv["iconPosition"]["m_PathID"] = nit
                appended += [(nig, go_type, F.write(go_type, icg)), (nit, tr_type, F.write(tr_type, ict))]
                new_pids += [nig, nit]
            children_add.setdefault(st["m_Father"]["m_PathID"], []).append(ntr)
            appended += [(ngo, go_type, F.write(go_type, sgo)), (ntr, tr_type, F.write(tr_type, st)), (nmb, src.tidx, F.write(src.tidx, sv))]
            new_pids += [ngo, ntr, nmb]
            print(f"  + launch bay for carrier slot {slot} (copy of {goname_of(sg)}, moved by {tuple(round(bay_offset[i]*k_,1) for i in range(3))})")
    for father, kids in children_add.items():
        fo = F.by_pid[father]; fv = copy.deepcopy(val(father))
        for k in kids: fv["m_Children"].append({"m_FileID": 0, "m_PathID": k})
        replaced[father] = F.write(fo, fv)
    script_state = {"types": 0, "scripts": 0}
    comp_add = {}                                        # GameObject pid -> [new component pids]
    res_extra = bytearray()                              # data appended to the bundle's .resS (mesh vertex data of copied objects)
    added_types = {}
    def add_type(raw):
        idx = len(F.types) + script_state["types"]
        pre = bytearray(F.prefix); nt_pos = 4 + len(F.unity) + 1 + 4 + 1
        struct.pack_into("<i", pre, nt_pos, struct.unpack_from("<i", pre, nt_pos)[0] + 1); pre += raw
        F.prefix = bytes(pre); script_state["types"] += 1; return idx
    def add_script_component(cls, type_raw, ms_raw, mb_raw, go_pid, patch):
        """append a MonoBehaviour of script class <cls> (adding its serialized type + MonoScript if the bundle lacks them)"""
        ms_pid = next((o.pid for o in F.objs if o.classid == 115 and val(o.pid)["m_ClassName"] == cls), None)
        ms_type = next(o.tidx for o in F.objs if o.classid == 115)
        if ms_pid is None:
            n_scripts = struct.unpack_from("<i", F.suffix, 0)[0]
            new_type_idx = len(F.types) + script_state["types"]
            t_raw = bytearray(type_raw); struct.pack_into("<h", t_raw, 5, n_scripts)             # point the type at the new script entry
            pre = bytearray(F.prefix)
            nt_pos = 4 + len(F.unity) + 1 + 4 + 1                                                # endian/reserved, unity string, platform, has_tt
            struct.pack_into("<i", pre, nt_pos, struct.unpack_from("<i", pre, nt_pos)[0] + 1); pre += t_raw
            F.prefix = bytes(pre)
            ms_pid = newpid()
            suf = bytearray(F.suffix); struct.pack_into("<i", suf, 0, n_scripts + 1)
            suf[4 + 12 * n_scripts:4 + 12 * n_scripts] = struct.pack("<iq", 0, ms_pid)
            F.suffix_len = (getattr(F, "suffix_len", None) or F._suffix_len()) + 12             # keep the whole extended suffix when rebuilding
            F.suffix = bytes(suf)
            script_state["types"] += 1
            appended.append((ms_pid, ms_type, ms_raw)); new_pids.append(ms_pid)
        else:
            idx = next(i for i in range(struct.unpack_from("<i", F.suffix, 0)[0]) if struct.unpack_from("<iq", F.suffix, 4 + 12 * i)[1] == ms_pid)
            new_type_idx = next(i for i, t in enumerate(F.types) if t.classid == 114 and t.script_idx == idx)
        mb_pid = newpid(); mbb = bytearray(mb_raw)
        struct.pack_into("<q", mbb, 4, go_pid); struct.pack_into("<q", mbb, 20, ms_pid); patch(mbb)
        appended.append((mb_pid, new_type_idx, bytes(mbb))); new_pids.append(mb_pid)
        comp_add.setdefault(go_pid, []).append(mb_pid)
        return mb_pid
    root = max((o for o in F.objs if o.classid == 1), key=lambda o: len(val(o.pid)["m_Component"]))
    if armour_regen and hull_regen is not None:
        assert hull_regen > 0, "--armour-regen needs a hull regen value above 0 (the sign of hullPerTick is the flag)"
    if hull_regen is not None:
        assert not any(o.classid == 114 and "hullPerTick" in val(o.pid) for o in F.objs), "this ship already has a Regenerative Hull Resin module"
        add_script_component("RegenerativeHullResinModule", RESIN_TYPE_RAW, RESIN_MS_RAW, RESIN_MB_RAW, root.pid, lambda b: struct.pack_into("<f", b, 32, -hull_regen if armour_regen else hull_regen))
        print(f"added Regenerative Hull Resin module to {val(root.pid)['m_Name']} (hullPerTick {-hull_regen if armour_regen else hull_regen}{', armour regeneration ON - needs deadlock_dll.py patch --armour-factor' if armour_regen else ''})")
    elif armour_regen:
        mods = [o for o in F.objs if o.classid == 114 and "hullPerTick" in val(o.pid)]
        assert mods, "this ship has no Regenerative Hull Resin module: add one with --hull-regen X --armour-regen"
        for o in mods:
            v = copy.deepcopy(val(o.pid)); assert v["hullPerTick"] != 0, "hullPerTick is 0"; v["hullPerTick"] = -abs(v["hullPerTick"]); replaced[o.pid] = F.write(o, v)
        print(f"flagged the existing Regenerative Hull Resin module for armour regeneration (hullPerTick {v['hullPerTick']}) - needs deadlock_dll.py patch --armour-factor")
    if munition_factory:
        assert not any(o.classid == 115 and val(o.pid)["m_ClassName"] == "MunitionFactoryModule" for o in F.objs), "this ship already has a Munition Factory module"
        add_script_component("MunitionFactoryModule", MFM_TYPE_RAW, MFM_MS_RAW, MFM_MB_RAW, root.pid, lambda b: None)
        print(f"added Munition Factory module (passive munition resupply) to {val(root.pid)['m_Name']}; the ship's Armory MunitionResupplyRange sets its speed")
    sub_state = {}
    act_used = {}                                                                             # radial positions taken in the Actions menu (shared by the abilities added below)
    def alloc_action_pos(preferred):
        scr_n = {m.pid: val(m.pid)["m_ClassName"] for m in F.objs if m.classid == 115}
        cs = [o for o in F.objs if o.classid == 114 and val(o.pid).get("m_Script") and val(o.pid)["m_Script"]["m_PathID"] in scr_n]
        mv = next(o for o in cs if scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "MoveAction"); g = val(mv.pid)["m_GameObject"]["m_PathID"]
        if g not in act_used: act_used[g] = {val(o.pid)["radialMenuPosition"] for o in cs if "radialMenuPosition" in val(o.pid) and val(o.pid)["m_GameObject"]["m_PathID"] == g and not val(o.pid)["hideInMenu"]}
        if ability_submenu:
            if "go" not in sub_state:
                fgo_ = next(o for o in F.objs if o.classid == 1 and val(o.pid)["m_Name"] == "Fighters")
                ftr_ = next(c["component"]["m_PathID"] for c in val(fgo_.pid)["m_Component"] if F.by_pid[c["component"]["m_PathID"]].classid == 4)
                back_ = next(o for c in val(fgo_.pid)["m_Component"] for o in [F.by_pid[c["component"]["m_PathID"]]] if o.classid == 114 and scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "BackAction")
                smb_ = next(o for o in cs if scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "SubMenuAction" and val(o.pid)["SubMenu"]["m_PathID"] == fgo_.pid)
                ngo, ntr, nback, nsub = newpid(), newpid(), newpid(), newpid()
                gv_ = copy.deepcopy(val(fgo_.pid)); gv_["m_Name"] = "Abilities"; gv_["m_Component"] = [{"component": {"m_FileID": 0, "m_PathID": p_}} for p_ in (ntr, nback)]
                tv_ = copy.deepcopy(val(ftr_)); tv_["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; tv_["m_Children"] = []
                bv_ = copy.deepcopy(val(back_.pid)); bv_["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; bv_["radialMenuPosition"] = 0
                sv_ = copy.deepcopy(val(smb_.pid)); sv_["SubMenu"] = {"m_FileID": 0, "m_PathID": ngo}
                u0 = act_used[g]; spos = preferred if preferred not in u0 else next((k for k in range(8) if k not in u0), None)
                assert spos is not None, "the Actions radial menu has no free position for the Abilities submenu button"
                u0.add(spos); sv_["radialMenuPosition"] = spos; sv_["m_GameObject"] = {"m_FileID": 0, "m_PathID": g}
                appended.extend([(ngo, F.by_pid[fgo_.pid].tidx, F.write(F.by_pid[fgo_.pid].tidx, gv_)), (ntr, F.by_pid[ftr_].tidx, F.write(F.by_pid[ftr_].tidx, tv_)),
                                 (nback, back_.tidx, F.write(back_.tidx, bv_)), (nsub, smb_.tidx, F.write(smb_.tidx, sv_))])
                new_pids.extend([ngo, ntr, nback, nsub]); comp_add.setdefault(g, []).append(nsub)
                sub_state.update(go=ngo, used={0}, father=val(ftr_)["m_Father"]["m_PathID"], tr=ntr, gov=gv_)
                print(f"created the 'Abilities' submenu (button at radial position {spos} in the Actions menu, back button at 0)")
            pos = next((k for k in range(8) if k not in sub_state["used"]), None)
            assert pos is not None, "the Abilities submenu is full"
            sub_state["used"].add(pos); return sub_state["go"], pos
        u = act_used[g]; pos = preferred if preferred not in u else next((k for k in range(8) if k not in u), None)
        assert pos is not None, "the Actions radial menu has no free position for another ability button"
        u.add(pos); return g, pos
    if mini_jump is not None:
        # micro-FTL jump = MiniJumpAgent on the ship + MiniJumpAction button in the Actions radial menu (+ the ship's FTLEffect for the visuals)
        scr_name = {m.pid: val(m.pid)["m_ClassName"] for m in F.objs if m.classid == 115}
        comps = [o for o in F.objs if o.classid == 114 and val(o.pid).get("m_Script") and val(o.pid)["m_Script"]["m_PathID"] in scr_name]
        kind = lambda o: scr_name[val(o.pid)["m_Script"]["m_PathID"]]
        assert any(kind(o) == "FTLEffect" for o in comps), "this ship has no FTLEffect component (the jump visuals) - mini-jump cannot be added"
        agents = [o for o in comps if kind(o) == "MiniJumpAgent"]
        jd, jdur, (jh, jv), jcool = mini_jump["delay"], mini_jump["duration"], mini_jump["range"], mini_jump["cooldown"]
        def set_agent(b):
            struct.pack_into("<i", b, 32, jd); struct.pack_into("<i", b, 36, jdur); struct.pack_into("<f", b, 40, jh); struct.pack_into("<f", b, 44, jv)
            struct.pack_into("<i", b, 48, jcool); struct.pack_into("<i", b, 52, 5); b[56] = 0
        if agents:
            for o in agents:
                v = copy.deepcopy(val(o.pid)); v.update(jumpDelay=jd, jumpDuration=jdur, jumpRangeHorizontal=jh, jumpRangeVertical=jv, cooldownTurns=jcool, preventMiniJumpAction=0); replaced[o.pid] = F.write(o, v)
            print("ship already has a MiniJumpAgent: updated its settings and cleared preventMiniJumpAction")
        else:
            add_script_component("MiniJumpAgent", MJA_TYPE_RAW, MJA_MS_RAW, MJA_MB_RAW, root.pid, set_agent)
            print(f"added MiniJumpAgent to {val(root.pid)['m_Name']} (range {jh:g} x {jv:g}, cooldown {jcool} turns, delay {jd}, duration {jdur})")
        techs = [o for o in comps if kind(o) == "TechLab"]
        assert techs, "this ship has no TechLab - the jump range comes from it, so mini-jump cannot work"
        for o in techs:                                                                   # effective range = jumpRange x TechLab range curve (by TechLab health), and 0 unless UseMiniJumpRange is on
            v = copy.deepcopy(val(o.pid)); v["UseMiniJumpRange"] = 1; replaced[o.pid] = F.write(o, v)
        print("TechLab: UseMiniJumpRange switched on (without it the jump range is 0 and the ship jumps on the spot)")
        if any(kind(o) == "MiniJumpAction" for o in comps):
            print("ship already has the MiniJumpAction button")
        else:
            agopid, pos = alloc_action_pos(7)
            def set_action(b): struct.pack_into("<i", b, 32, pos); b[36] = 0
            add_script_component("MiniJumpAction", MJX_TYPE_RAW, MJX_MS_RAW, MJX_MB_RAW, agopid, set_action)
            print(f"added the MiniJumpAction button to the Actions menu (radial position {pos})")
    if augury is not None:
        scr_n = {m.pid: val(m.pid)["m_ClassName"] for m in F.objs if m.classid == 115}
        have = {scr_n[val(o.pid)["m_Script"]["m_PathID"]] for o in F.objs if o.classid == 114 and val(o.pid).get("m_Script") and val(o.pid)["m_Script"]["m_PathID"] in scr_n}
        if "PredictiveAuguryModule" in have:
            for o in F.objs:
                if o.classid == 114 and val(o.pid).get("m_Script") and scr_n.get(val(o.pid)["m_Script"]["m_PathID"]) == "PredictiveAuguryModule":
                    v = copy.deepcopy(val(o.pid)); v["maxAmmunition"] = augury["uses"]; replaced[o.pid] = F.write(o, v)
            print(f"ship already has the Predictive Augury module: uses set to {augury['uses']}")
        else:
            add_script_component("PredictiveAuguryModule", PAM_TYPE_RAW, PAM_MS_RAW, PAM_MB_RAW, root.pid, lambda b: struct.pack_into("<i", b, 32, augury["uses"]))
            print(f"added the Predictive Augury module to {val(root.pid)['m_Name']} ({augury['uses']} use(s))")
        if "PredictiveAuguryAction" in have: print("ship already has the Predictive Augury button")
        else:
            agopid, pos = alloc_action_pos(7)
            def set_aug_action(b): struct.pack_into("<i", b, 32, pos); b[36] = 0
            add_script_component("PredictiveAuguryAction", PAX_TYPE_RAW, PAX_MS_RAW, PAX_MB_RAW, agopid, set_aug_action)
            print(f"added the Predictive Augury button to the Actions menu (radial position {pos})")
    if stealth is not None:
        # Stealth (Colonial Orion): StealthModule on the ship + StealthAction button, plus the two effect object trees the module points to, copied from the Orion bundle
        import tempfile
        dpath = stealth["donor"]
        if not open(dpath, "rb").read(7) == b"UnityFS":                                   # a game-folder (XOR-encrypted) bundle: decrypt a temporary copy
            dpath2 = os.path.join(tempfile.mkdtemp(), "donor"); open(dpath2, "wb").write(xor_bytes(open(dpath, "rb").read())); dpath = dpath2
        DB = read_bundle(dpath); (DO1, DS1, _, _), (DO2, DS2, _, _) = DB["nodes"]
        DF = SerFile(DB["data"][DO1:DO1 + DS1]); DRESS = DB["data"][DO2:DO2 + DS2]; dcache = {}
        def dval(p):
            if p not in dcache: dcache[p] = DF.read(DF.by_pid[p])[0]
            return dcache[p]
        draw = lambda o: bytes(DF.d[DF.data_offset + o.off:DF.data_offset + o.off + o.size])
        dscr = {o.pid: dval(o.pid)["m_ClassName"] for o in DF.objs if o.classid == 115}
        def dscript(cls):
            mso = next((o for o in DF.objs if o.classid == 115 and dval(o.pid)["m_ClassName"] == cls), None)
            assert mso is not None, f"{stealth['donor']}: no {cls} script - is this the Colonial Orion bundle?"
            mb = next(o for o in DF.objs if o.classid == 114 and dval(o.pid)["m_Script"]["m_PathID"] == mso.pid)
            return DF.types[mb.tidx].raw, draw(mso), draw(mb), mb
        scr_n = {m.pid: val(m.pid)["m_ClassName"] for m in F.objs if m.classid == 115}
        have = {scr_n[val(o.pid)["m_Script"]["m_PathID"]] for o in F.objs if o.classid == 114 and val(o.pid).get("m_Script") and val(o.pid)["m_Script"]["m_PathID"] in scr_n}
        assert "StealthModule" not in have, "this ship already has a Stealth module"
        t_raw, ms_raw, mb_raw, mod = dscript("StealthModule"); mv = dval(mod.pid)
        roots_ = [mv["stealthEffect"]["m_PathID"], mv["changeStealthEffect"]["m_PathID"]]
        assert struct.unpack_from("<f", mb_raw, 32)[0] == mv["batteryMax"] and struct.unpack_from("<iq", mb_raw, 44) == (0, roots_[0]) and struct.unpack_from("<iq", mb_raw, 56) == (0, roots_[1]), "unexpected StealthModule layout"
        def dsub(start):                                                                  # objects reachable from start (never going up through m_Father)
            seen = {}; todo = [start]
            def walk(v, key, out):
                if isinstance(v, dict):
                    if set(v.keys()) >= {"m_FileID", "m_PathID"}:
                        if key != "m_Father" and v["m_FileID"] == 0 and v["m_PathID"]: out.append(v["m_PathID"])
                    else:
                        for k_, x in v.items(): walk(x, k_, out)
                elif isinstance(v, list):
                    for x in v: walk(x, key, out)
            while todo:
                p = todo.pop()
                if p in seen or p not in DF.by_pid: continue
                seen[p] = DF.by_pid[p].classid; out = []; walk(dval(p), "", out); todo += out
            return seen
        tree = {}
        for r_ in roots_: tree.update(dsub(r_))
        mapping = {p: newpid() for p, cl in tree.items() if cl not in (114, 115)}
        skip_copy = set()
        if stealth_mesh == "ship":                                                        # use the ship's own hull mesh for the stealth projection instead of the Orion-shaped one
            lods = []
            for o_ in F.objs:
                if o_.classid == 43:
                    mm = re.match(r"^HS_.*_LOD(\d+)$", val(o_.pid)["m_Name"])
                    if mm: lods.append((int(mm.group(1)), o_.pid))
            if lods:
                ship_mesh = max(lods)[1]
                for p, cl in tree.items():
                    if cl == 43: mapping[p] = ship_mesh; skip_copy.add(p)
                print(f"stealth projection uses this ship's own hull mesh ({val(ship_mesh)['m_Name']}) instead of the Orion's")
            else: print("no HS_*_LODn hull mesh found on this ship: the stealth projection keeps the Orion-shaped mesh")
        for p in [p for p, cl in tree.items() if cl == 114]:                              # scripts on the effect objects (TimedDeactivate)
            cls = dscr[dval(p)["m_Script"]["m_PathID"]]; _, _, _, _ = dscript(cls)
            tr_, ms_, _, _ = dscript(cls); go_new = mapping[dval(p)["m_GameObject"]["m_PathID"]]
            mapping[p] = add_script_component(cls, tr_, ms_, draw(DF.by_pid[p]), go_new, lambda b: None); comp_add.pop(go_new, None)
        def remap(v):
            if isinstance(v, dict):
                if set(v.keys()) == {"m_FileID", "m_PathID"}: return {"m_FileID": v["m_FileID"], "m_PathID": mapping.get(v["m_PathID"], v["m_PathID"]) if v["m_FileID"] == 0 else v["m_PathID"]}
                return {k_: remap(x) for k_, x in v.items()}
            return [remap(x) for x in v] if isinstance(v, list) else v
        res_path = f"archive:/{N1}/{N2}"
        for p, cl in tree.items():
            if cl in (114, 115) or p in skip_copy: continue
            do = DF.by_pid[p]; v = remap(copy.deepcopy(dval(p)))
            if cl == 43 and v.get("m_StreamData", {}).get("size"):
                sd = v["m_StreamData"]; blob = DRESS[sd["offset"]:sd["offset"] + sd["size"]]
                while (len(RESS) + len(res_extra)) % 16: res_extra.append(0)
                v["m_StreamData"] = {"offset": len(RESS) + len(res_extra), "size": sd["size"], "path": res_path}; res_extra += blob
            dt = DF.types[do.tidx]
            ti = next((i for i, tt in enumerate(F.types) if tt.classid == cl and tt.script_idx == -1 and tt.hash == dt.hash), None)
            if ti is None:
                ti = added_types.get((cl, dt.hash))
                if ti is None: ti = added_types[(cl, dt.hash)] = add_type(dt.raw)
            appended.append((mapping[p], ti, DF.write(do, v))); new_pids.append(mapping[p])
        def set_mod(b):
            struct.pack_into("<f", b, 32, stealth["battery"]); b[40] = 1 if stealth["start_on"] else 0
            struct.pack_into("<iq", b, 44, 0, mapping[roots_[0]]); struct.pack_into("<iq", b, 56, 0, mapping[roots_[1]])
        add_script_component("StealthModule", t_raw, ms_raw, mb_raw, root.pid, set_mod)
        print(f"added the Stealth module to {val(root.pid)['m_Name']} (battery {stealth['battery']:g}, {'starts stealthed' if stealth['start_on'] else 'starts visible'}) with {len(mapping)} effect objects copied from the Orion")
        if "StealthAction" in have: print("ship already has the Stealth button")
        else:
            at_, ams_, amb_, _ = dscript("StealthAction"); agopid, pos = alloc_action_pos(7)
            def set_st_action(b): struct.pack_into("<i", b, 32, pos); b[36] = 0
            add_script_component("StealthAction", at_, ams_, amb_, agopid, set_st_action)
            print(f"added the Stealth button to the Actions menu (radial position {pos})")
    if squadron_revive:
        assert munition_factory or any(o.classid == 115 and val(o.pid)["m_ClassName"] == "MunitionFactoryModule" for o in F.objs), "--squadron-revive needs the Munition Factory module: add it with --munition-factory (this ship has none)"
        ships_ = [o for o in F.objs if o.classid == 114 and "ShowExclusionInEditor" in val(o.pid) and val(o.pid)["m_GameObject"]["m_PathID"] == root.pid]
        assert len(ships_) == 1, "could not find the ship's ShipInfo component"
        v = copy.deepcopy(val(ships_[0].pid)); v["ShowExclusionInEditor"] = 1; replaced[ships_[0].pid] = F.write(ships_[0], v)
        print("flagged the ship to rebuild destroyed squadrons (needs deadlock_dll.py patch --revive-turns in the DLL)")
    for gp, pids in comp_add.items():
        if gp == sub_state.get("go"):
            gv = sub_state["gov"]
            for p_ in pids: gv["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": p_}})
            i_ = next(i for i, t in enumerate(appended) if t[0] == gp); appended[i_] = (gp, appended[i_][1], F.write(appended[i_][1], gv)); continue
        gv = copy.deepcopy(val(gp))
        for p_ in pids: gv["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": p_}})
        replaced[gp] = F.write(F.by_pid[gp], gv)
    if sub_state:                                       # hang the Abilities submenu under the Actions object (like Munitions / Fighters)
        late_children.setdefault(sub_state["father"], []).append(sub_state["tr"])
    for fa, kids_ in late_children.items():
        assert fa not in replaced, "internal: Actions transform was already rewritten"
        fv = copy.deepcopy(val(fa))
        for k_ in kids_: fv["m_Children"].append({"m_FileID": 0, "m_PathID": k_})
        replaced[fa] = F.write(F.by_pid[fa], fv)
    abv = copy.deepcopy(val(ab.pid))
    for p in new_pids: abv["m_PreloadTable"].append({"m_FileID": 0, "m_PathID": p})
    for e in abv["m_Container"]: e["second"]["preloadSize"] += len(new_pids)
    replaced[ab.pid] = F.write(ab, abv)
    new_cab = F.rebuild(replaced=replaced, appended=appended)
    write_bundle(dst, B, [(F1, N1, new_cab), (F2, N2, RESS + bytes(res_extra))])
    # verify by re-reading
    B2 = read_bundle(dst); (P1, Q1, _, _), _ = B2["nodes"]
    G = SerFile(B2["data"][P1:P1 + Q1])
    n = sum(1 for o in G.objs if o.classid == 114 and "WeaponType" in G.read(o)[0] and "InitialAngles" in G.read(o)[0])
    print(f"wrote {dst}: {len(G.objs)} objects (was {len(F.objs)}), {n} weapon mount points (was {len(mounts)})")
    if fighter_slots is not None:
        fo = [o for o in G.objs if o.classid == 1 and G.read(o)[0]["m_Name"] == "Fighters"][0]
        comps = [c["component"]["m_PathID"] for c in G.read(fo)[0]["m_Component"]]
        print("Fighters menu object now has %d action components (incl. back/other buttons): positions %s" % (len(comps) - 1, sorted(G.read(G.by_pid[c])[0].get("radialMenuPosition", -1) for c in comps if G.by_pid[c].classid == 114)))
        print("launch bays by SlotNo:", sorted(G.read(o)[0]["SlotNo"] for o in G.objs if o.classid == 114 and "SlotNo" in G.read(o)[0] and G.read(o)[0].get("CanLaunch")))
    if munition_slots:
        gm = [o for o in G.objs if o.classid == 1 and G.read(o)[0]["m_Name"] == "Munitions"][0]
        comps = [c["component"]["m_PathID"] for c in G.read(gm)[0]["m_Component"]]
        print("Munitions menu object now has %d action components (incl. the back button): positions %s" % (len(comps) - 1, sorted(G.read(G.by_pid[c])[0].get("radialMenuPosition", -1) for c in comps if G.by_pid[c].classid == 114)))
    return dst


def build_parser():
    ap = argparse.ArgumentParser(description="Edit a decrypted Deadlock ship bundle: clone / resize turrets, squadron and munition slots, ship systems and abilities.\nNothing is changed unless you pass its option. DLL patches live in deadlock_dll.py.")
    g_weap = ap.add_argument_group("Weapons - cloning and resizing turrets")
    g_slot = ap.add_argument_group("Squadron / munition slots")
    g_sys = ap.add_argument_group("Ship systems")
    g_abil = ap.add_argument_group("Abilities")
    g_out = ap.add_argument_group("Output")
    ap.epilog = """EXAMPLES
  Mercury, everything (decrypt first with deadlock_bundle_xor.py, encrypt at the end with --encrypt):
    python deadlock_weapons.py colonialmercury.dec colonialmercury --clone fore=3 --layout free --scale fore=0.7 --clone-scale 0.7 \\
        --fighter-slots 4 --utility-slots 2 --split-squadrons --munition-slots 4 --hull-regen 0.1 --armour-regen --munition-factory --squadron-revive \\
        --mini-jump --augury --stealth orion.dec --ability-submenu --encrypt
  List the turrets:        python deadlock_weapons.py colonialmercury.dec x --list-mounts
  Slot counts here must match the level files:  python deadlock_shipdb.py set <levels> <dll> "Colonial Mercury" FighterSlots 4
  Needed in the DLL (deadlock_dll.py):  --armour-regen -> patch --armour-factor, --squadron-revive -> patch --revive-turns
LIMITS
  Radial menus hold 8 buttons (positions 0-7): Fighters launch buttons <= 8 in total, munition slots <= 7 (plus Back), Actions menu 8 (use --ability-submenu)."""
    ap.formatter_class = argparse.RawDescriptionHelpFormatter
    ap.usage = "deadlock_weapons.py SRC DST [options]      (SRC = decrypted .dec bundle, DST = output; options are grouped below)"
    ap.add_argument("src", help="decrypted bundle, e.g. colonialmercury.dec")
    ap.add_argument("dst", help="output file")
    g_weap.add_argument("--offset", default="1.5,0,0", help="local-space x,y,z offset of each clone (default 1.5,0,0)")
    g_weap.add_argument("--copies", type=int, default=0, help="clone every weapon mount N extra times (1 = double). Default 0: weapons are NOT touched unless you ask")
    g_weap.add_argument("--clone", action="append", default=[], metavar="SELECTOR=N", help="clone only the mounts matching SELECTOR N times; repeatable, later ones win, and it overrides --copies for those mounts. SELECTOR: all, sides (= port + starboard), port, starboard, fore, rear, dorsal, ventral, type:N (weapon type id), name:TEXT (part of the mount name). N=0 excludes. e.g. --clone fore=1 --clone dorsal=2")
    g_weap.add_argument("--layout", choices=["offset", "mid", "free"], default="offset", help="where clones go: 'offset' = shifted by --offset (x mirrored on the port side, so symmetric); 'mid' = slid along the row to sit BETWEEN existing turrets, following the hull (see --gap); 'free' = each clone goes to the nearest EMPTY spot next to its turret on the same hull surface (needs the hull mesh; see --min-gap)")
    g_weap.add_argument("--min-gap", type=float, help="--layout free: smallest distance between a clone and any other turret (default: 0.9 x the closest turret spacing on that side, scaled by --clone-scale)")
    g_weap.add_argument("--gap", type=float, default=1.5, help="--layout mid: how far (units) a single clone slides towards the next turret in its row (default 1.5, half the usual 3-unit turret spacing)")
    g_weap.add_argument("--no-mirror", action="store_true", help="--layout offset: apply --offset as-is to every mount (old behaviour, not symmetric)")
    g_weap.add_argument("--clone-scale", type=float, metavar="S", help="size of the CLONED turrets (1 = same as the original, 0.6 = 60%% - mesh, barrel position and collider all shrink). Helps clones fit beside their neighbours")
    g_weap.add_argument("--scale", action="append", default=[], metavar="SELECTOR=S", help="resize EXISTING turrets matching SELECTOR (same selectors as --clone), repeatable, e.g. --scale fore=0.7. Visual/physical size only; damage and range are unchanged")
    g_weap.add_argument("--list-mounts", action="store_true", help="print every weapon mount (name, quadrant, weapon type, position) and the groups usable with --clone, then exit")
    g_slot.add_argument("--munition-slots", type=int, help="make the prefab have this many munition slot actions (needed for the battle UI to show them). Mercury: 4")
    g_slot.add_argument("--fighter-slots", type=int, help="final FighterSlots value (as set in the level files) - adds launch buttons/bays. Mercury default is 3")
    g_slot.add_argument("--utility-slots", type=int, help="final UtilityFighterSlots value (support squadrons). Mercury default is 1")
    g_slot.add_argument("--bay-offset", default="0,3,4", help="x,y,z shift applied to each new launch bay (default 0,3,4)")
    g_sys.add_argument("--hull-regen", type=float, help="add a Regenerative Hull Resin module (passive hull repair, scaled by Engineering) with this hullPerTick; the Cylon modern basestar uses 0.2")
    g_sys.add_argument("--munition-factory", action="store_true", help="add the Munition Factory module (passive munition resupply, as on the Cylon Argos). Its speed comes from the ship's Armory MunitionResupplyRange; add --squadron-revive (and deadlock_dll.py patch --revive-turns) to also rebuild destroyed squadrons")
    g_sys.add_argument("--squadron-revive", action="store_true", help="flag the ship so its Munition Factory module rebuilds DESTROYED squadrons (needs deadlock_dll.py patch --revive-turns in the DLL; the ship needs the module: add it with --munition-factory)")
    g_sys.add_argument("--armour-regen", action="store_true", help="flag the ship's Regenerative Hull Resin module so it also regenerates ARMOUR (needs deadlock_dll.py patch --armour-factor in the DLL). With --hull-regen X it adds the module; alone it flags the module the ship already has")
    g_weap.add_argument("--no-hull-follow", action="store_true", help="skip the hull check. By default every clone is raised out of the hull if it would sit lower than the mount it was copied from (reads the hull mesh from the bundle)")
    g_abil.add_argument("--mini-jump", action="store_true", help="give the ship the micro-FTL jump (Cylon Argos style): adds the MiniJumpAgent and the jump button in the Actions radial menu. The ship needs an FTLEffect component (Mercury, Jupiter, Argos have one)")
    g_abil.add_argument("--jump-range", default="200,10", help="--mini-jump: horizontal,vertical jump range in game units (default 200,10 = Argos)")
    g_abil.add_argument("--jump-cooldown", type=int, default=5, help="--mini-jump: cooldown in turns (default 5)")
    g_abil.add_argument("--jump-delay", type=int, default=50, help="--mini-jump: delay before the jump in ticks (default 50 = Argos)")
    g_abil.add_argument("--jump-duration", type=int, default=3, help="--mini-jump: jump duration (default 3 = Argos)")
    g_abil.add_argument("--ability-submenu", action="store_true", help="put the abilities added by --mini-jump / --augury / --stealth into their own 'Abilities' submenu (one button in the Actions radial) instead of using up Actions positions")
    g_abil.add_argument("--stealth-mesh", choices=["ship", "orion"], default="ship", help="mesh for the stealth projection effect: the ship's own hull (default) or the Orion-shaped mesh copied from the donor")
    g_slot.add_argument("--squadron-groups", help="split the squadron launch buttons into submenus inside the Fighters menu, e.g. 4,2 (slot counts per submenu, in slot order; must add up to FighterSlots+UtilityFighterSlots)")
    g_slot.add_argument("--split-squadrons", action="store_true", help="shorthand: one submenu for the fighter slots and one for the support (utility) slots")
    g_slot.add_argument("--icon-offset", default="12,0,-4", help="where the build-screen icons of the NEW squadron bays sit relative to the copied bay icon, X,Y,Z per added slot (X is towards the centre line for both sides). Raise it if the new slot icons overlap the old ones")
    g_abil.add_argument("--augury", action="store_true", help="give the ship the Cylon guardian basestar's Predictive Augury ability (module + button in the Actions radial menu)")
    g_abil.add_argument("--augury-uses", default="1", help="--augury: number of uses per battle (maxAmmunition, default 1 = the guardian basestar). The game has no true unlimited setting: 'unlimited' = 999, which the button shows as 999/999")
    g_abil.add_argument("--stealth", metavar="ORION_BUNDLE", help="give the ship the Colonial Orion's stealth ability: path to the Orion bundle (encrypted game file or decrypted .dec). Adds the Stealth module, the button in the Actions radial menu and the two stealth effect objects")
    g_abil.add_argument("--stealth-battery", type=float, default=100.0, help="--stealth: stealth battery capacity (Orion: 100)")
    g_abil.add_argument("--stealth-start", choices=["on", "off"], default="on", help="--stealth: start battles stealthed (Orion: on)")
    g_slot.add_argument("--slot-parts", default="buttons,bays,icons", help="(for testing) which parts --fighter-slots adds: any of buttons, bays, icons")
    g_out.add_argument("--encrypt", action="store_true", help="XOR-encrypt the output so the game can load it")
    return ap


def main():
    ap = build_parser()
    a = ap.parse_args()
    off = tuple(float(x) for x in a.offset.split(","))
    tmp = a.dst + ".tmp" if a.encrypt else a.dst
    double_weapons(a.src, tmp, off, a.copies, a.munition_slots, a.fighter_slots, a.utility_slots, tuple(float(x) for x in a.bay_offset.split(',')), a.hull_regen, a.armour_regen, [(c.rsplit('=', 1)[0], int(c.rsplit('=', 1)[1])) for c in a.clone], a.list_mounts, a.clone_scale, [(c.rsplit('=', 1)[0], float(c.rsplit('=', 1)[1])) for c in a.scale], a.layout, a.gap, not a.no_mirror, a.munition_factory, a.squadron_revive, not a.no_hull_follow, a.min_gap, ({"delay": a.jump_delay, "duration": a.jump_duration, "range": tuple(float(x) for x in a.jump_range.split(',')), "cooldown": a.jump_cooldown} if a.mini_jump else None), ({"uses": 999 if a.augury_uses.lower() in ("unlimited", "inf", "infinite") else int(a.augury_uses)} if a.augury else None), ({"donor": a.stealth, "battery": a.stealth_battery, "start_on": a.stealth_start == "on"} if a.stealth else None), a.slot_parts, a.ability_submenu, a.stealth_mesh, ([int(x) for x in a.squadron_groups.split(",")] if a.squadron_groups else ([a.fighter_slots, a.utility_slots] if a.split_squadrons else None)), tuple(float(x) for x in a.icon_offset.split(",")))
    if a.encrypt:
        open(a.dst, "wb").write(xor_bytes(open(tmp, "rb").read())); os.remove(tmp)
        print("encrypted ->", a.dst)

if __name__ == "__main__":
    main()
