#!/usr/bin/env python3
"""
Decrypt / re-encrypt BSG Deadlock ship bundles (repeating-key XOR over the whole file).

The bundles are normal UnityFS bundles (Unity 2018.4.3f1, LZMA) that have been XORed
byte-by-byte with a short repeating key. XOR is symmetric, so the same operation
decrypts and re-encrypts.

Results always go into two dedicated folders next to this script (created on first use,
nothing else is created):
    decrypted/   <- decrypt writes here   (colonialmercury -> decrypted/colonialmercury.dec)
    encrypted/   <- encrypt writes here   (colonialmercury.dec -> encrypted/colonialmercury, the ".dec" is dropped
                                           so the file is ready to rename/copy into the game folder)
A source folder is processed recursively and its sub-folder layout is kept inside the dedicated folder.

Usage:
    python deadlock_bundle_xor.py decrypt  colonialmercury            -> decrypted/colonialmercury.dec
    python deadlock_bundle_xor.py decrypt  <folder>                   -> every file, into decrypted/
    python deadlock_bundle_xor.py encrypt  colonialmercury.dec        -> encrypted/colonialmercury
    python deadlock_bundle_xor.py encrypt  <folder>                   -> every file, into encrypted/
    python deadlock_bundle_xor.py check    colonialmercury            (just report, write nothing)

Options:
    --out DIR             use DIR instead of decrypted/ or encrypted/ (the only way to write elsewhere)
    --key 0f0f070d0507    override the key (hex). By default the key is recovered
                          automatically from the "UnityFS" header of each file.
"""
import argparse
import os
import struct
import sys

DEFAULT_KEY = bytes.fromhex("0f0f070d0507")  # recovered from colonialmercury
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEC_DIR = os.path.join(BASE_DIR, "decrypted")
ENC_DIR = os.path.join(BASE_DIR, "encrypted")
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["decrypt", "encrypt", "check"])
    ap.add_argument("src")
    ap.add_argument("--out", help="output folder instead of decrypted/ or encrypted/")
    ap.add_argument("--key", help="hex key override")
    a = ap.parse_args()
    key = bytes.fromhex(a.key) if a.key else None
    outdir = a.out or (DEC_DIR if a.mode == "decrypt" else ENC_DIR)

    if os.path.isdir(a.src):
        good = bad = 0
        for root, _d, files in os.walk(a.src):
            for name in sorted(files):
                s = os.path.join(root, name)
                rel = os.path.relpath(s, a.src)
                d = os.path.join(outdir, os.path.dirname(rel), out_name(os.path.basename(rel), a.mode))
                if a.mode != "check":
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                good, bad = (good + 1, bad) if process(s, None if a.mode == "check" else d, key, a.mode) else (good, bad + 1)
        print(f"\nDone: {good} processed, {bad} skipped/failed" + ("" if a.mode == "check" else f"  (output in {outdir})"))
    else:
        dst = None
        if a.mode != "check":
            os.makedirs(outdir, exist_ok=True)
            dst = os.path.join(outdir, out_name(os.path.basename(a.src), a.mode))
        sys.exit(0 if process(a.src, dst, key, a.mode) else 1)


if __name__ == "__main__":
    main()
