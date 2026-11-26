"""Jamming effects: the global penalty a jammed ship gets, and per-jammer overrides.

A Jammer (Wardriver squadron, or a ship given weapons --jamming) flags ships in range IsJammed. A jammed ship gets
DataTables.JammingPenalty (level5, level85, level9): MaxVelocityPenalty -1.2, EvadePenalty -15, AccuracyPenalty 0 in
the stock game, the same for every jammer. The custom-module DLL's CustomJamming remembers which jammer jammed a ship
and uses that jammer's own values from StreamingAssets/deadlock-jamming.txt when it has them:

    squadron 103 speed=-1.2 evade=-25 accuracy=-10
    ship 206 evade=-20

(squadron type id, or capital ship type id; a missing key keeps the global value). See docs/game/squadrons.md.
"""
import os
import struct

from deadlock_tools import shipdb as sd

FILE = "deadlock-jamming.txt"
KEYS = {"speed": "MaxVelocityPenalty", "evade": "EvadePenalty", "accuracy": "AccuracyPenalty"}
LIMIT = 1000.0


# ---- global penalty (level data) ----
def _data_tables(S, data):
    ti = S.typedef_index("DataTables")
    for pid, off, size, cid, _e in sd.read_objects(data):
        if cid != 114:
            continue
        reader = sd.Reader(S, data[off:off + size], 32)
        try:
            res = reader.obj(ti)
        except Exception:
            continue
        if reader.p == size and isinstance(res.get("JammingPenalty"), dict):
            return off, res
    return None


def global_penalty(S, levels):
    """{"speed": .., "evade": .., "accuracy": ..} from the first level file with DataTables"""
    for data in levels.values():
        found = _data_tables(S, data)
        if found:
            penalty = found[1]["JammingPenalty"]
            return {key: penalty[field] for key, field in KEYS.items()}
    raise ValueError("no level file holds DataTables (level5, level85, level9)")


def set_global(S, levels, values):
    """write the global jamming penalty into every level file with DataTables. Returns ({file: bytes}, log)"""
    for key, value in values.items():
        if key not in KEYS:
            raise ValueError("jamming penalties are %s, not %r" % (", ".join(KEYS), key))
        if not -LIMIT <= float(value) <= LIMIT:
            raise ValueError("%s must be between %g and %g" % (key, -LIMIT, LIMIT))
    out, files, log = {}, [], []
    for name, data in levels.items():
        found = _data_tables(S, data)
        if not found:
            continue
        off, res = found
        penalty = res["JammingPenalty"]
        buf = bytearray(data)
        for key, value in values.items():
            struct.pack_into("<f", buf, off + penalty["_off"][KEYS[key]][0], float(value))
        if bytes(buf) != data:
            out[name] = bytes(buf)
            files.append(name)
        if not log:
            log = ["%s %g -> %g" % (key, penalty[KEYS[key]], float(value)) for key, value in values.items()
                   if abs(penalty[KEYS[key]] - float(value)) > 1e-6]
    if out:
        log = ["global jamming penalty (%s): %s" % (", ".join(sorted(files)), ", ".join(log))]
    return out, log


# ---- per-jammer overrides (deadlock-jamming.txt next to the bundles) ----
def read_overrides(path):
    """{("squadron" | "ship", id): {"speed"/"evade"/"accuracy": value}}"""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    for raw in open(path, encoding="utf-8"):
        parts = raw.split("#", 1)[0].split()
        if len(parts) < 2 or parts[0] not in ("squadron", "ship") or not parts[1].lstrip("-").isdigit():
            continue
        values = {}
        for item in parts[2:]:
            key, sep, value = item.partition("=")
            try:
                if sep and key in KEYS:
                    values[key] = float(value)
            except ValueError:
                continue
        out[(parts[0], int(parts[1]))] = values
    return out


def render(overrides):
    lines = ["# per-jammer jamming penalties for the custom-module DLL (CustomJamming); written by deadlock-tools",
             "# kind id [speed=..] [evade=..] [accuracy=..]   (a missing key: the global DataTables.JammingPenalty)"]
    for (kind, ident), values in sorted(overrides.items()):
        if values:
            lines.append("%s %d %s" % (kind, ident, " ".join("%s=%g" % (k, values[k]) for k in KEYS if k in values)))
    return ("\n".join(lines) + "\n").encode("utf-8")


def set_override(overrides, kind, ident, values):
    """merge {key: value or None (= back to the global value)} into one jammer's overrides; returns (new dict, log)"""
    if kind not in ("squadron", "ship"):
        raise ValueError("a jammer is a 'squadron' or a 'ship'")
    out = {k: dict(v) for k, v in overrides.items()}
    entry = out.setdefault((kind, int(ident)), {})
    changes = []
    for key, value in values.items():
        if key not in KEYS:
            raise ValueError("jamming penalties are %s, not %r" % (", ".join(KEYS), key))
        if value in (None, ""):
            if key in entry:
                changes.append("%s back to global" % key)
                del entry[key]
            continue
        value = float(value)
        if not -LIMIT <= value <= LIMIT:
            raise ValueError("%s must be between %g and %g" % (key, -LIMIT, LIMIT))
        if entry.get(key) != value:
            changes.append("%s %s -> %g" % (key, entry.get(key, "global"), value))
            entry[key] = value
    if not entry:
        del out[(kind, int(ident))]
    return out, (["jamming effect of %s %d: %s" % (kind, int(ident), ", ".join(changes))] if changes else [])


def cli(a):
    """shipdb jamming | setjamming global|squadron ID|ship ID speed=.. evade=.. accuracy=..  (value "-" = global)"""
    from deadlock_tools import config, squadron_types as sqt
    S = sd.Schema(a.dll)
    folder = a.level0 if os.path.isdir(a.level0) else os.path.dirname(a.level0)
    levels = {}
    for f_ in sd.files_of(folder):
        base = os.path.basename(f_)
        if base.startswith("level") and not base.endswith(".resS"):
            levels[base] = open(sd.chain_source(a, f_, a.out, quiet=True), "rb").read()
    bundle_dir, _dll = sqt._cli_out_dirs(a)
    sources = [d for d in ([] if a.fresh else [bundle_dir]) + [config.setting("bundles", required=False)] if d]
    current = next((os.path.join(d, FILE) for d in sources if os.path.isfile(os.path.join(d, FILE))), None)
    overrides = read_overrides(current)
    rest = list(a.args)
    if a.cmd == "jamming":
        print("global jamming penalty: " + ", ".join("%s %g" % kv for kv in global_penalty(S, levels).items()))
        print("per-jammer overrides (%s):" % (current or "none yet"))
        for (kind, ident), values in sorted(overrides.items()):
            print("  %-8s %-5d %s" % (kind, ident, ", ".join("%s %g" % kv for kv in values.items())))
        print("(needs the custom-module DLL with CustomJamming)")
        return
    if not rest or rest[0] not in ("global", "squadron", "ship"):
        raise SystemExit(cli.__doc__)
    kind, ident, items = (rest[0], None, rest[1:]) if rest[0] == "global" else (rest[0], rest[1] if len(rest) > 1 else "", rest[2:])
    values = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit("expected KEY=VALUE, got %r\n%s" % (item, cli.__doc__))
        values[key] = None if value in ("-", "") else value
    try:
        if kind == "global":
            changed, log = set_global(S, levels, {k: v for k, v in values.items() if v is not None})
            os.makedirs(a.out, exist_ok=True)
            for base, data in changed.items():
                with open(os.path.join(a.out, base), "wb") as stream:
                    stream.write(data)
        else:
            if not str(ident).lstrip("-").isdigit():
                raise ValueError("give the %s type id (shipdb squadronabilities / list show them)" % kind)
            overrides, log = set_override(overrides, kind, int(ident), values)
            os.makedirs(bundle_dir, exist_ok=True)
            with open(os.path.join(bundle_dir, FILE), "wb") as stream:
                stream.write(render(overrides))
            log.append("wrote %s" % os.path.join(bundle_dir, FILE))
    except ValueError as e:
        raise SystemExit(str(e))
    print("\n".join("[ok] " + line for line in log) or "[ok] no change")
