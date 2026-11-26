#!/usr/bin/env python3
"""
weapons - edit a Deadlock ship bundle (built around the Colonial Mercury).

What it does (all optional, combine as needed; run with -h for every option and examples):
    turrets      clone / resize weapon mounts, placed on free hull space with left-right symmetry (--clone, --layout free, --scale, --clone-scale)
    slots        extra munition slots and squadron slots, with launch bays, build-screen icons and optional squadron submenus
    systems      hull-resin regen, Munition Factory, custom armour repair / squadron replacement
    abilities    micro-FTL jump, predictive augury, stealth (donors: Argos, guardian basestar, Orion), optionally grouped in an Abilities submenu

Usage
    python -m deadlock_tools weapons BUNDLE [options]
BUNDLE is a game bundle (encrypted) or a decrypted .dec, given as a path or as a bare name looked up in the configured
ship-bundle folder. Encrypted bundles are decrypted into the work folder's tool scratch area. The result is written,
encrypted and under the game's file name, to the work tree's BSG_Data/StreamingAssets/ folder.

Every run starts again from the original bundle (turret cloning is not repeatable), so give all options in ONE command.
DLL patches (commander ranks, munition load, repair shortcuts) are in 'dll'; slot counts and other ship data in the level
files are edited with 'shipdb' and must match the numbers used here (the battle UI shows min(prefab buttons, level-data slots)).
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
    def rebuild(self, replaced=None, appended=None, removed=None):
        """replaced: {pid: bytes}; appended: [(pid, type_index, bytes)]; removed: set of pids to drop. Returns new file bytes."""
        replaced = replaced or {}; appended = appended or []; removed = set(removed or ())
        blobs = []
        for o in self.objs:
            if o.pid in removed: continue
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
    if mode == 1:
        properties, payload = raw[:5], raw[5:]
        decoder = _lzma.LZMADecompressor(format=_lzma.FORMAT_ALONE)
        try:
            result = decoder.decompress(properties + struct.pack("<Q", usize) + payload, max_length=usize + 1)
        except _lzma.LZMAError:
            # The Windows runtime can reject a size-bearing header on streams
            # that carry their own end marker. Retry using that marker.
            decoder = _lzma.LZMADecompressor(format=_lzma.FORMAT_ALONE)
            result = decoder.decompress(properties + b"\xff" * 8 + payload, max_length=usize + 1)
        if not decoder.eof or len(result) != usize:
            raise _lzma.LZMAError("incomplete UnityFS LZMA block or incorrect declared size")
        return result
    if mode in (2, 3): return _lz4_block(raw, usize)
    raise AssertionError("unknown bundle compression %d" % mode)


def read_bundle(path):
    with open(path, "rb") as source:
        d = source.read()
    return parse_bundle(d)


def parse_bundle(d):
    if not d.startswith(b"UnityFS\0"):
        d = xor_bytes(d)
    if not d.startswith(b"UnityFS\0"):
        raise ValueError("not a supported Deadlock ship bundle")
    o = d.index(b"\0") + 1; ver = struct.unpack(">I", d[o:o + 4])[0]; o += 4
    j = d.index(b"\0", o); pl = d[o:j].decode(); o = j + 1
    j = d.index(b"\0", o); rev = d[o:j].decode(); o = j + 1
    size, cs, us, fl = struct.unpack(">qIII", d[o:o + 20]); hdr_end = o + 20
    comp = fl & 0x3f
    if ver != 6:
        raise ValueError(f"unsupported UnityFS version {ver} (expected version 6)")
    if not hdr_end <= size <= len(d) or not 0 < cs <= size - hdr_end:
        raise ValueError("invalid UnityFS size or block list length")
    if fl & 0x80:  # BlocksInfoAtTheEnd
        info_offset = size - cs
        data_offset, data_end = hdr_end, info_offset
    else:
        info_offset = hdr_end
        data_offset, data_end = hdr_end + cs, size
    bi = _decompress(d[info_offset:info_offset + cs], us, comp)
    if len(bi) != us:
        raise ValueError("UnityFS block list has an incorrect uncompressed size")
    p = 16; nb = struct.unpack(">i", bi[p:p + 4])[0]; p += 4
    blocks = []
    for _ in range(nb): blocks.append(struct.unpack(">IIH", bi[p:p + 10])); p += 10
    nn = struct.unpack(">i", bi[p:p + 4])[0]; p += 4
    nodes = []
    for _ in range(nn):
        off, sz, f = struct.unpack(">qqI", bi[p:p + 20]); p += 20
        e = bi.index(b"\0", p); nodes.append((off, sz, f, bi[p:e].decode())); p = e + 1
    decoded = []
    q = data_offset
    for uncompressed_size, compressed_size, block_flags in blocks:
        end = q + compressed_size
        if end > data_end:
            raise ValueError("UnityFS data block extends past the data section")
        block = _decompress(d[q:end], uncompressed_size, block_flags & 0x3f)
        if len(block) != uncompressed_size:
            raise ValueError("UnityFS data block has an incorrect uncompressed size")
        decoded.append(block)
        q = end
    return dict(version=ver, player=pl, revision=rev, flags=fl, hash=bi[:16], nodes=nodes, data=b"".join(decoded))


def write_bundle(path, b, node_blobs):
    """node_blobs: list of (flags, name, bytes) in order -> uncompressed single block bundle."""
    import sys
    # Validate all blobs exist and have valid size before writing
    nodes = []
    data = b""
    for f, name, blob in node_blobs:
        if not isinstance(blob, bytes):
            raise TypeError(f"Expected bytes for {name!r}, got {type(blob)}")
        if len(blob) != 0 and not blob:
            raise ValueError(f"Blob for {name!r} is empty but has non-zero length claim")
        nodes.append((len(data), len(blob), f, name))
        data += blob
    bi = bytearray(b["hash"]) + struct.pack(">i", 1) + struct.pack(">IIH", len(data), len(data), 0x40) + struct.pack(">i", len(nodes))
    for off, sz, f, name in nodes: bi += struct.pack(">qqI", off, sz, f) + name.encode() + b"\0"
    hdr = b"UnityFS\0" + struct.pack(">I", b["version"]) + b["player"].encode() + b"\0" + b["revision"].encode() + b"\0"
    total = len(hdr) + 8 + 4 + 4 + 4 + len(bi) + len(data)
    out = hdr + struct.pack(">qIII", total, len(bi), len(bi), 0x40) + bytes(bi) + data
    assert len(out) == total, (len(out), total)
    with open(path, "wb") as f:
        f.write(out)
        f.flush()
        # Try to sync to disk for data integrity, but handle platforms that don't support fsync
        if hasattr(f, 'fsync'):
            try:
                f.fsync()
            except (AttributeError, IOError):
                # Some platforms (Windows in some cases) or filesystems don't support fsync
                pass


def rename_bundle_asset(data, target_name, ship_type_id=None):
    """Relink a copied ship prefab and optionally assign its new database ID.

    Deadlock calls AssetBundle.LoadAsset<GameObject>(assetName), where the same
    assetName is also the bundle filename. Renaming only the file therefore
    leaves Unity's AssetBundle container pointing at the donor prefab name.
    A cloned ShipInfo also retains the donor ShipType unless a new ID is given.

    The bundle's internal files are named CAB-<32 hex> (plus CAB-<hex>.resS for
    mesh/texture data, referenced from inside the serialized file as
    "archive:/CAB-<hex>/CAB-<hex>.resS"). Unity refuses to load a second bundle
    holding a CAB name that is already loaded ("another AssetBundle with the
    same files is already loaded"), and Deadlock then hangs on the load. So a
    copy gets fresh CAB names, derived from the new bundle name so rebuilding
    the same clone gives the same bytes.
    """
    import hashlib, tempfile

    data = bytes(data)
    target_name = str(target_name).lower()
    if ship_type_id is not None and (type(ship_type_id) is not int or ship_type_id < 0):
        raise ValueError("Ship type ID must be a non-negative integer.")
    encrypted = not data.startswith(b"UnityFS\0")
    bundle = parse_bundle(data)
    cab_names = {}
    for _offset, _size, _flags, node_name in bundle["nodes"]:
        hit = re.match(r"CAB-([0-9a-f]{32})", node_name)
        if hit and hit.group(1) not in cab_names:
            cab_names[hit.group(1)] = hashlib.md5(("deadlock-tools:%s:%s" % (target_name, hit.group(1))).encode()).hexdigest()
    def rename_cabs(text):
        for old, new in cab_names.items(): text = text.replace(old, new)
        return text
    def rename_cab_bytes(blob):
        # same-length ASCII swap: length-prefixed strings and offsets in the serialized file stay valid
        for old, new in cab_names.items(): blob = blob.replace(b"CAB-" + old.encode(), b"CAB-" + new.encode())
        return blob
    node_blobs = []
    for offset, size, flags, node_name in bundle["nodes"]:
        blob = bundle["data"][offset:offset + size]
        if node_name.startswith("CAB-") and not node_name.endswith(".resS"):
            serialized = SerFile(blob)
            replacements = {}
            roots = []
            for obj in serialized.objs:
                if obj.classid != 142:  # Unity AssetBundle
                    continue
                asset_bundle, _ = serialized.read(obj)
                asset_bundle["m_Name"] = target_name
                container = asset_bundle.get("m_Container", [])
                if len(container) != 1:
                    raise ValueError("Expected one prefab in the copied ship bundle.")
                entry = container[0]
                old_path = entry["first"]
                directory = old_path.rsplit("/", 1)[0]
                entry["first"] = directory + "/" + target_name.lower() + ".prefab"
                pointer = entry["second"]["asset"]
                if pointer.get("m_FileID") != 0:
                    raise ValueError("The ship prefab points into an external serialized file.")
                roots.append(pointer["m_PathID"])
                replacements[obj.pid] = serialized.write(obj, asset_bundle)
            if len(roots) != 1:
                raise ValueError("Could not identify the copied bundle's single prefab root.")
            root = next((obj for obj in serialized.objs if obj.pid == roots[0]), None)
            if root is None or root.classid != 1:
                raise ValueError("The bundle's prefab pointer does not refer to a GameObject.")
            value, _ = serialized.read(root)
            value["m_Name"] = target_name
            replacements[root.pid] = serialized.write(root, value)
            if ship_type_id is not None:
                scripts = {obj.pid: serialized.read(obj)[0].get("m_ClassName")
                           for obj in serialized.objs if obj.classid == 115}
                ship_infos = []
                for obj in serialized.objs:
                    if obj.classid != 114:
                        continue
                    fields, _ = serialized.read(obj)
                    if (scripts.get(fields.get("m_Script", {}).get("m_PathID")) == "ShipInfo"
                            and fields.get("m_GameObject", {}).get("m_PathID") == root.pid):
                        ship_infos.append((obj, fields))
                if len(ship_infos) != 1:
                    raise ValueError(f"Expected one ShipInfo on the copied prefab root, found {len(ship_infos)}.")
                ship_info, fields = ship_infos[0]
                fields["ShipType"] = ship_type_id
                replacements[ship_info.pid] = serialized.write(ship_info, fields)
            blob = rename_cab_bytes(serialized.rebuild(replacements))
        node_blobs.append((flags, rename_cabs(node_name), blob))

    with tempfile.TemporaryDirectory(prefix="deadlock-bundle-rename-") as temp:
        plain_path = os.path.join(temp, "bundle.unity")
        write_bundle(plain_path, bundle, node_blobs)
        output = open(plain_path, "rb").read()
    return xor_bytes(output) if encrypted else output


# ======================================================================================
#  Ship bundle editor: turrets, slots, ship systems, abilities
# ======================================================================================
import argparse, os, copy, sys, re, json

if __package__ in (None, ""): sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deadlock_tools import config
from deadlock_tools.bundle_xor import DEFAULT_KEY, xor


def xor_bytes(data, key=DEFAULT_KEY):
    return xor(data, key)


def find_bundle(src, bundles=None):
    """Find a bundle path or bare name in the selected bundle folder or work scratch folder."""
    if os.path.exists(src): return src
    folders = [config.out_dir("bundles", create=False), config.setting("bundles", bundles, required=False)]
    folders.append(config.work_dir(create=False))
    seen = set()
    for folder in folders:
        if not folder or os.path.abspath(folder) in seen: continue
        seen.add(os.path.abspath(folder))
        for name in (src, src + ".dec"):
            if folder and os.path.isfile(os.path.join(folder, name)): return os.path.join(folder, name)
    raise SystemExit("no such bundle: %s (searched the configured ship-bundle folder and work folder)" % src)


def decrypted(path):
    """A plain UnityFS input stays in place; an encrypted input is decrypted into the work scratch folder."""
    data = open(path, "rb").read()
    if data[:7] == b"UnityFS": return path
    plain = xor_bytes(data)
    if plain[:7] != b"UnityFS": raise SystemExit("%s is neither a Unity bundle nor an XOR-encrypted one" % path)
    name = os.path.basename(path); dst = os.path.join(config.work_dir(), name if name.endswith(".dec") else name + ".dec")
    if not (os.path.isfile(dst) and open(dst, "rb").read() == plain): config.write(dst, plain, path)
    print("decrypted %s -> %s" % (path, dst)); return dst


def _noop(): pass


def _md4(data):
    """Return Unity's 128-bit MD4 digest for MonoScript and type-tree identities."""
    import struct

    mask = 0xffffffff
    message = bytearray(data)
    bit_length = len(message) * 8
    message.append(0x80)
    message.extend(b"\0" * ((56 - len(message) % 64) % 64))
    message.extend(struct.pack("<Q", bit_length))
    a0, b0, c0, d0 = 0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476
    for offset in range(0, len(message), 64):
        x = struct.unpack("<16I", message[offset:offset + 64])
        a, b, c, d = a0, b0, c0, d0
        for i in range(16):
            f = (b & c) | (~b & d)
            s = (3, 7, 11, 19)[i % 4]
            a = ((a + f + x[i]) & mask)
            a = ((a << s) | (a >> (32 - s))) & mask
            a, b, c, d = d, a, b, c
        for i in range(16):
            g = (b & c) | (b & d) | (c & d)
            k = (i % 4) * 4 + i // 4
            s = (3, 5, 9, 13)[i % 4]
            a = ((a + g + x[k] + 0x5a827999) & mask)
            a = ((a << s) | (a >> (32 - s))) & mask
            a, b, c, d = d, a, b, c
        order = (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15)
        for i in range(16):
            h = b ^ c ^ d
            s = (3, 9, 11, 15)[i % 4]
            a = ((a + h + x[order[i]] + 0x6ed9eba1) & mask)
            a = ((a << s) | (a >> (32 - s))) & mask
            a, b, c, d = d, a, b, c
        a0, b0, c0, d0 = (a0 + a) & mask, (b0 + b) & mask, (c0 + c) & mask, (d0 + d) & mask
    return struct.pack("<4I", a0, b0, c0, d0)


def _custom_module_type_raw(template, class_name, fields, script_index):
    """Build a Unity 2018.4 MonoBehaviour type record from an existing header."""
    import struct

    nodes = []
    name_index = next((i for i, node in enumerate(template.nodes)
                       if node.level == 1 and node.name == "m_Name"), None)
    if name_index is None:
        raise ValueError("bundle has no usable MonoBehaviour type-tree header")
    end = name_index + 1
    while end < len(template.nodes) and template.nodes[end].level > 1:
        end += 1
    for source in template.nodes[:end]:
        node = Node()
        node.ver, node.level, node.tflags = source.ver, source.level, source.tflags
        node.type, node.name = source.type, source.name
        node.size, node.index, node.meta = source.size, source.index, source.meta
        nodes.append(node)
    def add(level, type_name, name, size, meta, tflags=0):
        node = Node()
        node.ver, node.level, node.tflags = 1, level, tflags
        node.type, node.name = type_name, name
        node.size, node.index, node.meta = size, len(nodes), meta
        nodes.append(node)
    for name, value_type in fields:
        if value_type == "string":                      # as colonialcelestra's predefinedName
            add(1, "string", name, -1, 0x8000)
            add(2, "Array", "Array", -1, 0x4001, 1)
            add(3, "int", "size", 4, 0x1)
            add(3, "char", "data", 1, 0x1)
        elif value_type.startswith("PPtr<"):            # as the Orion's StealthModule effect pointers
            add(1, value_type, name, 12, 0)
            add(2, "int", "m_FileID", 4, 0x800001)
            add(2, "SInt64", "m_PathID", 8, 0x800001)
        elif value_type == "bool":
            add(1, "bool", name, 1, 0x4000)
        else:
            add(1, value_type, name, 4, 0)

    hashed = bytearray()
    for node in nodes:
        hashed += node.type.encode("utf-8") + node.name.encode("utf-8")
        hashed += struct.pack("<iiii", node.size, node.tflags, node.ver, node.meta & 0x4000)
    type_hash = _md4(hashed)
    script_hash = _md4(b"Assembly-CSharp.dll" + class_name.encode("utf-8"))

    strings = bytearray()
    string_offsets = {}
    def string_offset(value):
        if value not in string_offsets:
            string_offsets[value] = len(strings)
            strings.extend(value.encode("utf-8") + b"\0")
        return string_offsets[value]

    node_data = bytearray()
    for node in nodes:
        node_data += struct.pack("<HBBIIiiI", node.ver, node.level, node.tflags,
                                 string_offset(node.type), string_offset(node.name),
                                 node.size, node.index, node.meta)
    raw = struct.pack("<iBh", 114, 0, script_index) + script_hash + type_hash
    raw += struct.pack("<ii", len(nodes), len(strings)) + node_data + strings
    return raw, nodes, type_hash

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


def transform_matrix(tp, val):
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


def inverse_transform(M):
    a, b, c, d, e, f, g, h, i = (M[0][0], M[0][1], M[0][2], M[1][0], M[1][1], M[1][2], M[2][0], M[2][1], M[2][2])
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12: return None
    R = [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det], [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det], [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]
    return [R[r] + [-(R[r][0] * M[0][3] + R[r][1] * M[1][3] + R[r][2] * M[2][3])] for r in range(3)]


def hull_geometry(F, RESS, val, ship_space=False):
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
    if not cand: return [], []
    main = max(cand, key=lambda c: c[0]); lod = _re.search(r"lod\d+", main[1]["m_Name"].lower())
    parts = [c for c in cand if lod and lod.group(0) in c[1]["m_Name"].lower() and "col" not in c[1]["m_Name"].lower()] or [main]
    def world(go):
        tp = next((x["component"]["m_PathID"] for x in val(go)["m_Component"] if x["component"]["m_PathID"] in F.by_pid and F.by_pid[x["component"]["m_PathID"]].classid == 4), 0)
        return transform_matrix(tp, val)
    ref = None if ship_space else inverse_transform(world(main[2]))
    T = []; V_all = []
    for n, m, go in parts:
        sd = m.get("m_StreamData") or {}
        if sd.get("size"): raw = bytes(RESS[sd["offset"]:sd["offset"] + sd["size"]])
        else: raw = bytes(m["m_VertexData"]["m_DataSize"])
        ch = m["m_VertexData"]["m_Channels"][0]
        if not n or not raw or len(raw) % n or ch["format"] != 0 or ch["dimension"] < 3 or ch["stream"] != 0: continue
        st = len(raw) // n; off = ch["offset"]
        rel = world(go) if ship_space else None
        if ref is not None:
            W = world(go); rel = [[sum(ref[i][k] * W[k][j] for k in range(3)) + (ref[i][3] if j == 3 else 0) for j in range(4)] for i in range(3)]
        V = [_st.unpack_from("<3f", raw, i * st + off) for i in range(n)]
        if rel is not None: V = [tuple(rel[r][0] * v[0] + rel[r][1] * v[1] + rel[r][2] * v[2] + rel[r][3] for r in range(3)) for v in V]
        V_all += V
        if ship_space: continue
        ib = bytes(m["m_IndexBuffer"]); isz = 2 if m["m_IndexFormat"] == 0 else 4; idx = _st.unpack("<%d%s" % (len(ib) // isz, "H" if isz == 2 else "I"), ib)
        for sm in m["m_SubMeshes"]:
            if sm["topology"] != 0: continue
            f = sm["firstByte"] // isz
            for k in range(f, f + sm["indexCount"] - 2, 3): T.append((V[idx[k]], V[idx[k + 1]], V[idx[k + 2]]))
    return V_all, T


def build_hull_query(F, RESS, val):
    """Return ys(x, z) -> sorted list of heights where the ship's hull mesh crosses the vertical line through (x, z), or None if no
    usable hull mesh was found. Pure Python (grid index over the triangles' x/z footprint)."""
    V, T = hull_geometry(F, RESS, val)
    if not T: return None
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


PLATING_OPTIONS = {
    "plating_supply": ("platingCapacity", "total plating supply carried by the ship"),
    "plating_drone_load": ("dronePlatingCapacity", "plating carried per drone load"),
    "plating_facing_cap": ("maxPlating", "maximum additional plating on one facing"),
    "plating_rate": ("platingDeploymentRate", "rate at which the drone applies plating"),
}


# CLI field, serialized field, and type. Blank options preserve the source value.
SUPPORT_SETTINGS = {
    "deploy_plating": {**{name: (field, float) for name, (field, _) in PLATING_OPTIONS.items()},
                       "plating_range": ("deployRange", float)},
    "turnaround_repair_bay": {"repair_bay_fraction": ("fullRepairPercent", float),
                              "repair_bay_relaunch_offset": ("moveOnLaunchSquadronOffset", str)},
    "targeted_resupply": {"resupply_range": ("deployRange", float),
                          "resupply_swap_time": ("swapTime", float),
                          "resupply_cooldown": ("swapCooldown", int)},
    "technical_support": {"technical_support_range": ("deployRange", float),
                          "technical_support_spare_parts": ("sparePartsSupply", float),
                          "technical_support_parts_rate": ("sparePartsPerTick", float),
                          "technical_support_boost": ("boostValue", float),
                          "technical_support_resupply_seconds": ("resupplySeconds", float),
                          "technical_support_minimum_repair": ("minimumSubsystemRepairBoost", float),
                          "technical_support_unlimited_parts": ("unlimitedSpareParts", str)},
    "marine_reinforcement": {"marines_per_raptor": ("marinesPerRaptor", int)},
}


def configured_support_settings(args):
    import math

    settings = {}
    for ability, options in SUPPORT_SETTINGS.items():
        for option, (field, _) in options.items():
            value = getattr(args, option)
            if value is None:
                continue
            if field == "moveOnLaunchSquadronOffset":
                xyz = tuple(map(float, value.split(",")))
                if len(xyz) != 3 or any(not math.isfinite(n) or abs(n) > 3.4028234663852886e38 for n in xyz):
                    raise ValueError("repair bay relaunch offset expects finite X,Y,Z coordinates")
                value = dict(zip("xyz", xyz))
            elif field == "unlimitedSpareParts":
                value = value == "on"
            elif not math.isfinite(value) or value < 0 or value > (
                2**31 - 1 if isinstance(value, int) else 3.4028234663852886e38
            ) or (ability == "deploy_plating" and value == 0) or (field == "fullRepairPercent" and value > 1):
                raise ValueError(f"invalid value for --{option.replace('_', '-')}")
            settings.setdefault(ability, {})[field] = value
    return settings


SUPPORT_ABILITIES = {
    "turnaround_repair_bay": ("TurnaroundRepairBayModule", "TurnaroundRepairBayAction", "defender"),
    "targeted_resupply": ("ResupplyMunitionModule", "ResupplyMunitionAction", "celestra"),
    "deploy_plating": ("DeployPlatingModule", "DeployPlatingAction", "celestra"),
    "technical_support": ("JuryRigBoosterModule", "TechnicalSupportAction", "defender"),
    "marine_reinforcement": ("ReinforcementModule", "ReinforcementAction", "defender"),
}


def print_bay_layout(layout):
    """--list-bays: everything a --bay-layout spec can refer to, with its id"""
    fmt = lambda p: "%.2f, %.2f, %.2f" % (p["x"], p["y"], p["z"])
    print("launch bays (LaunchPoint), in hierarchy order:")
    for bay in layout["bays"]:
        flags = "+".join(name for name, on in (("launch", bay["canLaunch"]), ("dock", bay["canDock"])) if on) or "neither"
        print("  %s  slot %d  %-6s types %s  dock range %g  at %s  %s" % (
            bay["id"], bay["slot"], flags, bay["compat"], bay["dockRange"], fmt(bay["position"]), bay["path"]))
    print("launch buttons (LaunchFightersAction), button i launches slot i:")
    for index, button in enumerate(layout["launchButtons"]):
        print("  %d  %s  %s" % (index, button["id"], button["menu"]))
    print("munition buttons (FireMissileAction), one per munition slot:")
    for index, button in enumerate(layout["munitionButtons"]):
        print("  %d  %s  %s" % (index, button["id"], button["menu"]))
    print("munition launchers (MissileLauncher):")
    for launcher in layout["launchers"]:
        print("  %s  %d launch point(s)  at %s  %s" % (launcher["id"], len(launcher["launchPoints"]), fmt(launcher["position"]), launcher["path"]))


def default_support_donor(donor, bundles=None, ability=None):
    """The stock donor bundle (colonialdefender / colonialcelestra) for a support ability.

    Looks in the configured ship-bundle folder first, so a modded work-tree copy of the donor is
    only used when no stock one is configured; then falls back to find_bundle's search order."""
    name = "colonial" + donor
    folder = config.setting("bundles", bundles, required=False)
    for candidate in (name, name + ".dec"):
        if folder and os.path.isfile(os.path.join(folder, candidate)):
            return os.path.join(folder, candidate)
    try:
        return find_bundle(name, bundles)
    except SystemExit:
        flag = "--" + (ability or donor).replace("_", "-")
        raise SystemExit("%s copies its parts from the %s bundle, but %s(.dec) is not in the configured bundle folder "
                         "(%s) or the work folder. Copy it there from the game's StreamingAssets or pass --%s-donor PATH."
                         % (flag, donor.capitalize(), name, folder or "not set", donor)) from None


def load_support_donor(path):
    bundle = read_bundle(path)
    offset, size, *_ = bundle["nodes"][0]
    file = SerFile(bundle["data"][offset:offset + size])
    scripts = {obj.pid: file.read(obj)[0]["m_ClassName"] for obj in file.objs if obj.classid == 115}
    components = {}
    for obj in file.objs:
        if obj.classid == 114:
            fields = file.read(obj)[0]
            script = scripts.get(fields.get("m_Script", {}).get("m_PathID"))
            if script:
                components.setdefault(script, []).append(obj)
    return file, components





PREFAB_BOOLEAN_BYTES = {
    "StealthModule.activeByDefault",
    "MiniJumpAgent.preventMiniJumpAction",
    "JuryRigBoosterModule.unlimitedSpareParts",
}

# FEDNAJLFKEF values from the supported game assembly. These are used by the
# configurable custom modules' crew-veterancy selectors.
CREW_MODIFIER_FIELDS = {
    0: "Turret range", 1: "Turret accuracy", 2: "Munition salvo size",
    100: "Damage reduction", 101: "Subsystem repair", 102: "Squadron repair",
    103: "Hull regeneration", 200: "Marine armour", 201: "Marine strength",
    202: "Flak damage", 203: "Flak range", 204: "Boarding strength",
    300: "Posture effect", 301: "Processing power", 302: "System overclock",
    400: "Squadron launch", 401: "Squadron evade", 500: "Pitch control",
    501: "Yaw control", 502: "Maximum distance", 600: "DRADIS range",
    601: "Firewall regeneration", 602: "Hacking strength", 603: "Micro-FTL range",
    604: "Munition resupply rate",
}

INSTANT_PLATING_FIELDS = ("portPlating", "starboardPlating", "forePlating",
                          "aftPlating", "dorsalPlating", "ventralPlating")
INSTANT_PLATING_ACTION = "CustomInstantPlatingAction"
# Game classes written into a bundle the same way (no ship bundle has them to copy from: the Cylon Wardriver squadron
# in sharedassets9.assets is the only user). Field lists follow the type trees generated from Assembly-CSharp.dll.
OVERFLOW_MENU = "Abilities 2"      # second abilities submenu, made when "Abilities" holds 7 buttons
OVERFLOW_TEXT = 707                # its button's label: "Other" (no "More abilities" text in the game)
JAMMER_DEFINITION = {
    "fields": (("jamRange", "float"), ("jamMissileChance", "float"), ("jamAudioClip", "PPtr<$AudioClip>"),
               ("visualiseLayer", "string"), ("assignToSelf", "bool")),
    # the Wardriver's values except the range (150 there, squadron-sized); its jam sound lives in sharedassets9.assets,
    # which a bundle cannot point at, so the clip is empty
    "defaults": {"jamRange": 500.0, "jamMissileChance": 0.6, "jamAudioClip": {"m_FileID": 0, "m_PathID": 0},
                 "visualiseLayer": "UI_3D", "assignToSelf": False},
}
JAMMING_ACTION_DEFINITION = {"fields": (("radialMenuPosition", "int"), ("hideInMenu", "bool")),
                             "defaults": {"hideInMenu": False}}

CUSTOM_MODULE_DEFINITIONS = {
    "CustomInstantPlatingModule": {
        "label": "Instant plating",
        "fields": tuple((name, "float") for name in INSTANT_PLATING_FIELDS) + (("cooldownTurns", "int"), ("activationDelayTicks", "int")),
        "defaults": {**dict.fromkeys(INSTANT_PLATING_FIELDS, 200.0), "cooldownTurns": 1, "activationDelayTicks": 0},
    },
    "CustomArmourRepairModule": {
        "label": "Custom armour repair",
        "fields": (("baseRate", "float"), ("modifierRate", "float"), ("modifierField", "int")),
        "defaults": {"baseRate": 0.2, "modifierRate": 1.0, "modifierField": 103},
    },
    "CustomSquadronReplacementModule": {
        "label": "Custom squadron replacement",
        "fields": (("baseTicksPerReplacement", "float"), ("modifierRate", "float"), ("modifierField", "int")),
        "defaults": {"baseTicksPerReplacement": 5.0, "modifierRate": 1.0, "modifierField": 102},
    },
    "CustomDroneResupplyModule": {
        "label": "Custom drone resupply",
        "fields": (("basePlatingPerTick", "float"), ("modifierRate", "float"), ("modifierField", "int")),
        "defaults": {"basePlatingPerTick": 1.0, "modifierRate": 1.0, "modifierField": 604},
    },
    "CustomMagazineCapacityModule": {
        "label": "Custom magazine capacity",
        "fields": (("capacityMultiplier", "float"), ("modifierRate", "float"), ("modifierField", "int")),
        "defaults": {"capacityMultiplier": 10.0, "modifierRate": 1.0, "modifierField": 2},
    },
    "CustomEmpPulseModule": {
        "label": "Experimental EMP",
        "fields": (("range", "float"), ("cooldownTurns", "int"), ("detonationDelayTicks", "int"),
                   ("subsystemDamage", "float"), ("recoveryTicks", "int"),
                   ("disableSquadronTicks", "int"), ("fighterEvadeDebuffTicks", "int")),
        "defaults": {"range": 1000.0, "cooldownTurns": 1, "detonationDelayTicks": 0,
                     "subsystemDamage": 8.0, "recoveryTicks": 240,
                     "disableSquadronTicks": 360, "fighterEvadeDebuffTicks": 360},
    },
    "CustomTargetedEmpModule": {
        "label": "Targeted EMP",
        "fields": (("range", "float"), ("cooldownTurns", "int"),
                   ("detonationDelayTicks", "int"), ("subsystemDamage", "float"),
                   ("recoveryTicks", "int"), ("disableSquadronTicks", "int"),
                   ("chargesPerCooldown", "int"), ("targetRange", "float"),
                   ("fighterEvadeDebuffTicks", "int"), ("burstIntervalTicks", "int")),
        "defaults": {"range": 1000.0, "cooldownTurns": 1,
                     "detonationDelayTicks": 0, "subsystemDamage": 8.0,
                     "recoveryTicks": 240, "disableSquadronTicks": 360,
                     "fighterEvadeDebuffTicks": 360,
                     "chargesPerCooldown": 4, "targetRange": 4000.0,
                     "burstIntervalTicks": 10},
    },
}


# Every component the ship writer can add, with the label the Ships tab shows for it as an installed ability. Built from
# the same definitions the writer uses, so a new ability cannot be left out (tests/test_jamming.py checks it).
EDITABLE_ABILITY_MODULES = {
    "RegenerativeHullResinModule": "Hull and armour regeneration",
    "MunitionFactoryModule": "Munition Factory",
    **{cls: definition["label"] for cls, definition in CUSTOM_MODULE_DEFINITIONS.items()},
    "MiniJumpAgent": "Micro-FTL",
    "PredictiveAuguryModule": "Predictive Augury",
    "StealthModule": "Stealth",
    "Jammer": "Jamming",
    **{module: ability.replace("_", " ").capitalize()
       for ability, (module, _, _) in SUPPORT_ABILITIES.items()},
}
# per-ship jamming penalties (deadlock-jamming.txt, read by the custom-module DLL's CustomJamming)
JAM_PENALTY_OPTIONS = {"jam_speed": "speed", "jam_evade": "evade", "jam_accuracy": "accuracy"}


def bundle_ship_type(path):
    """ShipInfo.ShipType of a ship bundle's prefab"""
    bundle = read_bundle(path)
    offset, size, *_ = bundle["nodes"][0]
    prefab = SerFile(bundle["data"][offset:offset + size])
    scripts = {obj.pid: prefab.read(obj)[0]["m_ClassName"] for obj in prefab.objs if obj.classid == 115}
    types = {prefab.read(obj)[0]["ShipType"] for obj in prefab.objs if obj.classid == 114
             and scripts.get(prefab.read(obj)[0].get("m_Script", {}).get("m_PathID")) == "ShipInfo"
             and "ShipType" in prefab.read(obj)[0]}
    if len(types) != 1:
        raise ValueError("could not tell the bundle's ship type (%d ShipInfo types)" % len(types))
    return int(types.pop())


def set_ship_jamming(bundle_path, folder, sources, values):
    """write the ship's per-jammer penalties into folder/deadlock-jamming.txt (starting from the first existing file
    among folder and `sources`). values: {"speed"/"evade"/"accuracy": number or None (= global)}. Returns a log line."""
    from deadlock_tools import jamming as jm
    ship = bundle_ship_type(bundle_path)
    current = next((os.path.join(d, jm.FILE) for d in [folder, *sources] if d and os.path.isfile(os.path.join(d, jm.FILE))), None)
    overrides, log = jm.set_override(jm.read_overrides(current), "ship", ship, values)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, jm.FILE), "wb") as stream:
        stream.write(jm.render(overrides))
    return (log[0] if log else "jamming effect of ship %d: no change" % ship)


def upgrade_instant_plating(prefab):
    """Give existing six-field Instant Plating prefabs their default one-turn cooldown."""
    cls = "CustomInstantPlatingModule"
    script_ids = {obj.pid for obj in prefab.objs if obj.classid == 115
                  and prefab.read(obj)[0]["m_ClassName"] == cls}
    if not script_ids:
        return prefab
    by_type = {}
    for obj in prefab.objs:
        if obj.classid != 114 or obj.size < 32:
            continue
        file_id, script = struct.unpack_from("<iq", prefab.raw(obj), 16)
        if file_id == 0 and script in script_ids:
            by_type.setdefault((obj.tidx, script), []).append(obj)
    replaced = {}
    definition = CUSTOM_MODULE_DEFINITIONS[cls]
    for (type_index, script), objects in by_type.items():
        original = prefab.types[type_index]
        field_names = [node.name for node in original.nodes if node.level == 1 and not node.name.startswith("m_")]
        if "activationDelayTicks" in field_names:
            continue
        if tuple(field_names) not in (INSTANT_PLATING_FIELDS, INSTANT_PLATING_FIELDS + ("cooldownTurns",)) or prefab.prefix.count(original.raw) != 1:
            raise ValueError("Unrecognised Instant Plating type tree.")
        raw, nodes, properties_hash = _custom_module_type_raw(original, cls, definition["fields"], original.script_idx)
        prefab.prefix = prefab.prefix.replace(original.raw, raw, 1)
        for obj in objects:
            fields, size = prefab.read(obj)
            if size != obj.size:
                raise ValueError("Unrecognised Instant Plating data size.")
            fields.setdefault("cooldownTurns", 1)
            fields["activationDelayTicks"] = 0
            output = bytearray()
            prefab._wr(nodes, 0, fields, output)
            replaced[obj.pid] = output
        script_obj = prefab.by_pid[script]
        script_fields = prefab.read(script_obj)[0]
        script_fields["m_PropertiesHash"] = {f"bytes[{i}]": value for i, value in enumerate(properties_hash)}
        replaced[script] = prefab.write(script_obj, script_fields)
    return SerFile(prefab.rebuild(replaced=replaced)) if replaced else prefab


def upgrade_custom_emp_module(prefab):
    """Append newly introduced trailing config fields to existing EMP modules."""
    classes = {obj.pid: prefab.read(obj)[0].get("m_ClassName")
               for obj in prefab.objs if obj.classid == 115}
    by_type = {}
    for obj in prefab.objs:
        if obj.classid != 114 or obj.size < 32:
            continue
        file_id, script = struct.unpack_from("<iq", prefab.raw(obj), 16)
        cls = classes.get(script) if file_id == 0 else None
        if cls in ("CustomEmpPulseModule", "CustomTargetedEmpModule"):
            by_type.setdefault((obj.tidx, script, cls), []).append(obj)
    replaced = {}
    changed = False
    for (type_index, script, cls), objects in by_type.items():
        original = prefab.types[type_index]
        field_names = [node.name for node in original.nodes
                       if node.level == 1 and not node.name.startswith("m_")]
        definition = CUSTOM_MODULE_DEFINITIONS[cls]
        declared = [name for name, _ in definition["fields"]]
        if field_names == declared:
            continue
        if field_names != declared[:len(field_names)] or len(field_names) == len(declared):
            raise ValueError(f"Unrecognised {definition['label']} type tree.")
        if prefab.prefix.count(original.raw) != 1:
            raise ValueError(f"Ambiguous {definition['label']} type tree.")
        raw, nodes, properties_hash = _custom_module_type_raw(
            original, cls, definition["fields"], original.script_idx)
        prefab.prefix = prefab.prefix.replace(original.raw, raw, 1)
        missing = declared[len(field_names):]
        for obj in objects:
            fields, size = prefab.read(obj)
            if size != obj.size:
                raise ValueError(f"Unrecognised {definition['label']} data size.")
            for name in missing:
                fields[name] = definition["defaults"][name]
            output = bytearray()
            prefab._wr(nodes, 0, fields, output)
            replaced[obj.pid] = output
        script_obj = prefab.by_pid[script]
        script_fields = prefab.read(script_obj)[0]
        script_fields["m_PropertiesHash"] = {
            f"bytes[{i}]": value for i, value in enumerate(properties_hash)}
        replaced[script] = prefab.write(script_obj, script_fields)
        changed = True
    return SerFile(prefab.rebuild(replaced=replaced)) if changed else prefab


def prefab_vector(value):
    return (isinstance(value, dict) and set(value) == {"x", "y", "z"}
            and all(type(axis) in (float, int) for axis in value.values()))


def prefab_range(value):
    return (isinstance(value, dict) and set(value) == {"Min", "Max"}
            and all(type(endpoint) in (float, int) for endpoint in value.values()))


EDITABLE_SHIP_SUBSYSTEMS = {
    "CIC": "CIC",
    "Navigation": "Navigation",
    "FireControl": "Fire Control",
    "Engineering": "Engineering",
    "TechLab": "Tech Bay",
    "Hangar": "Hangar",
    "Armory": "Armoury",
    "RepairCrew": "Repair Crew",
}
SUBSYSTEM_SWITCHES = {"TechLab.UseHackingStrength", "TechLab.UseMiniJumpRange"}


def ship_prefab_fields(prefab):
    """Find editable fields on the main ship, its abilities, and its subsystems."""
    from math import isfinite
    scripts = {obj.pid: prefab.read(obj)[0]["m_ClassName"]
               for obj in prefab.objs if obj.classid == 115}
    ships = []
    for obj in prefab.objs:
        if obj.classid == 114:
            fields = prefab.read(obj)[0]
            if scripts.get(fields.get("m_Script", {}).get("m_PathID")) == "ShipInfo":
                ships.append((obj, fields))
    if not ships:
        return None, {}, {}
    _, ship = max(ships, key=lambda item: len(prefab.read(
        prefab.by_pid[item[1]["m_GameObject"]["m_PathID"]])[0]["m_Component"]))
    owner_pid = ship["m_GameObject"]["m_PathID"]
    owner = prefab.read(prefab.by_pid[owner_pid])[0]
    transforms = {obj.pid: prefab.read(obj)[0] for obj in prefab.objs if obj.classid == 4}
    root_transform = next((ref["component"]["m_PathID"] for ref in owner["m_Component"]
                           if ref["component"]["m_PathID"] in transforms), None)
    descendants = {owner_pid}
    pending = [root_transform] if root_transform is not None else []
    visited = set()
    while pending:
        pid = pending.pop()
        if pid in visited:
            continue
        visited.add(pid)
        fields = transforms[pid]
        descendants.add(fields["m_GameObject"]["m_PathID"])
        pending.extend(child["m_PathID"] for child in fields["m_Children"] if child["m_PathID"] in transforms)
    result = {}
    abilities = {}
    repair_bays = 0
    owner_components = [prefab.by_pid[ref["component"]["m_PathID"]]
                        for ref in owner["m_Component"] if not ref["component"]["m_FileID"]
                        and ref["component"]["m_PathID"] in prefab.by_pid]
    owner_ids = {obj.pid for obj in owner_components}
    for obj in owner_components + [obj for obj in prefab.objs if obj.classid == 114 and obj.pid not in owner_ids]:
        if obj.classid != 114:
            continue
        fields = prefab.read(obj)[0]
        script = scripts.get(fields.get("m_Script", {}).get("m_PathID"))
        subsystem = script in EDITABLE_SHIP_SUBSYSTEMS
        if obj.pid not in owner_ids and (
                not subsystem or fields["m_GameObject"]["m_PathID"] not in descendants):
            continue
        group = script
        if script == "TurnaroundRepairBayModule":
            repair_bays += 1
            if repair_bays > 1:
                group = f"{script}[{repair_bays}]"
        if script in EDITABLE_ABILITY_MODULES:
            label = EDITABLE_ABILITY_MODULES[script]
            abilities[group] = f"{label} {repair_bays}" if group != script else label
        elif not subsystem and script not in ("ShipInfo", "ArmourInfo"):
            continue
        for node in prefab.types[obj.tidx].nodes:
            if node.level != 1 or node.name.startswith("m_"):
                continue
            value = fields.get(node.name)
            if subsystem:
                if node.name == "Quadrant":
                    continue
                if prefab_range(value) or (isinstance(value, dict) and set(value) == {"Start", "End", "Power"}):
                    for endpoint, endpoint_value in value.items():
                        if type(endpoint_value) in (float, int) and isfinite(endpoint_value):
                            key = f"{group}.{node.name}.{endpoint}"
                            if key in result:
                                raise ValueError(f"ambiguous prefab field {key}")
                            result[key] = (obj, f"{node.name}.{endpoint}", "float", endpoint_value)
                    continue
                if type(value) is float and not isfinite(value):
                    continue
            if type(value) not in (int, float, bool) and not prefab_vector(value):
                continue
            key = f"{group}.{node.name}"
            if key in result:
                raise ValueError(f"ambiguous prefab field {key}")
            result[key] = (obj, node.name, node.type, value)
    return owner["m_Name"], result, abilities


def bundle_ship_stats(path):
    """Read editable prefab fields directly from a plain or encrypted bundle."""
    bundle = read_bundle(path)
    offset, size, *_ = bundle["nodes"][0]
    prefab = upgrade_custom_emp_module(upgrade_instant_plating(SerFile(bundle["data"][offset:offset + size])))
    name, fields, abilities = ship_prefab_fields(prefab)
    scripts = {obj.pid: prefab.read(obj)[0]["m_ClassName"] for obj in prefab.objs if obj.classid == 115}
    game_objects = {obj.pid: prefab.read(obj)[0].get("m_Name", "") for obj in prefab.objs if obj.classid == 1}
    def object_fields(pid): return prefab.read(prefab.by_pid[pid])[0]
    def script_of(pid):
        return scripts.get(object_fields(pid).get("m_Script", {}).get("m_PathID"), "")
    action_menus = [pid for pid, obj_name in game_objects.items() if obj_name == "Actions"]
    root_fighters = []
    if len(action_menus) == 1:
        for entry in object_fields(action_menus[0]).get("m_Component", []):
            pid = entry["component"]["m_PathID"]
            if script_of(pid) == "SubMenuAction":
                target = object_fields(pid).get("SubMenu", {}).get("m_PathID")
                if game_objects.get(target) == "Fighters": root_fighters.append(target)
    squadron_groups = []
    if len(set(root_fighters)) == 1:
        def launch_count(menu_pid, seen=None):
            seen = set() if seen is None else seen
            if menu_pid in seen or menu_pid not in prefab.by_pid: return 0
            seen.add(menu_pid); count = 0
            for entry in object_fields(menu_pid).get("m_Component", []):
                pid = entry["component"]["m_PathID"]
                if pid not in prefab.by_pid or prefab.by_pid[pid].classid != 114: continue
                script = script_of(pid)
                if script == "LaunchFightersAction": count += 1
                elif script == "SubMenuAction":
                    count += launch_count(object_fields(pid).get("SubMenu", {}).get("m_PathID"), seen)
            return count
        root = root_fighters[0]
        for entry in object_fields(root).get("m_Component", []):
            pid = entry["component"]["m_PathID"]
            if pid not in prefab.by_pid or prefab.by_pid[pid].classid != 114 or script_of(pid) != "SubMenuAction": continue
            count = launch_count(object_fields(pid).get("SubMenu", {}).get("m_PathID"))
            if count: squadron_groups.append(count)
    # FireMissileAction components = munition slot buttons in the battle UI; they must match
    # the level ship record's MunitionSlots (see docs/game/bundle-level-agreement.md).
    munition_actions = sum(1 for obj in prefab.objs if obj.classid == 114 and script_of(obj.pid) == "FireMissileAction")
    from deadlock_tools import flak as flak_mod
    flak_info = flak_mod.read(prefab)
    if flak_info:
        flak_info.update(fields=[{"key": key, "kind": kind, "min": minimum, "help": text, "label": flak_mod.LABELS[key]}
                                 for key, (kind, minimum, text) in flak_mod.FIELDS.items()],
                         hull=flak_mod.hull(prefab),
                         stock=[{"ship": row[0], "hull": row[1], "armour": row[2], "distance": row[3], "length": row[4],
                                 "radius": row[5], "bursts": row[6], "armory": list(row[7])} for row in flak_mod.STOCK])
    return {"prefab": name, "abilities": abilities, "subsystems": EDITABLE_SHIP_SUBSYSTEMS, "squadron_groups": squadron_groups,
        "flak": flak_info,
        "munition_actions": munition_actions,
        "launch_actions": sum(1 for obj in prefab.objs if obj.classid == 114 and script_of(obj.pid) == "LaunchFightersAction"),
        "modifier_fields": [{"value": value, "label": label}
                            for value, label in CREW_MODIFIER_FIELDS.items()], "fields": [
        {"key": key, "value": value, "kind": "bool" if storage == "bool" or key in PREFAB_BOOLEAN_BYTES or key in SUBSYSTEM_SWITCHES else
         "vector" if prefab_vector(value) else "float" if isinstance(value, float) else "int"}
        for key, (_, _, storage, value) in fields.items()
    ]}


def apply_prefab_fields(prefab, changes, replaced):
    import math

    if not isinstance(changes, dict):
        raise ValueError("prefab fields must be a mapping of field names to values")
    _, fields, _ = ship_prefab_fields(prefab)
    updates = {}
    for key, value in changes.items():
        if key not in fields:
            raise ValueError(f"unknown editable prefab field {key}")
        obj, name, storage, original = fields[key]
        if prefab_vector(original):
            if not prefab_vector(value) or any(not math.isfinite(axis) for axis in value.values()):
                raise ValueError(f"{key} requires finite X, Y, and Z coordinates")
        elif storage == "bool" or key in PREFAB_BOOLEAN_BYTES or key in SUBSYSTEM_SWITCHES:
            if type(value) is not bool:
                raise ValueError(f"{key} requires a checkbox value")
            if storage != "bool":
                value = int(value)
        elif isinstance(original, int):
            if type(value) is not int:
                raise ValueError(f"{key} requires a whole number")
        elif type(value) not in (float, int) or not math.isfinite(value):
            raise ValueError(f"{key} requires a finite number")
        if key.split(".", 1)[0] in EDITABLE_SHIP_SUBSYSTEMS and name == "MaxHealth" and value <= 0:
            raise ValueError(f"{key} must be greater than 0")
        if name == "fullRepairPercent" and not 0 <= value <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
        if name == "modifierField" and value not in CREW_MODIFIER_FIELDS:
            raise ValueError(f"{key} must select a supported crew-veterancy modifier")
        if name == "baseTicksPerReplacement" and value < 1:
            raise ValueError(f"{key} must be at least 1")
        if key == "CustomInstantPlatingModule.cooldownTurns" and not 1 <= value <= 999:
            raise ValueError("Instant Plating cooldown must be between 1 and 999 turns.")
        if name == "capacityMultiplier" and value <= 0:
            raise ValueError(f"{key} must be greater than 0")
        nonnegative = {"MaxHullPoints", "batteryMax", "maxAmmunition", "marinesPerRaptor",
                       "jumpDelay", "jumpDuration", "jumpRangeHorizontal", "jumpRangeVertical",
                       "cooldownTurns", "destealthCooldownTurns", "swapCooldown", "swapTime",
                       "deployRange", "platingCapacity", "dronePlatingCapacity", "maxPlating",
                       "platingDeploymentRate", "sparePartsSupply", "sparePartsPerTick", "resupplySeconds",
                       "minimumSubsystemRepairBoost", "boostValue", "baseRate",
                       "modifierRate", "basePlatingPerTick", *INSTANT_PLATING_FIELDS}
        if (name in nonnegative or name.startswith("defaultArmour") or
                name.startswith("MunitionResupplyRange.")) and value < 0:
            raise ValueError(f"{key} cannot be negative")
        updates.setdefault(obj.pid, {})[name] = value
    for pid, values in updates.items():
        obj = prefab.by_pid[pid]
        fields = (prefab._rd(prefab.types[obj.tidx].nodes, 0, replaced[pid], [0])[0]
                  if pid in replaced else prefab.read(obj)[0])
        for name, value in values.items():
            path = name.split(".")
            target = fields
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
        try:
            replaced[pid] = prefab.write(obj, fields)
        except (OverflowError, struct.error) as error:
            raise ValueError(f"prefab value outside its storage range: {', '.join(values)}") from error


def apply_ship_scale(prefab, target, replaced):
    """Set the whole ship's scale to target (1 = the stock size, 1.25 = 25% bigger; running it again with the same value
    changes nothing): the root Transform of the ShipInfo GameObject, so the model,
    turrets, launch bays, collision zones, engines, boarding points and hit/miss points (all children or local-space
    data) grow together. Values the game uses as world distances are scaled as well:
    ShipInfo.CollisionExclusionZoneRadius and WeaponsTarget.missSphereRadius. See docs/game/ship-scale.md."""
    import math
    if not (isinstance(target, (int, float)) and math.isfinite(target) and 0.1 <= target <= 10):
        raise ValueError("--ship-scale must be between 0.1 and 10")
    scripts = {obj.pid: prefab.read(obj)[0]["m_ClassName"] for obj in prefab.objs if obj.classid == 115}
    def current(obj):
        return prefab._rd(prefab.types[obj.tidx].nodes, 0, replaced[obj.pid], [0])[0] if obj.pid in replaced else prefab.read(obj)[0]
    ships = [(obj, current(obj)) for obj in prefab.objs if obj.classid == 114
             and scripts.get(prefab.read(obj)[0].get("m_Script", {}).get("m_PathID")) == "ShipInfo"]
    if not ships:
        raise ValueError("this bundle has no ShipInfo component to scale")
    obj, ship = max(ships, key=lambda item: len(prefab.read(prefab.by_pid[item[1]["m_GameObject"]["m_PathID"]])[0]["m_Component"]))
    owner_pid = ship["m_GameObject"]["m_PathID"]
    owner = prefab.read(prefab.by_pid[owner_pid])[0]
    root = next((prefab.by_pid[ref["component"]["m_PathID"]] for ref in owner["m_Component"]
                 if ref["component"]["m_PathID"] in prefab.by_pid and prefab.by_pid[ref["component"]["m_PathID"]].classid == 4), None)
    if root is None:
        raise ValueError("the ship GameObject has no Transform")
    transform = current(root)
    old = dict(transform["m_LocalScale"])
    if not all(abs(old[a] - old["x"]) < 1e-6 for a in "yz") or old["x"] <= 0:
        raise ValueError("the ship root has a non-uniform scale %s; refusing to rescale it" % old)
    factor = target / old["x"]
    if abs(factor - 1) < 1e-9:
        print("ship scale is already %g; nothing to change" % target)
        return target
    transform["m_LocalScale"] = {axis: target for axis in old}
    replaced[root.pid] = prefab.write(root, transform)
    ship["CollisionExclusionZoneRadius"] = ship["CollisionExclusionZoneRadius"] * factor
    replaced[obj.pid] = prefab.write(obj, ship)
    # Weapons keep their size: the game spawns each turret as a child of its WeaponMountPoint with
    # localScale = MeshScale (ShipInfo spawn code), so it would inherit the root scale. Counter-scale every mount
    # transform; mount POSITIONS still move outwards with the hull, MeshScale keeps meaning "turret size".
    compensated = 0
    for mount in prefab.objs:
        if mount.classid != 114:
            continue
        mb = prefab.read(mount)[0]
        if not all(key in mb for key in ("WeaponType", "Quadrant", "InitialAngles", "MeshScale")):
            continue
        go = prefab.read(prefab.by_pid[mb["m_GameObject"]["m_PathID"]])[0]
        mount_tr = next((prefab.by_pid[ref["component"]["m_PathID"]] for ref in go["m_Component"]
                         if ref["component"]["m_PathID"] in prefab.by_pid and prefab.by_pid[ref["component"]["m_PathID"]].classid == 4), None)
        if mount_tr is None:
            continue
        fields = current(mount_tr)
        fields["m_LocalScale"] = {axis: value / factor for axis, value in fields["m_LocalScale"].items()}
        replaced[mount_tr.pid] = prefab.write(mount_tr, fields)
        compensated += 1
    log = ["%d weapon mount(s) counter-scaled so turrets keep their size" % compensated,
           "root scale %s -> %s" % (",".join("%g" % old[a] for a in "xyz"), ",".join("%g" % transform["m_LocalScale"][a] for a in "xyz")),
           "CollisionExclusionZoneRadius -> %g" % ship["CollisionExclusionZoneRadius"]]
    transforms = {o.pid: prefab.read(o)[0] for o in prefab.objs if o.classid == 4}
    in_ship, pending = set(), [root.pid]
    while pending:                                          # every GameObject under the ship root
        pid = pending.pop()
        in_ship.add(transforms[pid]["m_GameObject"]["m_PathID"])
        pending.extend(c["m_PathID"] for c in transforms[pid]["m_Children"] if c["m_PathID"] in transforms)
    for component in prefab.objs:
        if component.classid != 114 or prefab.read(component)[0]["m_GameObject"]["m_PathID"] not in in_ship:
            continue
        fields = current(component)
        if scripts.get(fields.get("m_Script", {}).get("m_PathID")) == "WeaponsTarget":
            fields["missSphereRadius"] = fields["missSphereRadius"] * factor
            replaced[component.pid] = prefab.write(component, fields)
            log.append("missSphereRadius -> %g" % fields["missSphereRadius"])
    print("scaled ship %s to %g (x%g): %s" % (owner["m_Name"], target, factor, "; ".join(log)))
    return target


def respace_clones(prefab, RESS, replaced, selector, gap):
    """Move the existing clones (mounts named <original>_x2, _x3 ...) of the turrets matching selector so that every
    turret in the bow/quadrant is at least gap GAME units from every other (gap is divided by the ship's root scale, so it
    is measured in game units after --ship-scale). Originals stay put. Each clone stays on the same hull surface as its
    original (same height above the mesh), keeps the original's rotation, and mirror twins are placed together."""
    import math, re as _re
    def current(obj):
        return prefab._rd(prefab.types[obj.tidx].nodes, 0, replaced[obj.pid], [0])[0] if obj.pid in replaced else prefab.read(obj)[0]
    value = lambda pid: current(prefab.by_pid[pid])
    hq = build_hull_query(prefab, RESS, lambda pid: prefab.read(prefab.by_pid[pid])[0])
    if hq is None:
        raise ValueError("--respace needs a readable hull mesh in the bundle")
    quads = ["port", "starboard", "fore", "rear", "dorsal", "ventral"]
    mounts = []
    for mount in weapon_mounts(prefab, lambda pid: prefab.read(prefab.by_pid[pid])[0]):
        owner, transform = mount_objects(prefab, lambda pid: prefab.read(prefab.by_pid[pid])[0], mount)
        mb = value(mount.pid); name = value(owner.pid)["m_Name"]; tr = current(transform)
        quad = quads[mb["Quadrant"]] if 0 <= mb["Quadrant"] < len(quads) else str(mb["Quadrant"])
        p = tr["m_LocalPosition"]
        mounts.append({"name": name, "quad": quad, "type": mb["WeaponType"], "transform": transform, "tr": tr, "pos": (p["x"], p["y"], p["z"])})
    sel = selector.lower()
    group = [m for m in mounts if sel in ("all", m["quad"]) or m["name"].lower().startswith(sel)]
    if not group:
        raise ValueError("--respace: no turrets match %r" % selector)
    by_name = {m["name"]: m for m in mounts}
    originals = [m for m in group if not _re.search(r"_x\d+$", m["name"])]
    clones = [m for m in group if _re.search(r"_x\d+$", m["name"])]
    if not clones:
        print("respace %s: no clones to move" % selector); return
    # root scale: the game-unit gap becomes a local gap
    owner_scale = 1.0
    for m in mounts[:1]:
        pid = m["transform"].pid
        while pid:
            t = current(prefab.by_pid[pid]); owner_scale *= t["m_LocalScale"]["x"] if pid != m["transform"].pid else 1.0
            pid = t["m_Father"]["m_PathID"]
    local_gap = gap / owner_scale
    def surface(x, y, z):
        ys = hq(x, z)
        return min(ys, key=lambda v: abs(v - y)) if ys else None
    surf = {}
    for m in originals:
        sf = surface(*m["pos"]); surf[m["name"]] = (sf, m["pos"][1] - sf) if sf is not None else None
    def twin(m):
        x, y, z = m["pos"]
        if abs(x) < 0.05: return None
        return next((o for o in originals if o is not m and abs(o["pos"][0] + x) < 0.3 and abs(o["pos"][1] - y) < 0.3 and abs(o["pos"][2] - z) < 0.3), None)
    occupied = [m["pos"] for m in mounts if m not in clones]
    def valid(src, x, z, occ):
        sv = surf.get(src["name"])
        if not sv: return None
        ys = hq(x, z)
        if not ys: return None
        sf = min(ys, key=lambda v: abs(v - sv[0]))
        r = math.hypot(x - src["pos"][0], z - src["pos"][2])
        if abs(sf - sv[0]) > min(0.5 + 0.35 * r, 3.0): return None
        cand = (x, sf + sv[1], z)
        return None if any(math.dist(cand, q) < local_gap - 1e-6 for q in occ) else cand
    moved, done = {}, set()
    for clone in sorted(clones, key=lambda m: (-by_name[_re.sub(r"_x\d+$", "", m["name"])]["pos"][2], m["name"])):
        if clone["name"] in done: continue
        src = by_name.get(_re.sub(r"_x\d+$", "", clone["name"]))
        if src is None: continue
        suffix = clone["name"][len(src["name"]):]
        tw = twin(src); mate = by_name.get(tw["name"] + suffix) if tw else None
        centre = abs(src["pos"][0]) < 0.05
        best = None
        for ri in range(1, 200):
            r = 0.25 * ri
            for k in range(48):
                a = 2 * math.pi * k / 48
                dx, dz = r * math.cos(a), r * math.sin(a)
                if centre and abs(dx) > 1e-6: continue
                cand = valid(src, src["pos"][0] + dx, src["pos"][2] + dz, occupied)
                if not cand: continue
                if mate is not None:
                    mirror = (-cand[0], cand[1], cand[2])
                    if math.dist(cand, mirror) < local_gap - 1e-6 or not valid(tw, mirror[0], mirror[2], occupied + [cand]): continue
                best = cand; break
            if best: break
        if not best:
            raise ValueError("--respace: no free hull spot %.2f units from every turret for %s; use a smaller gap" % (gap, clone["name"]))
        moved[clone["name"]] = best; occupied.append(best); done.add(clone["name"])
        if mate is not None:
            mirror = (-best[0], best[1], best[2]); moved[mate["name"]] = mirror; occupied.append(mirror); done.add(mate["name"])
    for name, (x, y, z) in moved.items():
        m = by_name[name]; tr = m["tr"]
        tr["m_LocalPosition"] = {"x": x, "y": y, "z": z}
        replaced[m["transform"].pid] = prefab.write(m["transform"], tr)
    final = [moved.get(m["name"], m["pos"]) for m in group]
    closest = min(math.dist(a, b) for i, a in enumerate(final) for b in final[i + 1:]) * owner_scale
    print("respaced %d %s clone(s): closest turret pair now %.2f game units (asked %.2f)" % (len(moved), selector, closest, gap))


def print_bundle_settings(objects, value):
    """Show stored prefab values alongside the weapon mount list."""
    scripts = {o.pid: value(o.pid)["m_ClassName"] for o in objects if o.classid == 115}
    components = {}
    for obj in objects:
        if obj.classid == 114:
            fields = value(obj.pid)
            script = scripts.get(fields.get("m_Script", {}).get("m_PathID"))
            if script:
                components.setdefault(script, []).append(fields)

    print("\nPrefab settings")
    for bay in sorted(components.get("LaunchPoint", []), key=lambda fields: fields["SlotNo"]):
        if not bay["CanLaunch"]:
            continue
        name = value(bay["m_GameObject"]["m_PathID"])["m_Name"]
        icon = bay["iconPosition"]
        coordinates = "no local marker"
        if not icon["m_FileID"] and icon["m_PathID"]:
            position = value(icon["m_PathID"])["m_LocalPosition"]
            coordinates = ",".join(f"{position[axis]:g}" for axis in "xyz")
        print(f"Launch bay {bay['SlotNo']} ({name}): types {bay['LaunchCompatibility']}, icon {coordinates}")
    for label, script in (("Munition actions", "FireMissileAction"), ("Squadron launch actions", "LaunchFightersAction")):
        print(f"{label}: {len(components.get(script, []))}")
    def nested_value(fields, key):
        value = fields
        for part in key.split("."):
            value = value[part]
        return value

    def has_nested_value(fields, key):
        try:
            nested_value(fields, key)
            return True
        except (KeyError, TypeError):
            return False

    for label, script, keys in (
        ("Hull regeneration", "RegenerativeHullResinModule", ("hullPerTick",)),
        ("Munition Factory", "MunitionFactoryModule", ()),
        ("Armory munition resupply range", "Armory", ("MunitionResupplyRange.Min", "MunitionResupplyRange.Max")),
        ("Micro-FTL", "MiniJumpAgent", ("jumpDelay", "jumpDuration", "jumpRangeHorizontal", "jumpRangeVertical", "cooldownTurns")),
        ("Predictive Augury", "PredictiveAuguryModule", ("maxAmmunition",)),
        ("Stealth", "StealthModule", ("batteryMax", "activeByDefault", "destealthCooldownTurns")),
        ("Turnaround repair bay", "TurnaroundRepairBayModule", ("fullRepairPercent", "moveOnLaunchSquadronOffset")),
        ("Targeted resupply", "ResupplyMunitionModule", ("deployRange", "swapTime", "swapCooldown")),
        ("Deploy plating", "DeployPlatingModule", ("deployRange", "platingCapacity", "dronePlatingCapacity", "platingDeploymentRate", "maxPlating")),
        ("Technical support", "JuryRigBoosterModule", ("deployRange", "boostValue", "minimumSubsystemRepairBoost", "sparePartsSupply", "sparePartsPerTick", "unlimitedSpareParts", "resupplySeconds")),
        ("Marine reinforcement", "ReinforcementModule", ("marinesPerRaptor",)),
        ("Custom armour repair", "CustomArmourRepairModule", ("baseRate", "modifierRate", "modifierField")),
        ("Instant plating", "CustomInstantPlatingModule", (*INSTANT_PLATING_FIELDS, "cooldownTurns")),
        ("Custom squadron replacement", "CustomSquadronReplacementModule", ("baseTicksPerReplacement", "modifierRate", "modifierField")),
        ("Custom drone resupply", "CustomDroneResupplyModule", ("basePlatingPerTick", "modifierRate", "modifierField")),
        ("Custom magazine capacity", "CustomMagazineCapacityModule", ("capacityMultiplier", "modifierRate", "modifierField")),
        ("Experimental EMP", "CustomEmpPulseModule", ("range", "cooldownTurns", "detonationDelayTicks", "subsystemDamage", "recoveryTicks", "disableSquadronTicks", "fighterEvadeDebuffTicks")),
        ("Targeted EMP", "CustomTargetedEmpModule", ("range", "detonationDelayTicks", "subsystemDamage", "recoveryTicks", "disableSquadronTicks", "chargesPerCooldown", "targetRange", "fighterEvadeDebuffTicks", "burstIntervalTicks")),
    ):
        entries = components.get(script, [])
        if not entries:
            print(f"{label}: absent")
            continue
        for fields in entries:
            detail = ", ".join(f"{key} = {nested_value(fields, key)}"
                                for key in keys if has_nested_value(fields, key))
            print(f"{label}: {detail or 'present'}")


# SquadronShipType values used by the supported Deadlock assemblies.
FIGHTER_SQUADRON_TYPES = {0, 1, 5, 100, 101, 105, 110, 113, 114}
UTILITY_SQUADRON_TYPES = {2, 3, 4, 102, 103, 104, 106, 107, 108, 109, 111, 112}


def bay_alignment(launch_bays, fighter_slots, utility_slots):
    """Use compatible types already present on this ship, split by the database slot order."""
    available = set().union(*(bay["LaunchCompatibility"] for bay in launch_bays))
    fighter = sorted(available & FIGHTER_SQUADRON_TYPES)
    utility = sorted(available & UTILITY_SQUADRON_TYPES)
    if available - FIGHTER_SQUADRON_TYPES - UTILITY_SQUADRON_TYPES:
        raise ValueError("launch bays contain unknown squadron types")
    if (fighter_slots and not fighter) or (utility_slots and not utility):
        raise ValueError("the ship needs an existing compatibility list for each requested slot class")
    return fighter, utility


def mirror_fighter_bays(prefab, fighter_slots):
    """Mirror adjacent fighter bay pairs in their common launch-point frame."""
    if fighter_slots is None or fighter_slots < 2:
        raise ValueError("Mirroring fighter launch bays requires at least two fighter slots.")
    bays = {}
    def read(pid):
        return prefab.read(prefab.by_pid[pid])[0]

    for obj in prefab.objs:
        if obj.classid != 114:
            continue
        fields = read(obj.pid)
        slot = fields.get("SlotNo")
        if slot not in range(fighter_slots) or not fields.get("CanLaunch") or "LaunchPoints" not in fields:
            continue
        if slot in bays:
            raise ValueError(f"Fighter slot {slot} has multiple launch bays.")
        _, transform = mount_objects(prefab, read, obj)
        bays[slot] = obj, fields, transform, read(transform.pid)

    changes = {}
    occupied = set()
    for slot in range(0, fighter_slots - 1, 2):
        if slot not in bays or slot + 1 not in bays:
            raise ValueError(f"Cannot mirror missing launch bays for fighter slots {slot} and {slot + 1}.")
        left, right = bays[slot], bays[slot + 1]
        if left[3]["m_Father"] != right[3]["m_Father"]:
            raise ValueError(f"Fighter slots {slot} and {slot + 1} do not share a launch-point frame.")
        candidates = sorted((left, right), key=lambda bay: bay[3]["m_LocalPosition"]["x"] >= 0)
        for candidate in candidates:
            pos = candidate[3]["m_LocalPosition"]
            key = tuple(round(value, 4) for value in (abs(pos["x"]), pos["y"], pos["z"]))
            if key not in occupied:
                break
        occupied.add(key)
        source, source_transform = copy.deepcopy((candidate[1], candidate[3]))
        if abs(pos["x"]) < 1e-5:
            raise ValueError(f"Fighter slots {slot} and {slot + 1} need an off-centre launch bay.")
        for side, (obj, fields, transform, tr) in ((-1, left), (1, right)):
            mirrored = side * pos["x"] < 0
            tr["m_LocalPosition"] = dict(pos, x=side * abs(pos["x"]))
            rotation = source_transform["m_LocalRotation"]
            tr["m_LocalRotation"] = {axis: (-value if mirrored and axis in ("y", "z") else value)
                                     for axis, value in rotation.items()}
            tr["m_LocalScale"] = source_transform["m_LocalScale"]
            fields["LaunchPoints"] = [dict(point, x=-point["x"]) if mirrored else point
                                      for point in source["LaunchPoints"]]
            fields["OverrideLaunchPointRotation"] = source["OverrideLaunchPointRotation"]
            fields["LaunchPointRotation"] = [dict(point, y=-point["y"], z=-point["z"])
                                             if mirrored else point for point in source["LaunchPointRotation"]]
            changes[obj.pid] = prefab.write(obj, fields)
            changes[transform.pid] = prefab.write(transform, tr)
        print(f"mirrored fighter launch bays for slots {slot} and {slot + 1}")
    return prefab.rebuild(replaced=changes)


def parse_bay_icons(entries):
    from math import isfinite

    positions = {}
    for entry in entries:
        slot, coordinates = entry.split("=", 1)
        position = tuple(map(float, coordinates.split(",")))
        slot = int(slot)
        if slot < 0 or len(position) != 3 or not all(map(isfinite, position)):
            raise ValueError("--bay-icon expects SLOT=X,Y,Z with a non-negative slot and finite coordinates")
        positions[slot] = dict(zip("xyz", position))
    return positions


MOUNT_QUADRANTS = ("port", "starboard", "fore", "rear", "dorsal", "ventral")


def weapon_mounts(prefab, value):
    keys = {"WeaponType", "Quadrant", "InitialAngles"}
    return [obj for obj in prefab.objs if obj.classid == 114 and keys <= value(obj.pid).keys()]


def mount_objects(prefab, value, mount):
    owner = prefab.by_pid[value(mount.pid)["m_GameObject"]["m_PathID"]]
    transform = next(prefab.by_pid[entry["component"]["m_PathID"]]
                     for entry in value(owner.pid)["m_Component"]
                     if prefab.by_pid[entry["component"]["m_PathID"]].classid == 4)
    return owner, transform


def layout_revision(bundle):
    from hashlib import sha256
    return sha256(bundle["data"]).hexdigest()


def bundle_mount_layout(path):
    from functools import lru_cache

    bundle = read_bundle(path)
    offset, size, *_ = bundle["nodes"][0]
    prefab = SerFile(bundle["data"][offset:offset + size])

    @lru_cache(None)
    def value(pid):
        return prefab.read(prefab.by_pid[pid])[0]

    @lru_cache(None)
    def parent_matrix(pid):
        return transform_matrix(pid, value)

    mounts = []
    for mount in weapon_mounts(prefab, value):
        owner, transform = mount_objects(prefab, value, mount)
        tr, component = value(transform.pid), value(mount.pid)
        position = tr["m_LocalPosition"]
        parent = tr["m_Father"]["m_PathID"]
        matrix = parent_matrix(parent)
        mounts.append({
            "id": str(mount.pid), "name": value(owner.pid)["m_Name"],
            "weapon": component["WeaponType"], "quadrant": component["Quadrant"],
            "scale": component["MeshScale"], "position": position,
            "angles": component["InitialAngles"], "matrix": matrix,
            "inverse": inverse_transform(matrix),
            "can_clone": not tr["m_Children"] and parent != 0
                and len(value(owner.pid)["m_Component"]) == 2,
        })
    resource = next(((off, length) for off, length, _, name in bundle["nodes"] if name.endswith(".resS")), None)
    hull = []
    if resource and mounts:
        off, length = resource
        vertices, _ = hull_geometry(prefab, bundle["data"][off:off + length], value, ship_space=True)
        hull = [vertex for vertex in vertices if all(-1e6 <= coordinate <= 1e6 for coordinate in vertex)]
        hull = hull[::max(1, (len(hull) + 3999) // 4000)]
    return {"revision": layout_revision(bundle), "mounts": mounts, "quadrants": MOUNT_QUADRANTS, "hull": hull}


def layout_mount_edits(spec, bundle, prefab, mounts, value, new_pid):
    if not isinstance(spec, dict) or spec.get("revision") != layout_revision(bundle):
        raise ValueError("The source bundle changed. Reload the weapon layout before saving.")
    changes = spec.get("changes")
    if not isinstance(changes, list) or not changes or len(changes) > 1024:
        raise ValueError("Expected between 1 and 1024 weapon layout edits.")
    by_id = {str(mount.pid): mount for mount in mounts}
    replaced, additions, children = {}, [], {}
    edited = set()
    names = {value(obj.pid)["m_Name"] for obj in prefab.objs if obj.classid == 1}

    def vector(row, key):
        coordinates = row.get(key)
        if (not isinstance(coordinates, dict) or coordinates.keys() != set("xyz")
                or any(type(n) not in (float, int) or not -1e6 <= n <= 1e6 for n in coordinates.values())):
            raise ValueError(f"Invalid mount {key}.")
        return coordinates

    for row in changes:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or row["id"] not in by_id or type(row.get("clone")) is not bool):
            raise ValueError("Unknown weapon mount in layout.")
        new_mount = row.get("new_mount", False)
        if type(new_mount) is not bool or (new_mount and not row["clone"]):
            raise ValueError("Invalid new turret edit.")
        mount = by_id[row["id"]]
        owner, transform = mount_objects(prefab, value, mount)
        mb = copy.deepcopy(value(mount.pid))
        tr = copy.deepcopy(value(transform.pid))
        weapon, quadrant, scale = row.get("weapon"), row.get("quadrant"), row.get("scale")
        if type(weapon) is not int or not 0 <= weapon <= 2147483647:
            raise ValueError("Invalid weapon type.")
        if type(quadrant) is not int or (not 0 <= quadrant < len(MOUNT_QUADRANTS) and quadrant != mb["Quadrant"]):
            raise ValueError("Invalid weapon quadrant.")
        if type(scale) not in (int, float) or not 0 <= scale <= 1e6 or (scale == 0 and mb["MeshScale"] != 0):
            raise ValueError("Weapon mesh scale must be positive.")
        mb.update(WeaponType=weapon, Quadrant=quadrant, MeshScale=scale, InitialAngles=vector(row, "angles"))
        tr["m_LocalPosition"] = vector(row, "position")
        if not row["clone"]:
            if mount.pid in edited:
                raise ValueError("Duplicate edit to one weapon mount.")
            edited.add(mount.pid)
            for obj, fields in ((mount, mb), (transform, tr)):
                if fields != value(obj.pid):
                    replaced[obj.pid] = prefab.write(obj, fields)
            continue
        go = copy.deepcopy(value(owner.pid))
        parent = tr["m_Father"]["m_PathID"]
        if not parent or tr["m_Children"] or len(go["m_Component"]) != 2:
            raise ValueError(f"{go['m_Name']} has a hierarchy that cannot be cloned.")
        prefix = "Turret_" if new_mount else f'{go["m_Name"]}_x'
        suffix = 1 if new_mount else 2
        while f"{prefix}{suffix}" in names:
            suffix += 1
        go["m_Name"] = f"{prefix}{suffix}"
        names.add(go["m_Name"])
        ngo, ntr, nmb = new_pid(), new_pid(), new_pid()
        for entry in go["m_Component"]:
            ref = entry["component"]
            ref["m_PathID"] = {transform.pid: ntr, mount.pid: nmb}[ref["m_PathID"]]
        tr["m_GameObject"]["m_PathID"] = ngo
        mb["m_GameObject"]["m_PathID"] = ngo
        for pid, obj, fields in ((ngo, owner, go), (ntr, transform, tr), (nmb, mount, mb)):
            additions.append((pid, obj.tidx, prefab.write(obj, fields)))
        children.setdefault(parent, []).append(ntr)
    return replaced, additions, children


def double_weapons(src, dst, offset=(1.5, 0.0, 0.0), copies=0, munition_slots=None, fighter_slots=None, utility_slots=None, bay_offset=(0.0, 3.0, 4.0), hull_regen=None, clones=(), list_mounts=False, clone_scale=None, scales=(), layout="offset", gap=1.5, mirror=True, munition_factory=False, hull_follow=True, min_gap=None, mini_jump=None, augury=None, stealth=None, slot_parts="buttons,bays,icons", ability_submenu=False, stealth_mesh="ship", squadron_groups=None, icon_offset=(12.0, 0.0, -4.0), inspect=False, support_abilities=None, align_launch_bays=False, bay_icons=None, support_settings=None, prefab_fields=None, ship_scale=None, respace=(), turnaround_repair_actions=1, custom_modules=(), mount_layout=None, normalize_ability_submenu=False, mirror_launch_bays=False, jamming=None, flak=None):
    B = read_bundle(src)
    (O1, S1, F1, N1), (O2, S2, F2, N2) = B["nodes"]
    CAB = B["data"][O1:O1 + S1]; RESS = B["data"][O2:O2 + S2]
    F = upgrade_custom_emp_module(upgrade_instant_plating(SerFile(CAB)))
    cache = {}
    def goname_of(p): return val(p)["m_Name"]
    def val(pid):
        if pid not in cache: cache[pid] = F.read(F.by_pid[pid])[0]
        return cache[pid]
    mounts = weapon_mounts(F, val)
    print(f"found {len(mounts)} weapon mount points")
    QUADS = MOUNT_QUADRANTS
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
    if list_mounts or inspect:
        print(f"{'mount':<22}{'quadrant':<10}{'type':<6}x      y      z")
        for o in sorted(mounts, key=lambda o: (mount_info(o)[1], mount_info(o)[0])):
            n, q, t, p = mount_info(o); print(f"{n:<22}{q:<10}{t:<6}{p['x']:<7.1f}{p['y']:<7.1f}{p['z']:.1f}")
        by = {}
        for o in mounts: by.setdefault((mount_info(o)[1], mount_info(o)[2]), []).append(o)
        print("\ngroups (quadrant, weapon type): " + "; ".join(f"{q} type {t} x{len(l)}" for (q, t), l in sorted(by.items())))
        if inspect: print_bundle_settings(F.objs, val)
        return
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
    if mount_layout is not None:
        if any(ncopy.values()) or scales:
            raise ValueError("Save the weapon layout separately from bulk clone and scale operations.")
        replaced, appended, children_add = layout_mount_edits(mount_layout, B, F, mounts, val, newpid)
        new_pids.extend(pid for pid, _, _ in appended)
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
    late_children = {}  # parent transform -> new child transforms
    # ---- extra carrier (fighter / support squadron) slots ----------------------------------------------------------
    # game rules (from the code): slots 0..FighterSlots-1 are fighter slots, the next UtilityFighterSlots are support slots;
    # the prefab needs ONE LaunchFightersAction per slot (UI/launch) and a LaunchPoint bay per SlotNo (falls back to any compatible bay).
    script_names = {obj.pid: val(obj.pid)["m_ClassName"] for obj in F.objs if obj.classid == 115}
    def named_menu(name):
        candidates = [obj.pid for obj in F.objs if obj.classid == 1 and val(obj.pid).get("m_Name") == name]
        if len(candidates) <= 1: return candidates[0] if candidates else None
        actions = [obj.pid for obj in F.objs if obj.classid == 1 and val(obj.pid).get("m_Name") == "Actions"]
        roots = set()
        if len(actions) == 1:
            for component in val(actions[0])["m_Component"]:
                fields = val(component["component"]["m_PathID"])
                script = fields.get("m_Script", {}).get("m_PathID")
                if script_names.get(script) == "SubMenuAction":
                    target = fields.get("SubMenu", {}).get("m_PathID")
                    if target in candidates: roots.add(target)
        return next(iter(roots)) if len(roots) == 1 else None

    def menu_actions(menu_pid, script_name, seen=None):
        seen = set() if seen is None else seen
        if menu_pid in seen or menu_pid not in F.by_pid: return []
        seen.add(menu_pid); found = []
        for component in val(menu_pid)["m_Component"]:
            pid = component["component"]["m_PathID"]
            if pid not in F.by_pid or F.by_pid[pid].classid != 114: continue
            fields = val(pid); script = fields.get("m_Script", {}).get("m_PathID")
            if script_names.get(script) == script_name: found.append(F.by_pid[pid])
            elif script_names.get(script) == "SubMenuAction":
                found.extend(menu_actions(fields.get("SubMenu", {}).get("m_PathID"), script_name, seen))
        return found
    def launch_actions(menu_pid):
        return menu_actions(menu_pid, "LaunchFightersAction")

    if munition_slots:
        mgo = named_menu("Munitions")
        fire = menu_actions(mgo, "FireMissileAction")
        if not fire:
            raise ValueError("could not find FireMissileAction components in the Munitions menu")
        print(f"prefab has {len(fire)} munition slot action(s) (FireMissileAction); target {munition_slots}")
        ptr = lambda pid: {"m_FileID": 0, "m_PathID": pid}
        component = lambda pid: {"component": ptr(pid)}
        if munition_slots > len(fire):
            owner = val(fire[-1].pid)["m_GameObject"]["m_PathID"]
            go = copy.deepcopy(val(owner))
            objects = [F.by_pid[c["component"]["m_PathID"]] for c in go["m_Component"]]
            actions = [obj for obj in objects if obj.classid == 114 and "radialMenuPosition" in val(obj.pid)]
            used = {val(obj.pid)["radialMenuPosition"] for obj in actions if not val(obj.pid)["hideInMenu"]}
            free = [k for k in range(8) if k not in used]
            remaining = munition_slots - len(fire)
            if remaining > len(free):
                root_submenu = next((obj for obj in F.objs if obj.classid == 114
                    and script_names.get(val(obj.pid).get("m_Script", {}).get("m_PathID")) == "SubMenuAction"), None)
                back = next((obj for obj in actions
                    if script_names.get(val(obj.pid)["m_Script"]["m_PathID"]) == "BackAction"), None)
                if root_submenu is None or back is None:
                    raise ValueError("The Munitions radial needs a submenu and Back action template to add another page.")
                transform = next(obj for obj in objects if obj.classid == 4)
                # Reserve one position for the next page; move the last existing slot
                # to it if this page is already full. Unity uses hierarchy order for slot IDs.
                move = None
                if not free:
                    move = fire[-1]
                    free = [val(move.pid)["radialMenuPosition"]]
                    go["m_Component"] = [c for c in go["m_Component"] if c != component(move.pid)]
                for pos in free[:-1]:
                    pid = newpid()
                    fields = dict(val(fire[-1].pid), m_GameObject=ptr(owner), radialMenuPosition=pos)
                    appended.append((pid, fire[-1].tidx, F.write(fire[-1], fields)))
                    go["m_Component"].append(component(pid))
                    new_pids.append(pid)
                    remaining -= 1

                parent_transform = transform.pid
                parent_owner, parent_fields = owner, go
                next_position = free[-1]
                while remaining or move is not None:
                    page, child, back_pid, sub_pid = (newpid() for _ in range(4))
                    submenu = dict(val(root_submenu.pid), m_GameObject=ptr(parent_owner),
                                   SubMenu=ptr(page), radialMenuPosition=next_position, hideInMenu=False,
                                   descriptionID=54, Icon=10)
                    parent_fields["m_Component"].append(component(sub_pid))
                    appended.append((sub_pid, root_submenu.tidx, F.write(root_submenu, submenu)))
                    if parent_owner == owner:
                        replaced[owner] = F.write(F.by_pid[owner], parent_fields)
                        late_children.setdefault(parent_transform, []).append(child)
                    else:
                        pending_transform["m_Children"].append(ptr(child))
                        appended.extend([(parent_owner, F.by_pid[owner].tidx, F.write(F.by_pid[owner], parent_fields)),
                                         (parent_transform, transform.tidx, F.write(transform, pending_transform))])
                    page_fields = dict(val(owner), m_Name="Munitions (continued)",
                                       m_Component=[component(child), component(back_pid)])
                    pending_transform = dict(val(transform.pid), m_GameObject=ptr(page),
                                             m_Father=ptr(parent_transform), m_Children=[])
                    back_fields = dict(val(back.pid), m_GameObject=ptr(page), radialMenuPosition=0)
                    appended.append((back_pid, back.tidx, F.write(back, back_fields)))
                    new_pids.extend([page, child, back_pid, sub_pid])
                    position = 1
                    if move is not None:
                        fields = dict(val(move.pid), m_GameObject=ptr(page), radialMenuPosition=position)
                        replaced[move.pid] = F.write(move, fields)
                        page_fields["m_Component"].append(component(move.pid))
                        move = None
                        position += 1
                    limit = 6 if remaining > 8 - position else 7
                    for pos in range(position, limit + 1):
                        if not remaining: break
                        pid = newpid()
                        fields = dict(val(fire[-1].pid), m_GameObject=ptr(page), radialMenuPosition=pos)
                        appended.append((pid, fire[-1].tidx, F.write(fire[-1], fields)))
                        page_fields["m_Component"].append(component(pid))
                        new_pids.append(pid)
                        remaining -= 1
                    parent_owner, parent_transform, parent_fields = page, child, page_fields
                    next_position = 7
                appended.extend([(parent_owner, F.by_pid[owner].tidx, F.write(F.by_pid[owner], parent_fields)),
                                 (parent_transform, transform.tidx, F.write(transform, pending_transform))])
                print("  added continuation pages to the Munitions radial menu")
            else:
                for pos in free[:remaining]:
                    pid = newpid()
                    fields = dict(val(fire[-1].pid), m_GameObject=ptr(owner), radialMenuPosition=pos)
                    appended.append((pid, fire[-1].tidx, F.write(fire[-1], fields)))
                    go["m_Component"].append(component(pid))
                    new_pids.append(pid)
                replaced[owner] = F.write(F.by_pid[owner], go)

    bay_icons = dict(bay_icons or {})
    if squadron_groups and (fighter_slots is None) != (utility_slots is None):
        if fighter_slots is None:
            fighter_slots = sum(squadron_groups) - utility_slots
        else:
            utility_slots = sum(squadron_groups) - fighter_slots
    if align_launch_bays and (fighter_slots is None or utility_slots is None):
        raise ValueError("--align-launch-bays requires both --fighter-slots and --utility-slots")
    existing_bays = [obj for obj in F.objs if obj.classid == 114 and
                     "SlotNo" in val(obj.pid) and val(obj.pid).get("CanLaunch")] if align_launch_bays or bay_icons else []
    if align_launch_bays:
        fighter_types, utility_types = bay_alignment(
            [val(obj.pid) for obj in existing_bays], fighter_slots, utility_slots
        )

    def update_bay_icon(bay, position):
        icon = bay["iconPosition"]
        pid = icon["m_PathID"]
        if icon["m_FileID"] or not pid or pid not in F.by_pid or F.by_pid[pid].classid != 4:
            raise ValueError(f"slot {bay['SlotNo']} has no editable local icon marker")
        if sum(val(obj.pid)["iconPosition"] == icon for obj in existing_bays) != 1:
            raise ValueError(f"slot {bay['SlotNo']} shares its icon marker with another launch bay")
        marker = copy.deepcopy(val(pid))
        marker["m_LocalPosition"] = position
        replaced[pid] = F.write(F.by_pid[pid], marker)

    for obj in existing_bays:
        bay = val(obj.pid)
        slot = bay["SlotNo"]
        if align_launch_bays and 0 <= slot < fighter_slots + utility_slots:
            types = fighter_types if slot < fighter_slots else utility_types
            if set(bay["LaunchCompatibility"]) != set(types):
                edited = dict(bay, LaunchCompatibility=types)
                replaced[obj.pid] = F.write(obj, edited)
                print(f"aligned launch bay {slot} to squadron types {types}")
        if slot in bay_icons:
            if sum(val(other.pid)["SlotNo"] == slot for other in existing_bays) != 1:
                raise ValueError(f"multiple launch bays use slot {slot}; cannot choose an icon")
            update_bay_icon(bay, bay_icons.pop(slot))

    if fighter_slots is not None or utility_slots is not None:
        assert fighter_slots is not None and utility_slots is not None, "give BOTH --fighter-slots and --utility-slots (the final values you set in the level files)"
        if min(fighter_slots, utility_slots) < 0:
            raise ValueError("Fighter and support slot counts must be nonnegative.")
        total = fighter_slots + utility_slots
        MB2 = [o for o in F.objs if o.classid == 114 and list(val(o.pid))[4:] == ["radialMenuPosition", "hideInMenu"]]
        by_go2 = {}
        for o in MB2: by_go2.setdefault(val(o.pid)["m_GameObject"]["m_PathID"], []).append(o)
        fighters_menu = named_menu("Fighters")
        fg = [fighters_menu] if fighters_menu in by_go2 else []
        assert len(fg) == 1, "expected one 'Fighters' menu object, found %d" % len(fg)
        fgo = fg[0]
        launch = launch_actions(fgo)
        assert launch, "could not find LaunchFightersAction components in the Fighters menu or its squadron submenus"
        print(f"prefab has {len(launch)} fighter/support launch action(s); target {total} ({fighter_slots} fighter + {utility_slots} support)")
        used = {val(o.pid)["radialMenuPosition"] for o in by_go2[fgo]}
        free = [k for k in range(8) if k not in used]
        if not squadron_groups and max(0, total - len(launch)) > len(free):
            squadron_groups = [count for count in (fighter_slots, utility_slots) if count]
        if squadron_groups:
            if any(count <= 0 for count in squadron_groups):
                raise ValueError("Squadron groups must have positive slot counts.")
            squadron_groups = [min(7, count - start) for count in squadron_groups
                              for start in range(0, count, 7)]
        gov = copy.deepcopy(val(fgo)); added = 0; new_launch = {}
        for _ in range(max(0, total - len(launch)) if "buttons" in slot_parts else 0):
            nmb = newpid(); mbv = copy.deepcopy(val(launch[-1].pid))
            # A template action may already belong to a subgroup. The new component is first
            # attached to fgo, so keep its owner reference consistent until grouping rewrites it.
            mbv["m_GameObject"] = {"m_FileID": 0, "m_PathID": fgo}
            new_launch[nmb] = (launch[-1].tidx, mbv)
            mbv["radialMenuPosition"] = 0 if squadron_groups else free[added]
            appended.append((nmb, launch[-1].tidx, F.write(launch[-1].tidx, mbv)))
            gov["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": nmb}})
            new_pids.append(nmb); added += 1
            print(f"  + launch button for carrier slot {len(launch) + added - 1}")
        if squadron_groups:
            order_all = [o.pid for o in launch] + list(new_launch)                          # slot order = component order on the Fighters object
            assert sum(squadron_groups) == len(order_all), "--squadron-groups must add up to %d launch buttons (got %d)" % (len(order_all), sum(squadron_groups))
            assert all(0 < g_ <= 7 for g_ in squadron_groups) and len(squadron_groups) <= 6, "each group needs 1-7 buttons and there can be at most 6 groups"
            scr_n2 = {m_.pid: val(m_.pid)["m_ClassName"] for m_ in F.objs if m_.classid == 115}
            cs2 = [o for o in F.objs if o.classid == 114 and val(o.pid).get("m_Script") and val(o.pid)["m_Script"]["m_PathID"] in scr_n2]
            ftr_ = next(c["component"]["m_PathID"] for c in val(fgo)["m_Component"] if F.by_pid[c["component"]["m_PathID"]].classid == 4)
            back_ = next(o for c in val(fgo)["m_Component"] for o in [F.by_pid[c["component"]["m_PathID"]]] if o.classid == 114 and scr_n2[val(o.pid)["m_Script"]["m_PathID"]] == "BackAction")
            smb_ = next(o for o in cs2 if scr_n2[val(o.pid)["m_Script"]["m_PathID"]] == "SubMenuAction" and val(o.pid)["SubMenu"]["m_PathID"] == fgo)
            moved = set(order_all)
            old_group_menus = set()
            old_group_targets = set()
            generated_group_names = {"Fighter Squadrons", "Support Squadrons"} | {
                "Squadrons%d" % i for i in range(1, 7)
            }
            def is_generated_group(name):
                return name in generated_group_names or name.rsplit(" ", 1)[0] in {
                    "Fighter Squadrons", "Support Squadrons", "Squadrons"}
            for entry in val(fgo)["m_Component"]:
                pid = entry["component"]["m_PathID"]
                if pid not in F.by_pid or F.by_pid[pid].classid != 114: continue
                fields = val(pid)
                if scr_n2.get(fields.get("m_Script", {}).get("m_PathID")) != "SubMenuAction": continue
                target = fields.get("SubMenu", {}).get("m_PathID")
                if target in F.by_pid and launch_actions(target):
                    if is_generated_group(val(target).get("m_Name", "")):
                        old_group_menus.add(pid)
                        old_group_targets.add(target)
            moved.update(old_group_menus)
            keep_pos = {val(o.pid)["radialMenuPosition"] for o in by_go2[fgo] if o.pid not in moved}
            if len(squadron_groups) > 8 - len(keep_pos):
                raise ValueError("The Fighters radial has no room for all squadron groups.")
            gov["m_Component"] = [c for c in gov["m_Component"] if c["component"]["m_PathID"] not in moved]
            # Retire old generated squadron menus, including remnants from earlier regrouping
            # runs. Their GameObjects may still list LaunchFightersAction components after the
            # action's m_GameObject has been reassigned, leaving duplicate/mismatched owners.
            launch_pids = set(order_all)
            retire_targets = set(old_group_targets)
            for old_go in F.objs:
                if old_go.classid == 1 and is_generated_group(val(old_go.pid).get("m_Name", "")):
                    retire_targets.add(old_go.pid)
            for target in retire_targets:
                if target == fgo or target not in F.by_pid:
                    continue
                old_go = F.by_pid[target]
                old_fields = copy.deepcopy(val(target))
                old_fields["m_Name"] = "DeadlockRetiredSquadronMenu_%s" % target
                old_fields["m_Component"] = [entry for entry in old_fields.get("m_Component", [])
                    if entry["component"]["m_PathID"] not in F.by_pid or
                    F.by_pid[entry["component"]["m_PathID"]].classid != 114]
                replaced[target] = F.write(old_go, old_fields)
            # Remove stale references to moved actions from any other GameObject, including
            # duplicate generated menus whose parent submenu had already been discarded.
            for old_go in F.objs:
                if old_go.classid != 1 or old_go.pid == fgo:
                    continue
                old_fields = copy.deepcopy(val(old_go.pid))
                old_components = old_fields.get("m_Component", [])
                clean_components = [entry for entry in old_components
                                    if entry["component"]["m_PathID"] not in launch_pids]
                if len(clean_components) != len(old_components):
                    old_fields["m_Component"] = clean_components
                    replaced[old_go.pid] = F.write(old_go, old_fields)
            names = []
            offset = 0
            for count in squadron_groups:
                label = ("Fighter Squadrons" if offset + count <= fighter_slots else
                         "Support Squadrons" if offset >= fighter_slots else "Squadrons")
                names.append(label if label not in names else f"{label} {len(names) + 1}")
                offset += count
            at = 0
            for gi, cnt in enumerate(squadron_groups):
                ngo, ntr, nback, nsub = newpid(), newpid(), newpid(), newpid()
                members = order_all[at:at + cnt]; at += cnt
                g2 = copy.deepcopy(val(fgo)); g2["m_Name"] = names[gi]; g2["m_Component"] = [{"component": {"m_FileID": 0, "m_PathID": p_}} for p_ in [ntr, nback] + members]
                t2 = copy.deepcopy(val(ftr_)); t2["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; t2["m_Children"] = []
                b2 = copy.deepcopy(val(back_.pid)); b2["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; b2["radialMenuPosition"] = 0
                spos = next(k for k in range(8) if k not in keep_pos); keep_pos.add(spos)
                s2 = copy.deepcopy(val(smb_.pid)); s2["SubMenu"] = {"m_FileID": 0, "m_PathID": ngo}; s2["radialMenuPosition"] = spos; s2["m_GameObject"] = {"m_FileID": 0, "m_PathID": fgo}
                if names[gi].startswith(("Fighter Squadrons", "Support Squadrons")):
                    s2["descriptionID"] = 251 if names[gi].startswith("Support") else 1040
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
            if align_launch_bays:
                sv["LaunchCompatibility"] = fighter_types if slot < fighter_slots else utility_types
            # clone the Icon marker (child transform) so the UI icon sits with the new bay
            ic = sv["iconPosition"]["m_PathID"]
            if ic and "icons" in slot_parts:
                ict = copy.deepcopy(val(ic)); icg = copy.deepcopy(val(ict["m_GameObject"]["m_PathID"]))
                nig, nit = newpid(), newpid()
                icg["m_Component"] = [{"component": {"m_FileID": 0, "m_PathID": nit}}]
                ict["m_GameObject"]["m_PathID"] = nig; ict["m_Father"]["m_PathID"] = ntr; ict["m_Children"] = []
                side_ = -1.0 if st["m_LocalPosition"]["x"] > 0 else 1.0                            # +x offset = towards the centre line, mirrored for port / starboard
                il_ = ict["m_LocalPosition"]; il_["x"] += icon_offset[0] * side_ * k_; il_["y"] += icon_offset[1] * k_; il_["z"] += icon_offset[2] * k_
                if slot in bay_icons:
                    ict["m_LocalPosition"] = bay_icons.pop(slot)
                st["m_Children"].append({"m_FileID": 0, "m_PathID": nit}); sv["iconPosition"]["m_PathID"] = nit
                appended += [(nig, go_type, F.write(go_type, icg)), (nit, tr_type, F.write(tr_type, ict))]
                new_pids += [nig, nit]
            children_add.setdefault(st["m_Father"]["m_PathID"], []).append(ntr)
            appended += [(ngo, go_type, F.write(go_type, sgo)), (ntr, tr_type, F.write(tr_type, st)), (nmb, src.tidx, F.write(src.tidx, sv))]
            new_pids += [ngo, ntr, nmb]
            print(f"  + launch bay for carrier slot {slot} (copy of {goname_of(sg)}, moved by {tuple(round(bay_offset[i]*k_,1) for i in range(3))})")
    if bay_icons:
        raise ValueError("no editable launch bay icon for slot(s) " + ", ".join(map(str, sorted(bay_icons))))
    for father, kids in children_add.items():
        fo = F.by_pid[father]; fv = copy.deepcopy(val(father))
        if father in replaced:
            raise ValueError("A cloned mount has another edited mount as its parent.")
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
    injected_scripts = {}
    def add_script_component(cls, type_raw, ms_raw, mb_raw, go_pid, patch):
        """append a MonoBehaviour of script class <cls> (adding its serialized type + MonoScript if the bundle lacks them)"""
        ms_pid = next((o.pid for o in F.objs if o.classid == 115 and val(o.pid)["m_ClassName"] == cls), None)
        ms_type = next(o.tidx for o in F.objs if o.classid == 115)
        if cls in injected_scripts:
            ms_pid, new_type_idx = injected_scripts[cls]
        elif ms_pid is None:
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
            injected_scripts[cls] = ms_pid, new_type_idx
        else:
            idx = next(i for i in range(struct.unpack_from("<i", F.suffix, 0)[0]) if struct.unpack_from("<iq", F.suffix, 4 + 12 * i)[1] == ms_pid)
            new_type_idx = next(i for i, t in enumerate(F.types) if t.classid == 114 and t.script_idx == idx)
        mb_pid = newpid(); mbb = bytearray(mb_raw)
        struct.pack_into("<q", mbb, 4, go_pid); struct.pack_into("<q", mbb, 20, ms_pid); patch(mbb)
        appended.append((mb_pid, new_type_idx, bytes(mbb))); new_pids.append(mb_pid)
        comp_add.setdefault(go_pid, []).append(mb_pid)
        return mb_pid

    def add_custom_module_component(cls, go_pid, definition=None, **overrides):
        """Add one of this tool's modules with an inline Unity 2018.4 type tree."""
        definition = definition or CUSTOM_MODULE_DEFINITIONS[cls]
        script_obj = next((o for o in F.objs if o.classid == 115 and val(o.pid).get("m_ClassName") == cls), None)
        if script_obj is not None:
            script_pid = script_obj.pid
            script_index = next(i for i in range(struct.unpack_from("<i", F.suffix, 0)[0])
                                if struct.unpack_from("<iq", F.suffix, 4 + 12 * i)[1] == script_pid)
            type_index = next(i for i, t in enumerate(F.types)
                              if t.classid == 114 and t.script_idx == script_index)
            nodes = F.types[type_index].nodes
            type_hash = F.types[type_index].hash
        elif cls in injected_scripts:
            script_pid, type_index = injected_scripts[cls]
            nodes = injected_scripts[cls][2]
            type_hash = injected_scripts[cls][3]
        else:
            script_index = struct.unpack_from("<i", F.suffix, 0)[0]
            template = next((t for t in F.types if t.classid == 114 and
                             any(node.level == 1 and node.name == "m_Name" for node in t.nodes)), None)
            if template is None:
                raise ValueError("bundle has no MonoBehaviour type tree to use as a module template")
            type_raw, nodes, type_hash = _custom_module_type_raw(
                template, cls, definition["fields"], script_index)
            type_index = add_type(type_raw)

            mono_template = next((o for o in F.objs if o.classid == 115), None)
            if mono_template is None:
                raise ValueError("bundle has no MonoScript object template")
            script_pid = newpid()
            script_fields = copy.deepcopy(val(mono_template.pid))
            script_fields.update(m_Name=cls, m_ExecutionOrder=0, m_ClassName=cls,
                                 m_Namespace="", m_AssemblyName="Assembly-CSharp.dll")
            script_fields["m_PropertiesHash"] = {
                f"bytes[{i}]": value for i, value in enumerate(type_hash)
            }
            appended.append((script_pid, mono_template.tidx,
                             F.write(mono_template, script_fields)))
            new_pids.append(script_pid)
            suffix_count = struct.unpack_from("<i", F.suffix, 0)[0]
            suffix = bytearray(F.suffix)
            struct.pack_into("<i", suffix, 0, suffix_count + 1)
            suffix[4 + 12 * suffix_count:4 + 12 * suffix_count] = struct.pack("<iq", 0, script_pid)
            F.suffix_len = (getattr(F, "suffix_len", None) or F._suffix_len()) + 12
            F.suffix = bytes(suffix)
            injected_scripts[cls] = (script_pid, type_index, nodes, type_hash)

        module_fields = dict(definition["defaults"], **overrides)
        component_fields = {
            "m_GameObject": {"m_FileID": 0, "m_PathID": go_pid},
            "m_Enabled": 1,
            "m_Script": {"m_FileID": 0, "m_PathID": script_pid},
            "m_Name": "",
            **module_fields,
        }
        module_data = bytearray()
        F._wr(nodes, 0, component_fields, module_data)
        module_pid = newpid()
        appended.append((module_pid, type_index, bytes(module_data)))
        new_pids.append(module_pid)
        comp_add.setdefault(go_pid, []).append(module_pid)
        return module_pid

    root = max((o for o in F.objs if o.classid == 1), key=lambda o: len(val(o.pid)["m_Component"]))
    if any(cls not in CUSTOM_MODULE_DEFINITIONS for cls in custom_modules):
        raise ValueError("unknown custom ship module requested")
    custom_scripts = {obj.pid: val(obj.pid).get("m_ClassName")
                      for obj in F.objs if obj.classid == 115}
    custom_present = {}
    for cls in CUSTOM_MODULE_DEFINITIONS:
        matches = [obj for obj in F.objs if obj.classid == 114
                   and val(obj.pid).get("m_Script", {}).get("m_PathID") in custom_scripts
                   and custom_scripts[val(obj.pid)["m_Script"]["m_PathID"]] == cls
                   and val(obj.pid).get("m_GameObject", {}).get("m_PathID") == root.pid]
        if len(matches) > 1:
            raise ValueError(f"ship has multiple {cls} components")
        custom_present[cls] = bool(matches)
        if cls in custom_modules and not matches:
            add_custom_module_component(cls, root.pid)
            custom_present[cls] = True
            print(f"added {CUSTOM_MODULE_DEFINITIONS[cls]['label']} module")
    custom_armour_active = custom_present["CustomArmourRepairModule"]
    custom_squadron_active = custom_present["CustomSquadronReplacementModule"]
    if hull_regen is not None:
        if hull_regen < 0:
            raise ValueError("--hull-regen cannot be negative (the old negative-rate armour flag was removed; use --custom-armour-repair)")
        existing_resin = [obj for obj in F.objs if obj.classid == 114 and "hullPerTick" in val(obj.pid)]
        if len(existing_resin) > 1:
            raise ValueError("the bundle has multiple Hull Resin modules")
        if existing_resin:
            obj = existing_resin[0]
            fields = val(obj.pid)
            rate = hull_regen
            replaced[obj.pid] = F.write(obj, dict(fields, hullPerTick=rate))
            print(f"updated Hull Resin rate to {rate:g}")
        else:
            rate = hull_regen
            add_script_component("RegenerativeHullResinModule", RESIN_TYPE_RAW, RESIN_MS_RAW, RESIN_MB_RAW,
                                 root.pid, lambda data: struct.pack_into("<f", data, 32, rate))
            print(f"added Hull Resin at {rate:g} per tick")
    if munition_factory:
        has_factory = any(o.classid == 115 and val(o.pid)["m_ClassName"] == "MunitionFactoryModule" for o in F.objs)
        if not has_factory:
            add_script_component("MunitionFactoryModule", MFM_TYPE_RAW, MFM_MS_RAW, MFM_MB_RAW, root.pid, lambda b: None)
            print(f"added Munition Factory module (passive munition resupply) to {val(root.pid)['m_Name']}; the ship's Armory MunitionResupplyRange sets its speed")
    def current_fields(pid):
        if pid in F.by_pid:
            obj = F.by_pid[pid]
            if pid not in replaced:
                return val(pid)
            type_index, data = obj.tidx, replaced[pid]
        else:
            _, type_index, data = next(entry for entry in appended if entry[0] == pid)
        return F._rd(F.types[type_index].nodes, 0, data, [0])[0]

    sub_state = {}
    act_used = {}                                                                             # radial positions taken in the Actions menu (shared by the abilities added below)
    sub2_state = {}

    def alloc_action_pos(preferred, label=None):
        scr_n = {m.pid: val(m.pid)["m_ClassName"] for m in F.objs if m.classid == 115}
        cs = [o for o in F.objs if o.classid == 114 and val(o.pid).get("m_Script") and val(o.pid)["m_Script"]["m_PathID"] in scr_n]
        mv = next(o for o in cs if scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "MoveAction"); g = val(mv.pid)["m_GameObject"]["m_PathID"]
        if g not in act_used: act_used[g] = {val(o.pid)["radialMenuPosition"] for o in cs if "radialMenuPosition" in val(o.pid) and val(o.pid)["m_GameObject"]["m_PathID"] == g and not val(o.pid)["hideInMenu"]}
        if ability_submenu and not submenu_has_room(scr_n, cs, g):
            # the Abilities submenu holds 7 buttons: overflow into a second submenu ("Abilities 2", button "Other"),
            # created in a free slot of the Actions menu
            menu = overflow_submenu(scr_n, cs, g)
            if menu is None:
                raise ValueError("the Abilities submenu is full and the Actions menu has no free slot for a second one")
            pos = next((k for k in range(1, 8) if k not in sub2_state["used"]), None)
            if pos is None:
                raise ValueError("both ability submenus are full (14 abilities); no room for another ability button")
            sub2_state["used"].add(pos)
            print("the Abilities submenu is full: %s goes in '%s' (radial position %d)" % (label or "the button", OVERFLOW_MENU, pos))
            return menu, pos
        if ability_submenu:
            if "go" not in sub_state:
                existing = [
                    v["SubMenu"]["m_PathID"]
                    for obj in cs
                    for v in (val(obj.pid),)
                    if scr_n[v["m_Script"]["m_PathID"]] == "SubMenuAction"
                    and v["m_GameObject"]["m_PathID"] == g
                    and not v["SubMenu"]["m_FileID"]
                    and v["SubMenu"]["m_PathID"] in F.by_pid
                    and "abilit" in val(v["SubMenu"]["m_PathID"]).get("m_Name", "").casefold()
                    and val(v["SubMenu"]["m_PathID"]).get("m_Name", "") != OVERFLOW_MENU
                ]
                if len(existing) > 1:
                    raise ValueError("multiple existing ability submenus; merge or rename them before editing")
                if existing:
                    menu = existing[0]
                    menu_fields = copy.deepcopy(val(menu))
                    previous_name = menu_fields.get("m_Name", "")
                    if previous_name != "Abilities":
                        menu_fields["m_Name"] = "Abilities"
                        replaced[menu] = F.write(F.by_pid[menu], menu_fields)
                        print(f"renamed the existing '{previous_name}' submenu to 'Abilities'")
                    used = {
                        v["radialMenuPosition"] for obj in cs for v in (val(obj.pid),)
                        if v["m_GameObject"]["m_PathID"] == menu
                        and "radialMenuPosition" in v and not v.get("hideInMenu")
                    }
                    for obj in cs:
                        fields = val(obj.pid)
                        if (fields["m_GameObject"]["m_PathID"] == g
                                and fields.get("SubMenu", {}).get("m_PathID") == menu):
                            replaced[obj.pid] = F.write(obj, dict(current_fields(obj.pid), descriptionID=21))
                    sub_state.update(go=menu, used=used)
            if "go" not in sub_state:
                fgo_pid = named_menu("Fighters")
                if fgo_pid is None:
                    raise ValueError("--ability-submenu needs an existing Fighters menu as a template")
                fgo_ = F.by_pid[fgo_pid]
                ftr_ = next(c["component"]["m_PathID"] for c in val(fgo_.pid)["m_Component"] if F.by_pid[c["component"]["m_PathID"]].classid == 4)
                back_ = next(o for c in val(fgo_.pid)["m_Component"] for o in [F.by_pid[c["component"]["m_PathID"]]] if o.classid == 114 and scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "BackAction")
                smb_ = next(o for o in cs if scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "SubMenuAction" and val(o.pid)["SubMenu"]["m_PathID"] == fgo_.pid)
                ngo, ntr, nback, nsub = newpid(), newpid(), newpid(), newpid()
                gv_ = copy.deepcopy(val(fgo_.pid)); gv_["m_Name"] = "Abilities"; gv_["m_Component"] = [{"component": {"m_FileID": 0, "m_PathID": p_}} for p_ in (ntr, nback)]
                tv_ = copy.deepcopy(val(ftr_)); tv_["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; tv_["m_Children"] = []
                bv_ = copy.deepcopy(val(back_.pid)); bv_["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; bv_["radialMenuPosition"] = 0
                sv_ = copy.deepcopy(val(smb_.pid)); sv_["SubMenu"] = {"m_FileID": 0, "m_PathID": ngo}
                sv_["descriptionID"] = 21  # Abilities
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

    def overflow_submenu(scr_n, cs, g):
        """the second abilities submenu: the existing one, or a new one in a free Actions slot (None if no slot)"""
        if "go" in sub2_state:
            return sub2_state["go"]
        for obj in cs:
            v = val(obj.pid)
            if (scr_n[v["m_Script"]["m_PathID"]] == "SubMenuAction" and v["m_GameObject"]["m_PathID"] == g
                    and not v["SubMenu"]["m_FileID"] and v["SubMenu"]["m_PathID"] in F.by_pid
                    and val(v["SubMenu"]["m_PathID"]).get("m_Name", "") == OVERFLOW_MENU):
                menu = v["SubMenu"]["m_PathID"]
                used = {w["radialMenuPosition"] for o in cs for w in (val(o.pid),)
                        if w["m_GameObject"]["m_PathID"] == menu and "radialMenuPosition" in w and not w.get("hideInMenu")}
                sub2_state.update(go=menu, used=used)
                return menu
        u0 = act_used[g]
        spos = next((k for k in range(1, 8) if k not in u0), None)
        if spos is None:
            return None
        fgo_pid = named_menu("Fighters")
        if fgo_pid is None:
            raise ValueError("a second ability submenu needs an existing Fighters menu as a template")
        fgo_ = F.by_pid[fgo_pid]
        ftr_ = next(c["component"]["m_PathID"] for c in val(fgo_.pid)["m_Component"] if F.by_pid[c["component"]["m_PathID"]].classid == 4)
        back_ = next(o for c in val(fgo_.pid)["m_Component"] for o in [F.by_pid[c["component"]["m_PathID"]]] if o.classid == 114 and scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "BackAction")
        smb_ = next(o for o in cs if scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "SubMenuAction" and val(o.pid)["SubMenu"]["m_PathID"] == fgo_.pid)
        ngo, ntr, nback, nsub = newpid(), newpid(), newpid(), newpid()
        gv_ = copy.deepcopy(val(fgo_.pid)); gv_["m_Name"] = OVERFLOW_MENU; gv_["m_Component"] = [{"component": {"m_FileID": 0, "m_PathID": p_}} for p_ in (ntr, nback)]
        tv_ = copy.deepcopy(val(ftr_)); tv_["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; tv_["m_Children"] = []
        bv_ = copy.deepcopy(val(back_.pid)); bv_["m_GameObject"] = {"m_FileID": 0, "m_PathID": ngo}; bv_["radialMenuPosition"] = 0
        sv_ = copy.deepcopy(val(smb_.pid)); sv_["SubMenu"] = {"m_FileID": 0, "m_PathID": ngo}
        sv_["descriptionID"] = OVERFLOW_TEXT
        u0.add(spos); sv_["radialMenuPosition"] = spos; sv_["m_GameObject"] = {"m_FileID": 0, "m_PathID": g}
        appended.extend([(ngo, F.by_pid[fgo_.pid].tidx, F.write(F.by_pid[fgo_.pid].tidx, gv_)), (ntr, F.by_pid[ftr_].tidx, F.write(F.by_pid[ftr_].tidx, tv_)),
                         (nback, back_.tidx, F.write(back_.tidx, bv_)), (nsub, smb_.tidx, F.write(smb_.tidx, sv_))])
        new_pids.extend([ngo, ntr, nback, nsub]); comp_add.setdefault(g, []).append(nsub)
        sub2_state.update(go=ngo, used={0}, father=val(ftr_)["m_Father"]["m_PathID"], tr=ntr, gov=gv_)
        print(f"created a second ability submenu '{OVERFLOW_MENU}' (button 'Other' at radial position {spos} in the Actions menu)")
        return ngo

    def submenu_has_room(scr_n=None, cs=None, g=None):
        """False when the ship already has an Abilities submenu with all 7 button positions taken"""
        if not ability_submenu:
            return False
        if "go" in sub_state:
            return any(k not in sub_state["used"] for k in range(8))
        scr_n = scr_n or {m.pid: val(m.pid)["m_ClassName"] for m in F.objs if m.classid == 115}
        cs = cs or [o for o in F.objs if o.classid == 114 and val(o.pid).get("m_Script") and val(o.pid)["m_Script"]["m_PathID"] in scr_n]
        if g is None:
            mv = next(o for o in cs if scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "MoveAction"); g = val(mv.pid)["m_GameObject"]["m_PathID"]
        menus = [v["SubMenu"]["m_PathID"] for obj in cs for v in (val(obj.pid),)
                 if scr_n[v["m_Script"]["m_PathID"]] == "SubMenuAction" and v["m_GameObject"]["m_PathID"] == g
                 and not v["SubMenu"]["m_FileID"] and v["SubMenu"]["m_PathID"] in F.by_pid
                 and "abilit" in val(v["SubMenu"]["m_PathID"]).get("m_Name", "").casefold()
                    and val(v["SubMenu"]["m_PathID"]).get("m_Name", "") != OVERFLOW_MENU]
        if len(menus) != 1:
            return True                             # none yet (one gets created) or several (alloc_action_pos reports it)
        used = {v["radialMenuPosition"] for obj in cs for v in (val(obj.pid),)
                if v["m_GameObject"]["m_PathID"] == menus[0] and "radialMenuPosition" in v and not v.get("hideInMenu")}
        return any(k not in used for k in range(8))
    if normalize_ability_submenu:
        scr_n = {m.pid: val(m.pid)["m_ClassName"] for m in F.objs if m.classid == 115}
        cs = [o for o in F.objs if o.classid == 114 and val(o.pid).get("m_Script")
              and val(o.pid)["m_Script"]["m_PathID"] in scr_n]
        move = next(o for o in cs if scr_n[val(o.pid)["m_Script"]["m_PathID"]] == "MoveAction")
        actions_go = val(move.pid)["m_GameObject"]["m_PathID"]
        menus = [val(obj.pid)["SubMenu"]["m_PathID"] for obj in cs
                 if scr_n[val(obj.pid)["m_Script"]["m_PathID"]] == "SubMenuAction"
                 and val(obj.pid)["m_GameObject"]["m_PathID"] == actions_go
                 and not val(obj.pid)["SubMenu"]["m_FileID"]
                 and val(obj.pid)["SubMenu"]["m_PathID"] in F.by_pid
                 and "abilit" in val(val(obj.pid)["SubMenu"]["m_PathID"]).get("m_Name", "").casefold()]
        menus = list(dict.fromkeys(menus))
        if len(menus) > 1:
            raise ValueError("multiple existing ability submenus; merge or rename them before editing")
        if not menus:
            raise ValueError("this bundle has no existing ability submenu to rename")
        menu = menus[0]
        menu_fields = copy.deepcopy(val(menu))
        previous_name = menu_fields.get("m_Name", "")
        if previous_name != "Abilities":
            menu_fields["m_Name"] = "Abilities"
            replaced[menu] = F.write(F.by_pid[menu], menu_fields)
            print(f"renamed the existing '{previous_name}' submenu to 'Abilities'")
        else:
            print("the existing ability submenu is already named 'Abilities'")
        fighter_menu = named_menu("Fighters")
        for obj in cs:
            fields = val(obj.pid)
            if scr_n[fields["m_Script"]["m_PathID"]] != "SubMenuAction":
                continue
            owner = fields["m_GameObject"]["m_PathID"]
            target = fields["SubMenu"]["m_PathID"]
            if fields["SubMenu"]["m_FileID"] or target not in F.by_pid or owner not in (actions_go, fighter_menu):
                continue
            if obj.pid not in {entry["component"]["m_PathID"] for entry in current_fields(owner)["m_Component"]}:
                continue
            if owner == actions_go and target == menu:
                label = 21
            elif owner == fighter_menu:
                label = {"Fighter Squadrons": 1040, "Support Squadrons": 251}.get(val(target)["m_Name"])
            else:
                label = None
            if label is not None and fields["descriptionID"] != label:
                replaced[obj.pid] = F.write(obj, dict(current_fields(obj.pid), descriptionID=label))
    if "CustomInstantPlatingModule" in custom_modules:
        actions = [obj for obj in F.objs if obj.classid == 114 and
                   custom_scripts.get(val(obj.pid).get("m_Script", {}).get("m_PathID")) == INSTANT_PLATING_ACTION]
        if len(actions) > 1:
            raise ValueError("The ship has multiple Instant Plating buttons.")
        if not actions:
            menu, position = alloc_action_pos(7)
            action_type = {"fields": (("radialMenuPosition", "int"), ("hideInMenu", "bool")),
                           "defaults": {"hideInMenu": False}}
            add_custom_module_component(INSTANT_PLATING_ACTION, menu, action_type, radialMenuPosition=position)
            print(f"added Instant Plating to the action menu (radial position {position})")
        elif ability_submenu:
            action = actions[0]
            fields = copy.deepcopy(val(action.pid))
            previous = fields["m_GameObject"]["m_PathID"]
            # Already in the ship's Abilities submenu.
            if "abilit" not in val(previous)["m_Name"].casefold() and submenu_has_room():
                menu, position = alloc_action_pos(7)
                owner = copy.deepcopy(current_fields(previous))
                owner["m_Component"] = [item for item in owner["m_Component"]
                                        if item["component"]["m_PathID"] != action.pid]
                replaced[previous] = F.write(F.by_pid[previous], owner)
                fields["m_GameObject"] = {"m_FileID": 0, "m_PathID": menu}
                fields.update(radialMenuPosition=position, hideInMenu=False)
                replaced[action.pid] = F.write(action, fields)
                comp_add.setdefault(menu, []).append(action.pid)
                print(f"moved Instant Plating into Abilities (radial position {position})")
    if "CustomEmpPulseModule" in custom_modules:
        action_name = "CustomEmpPulseAction"
        actions = [obj for obj in F.objs if obj.classid == 114 and
                   custom_scripts.get(val(obj.pid).get("m_Script", {}).get("m_PathID")) == action_name]
        if len(actions) > 1:
            raise ValueError("The ship has multiple EMP Pulse buttons.")
        if not actions:
            menu, position = alloc_action_pos(7)
            action_type = {"fields": (("radialMenuPosition", "int"), ("hideInMenu", "bool")),
                           "defaults": {"hideInMenu": False}}
            add_custom_module_component(action_name, menu, action_type, radialMenuPosition=position)
            print(f"added Experimental EMP to the action menu (radial position {position})")
        elif ability_submenu:
            action = actions[0]
            fields = copy.deepcopy(val(action.pid))
            previous = fields["m_GameObject"]["m_PathID"]
            if "abilit" not in val(previous)["m_Name"].casefold() and submenu_has_room():
                menu, position = alloc_action_pos(7)
                owner = copy.deepcopy(current_fields(previous))
                owner["m_Component"] = [item for item in owner["m_Component"]
                                        if item["component"]["m_PathID"] != action.pid]
                replaced[previous] = F.write(F.by_pid[previous], owner)
                fields["m_GameObject"] = {"m_FileID": 0, "m_PathID": menu}
                fields.update(radialMenuPosition=position, hideInMenu=False)
                replaced[action.pid] = F.write(action, fields)
                comp_add.setdefault(menu, []).append(action.pid)
                print(f"moved Experimental EMP into Abilities (radial position {position})")
    if "CustomTargetedEmpModule" in custom_modules:
        action_name = "CustomTargetedEmpAction"
        actions = [obj for obj in F.objs if obj.classid == 114 and
                   custom_scripts.get(val(obj.pid).get("m_Script", {}).get("m_PathID")) == action_name]
        if len(actions) > 1:
            raise ValueError("The ship has multiple Targeted EMP buttons.")
        if not actions:
            menu, position = alloc_action_pos(7)
            action_type = {"fields": (("radialMenuPosition", "int"), ("hideInMenu", "bool")),
                           "defaults": {"hideInMenu": False}}
            add_custom_module_component(action_name, menu, action_type, radialMenuPosition=position)
            print(f"added Targeted EMP to the action menu (radial position {position})")
        elif ability_submenu:
            action = actions[0]
            fields = copy.deepcopy(val(action.pid))
            previous = fields["m_GameObject"]["m_PathID"]
            if "abilit" not in val(previous)["m_Name"].casefold() and submenu_has_room():
                menu, position = alloc_action_pos(7)
                owner = copy.deepcopy(current_fields(previous))
                owner["m_Component"] = [item for item in owner["m_Component"]
                                        if item["component"]["m_PathID"] != action.pid]
                replaced[previous] = F.write(F.by_pid[previous], owner)
                fields["m_GameObject"] = {"m_FileID": 0, "m_PathID": menu}
                fields.update(radialMenuPosition=position, hideInMenu=False)
                replaced[action.pid] = F.write(action, fields)
                comp_add.setdefault(menu, []).append(action.pid)
                print(f"moved Targeted EMP into Abilities (radial position {position})")
    if jamming is not None:
        # Jamming mode (the Cylon Wardriver's): a Jammer on the ship root and a JammingAction button. The Jammer acts
        # on what is in range (enemy squadrons and missiles), not on its own ship, so it works on a capital ship.
        jammers = [obj for obj in F.objs if obj.classid == 114 and
                   custom_scripts.get(val(obj.pid).get("m_Script", {}).get("m_PathID")) == "Jammer"]
        settings = {k: v for k, v in (("jamRange", jamming.get("range")), ("jamMissileChance", jamming.get("missile_chance")))
                    if v is not None}
        if settings.get("jamMissileChance") is not None and not 0 <= settings["jamMissileChance"] <= 1:
            raise ValueError("the jam missile chance is a fraction from 0 to 1")
        if settings.get("jamRange") is not None and not settings["jamRange"] > 0:
            raise ValueError("the jam range must be above 0")
        if len(jammers) > 1:
            raise ValueError("The ship has more than one Jammer.")
        if jammers:
            fields = copy.deepcopy(current_fields(jammers[0].pid))
            if any(fields.get(k) != v for k, v in settings.items()):
                fields.update(settings)
                replaced[jammers[0].pid] = F.write(jammers[0], fields)
                print("updated the Jammer (range %g, missile chance %g)" % (fields["jamRange"], fields["jamMissileChance"]))
        else:
            add_custom_module_component("Jammer", root.pid, JAMMER_DEFINITION, **settings)
            values = dict(JAMMER_DEFINITION["defaults"], **settings)
            print("added a Jammer (range %g, missile chance %g)" % (values["jamRange"], values["jamMissileChance"]))
        actions = [obj for obj in F.objs if obj.classid == 114 and
                   custom_scripts.get(val(obj.pid).get("m_Script", {}).get("m_PathID")) == "JammingAction"]
        if len(actions) > 1:
            raise ValueError("The ship has multiple Jamming buttons.")
        if not actions:
            menu, position = alloc_action_pos(7, "Jamming mode")
            add_custom_module_component("JammingAction", menu, JAMMING_ACTION_DEFINITION, radialMenuPosition=position)
            print(f"added Jamming mode to the action menu (radial position {position})")
        elif ability_submenu:
            action = actions[0]
            fields = copy.deepcopy(val(action.pid))
            previous = fields["m_GameObject"]["m_PathID"]
            if "abilit" not in val(previous)["m_Name"].casefold() and submenu_has_room():
                menu, position = alloc_action_pos(7)
                owner = copy.deepcopy(current_fields(previous))
                owner["m_Component"] = [item for item in owner["m_Component"]
                                        if item["component"]["m_PathID"] != action.pid]
                replaced[previous] = F.write(F.by_pid[previous], owner)
                fields["m_GameObject"] = {"m_FileID": 0, "m_PathID": menu}
                fields.update(radialMenuPosition=position, hideInMenu=False)
                replaced[action.pid] = F.write(action, fields)
                comp_add.setdefault(menu, []).append(action.pid)
                print(f"moved Jamming mode into Abilities (radial position {position})")
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
        scripts = {obj.pid for obj in F.objs if obj.classid == 115 and val(obj.pid)["m_ClassName"] == "StealthModule"}
        modules = [obj for obj in F.objs if obj.classid == 114
                   and val(obj.pid).get("m_Script", {}).get("m_PathID") in scripts]
        if len(modules) > 1:
            raise ValueError("the bundle has multiple Stealth modules")
        if modules:
            module = modules[0]
            fields = dict(val(module.pid))
            for option, key in (("battery", "batteryMax"), ("start_on", "activeByDefault")):
                if stealth[option] is not None:
                    fields[key] = stealth[option]
            replaced[module.pid] = F.write(module, fields)
            print("updated existing Stealth settings")
            stealth = None
        elif not stealth["donor"]:
            raise ValueError("select a bundle with Stealth or provide an Orion donor using --stealth")
        else:
            stealth["battery"] = 100.0 if stealth["battery"] is None else stealth["battery"]
            stealth["start_on"] = True if stealth["start_on"] is None else stealth["start_on"]
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
    support_settings = support_settings or {}
    if not isinstance(turnaround_repair_actions, int) or not 1 <= turnaround_repair_actions <= 8:
        raise ValueError("turnaround repair action count must be between 1 and 8")
    external_preloads = []

    if support_abilities or support_settings:
        scripts = {obj.pid: val(obj.pid)["m_ClassName"] for obj in F.objs if obj.classid == 115}
        components = {}
        for obj in F.objs:
            if obj.classid != 114:
                continue
            fields = val(obj.pid)
            script = scripts.get(fields.get("m_Script", {}).get("m_PathID"))
            if script:
                components.setdefault(script, []).append(obj)
        for ability, changes in support_settings.items():
            module, _, _ = SUPPORT_ABILITIES[ability]
            matches = components.get(module, [])
            if len(matches) == 1:
                obj = matches[0]
                fields = val(obj.pid)
                if not changes.keys() <= fields.keys():
                    raise ValueError(f"{module} lacks one or more requested fields")
                replaced[obj.pid] = F.write(obj, dict(fields, **changes))
                print(f"updated existing {ability.replace('_', ' ')} settings")
            elif matches or ability not in (support_abilities or {}):
                raise ValueError(f"select a bundle with one {module} or enable --{ability.replace('_', '-')}")
        donors = {}

        def ptr(pid):
            return {"m_FileID": 0, "m_PathID": pid}

        def external_table(file):
            count = struct.unpack_from("<i", file.suffix)[0]
            end = getattr(file, "suffix_len", None) or file._suffix_len()
            return file.suffix[4 + 12 * count:end]

        def donor_component(donor, script):
            matches = donor[1].get(script, [])
            if len(matches) != 1:
                raise ValueError(f"donor needs exactly one {script} component")
            return matches[0]

        def copy_component(file, script, obj, parent, **changes):
            fields = file.read(obj)[0]
            script_ref = fields["m_Script"]
            if script_ref["m_FileID"] or script_ref["m_PathID"] not in file.by_pid:
                raise ValueError(f"{script} has an unsupported script reference")
            if struct.unpack_from("<iq", file.raw(obj), 16) != (0, script_ref["m_PathID"]):
                raise ValueError(f"{script} has an unsupported component layout")
            source_type = file.types[obj.tidx]
            for existing in components.get(script, []):
                if F.types[existing.tidx].script_hash != source_type.script_hash:
                    raise ValueError(f"{script} differs between the donor and recipient bundles")
            fields.update(changes)
            raw = file.write(obj, fields)
            return add_script_component(script, file.types[obj.tidx].raw,
                                        file.raw(file.by_pid[script_ref["m_PathID"]]),
                                        raw, parent, lambda _: None)

        def add_restock_menu(donor, actions):
            # Preserve the donor's two numbered restock controls and its submenu metadata.
            file, catalogue = donor
            action_fields = [file.read(obj)[0] for obj in actions]
            parents = {fields["m_GameObject"]["m_PathID"] for fields in action_fields}
            if len(parents) != 1 or {fields["radialMenuPosition"] for fields in action_fields} != {2, 3}:
                raise ValueError("expected the Celestra's two Restock Munitions controls")
            original_go = file.by_pid[parents.pop()]
            go = file.read(original_go)[0]
            if go["m_Name"] != "RestockMunitions":
                raise ValueError("expected a RestockMunitions menu in the donor")
            original_transform = next(file.by_pid[entry["component"]["m_PathID"]]
                                      for entry in go["m_Component"]
                                      if file.by_pid[entry["component"]["m_PathID"]].classid == 4)
            back, = [obj for obj in catalogue.get("BackAction", [])
                     if file.read(obj)[0]["m_GameObject"] == ptr(original_go.pid)]
            submenu, = [obj for obj in catalogue.get("SubMenuAction", [])
                        if file.read(obj)[0]["SubMenu"] == ptr(original_go.pid)]
            parent_go, position = alloc_action_pos(7)
            actions_go = val(components["MoveAction"][0].pid)["m_GameObject"]["m_PathID"]
            actions_transform = next(entry["component"]["m_PathID"] for entry in val(actions_go)["m_Component"]
                                     if F.by_pid[entry["component"]["m_PathID"]].classid == 4)
            # Submenus are sibling transforms under Actions, including when linked from Abilities.
            new_go, new_transform = newpid(), newpid()
            transform = file.read(original_transform)[0]
            transform.update(m_GameObject=ptr(new_go), m_Father=ptr(actions_transform), m_Children=[])
            go_type = F.by_pid[actions_go].tidx
            transform_type = F.by_pid[actions_transform].tidx
            appended.append((new_transform, transform_type, F.write(transform_type, transform)))
            new_pids.extend([new_go, new_transform])
            late_children.setdefault(actions_transform, []).append(new_transform)
            copy_component(file, "BackAction", back, new_go)
            for obj in sorted(actions, key=lambda entry: file.read(entry)[0]["radialMenuPosition"]):
                copy_component(file, "ResupplyMunitionAction", obj, new_go)
            copy_component(file, "SubMenuAction", submenu, parent_go,
                           SubMenu=ptr(new_go), radialMenuPosition=position)
            go["m_Component"] = [{"component": ptr(pid)} for pid in [new_transform] + comp_add.pop(new_go)]
            appended.append((new_go, go_type, F.write(go_type, go)))

        bays = [val(obj.pid) for obj in components.get("LaunchPoint", [])]
        for ability, donor_path in (support_abilities or {}).items():
            module, action, _ = SUPPORT_ABILITIES[ability]
            existing_module = components.get(module, [])
            existing_action = components.get(action, [])
            if ability == "turnaround_repair_bay" and existing_module and existing_action:
                if len(existing_module) > len(existing_action):
                    raise ValueError("this ship has more repair modules than repair actions; remove extras before rebuilding")
                if len(existing_action) > turnaround_repair_actions:
                    raise ValueError("this ship already has more turnaround repair actions than requested; remove extras manually first")
                for _ in range(len(existing_action) - len(existing_module)):
                    copy_component(F, module, existing_module[0], root.pid)
                if len(existing_action) == turnaround_repair_actions:
                    print(f"turnaround repair already has {len(existing_action)} indexed module/action slot(s); kept them")
                    continue
                menu_pid = named_menu("Fighters")
                if menu_pid is None:
                    raise ValueError("turnaround repair bay requires a Fighters menu")
                used = set()
                for entry in current_fields(menu_pid)["m_Component"]:
                    fields = current_fields(entry["component"]["m_PathID"])
                    if "radialMenuPosition" in fields and not fields.get("hideInMenu"):
                        used.add(fields["radialMenuPosition"])
                for _ in range(turnaround_repair_actions - len(existing_action)):
                    position = next((pos for pos in (3, 2, 1, 4, 5, 6, 7) if pos not in used), None)
                    if position is None:
                        raise ValueError("the Fighters menu has no free position for another turnaround repair action")
                    copy_component(F, module, existing_module[0], root.pid)
                    copy_component(F, action, existing_action[0], menu_pid,
                                   radialMenuPosition=position, hideInMenu=0)
                    used.add(position)
                print(f"added {turnaround_repair_actions - len(existing_action)} turnaround repair action(s) to Fighters")
                continue
            if ability == "turnaround_repair_bay" and (existing_module or existing_action):
                raise ValueError("turnaround repair module and action must both exist before adding another action")
            repair_plating = ability == "deploy_plating" and len(existing_module) == len(existing_action) == 1
            if len(existing_module) == len(existing_action) == 1 and not repair_plating:
                print(f"{ability.replace('_', ' ')} is already installed; kept its existing action")
                continue
            if ability in support_settings and len(existing_module) == 1 and existing_action and not repair_plating:
                continue
            if (existing_module or existing_action) and not repair_plating:
                raise ValueError(f"this bundle already has {ability.replace('_', ' ')}")
            if ability == "turnaround_repair_bay" and donor_path is None:
                raise ValueError("this bundle has no turnaround repair action; add it first with --turnaround-repair-bay and --defender-donor")
            if donor_path not in donors:
                donors[donor_path] = load_support_donor(donor_path)
            donor = donors[donor_path]
            file, catalogue = donor
            source = donor_component(donor, module)
            module_fields = file.read(source)[0]
            references = [value for key, value in module_fields.items()
                          if key not in ("m_GameObject", "m_Script")
                          and isinstance(value, dict) and "m_FileID" in value]
            if references and (ability != "deploy_plating" or references != [module_fields["droneTemplate"]]
                               or not 0 < references[0]["m_FileID"] <= struct.unpack_from("<i", external_table(file))[0]
                               or not references[0]["m_PathID"]
                               or external_table(F) != external_table(file)):
                raise ValueError(f"{module} has an external reference that does not match the recipient bundle")
            if ability == "deploy_plating":
                drone = module_fields["droneTemplate"]
                if not references or drone not in references:
                    raise ValueError("the donor has no external plating drone template")
                donor_bundle, = [obj for obj in file.objs if obj.classid == 142]
                donor_preload = file.read(donor_bundle)[0]["m_PreloadTable"]
                if drone not in donor_preload:
                    raise ValueError("the donor does not preload its plating drone")
                # Carry the donor's external preload dependencies. File IDs are safe to
                # reuse because the complete external tables were checked above.
                external_preloads.extend(ref for ref in donor_preload
                                         if ref["m_FileID"] and ref["m_PathID"])
                if repair_plating:
                    if val(existing_module[0].pid)["droneTemplate"] != drone:
                        raise ValueError("the existing plating drone differs from the donor")
                    print("updated Deploy Plating drone preload dependencies")
                    continue
            if ability in ("technical_support", "marine_reinforcement") and not any(
                bay["CanLaunch"] and 2 in bay["LaunchCompatibility"] for bay in bays
            ):
                raise ValueError(f"{ability.replace('_', ' ')} requires a Raptor-compatible launch bay")
            if ability == "turnaround_repair_bay":
                docks = [obj for obj in components.get("LaunchPoint", []) if val(obj.pid)["CanDock"]]
                if not docks:
                    raise ValueError("turnaround repair bay requires an existing docking bay")
                dock = min(docks, key=lambda obj: bool(val(obj.pid)["CanLaunch"]))
                dock_fields = copy.deepcopy(current_fields(dock.pid))
                donor_dock = next(file.read(obj)[0] for obj in catalogue.get("LaunchPoint", [])
                                  if file.read(obj)[0]["CanDock"])
                dock_fields["LaunchCompatibility"] = sorted(set(dock_fields["LaunchCompatibility"])
                                                            | set(donor_dock["LaunchCompatibility"]))
                if dock_fields["CanLaunch"]:
                    dock_go = F.by_pid[dock_fields["m_GameObject"]["m_PathID"]]
                    go = copy.deepcopy(val(dock_go.pid))
                    transform_obj = next(F.by_pid[entry["component"]["m_PathID"]] for entry in go["m_Component"]
                                         if F.by_pid[entry["component"]["m_PathID"]].classid == 4)
                    transform = copy.deepcopy(val(transform_obj.pid))
                    parent = transform["m_Father"]["m_PathID"]
                    if not parent or parent not in F.by_pid:
                        raise ValueError("the docking bay needs a local parent transform")
                    go_id, transform_id, dock_id = newpid(), newpid(), newpid()
                    go.update(m_Name="TurnaroundLandingBay",
                              m_Component=[{"component": ptr(pid)} for pid in (transform_id, dock_id)])
                    transform.update(m_GameObject=ptr(go_id), m_Children=[])
                    dock_fields.update(m_GameObject=ptr(go_id), CanLaunch=0, iconPosition=ptr(0))
                    for pid, obj, fields in ((go_id, dock_go, go), (transform_id, transform_obj, transform),
                                             (dock_id, dock, dock_fields)):
                        appended.append((pid, obj.tidx, F.write(obj, fields)))
                        new_pids.append(pid)
                    late_children.setdefault(parent, []).append(transform_id)
                else:
                    replaced[dock.pid] = F.write(dock, dock_fields)
                menu_pid = named_menu("Fighters")
                if menu_pid is None:
                    raise ValueError("turnaround repair bay requires a Fighters menu")
                menu = F.by_pid[menu_pid]
                used = set()
                for entry in current_fields(menu.pid)["m_Component"]:
                    fields = current_fields(entry["component"]["m_PathID"])
                    if "radialMenuPosition" in fields and not fields.get("hideInMenu"):
                        used.add(fields["radialMenuPosition"])
                positions = []
                for _ in range(turnaround_repair_actions):
                    position = next((pos for pos in (3, 2, 1, 4, 5, 6, 7) if pos not in used), None)
                    if position is None:
                        break
                    positions.append(position)
                    used.add(position)
                if len(positions) != turnaround_repair_actions:
                    raise ValueError("the Fighters menu has no free position for turnaround repair")
                position = positions[0]
                parent_go = menu.pid
            elif ability != "targeted_resupply":
                parent_go, position = alloc_action_pos(7)
            changes = support_settings.get(ability, {})
            if not changes.keys() <= module_fields.keys():
                raise ValueError("the donor plating module lacks one or more requested fields")
            copy_component(file, module, source, root.pid, **changes)
            if ability == "targeted_resupply":
                add_restock_menu(donor, catalogue.get(action, []))
            elif ability == "turnaround_repair_bay":
                for _ in range(1, len(positions)):
                    copy_component(file, module, source, root.pid, **changes)
                for action_position in positions:
                    copy_component(file, action, donor_component(donor, action), parent_go,
                                   radialMenuPosition=action_position, hideInMenu=0)
            else:
                copy_component(file, action, donor_component(donor, action), parent_go,
                               radialMenuPosition=position, hideInMenu=0)
            print(f"added {ability.replace('_', ' ')}" +
                  (f" with {len(positions)} repair actions" if ability == "turnaround_repair_bay" else ""))

    # Bundles saved by older versions may carry the removed legacy hooks: a negative Hull Resin
    # rate (armour regeneration) or ShipInfo.ShowExclusionInEditor (squadron revive). Clear them
    # when the replacement custom module is installed.
    if custom_armour_active:
        for resin in (o for o in F.objs if o.classid == 114 and
                      "hullPerTick" in val(o.pid) and val(o.pid)["m_GameObject"]["m_PathID"] == root.pid):
            fields = current_fields(resin.pid)
            if fields["hullPerTick"] < 0:
                fields["hullPerTick"] = abs(fields["hullPerTick"])
                replaced[resin.pid] = F.write(resin, fields)
                print("disabled the Hull Resin armour hack; retained positive hull regeneration")
    if custom_squadron_active:
        ship_infos = [o for o in F.objs if o.classid == 114 and
                      custom_scripts.get(val(o.pid).get("m_Script", {}).get("m_PathID")) == "ShipInfo" and
                      val(o.pid).get("m_GameObject", {}).get("m_PathID") == root.pid and
                      "ShowExclusionInEditor" in val(o.pid)]
        if len(ship_infos) != 1:
            raise ValueError("custom squadron replacement needs one ShipInfo with the legacy revive flag")
        ship_fields = current_fields(ship_infos[0].pid)
        if ship_fields["ShowExclusionInEditor"]:
            ship_fields["ShowExclusionInEditor"] = 0
            replaced[ship_infos[0].pid] = F.write(ship_infos[0], ship_fields)
            print("disabled the ShipInfo-flagged Munition Factory revive hack")
    for gp, pids in comp_add.items():
        new_menu = next((st for st in (sub_state, sub2_state) if gp == st.get("go") and "gov" in st), None)
        if new_menu is not None:                        # a submenu created in this run: its record is still pending
            gv = new_menu["gov"]
            for p_ in pids: gv["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": p_}})
            i_ = next(i for i, t in enumerate(appended) if t[0] == gp); appended[i_] = (gp, appended[i_][1], F.write(appended[i_][1], gv)); continue
        gv = copy.deepcopy(val(gp))
        if gp in replaced:
            gv = current_fields(gp)
        for p_ in pids: gv["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": p_}})
        replaced[gp] = F.write(F.by_pid[gp], gv)
    for st in (sub_state, sub2_state):                  # hang new ability submenus under the Actions object (like Munitions / Fighters)
        if "tr" in st:
            late_children.setdefault(st["father"], []).append(st["tr"])
    for fa, kids_ in late_children.items():
        if support_abilities and fa in replaced:
            fv = current_fields(fa)
        else:
            assert fa not in replaced, "internal: Actions transform was already rewritten"
            fv = copy.deepcopy(val(fa))
        for k_ in kids_: fv["m_Children"].append({"m_FileID": 0, "m_PathID": k_})
        replaced[fa] = F.write(F.by_pid[fa], fv)
    abv = copy.deepcopy(val(ab.pid))
    preload = abv["m_PreloadTable"]
    if external_preloads:
        entries = [entry["second"] for entry in abv["m_Container"]]
        if len(entries) != 1 or entries[0]["preloadIndex"] + entries[0]["preloadSize"] != len(preload):
            raise ValueError("unsupported ship prefab preload layout for Deploy Plating")
    added_preloads = [{"m_FileID": 0, "m_PathID": pid} for pid in new_pids]
    seen = {(ref["m_FileID"], ref["m_PathID"]) for ref in preload}
    for ref in external_preloads:
        key = ref["m_FileID"], ref["m_PathID"]
        if key not in seen:
            added_preloads.append(ref)
            seen.add(key)
    preload.extend(added_preloads)
    for entry in abv["m_Container"]:
        entry["second"]["preloadSize"] += len(added_preloads)
    replaced[ab.pid] = F.write(ab, abv)
    if prefab_fields:
        apply_prefab_fields(F, prefab_fields, replaced)
    if flak:
        from deadlock_tools import flak as flak_mod
        for line in flak_mod.apply(F, flak, replaced):
            print(line)
    if ship_scale:
        apply_ship_scale(F, ship_scale, replaced)
    for spec in respace or ():
        selector, _, gap_text = spec.rpartition("=")
        if not selector: raise ValueError("--respace needs SELECTOR=GAP, e.g. fore=4")
        respace_clones(F, RESS, replaced, selector, float(gap_text))
    new_cab = F.rebuild(replaced=replaced, appended=appended)
    if mirror_launch_bays:
        new_cab = mirror_fighter_bays(SerFile(new_cab), fighter_slots)
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
    ap = argparse.ArgumentParser(description="Edit a decrypted Deadlock ship bundle: clone / resize turrets, squadron and munition slots, ship systems and abilities.\nNothing is changed unless you pass its option. DLL patches live in dll.")
    g_weap = ap.add_argument_group("Weapons - cloning and resizing turrets")
    g_slot = ap.add_argument_group("Squadron / munition slots")
    g_sys = ap.add_argument_group("Ship systems")
    g_custom = ap.add_argument_group("Custom ship modules")
    g_abil = ap.add_argument_group("Abilities")
    g_out = ap.add_argument_group("Output")
    ap.epilog = """EXAMPLES
  Mercury, everything (writes to the configured work tree, encrypted and ready for the game):
    python -m deadlock_tools weapons colonialmercury --clone fore=3 --layout free --scale fore=0.7 --clone-scale 0.7 \\
        --fighter-slots 4 --utility-slots 2 --split-squadrons --munition-slots 4 --hull-regen 0.1 --munition-factory --custom-armour-repair --custom-squadron-replacement \\
        --mini-jump --augury --stealth colonialorion --ability-submenu
  List the turrets:        python -m deadlock_tools weapons colonialmercury --list-mounts
  Slot counts here must match the level files:  python -m deadlock_tools shipdb set "Colonial Mercury" FighterSlots 4
  Custom modules (--custom-*) need the custom-module DLL; the editor rebuilds it when it saves the bundle.
LIMITS
  Radial menus hold 8 buttons (positions 0-7): Fighters launch buttons <= 8 in total, munition slots <= 7 (plus Back), Actions menu 8 (use --ability-submenu)."""
    ap.formatter_class = argparse.RawDescriptionHelpFormatter
    ap.prog = "deadlock weapons"
    ap.usage = "deadlock weapons BUNDLE [DST] [options]      (BUNDLE = game bundle or .dec, path or name; options are grouped below)"
    ap.add_argument("src", metavar="BUNDLE", help="ship bundle (encrypted game file or decrypted .dec), a path or a name in the 'bundles' folder, e.g. colonialmercury")
    ap.add_argument("dst", nargs="?", help="optional temporary destination file")
    g_weap.add_argument("--offset", default="1.5,0,0", help="local-space x,y,z offset of each clone (default 1.5,0,0)")
    g_weap.add_argument("--copies", type=int, default=0, help="clone every weapon mount N extra times (1 = double). Default 0: weapons are NOT touched unless you ask")
    g_weap.add_argument("--clone", action="append", default=[], metavar="SELECTOR=N", help="clone only the mounts matching SELECTOR N times; repeatable, later ones win, and it overrides --copies for those mounts. SELECTOR: all, sides (= port + starboard), port, starboard, fore, rear, dorsal, ventral, type:N (weapon type id), name:TEXT (part of the mount name). N=0 excludes. e.g. --clone fore=1 --clone dorsal=2")
    g_weap.add_argument("--layout", choices=["offset", "mid", "free"], default="offset", help="where clones go: 'offset' = shifted by --offset (x mirrored on the port side, so symmetric); 'mid' = slid along the row to sit BETWEEN existing turrets, following the hull (see --gap); 'free' = each clone goes to the nearest EMPTY spot next to its turret on the same hull surface (needs the hull mesh; see --min-gap)")
    g_weap.add_argument("--min-gap", type=float, help="--layout free: smallest distance between a clone and any other turret (default: 0.9 x the closest turret spacing on that side, scaled by --clone-scale)")
    g_weap.add_argument("--gap", type=float, default=1.5, help="--layout mid: how far (units) a single clone slides towards the next turret in its row (default 1.5, half the usual 3-unit turret spacing)")
    g_weap.add_argument("--no-mirror", action="store_true", help="--layout offset: apply --offset as-is to every mount (old behaviour, not symmetric)")
    g_weap.add_argument("--respace", action="append", default=[], metavar="SELECTOR=GAP", help="move the existing clones (..._x2, _x3) of the matching turrets so every turret there is at least GAP game units from every other (after --ship-scale), on the same hull surface, mirrored; originals stay put. Stock spacing is 4 between full-size turrets, e.g. --respace fore=4 --scale fore=1")
    g_weap.add_argument("--ship-scale", type=float, metavar="S", help="set the WHOLE ship's size (model, turrets, launch bays, collision zones, hit points) to S times the stock size, e.g. 1.25 = 25%% bigger; repeating it is harmless. Also scales its collision exclusion radius and miss-sphere radius; set the same CollisionExclusionZoneRadius on the Ships page so fleet deployment spacing matches")
    g_weap.add_argument("--clone-scale", type=float, metavar="S", help="size of the CLONED turrets (1 = same as the original, 0.6 = 60%% - mesh, barrel position and collider all shrink). Helps clones fit beside their neighbours")
    g_weap.add_argument("--scale", action="append", default=[], metavar="SELECTOR=S", help="resize EXISTING turrets matching SELECTOR (same selectors as --clone), repeatable, e.g. --scale fore=0.7. Visual/physical size only; damage and range are unchanged")
    g_weap.add_argument("--list-mounts", action="store_true", help="print every weapon mount (name, quadrant, weapon type, position) and the groups usable with --clone, then exit")
    g_slot.add_argument("--munition-slots", type=int, help="make the prefab have this many munition slot actions (needed for the battle UI to show them). Mercury: 4")
    g_slot.add_argument("--fighter-slots", type=int, help="final FighterSlots value (as set in the level files) - adds launch buttons/bays. Mercury default is 3")
    g_slot.add_argument("--utility-slots", type=int, help="final UtilityFighterSlots value (support squadrons). Mercury default is 1")
    g_slot.add_argument("--bay-offset", default="0,3,4", help="x,y,z shift applied to each new launch bay (default 0,3,4)")
    g_sys.add_argument("--hull-regen", type=float, help="add a Regenerative Hull Resin module (passive hull repair, scaled by Engineering) with this hullPerTick; the Cylon modern basestar uses 0.2")
    g_sys.add_argument("--munition-factory", action="store_true", help="add the Munition Factory module (passive munition resupply, as on the Cylon Argos). Its speed comes from the ship's Armory MunitionResupplyRange; use --custom-squadron-replacement to rebuild destroyed squadrons")
    g_custom.add_argument("--custom-instant-plating", action="store_true", help="add Instant Plating to Actions; applies 200 stacking extra armour per facing on activation; requires the custom-module DLL")
    g_custom.add_argument("--custom-armour-repair", action="store_true", help="add the standalone armour repair module; switches Hull Resin back to positive hull-only repair and disables the old sign-based armour hook; requires the custom-module DLL")
    g_custom.add_argument("--custom-squadron-replacement", action="store_true", help="add the standalone destroyed-squadron replacement module and clear the legacy ShipInfo revive flag; keep Munition Factory for ordinary ammo resupply; requires the custom-module DLL")
    g_custom.add_argument("--custom-drone-resupply", action="store_true", help="add the standalone passive plating-drone resupply module (base rate defaults to 1 plating per battle tick); requires the custom-module DLL")
    g_custom.add_argument("--custom-magazine-capacity", action="store_true", help="add the standalone magazine capacity multiplier module; requires the custom-module DLL")
    g_custom.add_argument("--custom-emp-pulse", action="store_true", help="add Experimental EMP: queue a start-of-turn pulse that disrupts enemy ships/fighters, jams fighters with the native evade penalty, and destroys enemy missiles; range, delay, cooldown, stun and jam duration are configurable; requires the custom-module DLL")
    g_custom.add_argument("--custom-targeted-emp", action="store_true", help="add Targeted EMP: each per-turn charge selects one PCM 3D point; all queued enemy-only bursts fire at turn start; charge count, target range, blast size, delay, burst spacing, fighter stun and evade-jam durations are configurable; requires the custom-module DLL")
    g_custom.add_argument("--subsystem-modifier", action="append", default=[], metavar="STAT:SOURCE:MIN:MAX", help="replace the ship's subsystem crew modifiers with these entries (repeatable): crew STAT (number or name, e.g. 102 or squadron_repair) gets a bonus from SOURCE subsystem's health (0-6 or name; empty = the stat's own subsystem), MIN at 0 health to MAX at full health, in veterancy BonusAmount units (0.25 = +25%% for percent stats). See docs/game/subsystems.md; requires the custom-module DLL")
    g_custom.add_argument("--clear-subsystem-modifiers", action="store_true", help="remove the ship's subsystem crew modifiers component")
    g_custom.add_argument("--module-subsystem", action="append", default=[], metavar="MODULE:SOURCE:MIN:MAX", help="replace the ship's module subsystem drivers with these entries (repeatable): MODULE (hull_regeneration, munition_factory, custom_armour_repair, custom_drone_resupply, custom_squadron_replacement, custom_magazine_capacity, or 0-5) is driven by SOURCE subsystem (0-6 or name) at MIN %% at 0 health to MAX %% at full health; several entries for one module add up and replace its stock subsystem factor (Hull Resin: Engineering, Munition Factory: Armoury; custom modules: none = 100 %%). Requires the custom-module DLL")
    g_custom.add_argument("--clear-module-subsystems", action="store_true", help="remove the ship's module subsystem drivers (modules go back to their stock subsystem)")
    g_weap.add_argument("--no-hull-follow", action="store_true", help="skip the hull check. By default every clone is raised out of the hull if it would sit lower than the mount it was copied from (reads the hull mesh from the bundle)")
    g_abil.add_argument("--mini-jump", action="store_true", help="give the ship the micro-FTL jump (Cylon Argos style): adds the MiniJumpAgent and the jump button in the Actions radial menu. The ship needs an FTLEffect component (Mercury, Jupiter, Argos have one)")
    g_abil.add_argument("--jump-range", default="200,10", help="--mini-jump: horizontal,vertical jump range in game units (default 200,10 = Argos)")
    g_abil.add_argument("--jump-cooldown", type=int, default=5, help="--mini-jump: cooldown in turns (default 5)")
    g_abil.add_argument("--jump-delay", type=int, default=50, help="--mini-jump: delay before the jump in ticks (default 50 = Argos)")
    g_abil.add_argument("--jump-duration", type=int, default=3, help="--mini-jump: jump duration (default 3 = Argos)")
    g_abil.add_argument("--ability-submenu", action="store_true", help="group the added Actions-menu abilities under one Abilities button (requires a Fighters menu as a template)")
    g_abil.add_argument("--normalize-ability-submenu", action="store_true", help="rename the existing Actions-menu ability submenu to Abilities without adding abilities")
    g_abil.add_argument("--stealth-mesh", choices=["ship", "orion"], default="ship", help="mesh for the stealth projection effect: the ship's own hull (default) or the Orion-shaped mesh copied from the donor")
    g_slot.add_argument("--squadron-groups", help="split the squadron launch buttons into submenus inside the Fighters menu, e.g. 4,2 (slot counts per submenu, in slot order; must add up to FighterSlots+UtilityFighterSlots)")
    g_slot.add_argument("--split-squadrons", action="store_true", help="shorthand: one submenu for the fighter slots and one for the support (utility) slots")
    g_slot.add_argument("--icon-offset", default="12,0,-4", help="where the build-screen icons of the NEW squadron bays sit relative to the copied bay icon, X,Y,Z per added slot (X is towards the centre line for both sides). Raise it if the new slot icons overlap the old ones")
    g_abil.add_argument("--jamming", action="store_true", help="give the ship the Cylon Wardriver's Jamming mode: adds a Jammer (jams enemy squadrons and missiles in range) and the Jamming button. Run again with new --jam-range / --jam-missile-chance to change it")
    g_abil.add_argument("--jam-range", type=float, help="--jamming: jam range in game units (default 500; the Wardriver squadron has 150)")
    g_abil.add_argument("--jam-missile-chance", type=float, help="--jamming: chance (0-1) to jam each missile in range (default 0.6, the Wardriver's)")
    g_abil.add_argument("--jam-speed", help="jamming: speed penalty this ship's jamming gives (number, or 'global'); written to deadlock-jamming.txt, needs the custom-module DLL's CustomJamming")
    g_abil.add_argument("--jam-evade", help="jamming: evasion penalty this ship's jamming gives (number, or 'global')")
    g_abil.add_argument("--jam-accuracy", help="jamming: accuracy penalty this ship's jamming gives (number, or 'global')")
    g_abil.add_argument("--augury", action="store_true", help="give the ship the Cylon guardian basestar's Predictive Augury ability (module + button in the Actions radial menu)")
    g_abil.add_argument("--augury-uses", default="1", help="--augury: number of uses per battle (maxAmmunition, default 1 = the guardian basestar). The game has no true unlimited setting: 'unlimited' = 999, which the button shows as 999/999")
    g_abil.add_argument("--stealth", metavar="ORION_BUNDLE", help="give the ship the Colonial Orion's stealth ability: path to the Orion bundle (encrypted game file or decrypted .dec). Adds the Stealth module, the button in the Actions radial menu and the two stealth effect objects")
    g_abil.add_argument("--stealth-battery", type=float, help="stealth battery capacity (Orion: 100)")
    g_abil.add_argument("--stealth-start", choices=["on", "off"], help="start battles stealthed (Orion: on)")
    g_abil.add_argument("--turnaround-repair-bay", action="store_true", help="add Defender emergency squadron repair to the Fighters menu (requires an existing docking bay)")
    g_abil.add_argument("--turnaround-repair-actions", type=int, default=1, metavar="N", help="number of indexed turnaround repair bays, each with its own Fighters action and battle-panel slot button (1-8)")
    g_abil.add_argument("--targeted-resupply", action="store_true", help="add the Celestra Restock Munitions submenu and targeted resupply module")
    g_abil.add_argument("--deploy-plating", action="store_true", help="add Celestra armour plating drones (requires the matching shared asset reference)")
    for options in SUPPORT_SETTINGS.values():
        for option, (field, value_type) in options.items():
            description = PLATING_OPTIONS.get(option, (field, f"the {field} setting"))[1]
            choices = ("on", "off") if field == "unlimitedSpareParts" else None
            metavar = {"fullRepairPercent": "0..1", "moveOnLaunchSquadronOffset": "X,Y,Z"}.get(field)
            g_abil.add_argument("--" + option.replace("_", "-"), type=value_type, choices=choices,
                                metavar=metavar, help=f"set {description}; blank keeps the source value")
    g_abil.add_argument("--technical-support", action="store_true", help="add Defender subsystem support (requires a Raptor-compatible launch bay and a Raptor in battle)")
    g_abil.add_argument("--marine-reinforcement", action="store_true", help="add Defender friendly marine reinforcement (requires a Raptor-compatible launch bay and a Raptor in battle)")
    g_abil.add_argument("--defender-donor", help="bundle path or name to copy Defender abilities from (default: colonialdefender in the configured bundle folder)")
    g_abil.add_argument("--celestra-donor", help="bundle path or name to copy Celestra abilities from (default: colonialcelestra in the configured bundle folder)")
    g_slot.add_argument("--bay-layout", metavar="FILE.json", help="edit launch bays, munition launchers and slot buttons from a JSON spec: {bays: [{id, position, slot, canLaunch, canDock, dockRange, compat}], launchers: [{id, position, launchPoints}], removeSlots: [slot], removeMunitions: [button id], removeLaunchers: [id], revision?}. Ids come from --list-bays. Removing slots does not change the level files: lower FighterSlots / UtilityFighterSlots / MunitionSlots to match. See docs/game/launch-bays.md")
    g_slot.add_argument("--realign-bays", action="store_true", help="lay the cloned launch bays (and the launch+dock bays they were copied from, Mercury: PortRear / StarboardRear) out as mirrored port/starboard pairs in slot order, in a grid starting at the stock bay; each takes its side's rotation, launch path and icon offset. Runs after the other options; --bay-layout entries for the same bay win. See docs/game/launch-bays.md")
    g_slot.add_argument("--list-bays", action="store_true", help="print the launch bays, launch and munition buttons and munition launchers with the ids --bay-layout uses, then exit")
    g_slot.add_argument("--align-launch-bays", action="store_true", help="match launch bay compatibility to the requested fighter and utility slot counts")
    g_slot.add_argument("--mirror-launch-bays", action="store_true", help="mirror adjacent fighter launch bays across the ship in port/starboard pairs")
    g_slot.add_argument("--bay-icon", action="append", default=[], metavar="SLOT=X,Y,Z", help="set the local icon marker position for an existing or newly cloned bay (repeatable; use --inspect to see current coordinates)")
    g_slot.add_argument("--slot-parts", default="buttons,bays,icons", help="(for testing) which parts --fighter-slots adds: any of buttons, bays, icons")
    g_out.add_argument("--mount-layout", help=argparse.SUPPRESS)
    g_out.add_argument("--prefab-fields", type=json.loads, help=argparse.SUPPRESS)
    g_abil.add_argument("--flak", action="append", default=[], metavar="FIELD=VALUE",
                        help="edit the ship's flak field (repeatable), e.g. --flak FlakRange.Min=120 --flak BaseDamagePerTick.HullDamage=1.5; "
                             "fields: BaseDamagePerTick.{HullDamage,ArmourDamage,SubSystemDamage,SubsystemSpreadCount,SubsystemSpreadDamage,"
                             "AppliesIrradiatedStatus,IrradiatedTurns,IrradiatedPercentage,Range}, FlakRange.Min/Max, FlakAreaLength, FlakAreaRadius, "
                             "FlakExplosionsPerTick (see docs/game/flak.md); --inspect shows the current values")
    g_out.add_argument("--inspect", action="store_true", help="report weapon mounts and current prefab settings")
    g_out.add_argument("--no-encrypt", action="store_true", help="write a plain bundle into the configured work tree")
    g_out.add_argument("--encrypt", action="store_true", help=argparse.SUPPRESS)      # the default now; kept so old commands still work
    config.add_path_args(g_out, "out", "bundles")
    return ap


def main():
    ap = build_parser()
    a = ap.parse_args()
    off = tuple(float(x) for x in a.offset.split(","))
    original = find_bundle(a.src, a.bundles); a.src = decrypted(original)
    if a.stealth: a.stealth = find_bundle(a.stealth, a.bundles)
    stem = os.path.basename(a.src)[:-4] if a.src.endswith(".dec") else os.path.basename(a.src)
    a.encrypt = not a.no_encrypt
    if a.list_bays:
        from deadlock_tools import bays as bay_mod
        print_bay_layout(bay_mod.read_layout(a.src)); return
    if a.list_mounts or a.inspect: a.dst = os.devnull
    elif not a.dst: a.dst = os.path.join(config.out_dir("bundles", a.out), stem if a.encrypt else stem + ".dec")
    for f in (original, a.src):
        if os.path.exists(a.dst) and config.same_file(a.dst, f): raise SystemExit("refusing to overwrite the input %s" % f)
    os.makedirs(os.path.dirname(os.path.abspath(a.dst)), exist_ok=True)
    tmp = a.dst + ".tmp" if a.encrypt and not (a.list_mounts or a.inspect) else a.dst
    support_abilities = {}
    if not (a.list_mounts or a.inspect):
        if not 1 <= a.turnaround_repair_actions <= 8:
            raise SystemExit("--turnaround-repair-actions must be between 1 and 8")
        for ability, (_, _, donor) in SUPPORT_ABILITIES.items():
            if getattr(a, ability):
                name = getattr(a, donor + "_donor")
                path = find_bundle(name, a.bundles) if name else default_support_donor(donor, a.bundles, ability)
                support_abilities[ability] = decrypted(path)
        if a.turnaround_repair_actions > 1 and "turnaround_repair_bay" not in support_abilities:
            support_abilities["turnaround_repair_bay"] = None
    try:
        from deadlock_tools import subsystem_modifiers as sm
        if a.subsystem_modifier and a.clear_subsystem_modifiers:
            raise ValueError("use either --subsystem-modifier or --clear-subsystem-modifiers, not both")
        modifier_entries = [sm.parse_spec(text) for text in a.subsystem_modifier]
        from deadlock_tools import module_subsystems as msub
        if a.module_subsystem and a.clear_module_subsystems:
            raise ValueError("use either --module-subsystem or --clear-module-subsystems, not both")
        driver_entries = [msub.parse_spec(text) for text in a.module_subsystem]
        bay_spec = None
        if a.bay_layout and not (a.list_mounts or a.inspect):
            with open(a.bay_layout, encoding="utf-8") as source:
                bay_spec = json.load(source)
            if not isinstance(bay_spec, dict):
                raise ValueError("--bay-layout must hold a JSON object")
            if bay_spec.get("revision") and bay_spec["revision"] != layout_revision(read_bundle(a.src)):
                raise ValueError("The bundle changed since the bay layout was read. Reload it before saving.")
            if bay_spec.get("removeSlots") or bay_spec.get("removeMunitions"):
                from deadlock_tools import bays as bay_mod
                current = bay_mod.read_layout(a.src)
                adds_launch = a.fighter_slots is not None and a.utility_slots is not None and \
                    a.fighter_slots + a.utility_slots > len(current["launchButtons"])
                adds_munitions = a.munition_slots is not None and a.munition_slots > len(current["munitionButtons"])
                if adds_launch or adds_munitions:
                    raise ValueError("remove slots with --bay-layout or add them with --fighter-slots / --utility-slots / "
                                     "--munition-slots, not both in one run")
        from deadlock_tools import flak as flak_mod
        flak_changes = flak_mod.parse(a.flak) if a.flak and not (a.list_mounts or a.inspect) else None
        mount_layout = None
        if a.mount_layout:
            with open(a.mount_layout, encoding="utf-8") as source:
                mount_layout = json.load(source)
        double_weapons(
            a.src, tmp,
            offset=off, copies=a.copies,
            munition_slots=a.munition_slots, fighter_slots=a.fighter_slots, utility_slots=a.utility_slots,
            bay_offset=tuple(map(float, a.bay_offset.split(","))),
            hull_regen=a.hull_regen,
            clones=[(selector, int(count)) for selector, count in (item.rsplit("=", 1) for item in a.clone)],
            list_mounts=a.list_mounts, clone_scale=a.clone_scale,
            scales=[(selector, float(scale)) for selector, scale in (item.rsplit("=", 1) for item in a.scale)],
            layout=a.layout, gap=a.gap, mirror=not a.no_mirror,
            munition_factory=a.munition_factory,
            custom_modules=[cls for cls, enabled in (
                ("CustomArmourRepairModule", a.custom_armour_repair),
                ("CustomInstantPlatingModule", a.custom_instant_plating),
                ("CustomSquadronReplacementModule", a.custom_squadron_replacement),
                ("CustomDroneResupplyModule", a.custom_drone_resupply),
                ("CustomMagazineCapacityModule", a.custom_magazine_capacity),
                ("CustomEmpPulseModule", a.custom_emp_pulse),
                ("CustomTargetedEmpModule", a.custom_targeted_emp),
            ) if enabled],
            hull_follow=not a.no_hull_follow, min_gap=a.min_gap,
            mini_jump={"delay": a.jump_delay, "duration": a.jump_duration,
                       "range": tuple(map(float, a.jump_range.split(","))),
                       "cooldown": a.jump_cooldown} if a.mini_jump else None,
            jamming={"range": a.jam_range, "missile_chance": a.jam_missile_chance} if a.jamming else None,
            augury={"uses": 999 if a.augury_uses.lower() in ("unlimited", "inf", "infinite")
                    else int(a.augury_uses)} if a.augury else None,
            stealth={"donor": a.stealth, "battery": a.stealth_battery,
                     "start_on": None if a.stealth_start is None else a.stealth_start == "on"}
                    if a.stealth or a.stealth_battery is not None or a.stealth_start is not None else None,
            slot_parts=a.slot_parts, ability_submenu=a.ability_submenu, stealth_mesh=a.stealth_mesh,
            squadron_groups=([a.fighter_slots, a.utility_slots] if a.split_squadrons else
                             [int(value) for value in a.squadron_groups.split(",")] if a.squadron_groups else None),
            icon_offset=tuple(map(float, a.icon_offset.split(","))),
            inspect=a.inspect, support_abilities=support_abilities, prefab_fields=a.prefab_fields, ship_scale=a.ship_scale, respace=a.respace,
            align_launch_bays=a.align_launch_bays, mirror_launch_bays=a.mirror_launch_bays, bay_icons=parse_bay_icons(a.bay_icon),
            normalize_ability_submenu=a.normalize_ability_submenu,
            support_settings=configured_support_settings(a) if not (a.list_mounts or a.inspect) else {},
            turnaround_repair_actions=a.turnaround_repair_actions, mount_layout=mount_layout,
            flak=flak_changes,
        )
        if a.realign_bays and not (a.list_mounts or a.inspect):
            from deadlock_tools import bays as bay_mod
            rows, notes = bay_mod.realign_spec(bay_mod.read_layout(tmp))
            for line in notes: print("realign bays: " + line)
            own = {str(row["id"]): row for row in (bay_spec or {}).get("bays") or []}
            merged = [dict(row, **own.pop(str(row["id"]), {})) for row in rows] + list(own.values())
            bay_spec = dict(bay_spec or {}, bays=merged)
        if bay_spec:
            from deadlock_tools import bays as bay_mod
            spec = {key: value for key, value in bay_spec.items() if key != "revision"}   # checked against the source above
            result = bay_mod.apply(tmp, tmp, spec, encrypt=False)
            for line in result["log"]: print("bay layout: " + line)
            print("bay layout: %d bays, %d launch buttons, %d munition buttons, %d munition launchers" % (
                result["bays"], result["launchButtons"], result["munitionButtons"], result["launchers"]))
        if a.inspect:
            bundle = read_bundle(a.src)
            offset, size, *_ = bundle["nodes"][0]
            for line in flak_mod.describe(flak_mod.read(SerFile(bundle["data"][offset:offset + size]))):
                print(line)
            entries = sm.read(a.src)["entries"]
            print("subsystem crew modifiers: %s" % (len(entries) or "none"))
            for entry in entries: print("  " + sm.describe(entry))
            drivers = msub.read(a.src)["entries"]
            print("module subsystem drivers: %s" % (len(drivers) or "none (stock subsystems)"))
            for entry in drivers: print("  " + msub.describe(entry))
        elif (driver_entries or a.clear_module_subsystems) and not a.list_mounts:
            drivers = msub.apply(tmp, tmp, driver_entries)["entries"]
            print("module subsystem drivers: %d entr%s" % (len(drivers), "y" if len(drivers) == 1 else "ies") if drivers else "module subsystem drivers removed")
            for entry in drivers: print("  " + msub.describe(entry))
        elif (modifier_entries or a.clear_subsystem_modifiers) and not a.list_mounts:
            entries = sm.apply(tmp, tmp, modifier_entries)["entries"]
            print("subsystem crew modifiers: %d entr%s" % (len(entries), "y" if len(entries) == 1 else "ies") if entries else "subsystem crew modifiers removed")
            for entry in entries: print("  " + sm.describe(entry))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if a.list_mounts or a.inspect: return
    penalties = {key: (None if str(getattr(a, opt)).strip().lower() in ("global", "-", "") else getattr(a, opt))
                 for opt, key in JAM_PENALTY_OPTIONS.items() if getattr(a, opt) is not None}
    if penalties:
        try:
            print(set_ship_jamming(tmp, os.path.dirname(a.dst), [a.bundles] if a.bundles else [], penalties))
        except ValueError as error:
            raise SystemExit(str(error)) from None
    if a.encrypt:
        open(a.dst, "wb").write(xor_bytes(open(tmp, "rb").read())); os.remove(tmp)
        print("encrypted ->", a.dst)
    print("Back up the game's %s, then replace it with %s." % (stem, a.dst) if a.encrypt else "Decrypted result: %s (encrypt it with 'xor encrypt' before use)." % a.dst)

if __name__ == "__main__":
    main()
