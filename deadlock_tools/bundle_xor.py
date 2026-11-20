#!/usr/bin/env python3
"""
xor - decrypt / re-encrypt BSG Deadlock ship bundles (repeating-key XOR over the whole file).

The bundles are normal UnityFS bundles (Unity 2018.4.3f1, LZMA) that have been XORed byte-by-byte with a short
repeating key. XOR is symmetric, so the same operation decrypts and re-encrypts.

Where results go (the same places every tool uses, see deadlock.json):
    decrypt  -> work/.deadlock-tools/decrypted/<name>.dec
    encrypt  -> work/BSG_Data/StreamingAssets/<name> (the ".dec" is dropped)
A folder is processed recursively and its sub-folder layout is kept.

Usage:
    python -m deadlock_tools xor decrypt  colonialmercury        a file, or a name looked up in the 'bundles' folder
    python -m deadlock_tools xor decrypt  [FOLDER]               every file (default: the 'bundles' folder)
    python -m deadlock_tools xor encrypt  colonialmercury.dec    (a bare name is looked up in the work scratch folder)
    python -m deadlock_tools xor encrypt  FOLDER
    python -m deadlock_tools xor check    colonialmercury        just report, write nothing

Options:
    --bundles DIR         where bare bundle names are looked up
    --key 0f0f070d0507    override the key (hex). By default the key is recovered
                          automatically from the "UnityFS" header of each file.
"""
import argparse
import os
from pathlib import Path
import struct
import sys

if __package__ in (None, ""): sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deadlock_tools import config

DEFAULT_KEY = bytes.fromhex("0f0f070d0507")  # recovered from colonialmercury
MAGIC = b"UnityFS\x00"


def xor(data: bytes, key: bytes) -> bytes:
    """Fast repeating-key XOR using big integers (no Python-level byte loop)."""
    n = len(data)
    if not key:
        return data
    reps = n // len(key) + 1
    keystream = (key * reps)[:n]
    out = int.from_bytes(data, "big") ^ int.from_bytes(keystream, "big")
    return out.to_bytes(n, "big")


def recover_key(head: bytes):
    """Known-plaintext recovery: file starts with 'UnityFS\\0' + 00 00 00 <ver>."""
    known = MAGIC + b"\x00\x00\x00"          # 11 bytes we are sure about
    if len(head) < len(known):
        return None
    ks = bytes(a ^ b for a, b in zip(head, known))
    for period in range(1, 9):               # smallest period consistent with all 11 bytes
        if all(ks[i] == ks[i % period] for i in range(len(known))):
            if period * 2 <= len(known):      # need to have seen the key at least twice
                return ks[:period]
    return None


def header_ok(plain: bytes, total_size: int):
    """Validate a decrypted UnityFS header. Returns (ok, description)."""
    if not plain.startswith(MAGIC):
        return False, "no UnityFS magic"
    o = len(MAGIC)
    ver = struct.unpack(">I", plain[o:o + 4])[0]
    o += 4
    j = plain.index(b"\0", o); player = plain[o:j].decode("latin-1"); o = j + 1
    j = plain.index(b"\0", o); rev = plain[o:j].decode("latin-1"); o = j + 1
    size, cs, us, flags = struct.unpack(">qIII", plain[o:o + 20])
    comp = {0: "none", 1: "LZMA", 2: "LZ4", 3: "LZ4HC"}.get(flags & 0x3F, "?")
    ok = size == total_size
    return ok, (f"UnityFS v{ver}, Unity {rev}, blockinfo compression {comp}, "
                f"declared size {size:,} {'== file size' if ok else '!= file size ' + format(total_size, ',')}")


def process(src: str, dst, key_override, mode: str):
    data = open(src, "rb").read()
    if mode == "encrypt":
        if not data.startswith(MAGIC):
            print(f"[skip] {src}: encryption requires a plain UnityFS bundle")
            return False
        key = key_override or DEFAULT_KEY
        if dst:
            open(dst, "wb").write(xor(data, key))
        print(f"[encrypted] {src} -> {dst}  (key {key.hex()})")
        return True

    # decrypt / check
    if data.startswith(MAGIC):
        print(f"[skip] {src}: already a plain UnityFS bundle")
        return False
    key = key_override or recover_key(data[:16]) or DEFAULT_KEY
    plain = xor(data, key)
    try:
        ok, desc = header_ok(plain, len(data))
    except Exception as e:  # noqa: BLE001
        ok, desc = False, f"header parse failed ({e})"
    if not ok:
        print(f"[FAIL] {src}: key {key.hex()} did not produce a valid bundle: {desc}")
        return False
    print(f"[ok]   {src}: key {key.hex()}  {desc}")
    if mode == "decrypt" and dst:
        open(dst, "wb").write(plain)
        print(f"       wrote {dst}")
    return True


def out_name(name: str, mode: str) -> str:
    if mode == "decrypt":
        return name + ".dec"
    return name[:-4] if name.endswith(".dec") else name


def find_source(src, mode, bundles=None):
    """Resolve explicit paths, work-tree bundles, configured source bundles, or decrypted scratch files."""
    if src and os.path.exists(src):
        return src
    if not src:
        return config.setting("bundles", bundles)
    folders = [config.work_dir(create=False)] if mode == "encrypt" else [
        config.out_dir("bundles", create=False), config.setting("bundles", bundles, required=False)]
    for folder in folders:
        for name in (src, src + ".dec") if mode == "encrypt" else (src,):
            cand = os.path.join(folder, name) if folder else ""
            if cand and os.path.exists(cand): return cand
    raise SystemExit("no such file: %s%s" % (src, " (also looked in %s)" % folder if folder else ""))


def main():
    ap = argparse.ArgumentParser(prog="deadlock xor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["decrypt", "encrypt", "check"])
    ap.add_argument("src", nargs="?", help="bundle file, folder, or bundle name (default: the 'bundles' folder)")
    config.add_path_args(ap, "out", "bundles")
    ap.add_argument("--key", help="hex key override")
    a = ap.parse_args()
    key = bytes.fromhex(a.key) if a.key else None
    src = find_source(a.src, a.mode, a.bundles)
    if a.mode == "decrypt":
        outdir = config.work_dir()
    elif a.mode == "encrypt":
        outdir = config.out_dir("bundles", a.out)
    else:
        outdir = None

    if os.path.isdir(src):
        source_folder = Path(src).resolve()
        if outdir:
            output_folder = Path(outdir).resolve()
            if source_folder == output_folder or source_folder in output_folder.parents:
                raise SystemExit("choose an output folder outside the input folder")
        good = bad = 0
        for root, _d, files in os.walk(src):
            for name in sorted(files):
                s = os.path.join(root, name)
                rel = os.path.relpath(s, src)
                d = None
                if outdir:
                    d = os.path.join(outdir, os.path.dirname(rel), out_name(os.path.basename(rel), a.mode))
                    if os.path.exists(d) and config.same_file(s, d):
                        print(f"[skip] {s}: output resolves to the input file")
                        bad += 1
                        continue
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                good, bad = (good + 1, bad) if process(s, d, key, a.mode) else (good, bad + 1)
        print(f"\nDone: {good} processed, {bad} skipped/failed" + (f"  (output in {outdir})" if outdir else ""))
    else:
        dst = None
        if outdir:
            os.makedirs(outdir, exist_ok=True)
            dst = os.path.join(outdir, out_name(os.path.basename(src), a.mode))
            if os.path.exists(dst) and config.same_file(dst, src):
                raise SystemExit("refusing to overwrite the input %s" % src)
        sys.exit(0 if process(src, dst, key, a.mode) else 1)


if __name__ == "__main__":
    main()
