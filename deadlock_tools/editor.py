#!/usr/bin/env python3
"""
gui - graphical editor for BSG Deadlock (runs a small web page on your own computer).

  python -m deadlock_tools gui [LEVELS DLL] [--bundles DIR] [--out DIR] [--port 8765] [--no-browser]

The paths come from deadlock.json (the same settings file the command-line tools use) and can be changed in the page
(Settings tab, with a Browse button); changes are saved back to deadlock.json. On the command line:
    LEVELS         the folder with the game's level files
    DLL            Assembly-CSharp.dll
    --bundles DIR  folder with ship bundles (encrypted or .dec; encrypted ones are decrypted automatically) for the Bundles tab (optional)
    --out DIR      output folder (default out/): results go to its levels/, dll/ and bundles/ sub-folders, the same places the
                   command-line tools use, so edits made in either stack

It opens http://127.0.0.1:8765 by itself. Nothing leaves your computer and the originals are never touched.

TABS
  Settings the four paths (level data folder, DLL, ship asset folder, output folder), each with a Browse button.
  Ships    pick a ship, edit its stats (hull, armour per side, speed, turn rate, DRADIS range, build cost/time/rush, fleet
           points, carrier slots, flags ...), press Apply: every level file with the ship table is rewritten into out/levels/.
  Bundles  every option of the 'weapons' tool (turrets, slots, abilities, systems) as a form, run on a bundle of your choice.
  DLL      the 'dll' patches (armour regen, squadron revive, max rank, command nodes, munition load).
  Tools    every other 'shipdb' command as a form: commanders (ranks, fixspecs), ship list, XP per rank, copy veterancy,
           munitions, research lists, fleet limits, fleet map icons, squadron hull. Research for ONE ship is on its Ships page.

Copy out/levels/*, out/dll/* and out/bundles/* over the game's originals (back them up first).
"""
import argparse, json, os, subprocess, sys, struct, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

if __package__ in (None, ""): sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deadlock_tools import config
from deadlock_tools import shipdb as sd
from deadlock_tools import weapons as wmod

SIMPLE = {0x02: "bool", 0x08: "int", 0x0c: "float", 0x11: "int"}      # field type codes that can be written in place
ENUM_FIELDS = {"Faction": "faction", "ShipClass": "sclass"}
READONLY = {"name", "id"}
HIDDEN = {"AttachmentInfo", "CrewVeterancyRankEffects", "ArmamentInfoList"}


def tool_cmd(tool):
    """argv prefix that runs one of the command-line tools (python -m deadlock_tools <tool>) as its own process"""
    return [sys.executable, "-m", "deadlock_tools", tool]


def run_cmd(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=config.PROJECT_DIR, timeout=timeout)


def num(x):
    return float("%.7g" % x) if isinstance(x, float) else x


class Project:
    def __init__(self, levels, dll, out, bundles):
        ab = lambda x: os.path.abspath(x) if x else x                  # the helper tools run from the script's folder, so no relative paths
        self.levels, self.dll, self.out, self.bundles = ab(levels), ab(dll), ab(out), ab(bundles)
        self.lev_out, self.dll_out, self.bundle_out = (os.path.join(self.out, k) for k in config.KINDS)
        levels, dll = self.levels, self.dll
        self.S = sd.Schema(dll)
        self.lock = threading.Lock()
        self.table_files = []
        for f in sd.files_of(levels):
            try: sd.Db(dll, f, self.S); self.table_files.append(f)
            except BaseException: pass
        if not self.table_files: raise SystemExit("no level file with the ship table found in %s" % levels)
        self._view = None

    def src_for(self, f, fresh=False):
        """the copy in --out if an earlier edit made one (so edits stack), else the original"""
        cand = os.path.join(self.lev_out, os.path.basename(f))
        return cand if (not fresh and os.path.exists(cand) and os.path.abspath(cand) != os.path.abspath(f)) else f

    def view(self):
        path = self.src_for(self.table_files[0]); key = (path, os.path.getmtime(path))
        if not self._view or self._view[0] != key: self._view = (key, sd.Db(self.dll, path, self.S))
        return self._view[1]

    def ships(self):
        db = self.view()
        return [{"name": s["name"], "faction": db.faction.get(s["Faction"], str(s["Faction"])), "class": db.sclass.get(s["ShipClass"], str(s["ShipClass"])),
                 "buildable": s["Buildable"], "cost": s["BuildCost"], "hull": num(s["MaxHullPoints"]), "fleet": s["FleetPointValue"]} for s in db.ships]

    def ship(self, name):
        db = self.view(); s = db.find(name); fields = []
        for k, (off, code) in s["_off"].items():
            if k in HIDDEN: continue
            v = s.get(k); kind = SIMPLE.get(code)
            if k in ENUM_FIELDS:
                fields.append({"name": k, "type": "enum", "value": v, "options": {str(i): n for i, n in getattr(db, ENUM_FIELDS[k]).items()}})
            elif kind and k not in READONLY and isinstance(v, (int, float)): fields.append({"name": k, "type": kind, "value": num(v)})
            else: fields.append({"name": k, "type": "ro", "value": str(num(v) if not isinstance(v, (list, tuple, dict)) else v)})
        return {"name": s["name"], "fields": fields, "advantages": [db.eff(x) for x in s["AdvantageList"]], "penalties": [db.eff(x) for x in s["PenaltyList"]],
                "research": self.research(name),
                "vet": {"ranks": [{"rank": r["Rank"], "xp": r["XPRequired"]} for r in db.res["CrewVeterancyRankInfo"]],
                        "effects": {str(i): n for i, n in db.effect.items()},
                        "current": [{"rank": rk["Rank"], "effects": [{"effect": e["Effect"], "value": num(e["BonusAmount"])} for e in rk["Effects"]]} for rk in s["CrewVeterancyRankEffects"]]}}

    # ---- research (the war-room blueprint lists: which campaign can research the ship, for how many RP) ----
    def _war_path(self):
        for f in self.table_files:
            p = self.src_for(f)
            try: sd.load_wardata(self.S, open(p, "rb").read()); return p
            except LookupError: continue
        return None

    def research(self, name):
        p = self._war_path()
        if not p: return None
        db = self.view(); s = db.find(name); sid = s["id"]; cls = str(db.sclass.get(s["ShipClass"], "")).lower()
        pid, off, size, res = sd.load_wardata(self.S, open(p, "rb").read()); rows = []
        for it in res["BlueprintInfo"]:
            row = None
            for key in ("CapitalShipUnlocks", "BattlestarShipUnlocks"):
                for u in it[key]:
                    if u["ODOMECMDGKJ"] == sid and row is None:
                        row = {"campaign": it["Campaign"], "on": True, "key": key, "cost": u["GKIMOHIEFNI"], "mission": u["GEIBBHBCKAM"], "label": u["NPFHKIHKKIC"], "unlocked": bool(u["HJGFMHOLHPG"])}
            rows.append(row or {"campaign": it["Campaign"], "on": False, "key": "BattlestarShipUnlocks" if "battlestar" in cls else "CapitalShipUnlocks", "cost": 3000, "mission": -1, "label": "Mc", "unlocked": False})
        return rows

    def _set_research(self, dst, name, rows):
        buf = open(dst, "rb").read()
        try: pid, off, size, res = sd.load_wardata(self.S, buf)
        except LookupError: return                                         # this level file has no research table
        mono = bytearray(buf[off:off + size]); sd.check_unlock_layout(bytes(mono), res)   # refuses to edit if the entry layout is not the expected one
        sid = sd.Db(self.dll, dst, self.S).find(name)["id"]; want = {int(r["campaign"]): r for r in rows}; jobs = []
        pk = lambda u: sd.pack_unlock(u["ODOMECMDGKJ"], u["GEIBBHBCKAM"], u["GKIMOHIEFNI"], u["NPFHKIHKKIC"], u["HJGFMHOLHPG"], u["COPOFKMOPIH"])
        for ci, it in enumerate(res["BlueprintInfo"]):
            r = want.get(it["Campaign"])
            if r is None: continue
            key = r["key"]
            if key not in sd.UNLOCK_KEYS.values(): raise ValueError("bad research list %r" % key)
            cost, mission, label = int(float(r["cost"])), int(float(r["mission"])), str(r["label"])
            if not 0 <= cost <= 10 ** 7: raise ValueError("research cost must be 0 to 10000000")
            if not -1 <= mission <= 10 ** 4: raise ValueError("mission must be -1 (none) or a mission number")
            if len(label) > 12 or not label.isascii(): raise ValueError("label: at most 12 plain characters")
            cpos, first, end = sd.list_span(res, ci, key); new = []; placed = False
            for u in it[key]:
                if u["ODOMECMDGKJ"] != sid: new.append(pk(u)); continue
                if r["on"] and not placed: new.append(sd.pack_unlock(sid, mission, cost, label, bool(r["unlocked"]), bool(u["COPOFKMOPIH"]))); placed = True
            if r["on"] and not placed: new.append(sd.pack_unlock(sid, mission, cost, label, bool(r["unlocked"]), False))
            jobs.append((cpos, end, struct.pack("<i", len(new)) + b"".join(new)))
        for cpos, end, blob in sorted(jobs, reverse=True): mono[cpos:end] = blob
        open(dst, "wb").write(sd.resize_object(buf, pid, bytes(mono)))
        p2, o2, s2, r2 = sd.load_wardata(self.S, open(dst, "rb").read()); sd.Db(self.dll, dst, self.S)
        for it in r2["BlueprintInfo"]:
            r = want.get(it["Campaign"])
            if r is None: continue
            got = [u for u in it[r["key"]] if u["ODOMECMDGKJ"] == sid]
            if bool(got) != bool(r["on"]): raise ValueError("verification failed for the research list of %s (campaign %s)" % (name, it["Campaign"]))
            if got and (got[0]["GKIMOHIEFNI"], got[0]["GEIBBHBCKAM"], got[0]["NPFHKIHKKIC"], bool(got[0]["HJGFMHOLHPG"])) != (int(float(r["cost"])), int(float(r["mission"])), str(r["label"]), bool(r["unlocked"])):
                raise ValueError("verification failed for the research entry of %s (campaign %s)" % (name, it["Campaign"]))

    def _vet_clean(self, db, ranks):
        valid = {r["Rank"] for r in db.res["CrewVeterancyRankInfo"]}; clean = []
        for r in sorted(ranks, key=lambda x: int(x["rank"])):
            rk = int(r["rank"]); effs = []; seen = set()
            if rk not in valid: raise ValueError("rank %d does not exist in this game (ranks: %s)" % (rk, sorted(valid)))
            for e in r["effects"]:
                eid = int(e["effect"])
                if eid not in db.effect: raise ValueError("unknown effect %s" % eid)
                if eid in seen: raise ValueError("rank %d lists %s twice" % (rk, db.effect[eid]))
                seen.add(eid); effs.append((eid, float(e["value"])))
            if effs: clean.append((rk, effs))               # a rank with no effects is simply left out
        return clean

    def _set_vet(self, db, dst, name, ranks):
        """replace a ship's whole veterancy list (the object changes size, so the file is written and reloaded)"""
        s = db.find(name); clean = self._vet_clean(db, ranks)
        blob = struct.pack("<i", len(clean)) + b"".join(struct.pack("<ii", rk, len(effs)) + b"".join(struct.pack("<if", e_, v_) for e_, v_ in effs) for rk, effs in clean)
        ts, te = db.veterancy_span(s); mono = bytes(db.buf[db.base:db.base + db.size])
        open(dst, "wb").write(sd.resize_object(bytes(db.buf), db.pid, mono[:ts] + blob + mono[te:]))
        return sd.Db(self.dll, dst, self.S), clean

    def apply(self, edits, fresh=False):
        log = []; os.makedirs(self.lev_out, exist_ok=True)
        scalar = [e for e in edits if "field" in e]; vets = [e for e in edits if "vet" in e]; resea = [e for e in edits if "research" in e]
        with self.lock:
            for f in self.table_files:
                db = sd.Db(self.dll, self.src_for(f, fresh), self.S)
                for e in scalar:
                    s = db.find(e["ship"]); fld = e["field"]
                    if fld not in s["_off"] or fld in READONLY or not isinstance(s[fld], (int, float)): raise ValueError("%s cannot be edited" % fld)
                    off, code = s["_off"][fld]; kind = SIMPLE.get(code); v = e["value"]
                    if kind == "float": db.write_float(off, float(v))
                    elif kind == "int":
                        fv = float(v)
                        if not fv.is_integer() or abs(fv) > 2 ** 31 - 1: raise ValueError("%s needs a whole number" % fld)
                        db.write_int(off, int(fv))
                    elif kind == "bool": struct.pack_into("<B", db.buf, db.base + off, 1 if v in (True, 1, "1", "true", "True") else 0)
                    else: raise ValueError("%s is not a simple number/flag" % fld)
                dst = os.path.join(self.lev_out, os.path.basename(f)); config.write(dst, db.buf, f)
                wanted = {}
                for e in vets:
                    db, clean = self._set_vet(db, dst, e["ship"], e["vet"]); wanted[e["ship"]] = clean
                for e in resea: self._set_research(dst, e["ship"], e["research"])
                chk = sd.Db(self.dll, dst, self.S)
                for e in scalar:
                    got = chk.find(e["ship"])[e["field"]]; want = e["value"]
                    ok = abs(float(got) - float(want)) <= 1e-5 * max(1.0, abs(float(want))) if not isinstance(got, bool) else bool(got) == (want in (True, 1, "1", "true", "True"))
                    if not ok: raise ValueError("verification failed for %s.%s: wrote %s, read back %s" % (e["ship"], e["field"], want, got))
                for name, clean in wanted.items():
                    have = {rk["Rank"]: {x["Effect"]: x["BonusAmount"] for x in rk["Effects"]} for rk in chk.find(name)["CrewVeterancyRankEffects"]}
                    want = {rk: dict(effs) for rk, effs in clean}
                    if set(have) != set(want) or any(set(have[k]) != set(want[k]) or any(abs(have[k][i] - want[k][i]) > 1e-4 for i in want[k]) for k in want):
                        raise ValueError("verification failed for the veterancy of %s" % name)
                log.append("wrote %s" % dst)
            for e in scalar: log.append("%s.%s = %s" % (e["ship"], e["field"], e["value"]))
            for e in resea: log.append("%s: research entries updated (%d campaign(s))" % (e["ship"], len(e["research"])))
            for e in vets: log.append("%s: veterancy updated (%d rank(s) with effects)" % (e["ship"], len([r for r in e["vet"] if r["effects"]])))
        self._view = None
        return log

    # ---- the other command-line commands (shipdb) ----
    def tools(self):
        assets = os.path.join(self.levels, "sharedassets9.assets")
        out = []
        for t in TOOLS:
            fields = [dict(f, default=(assets if f["default"] == "@assets" and os.path.isfile(assets) else "" if f["default"] == "@assets" else f["default"])) for f in t["fields"]]
            out.append({k: t.get(k) for k in ("id", "group", "title", "help")} | {"writes": bool(t.get("writes")), "fields": fields})
        return out

    def run_tool(self, tid, v, fresh=False):
        t = TOOLS_BY_ID.get(tid)
        if not t: raise ValueError("unknown tool")
        argv = t["build"](self, v or {})
        if "--levels" not in argv: argv += ["--levels", self.levels]
        argv += ["--dll", self.dll]
        if t.get("writes"): argv += ["-o", self.out] + (["--fresh"] if fresh else [])
        with self.lock: r = run_cmd(tool_cmd("shipdb") + argv, 900)
        if t.get("writes"): self._view = None
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-30000:], "cmd": "shipdb " + " ".join(argv)}

    # ---- bundles (weapons) ----
    def bundle_files(self):
        if not self.bundles or not os.path.isdir(self.bundles): return []
        return sorted(f for f in os.listdir(self.bundles) if os.path.isfile(os.path.join(self.bundles, f)))

    def bundle_form(self):
        ap = wmod.build_parser(); out = []
        for g in ap._action_groups:
            if g.title in ("positional arguments", "options"): continue
            for a in g._group_actions:
                if not a.option_strings or a.dest in ("help", "out", "bundles") or a.help == argparse.SUPPRESS: continue
                kind = "bool" if isinstance(a, argparse._StoreTrueAction) else "choice" if a.choices else "list" if isinstance(a, argparse._AppendAction) else "text"
                out.append({"group": g.title, "dest": a.dest, "flag": a.option_strings[-1], "kind": kind, "help": (a.help or "").replace("%%", "%"), "choices": list(a.choices) if a.choices else None,
                            "default": None if a.default in (None, False) else str(a.default), "metavar": a.metavar or ""})
        return out

    def bundle_src(self, bundle):
        """(path, file name) of the bundle chosen in the Bundles tab"""
        if os.path.isabs(bundle):                                      # a path picked with the Browse button
            if not os.path.isfile(bundle): raise ValueError("bundle file not found: %s" % bundle)
            return bundle, os.path.basename(bundle)
        if bundle in self.bundle_files(): return os.path.join(self.bundles, bundle), bundle
        raise ValueError("choose a bundle file")

    def run_xor(self, bundle, mode):
        """the 'xor' tool on one bundle: decrypt -> work/decrypted/, encrypt -> out/bundles/, check = report only"""
        if mode not in ("decrypt", "encrypt", "check"): raise ValueError("bad mode")
        src, _name = self.bundle_src(bundle)
        cmd = tool_cmd("xor") + [mode, src, "-o", self.out]
        with self.lock: r = run_cmd(cmd, 300)
        return {"ok": r.returncode == 0, "output": r.stdout + r.stderr, "cmd": " ".join(cmd[3:])}

    def run_bundle(self, bundle, opts):
        src, bundle = self.bundle_src(bundle)
        ap = wmod.build_parser()
        stem = bundle[:-4] if bundle.endswith(".dec") else bundle
        dst = None if opts.get("list_mounts") else os.path.join(self.bundle_out, stem + (".dec" if opts.get("no_encrypt") else ""))
        cmd = tool_cmd("weapons") + [src, "-o", self.out] + (["--bundles", self.bundles] if self.bundles else [])
        for a in ap._actions:
            v = opts.get(a.dest)
            if not a.option_strings or a.dest in ("help", "out", "bundles", "encrypt") or v in (None, "", False): continue
            if isinstance(a, argparse._StoreTrueAction): cmd.append(a.option_strings[-1])
            elif isinstance(a, argparse._AppendAction):
                for line in str(v).splitlines():
                    if line.strip(): cmd += [a.option_strings[-1], line.strip()]
            else: cmd += [a.option_strings[-1], str(v)]
        with self.lock: r = run_cmd(cmd, 900)
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-20000:], "dst": dst, "cmd": " ".join(cmd[3:])}

    # ---- DLL patches (the 'dll' tool; it continues from out/dll/ itself, so patches stack) ----
    def dll_src(self):
        return config.latest(self.dll, "dll", self.out, quiet=True)

    def dll_check(self):
        r = run_cmd(tool_cmd("dll") + ["check", self.dll_src()], 120)
        return {"source": self.dll_src(), "output": r.stdout + r.stderr}

    def dll_patch(self, o):
        dst = os.path.join(self.dll_out, os.path.basename(self.dll))
        cmd = tool_cmd("dll") + ["patch", self.dll, "-o", self.out]; base = len(cmd)
        for key, flag in (("armour_factor", "--armour-factor"), ("revive_turns", "--revive-turns"), ("max_rank", "--max-rank"), ("command_nodes", "--command-nodes"),
                          ("command_step", "--command-step"), ("command_first_rank", "--command-first-rank")):
            if str(o.get(key, "")).strip(): cmd += [flag, str(o[key]).strip()]
        if o.get("munition_load") in (True, "true", 1, "1"): cmd.append("--munition-load")
        if o.get("fresh") in (True, "true", 1, "1"): cmd.append("--fresh")
        if not any(c.startswith("--") and c not in ("--command-step", "--command-first-rank") for c in cmd[base:]): raise ValueError("fill in at least one patch value")
        with self.lock: r = run_cmd(cmd, 300)
        return {"ok": r.returncode == 0, "output": r.stdout + r.stderr, "dst": dst}

    def dll_revert(self, o):
        """undo patches in out/dll/ (the ticked ones, or all of them when none is ticked)"""
        dst = os.path.join(self.dll_out, os.path.basename(self.dll))
        cmd = tool_cmd("dll") + ["revert", self.dll, "-o", self.out]
        for key, flag in (("armour", "--armour"), ("revive", "--revive"), ("max_rank", "--max-rank"), ("command_nodes", "--command-nodes"), ("munition_load", "--munition-load")):
            if o.get(key) in (True, "true", 1, "1"): cmd.append(flag)
        with self.lock: r = run_cmd(cmd, 300)
        return {"ok": r.returncode == 0, "output": r.stdout + r.stderr, "dst": dst}

PATH_KEYS = ("levels", "dll", "bundles", "out", "work")
STATE = {"cfg": {"levels": "", "dll": "", "bundles": "", "out": config.resolve(config.DEFAULTS["out"]), "work": config.resolve(config.DEFAULTS["work"])}, "P": None, "error": ""}


def _need(v, k, what):
    x = str(v.get(k, "")).strip()
    if not x: raise ValueError(what + " is required")
    return x


def _pairs(v, k):
    toks = []
    for ln in str(v.get(k, "")).splitlines():
        p = ln.split()
        if not p: continue
        if len(p) != 2: raise ValueError("each line needs a field name and a value, e.g. MaxMissiles 80 (got %r)" % ln)
        float(p[1]); toks += p
    if not toks: raise ValueError("enter at least one line: field value")
    return toks


def _bool_flag(v, k, flag): return [flag] if v.get(k) in (True, "true", "1", 1) else []


F = lambda name, label, kind="text", default="", help="": {"name": name, "label": label, "kind": kind, "default": default, "help": help}
# every command of shipdb: id, group, title, help, writes?, fields, argv builder (P = Project, v = form values)
TOOLS = [
    {"id": "commanders", "group": "Commanders", "title": "Show commanders", "help": "Ranks, XP table, leadership interval and trait counts.", "fields": [],
     "build": lambda P, v: ["commanders"]},
    {"id": "setcommanders", "group": "Commanders", "title": "Add commander ranks", "writes": True,
     "help": "Adds ranks above the original maximum. Afterwards patch the DLL (DLL tab: max rank 20, command nodes 10) or the new ranks do nothing.",
     "fields": [F("add", "ranks to add", "int", "10"), F("every_level", "a fleet-size trait at every rank (leadership interval 1)", "bool", True), F("xp_growth", "XP growth per rank", "float", "1.2", "each new rank costs this much more XP than the previous one")],
     "build": lambda P, v: ["setcommanders", "--add", str(int(float(v.get("add") or 10))), "--xp-growth", str(float(v.get("xp_growth") or 1.2))] + _bool_flag(v, "every_level", "--every-level")},
    {"id": "fixspecs", "group": "Commanders", "title": "Specialisation lists (fixspecs)", "writes": True,
     "help": "Sets each subsystem's captain-skill list to its own three skills. Tick the box to put the game's original lists back.",
     "fields": [F("revert", "undo: restore the original lists", "bool", False)],
     "build": lambda P, v: ["fixspecs"] + _bool_flag(v, "revert", "--revert-specs")},
    {"id": "list", "group": "Ships", "title": "List all ships", "help": "Name, buildable, cost, time, rush cost, hull, fleet points, faction, class.", "fields": [],
     "build": lambda P, v: ["list", "--levels", P.src_for(P.table_files[0])]},
    {"id": "setxp", "group": "Ships", "title": "XP needed per veterancy rank", "writes": True, "help": "Changes the XP a crew needs to reach a rank (the same for every ship).",
     "fields": [F("rank", "rank", "int", "2"), F("xp", "XP needed", "int", "10")],
     "build": lambda P, v: ["setxp", str(int(float(_need(v, "rank", "rank")))), str(int(float(_need(v, "xp", "XP"))))]},
    {"id": "copyvet", "group": "Ships", "title": "Copy veterancy between ships", "writes": True, "help": "Copies every rank of one ship's veterancy bonuses onto another (replaces the target's).",
     "fields": [F("src", "copy FROM ship", "ship"), F("dst", "copy TO ship", "ship")],
     "build": lambda P, v: ["copyvet", _need(v, "src", "source ship"), _need(v, "dst", "target ship")]},
    {"id": "munitions", "group": "Munitions", "title": "List munition types", "help": "Capacity, salvo size, supply cost.", "fields": [],
     "build": lambda P, v: ["munitions"]},
    {"id": "setmunition", "group": "Munitions", "title": "Edit a munition type", "writes": True,
     "help": "Editable: StandardSalvoSize MaxMissiles HasUnlimitedMunitions SupplyCostPerMunition SurvivalCostPerMunition PointValue. One 'field value' per line.",
     "fields": [F("name", "munition (part of its name)", "text", "colonial guided"), F("settings", "settings", "lines", "MaxMissiles 80")],
     "build": lambda P, v: ["setmunition", _need(v, "name", "munition name")] + _pairs(v, "settings")},
    {"id": "blueprints", "group": "Research", "title": "List research unlocks", "help": "Per campaign: ships that can be researched, cost, mission, starts-unlocked. Edit one ship's entries on its Ships page.", "fields": [],
     "build": lambda P, v: ["blueprints"]},
    {"id": "addblueprint", "group": "Research", "title": "Add a ship to research (bulk)", "writes": True,
     "help": "Adds the ship to the research lists AND makes it buildable (build stats copied from the reference ship). Needs a NEW campaign.",
     "fields": [F("ship", "ship", "ship"), F("cost", "research cost (blank = same as reference)", "int"), F("like", "reference ship", "ship", "Jupiter Mk2"), F("campaigns", "campaigns (e.g. 2,3,4,5; blank = all)"),
                F("mission", "mission required (-1 none)", "int", "-1"), F("label", "label", "text", "Mc"), F("list", "list", "choice:battlestar,capital", "battlestar"),
                F("unlocked", "available from the start", "bool", False), F("keep", "keep the ship's own build stats", "bool", False)],
     "build": lambda P, v: ["addblueprint", _need(v, "ship", "ship"), "--like", str(v.get("like") or "Jupiter Mk2"), "--mission", str(int(float(v.get("mission") or -1))), "--label", str(v.get("label") or "Mc"),
                            "--list", str(v.get("list") or "battlestar")] + (["--cost", str(int(float(v["cost"])))] if str(v.get("cost", "")).strip() else []) + (["--campaigns", str(v["campaigns"]).strip()] if str(v.get("campaigns", "")).strip() else [])
                           + _bool_flag(v, "unlocked", "--unlocked") + _bool_flag(v, "keep", "--keep-build-stats")},
    {"id": "fleetinfo", "group": "Fleet limits", "title": "Show fleet limits", "help": "MaxShips (per fleet group), StartingFleetPointCap ...", "fields": [],
     "build": lambda P, v: ["fleetinfo"]},
    {"id": "setfleetinfo", "group": "Fleet limits", "title": "Change fleet limits", "writes": True, "help": "One 'field value' per line, e.g. MaxShips 10 and StartingFleetPointCap 6000.",
     "fields": [F("settings", "settings", "lines", "MaxShips 10")], "build": lambda P, v: ["setfleetinfo"] + _pairs(v, "settings")},
    {"id": "fleeticons", "group": "Fleet map icons", "title": "List icon tables", "help": "The war-room icon tables.", "fields": [],
     "build": lambda P, v: ["fleeticons"]},
    {"id": "fleeticon", "group": "Fleet map icons", "title": "Copy a fleet icon", "writes": True, "help": "Gives other ships the fleet-map icon of one ship (in every scene that has the table).",
     "fields": [F("ships", "ships to change (comma separated)"), F("like", "copy the icon of", "ship"), F("factory", "icon table", "choice:FleetModelFactory,ProjectionFactory", "FleetModelFactory")],
     "build": lambda P, v: ["fleeticon", _need(v, "ships", "ships"), _need(v, "like", "icon ship"), "--factory", str(v.get("factory") or "FleetModelFactory")]},
    {"id": "squadrons", "group": "Squadrons", "title": "List squadrons", "help": "Fighter count, hull pool, hull per fighter, loss thresholds (read from sharedassets9.assets).",
     "fields": [F("assets", "sharedassets9.assets", "file", "@assets")], "build": lambda P, v: ["squadrons", "--levels", _need(v, "assets", "assets file")]},
    {"id": "setsquadron", "group": "Squadrons", "title": "Change squadron hull", "writes": True,
     "help": "HullFactor F multiplies the pool and every fighter-loss threshold; MaxHullPoints N sets the pool; FighterHull N sets hull per fighter. Name is part of the squadron name, or 'all'.",
     "fields": [F("assets", "sharedassets9.assets", "file", "@assets"), F("name", "squadron", "text", "all"), F("settings", "settings", "lines", "HullFactor 2")],
     "build": lambda P, v: ["setsquadron", _need(v, "name", "squadron")] + _pairs(v, "settings") + ["--levels", _need(v, "assets", "assets file")]},
]
TOOLS_BY_ID = {t["id"]: t for t in TOOLS}


def load_cfg():
    """the paths saved in deadlock.json, made absolute"""
    c = config.load()
    return {k: config.resolve(c.get(k, "")) for k in PATH_KEYS}


def configure(cfg, save=True):
    """validate the four paths and (re)load the level data; the GUI calls this from the Settings tab"""
    cfg = {k: os.path.abspath(os.path.expanduser(v)) if v else "" for k, v in ((k, (cfg.get(k) or "").strip()) for k in PATH_KEYS)}
    cfg["out"] = cfg["out"] or config.resolve(config.DEFAULTS["out"]); cfg["work"] = cfg["work"] or config.resolve(config.DEFAULTS["work"])
    if not cfg["levels"] or not os.path.exists(cfg["levels"]): raise ValueError("level data folder not found: %r" % cfg["levels"])
    if not os.path.isfile(cfg["dll"]): raise ValueError("Assembly-CSharp.dll not found: %r" % cfg["dll"])
    if cfg["bundles"] and not os.path.isdir(cfg["bundles"]): raise ValueError("ship asset folder not found: %r" % cfg["bundles"])
    if config.same_file(os.path.join(cfg["out"], "levels"), cfg["levels"]):
        raise ValueError("the level data folder is the output folder's levels/ - point it at the game's original level files")
    try: P = Project(cfg["levels"], cfg["dll"], cfg["out"], cfg["bundles"] or None)
    except BaseException as e: raise ValueError(str(e) or "could not read the level data")
    STATE.update(cfg=cfg, P=P, error="")
    if save:
        try: config.save({**config.load(), **cfg})
        except OSError: pass


def config_info():
    P = STATE["P"]; c = STATE["cfg"]
    return {**c, "ready": P is not None, "error": STATE["error"], "tables": len(P.table_files) if P else 0, "bundles": P.bundle_files() if P else [],
            "bundle_dir": P.bundles if P else "", "out": P.out if P else c["out"], "home": os.path.expanduser("~")}


def browse(path, files):
    path = os.path.abspath(os.path.expanduser(path or "~"))
    while not os.path.isdir(path) and os.path.dirname(path) != path: path = os.path.dirname(path)
    entries = []
    try:
        for n in sorted(os.listdir(path), key=str.lower):
            if n.startswith("."): continue
            isd = os.path.isdir(os.path.join(path, n))
            if isd or files: entries.append({"name": n, "dir": isd})
    except OSError as e: return {"path": path, "parent": os.path.dirname(path), "entries": [], "error": str(e)}
    up = os.path.dirname(path)
    return {"path": path, "parent": up if up != path else None, "entries": entries[:2000]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, obj, code=200, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def local_only(self):
        """only this computer's own browser tab may use the API (blocks other sites calling it, and DNS rebinding)"""
        return self.headers.get("Host", "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

    def need(self):
        if STATE["P"] is None: raise ValueError("Set the level data folder and the DLL in the Settings tab first")
        return STATE["P"]

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if not self.local_only(): return self.send({"error": "forbidden"}, 403)
        try:
            if u.path == "/": return self.send(PAGE, ctype="text/html")
            if u.path in ("/api/state", "/api/config"): return self.send(config_info())
            if u.path == "/api/browse": return self.send(browse(q.get("path", [""])[0], q.get("files", ["0"])[0] == "1"))
            if u.path == "/api/ships": return self.send(self.need().ships())
            if u.path == "/api/ship": return self.send(self.need().ship(q["name"][0]))
            if u.path == "/api/bundle/form": return self.send(self.need().bundle_form())
            if u.path == "/api/dll/check": return self.send(self.need().dll_check())
            if u.path == "/api/tools": return self.send(self.need().tools())
            self.send({"error": "not found"}, 404)
        except BaseException as e: self.send({"error": str(e)}, 400)

    def do_POST(self):
        if not self.local_only() or "application/json" not in self.headers.get("Content-Type", ""): return self.send({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/config": configure(body); return self.send(config_info())
            P = self.need()
            if self.path == "/api/apply": return self.send({"log": P.apply(body["edits"], body.get("fresh", False))})
            if self.path == "/api/bundle/run": return self.send(P.run_bundle(body["bundle"], body["options"]))
            if self.path == "/api/dll/patch": return self.send(P.dll_patch(body))
            if self.path == "/api/dll/revert": return self.send(P.dll_revert(body))
            if self.path == "/api/xor": return self.send(P.run_xor(body["bundle"], body["mode"]))
            if self.path == "/api/tools/run": return self.send(P.run_tool(body["id"], body.get("values", {}), body.get("fresh", False)))
            self.send({"error": "not found"}, 404)
        except BaseException as e: self.send({"error": str(e)}, 400)


def serve(a):
    given = {k: os.path.abspath(v) for k, v in (("levels", a.levels), ("dll", a.dll), ("bundles", a.bundles), ("out", a.out)) if v}
    cfg = {**{k: v for k, v in load_cfg().items() if v}, **given}
    STATE["cfg"].update(cfg)
    if cfg.get("levels") and cfg.get("dll"):
        try: configure(cfg, save=bool(given))
        except ValueError as e: STATE["error"] = str(e); print("could not load the saved paths:", e)
    for port in range(a.port, a.port + 20):
        try: srv = ThreadingHTTPServer(("127.0.0.1", port), Handler); break
        except OSError: continue
    else: raise SystemExit("no free port near %d" % a.port)
    url = "http://127.0.0.1:%d" % port; P = STATE["P"]
    print("ship editor running at %s  (%s; Ctrl+C to stop)" % (url, "%d level file(s) hold the ship table" % len(P.table_files) if P else "set the paths in the Settings tab"))
    if not a.no_browser: threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("stopped")


def main():
    ap = argparse.ArgumentParser(prog="deadlock gui", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("levels", nargs="?"); ap.add_argument("dll", nargs="?")
    ap.add_argument("--bundles"); ap.add_argument("-o", "--out"); ap.add_argument("--port", type=int, default=8765); ap.add_argument("--no-browser", action="store_true")
    argv = sys.argv[1:]
    if argv[:1] == ["gui"]: argv = argv[1:]                       # old form: deadlock_editor.py gui ...
    serve(ap.parse_args(argv))


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deadlock Ship Editor</title>
<style>
:root{--bg:#f6f6f4;--panel:#fff;--ink:#1d2024;--muted:#6a7078;--line:#dcdcd6;--accent:#c4501b;--accent-ink:#fff;--chg:#fff3e0;--ok:#1b7f3a;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14171a;--panel:#1c2024;--ink:#e6e8ea;--muted:#9097a0;--line:#30363c;--accent:#e2733a;--accent-ink:#111;--chg:#3a2a1a;--ok:#5fcf80;--bad:#ff8a80}}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5;flex-wrap:wrap}
h1{font-size:16px;margin:0 8px 0 0}nav button{background:none;border:0;padding:6px 10px;color:var(--muted);cursor:pointer;font:inherit;border-bottom:2px solid transparent}
nav button.on{color:var(--ink);border-color:var(--accent)}.sp{flex:1}
button.pri{background:var(--accent);color:var(--accent-ink);border:0;border-radius:6px;padding:7px 14px;font:inherit;cursor:pointer}button.pri:disabled{opacity:.4;cursor:default}
button.sec{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 12px;font:inherit;cursor:pointer}
main{display:grid;grid-template-columns:270px 1fr;gap:0;min-height:calc(100vh - 54px)}
#side{border-right:1px solid var(--line);background:var(--panel);padding:10px;overflow:auto;max-height:calc(100vh - 54px);position:sticky;top:54px}
#side input{width:100%;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);margin-bottom:8px}
.ship{padding:6px 8px;border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;gap:6px}.ship:hover{background:var(--bg)}.ship.on{background:var(--chg);outline:1px solid var(--accent)}
.ship small{color:var(--muted)}.dot{color:var(--accent)}
#view{padding:16px 20px;max-width:980px}.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:14px}
.card h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px 18px}
label.f{display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--muted)}label.f input,label.f select{padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:14px system-ui}
label.f input.chg,label.f select.chg{background:var(--chg);border-color:var(--accent)}label.f.chk{flex-direction:row;align-items:center;gap:8px;color:var(--ink)}
.orig{font-size:11px}.ro{font-size:13px;color:var(--ink);word-break:break-word}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;white-space:pre-wrap;max-height:340px;overflow:auto;font-size:12px}
.msg-ok{color:var(--ok)}.msg-bad{color:var(--bad)}.hint{color:var(--muted);font-size:12px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.row input[type=number],.row input[type=text],.row select{padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
@media (max-width:760px){main{grid-template-columns:1fr}#side{position:static;max-height:240px;border-right:0;border-bottom:1px solid var(--line)}}
</style></head><body>
<header><h1>Deadlock Ship Editor</h1>
<nav><button data-t="ships" class="on">Ships</button><button data-t="bundles">Bundles</button><button data-t="dll">DLL</button><button data-t="tools">Tools</button><button data-t="settings">Settings</button></nav>
<span class="sp"></span><span id="pend" class="hint"></span>
<label class="hint"><input type="checkbox" id="fresh"> start from the originals</label>
<button class="sec" id="discard" disabled>Discard</button><button class="pri" id="apply" disabled>Apply</button></header>
<main id="tab-ships"><aside id="side"><input id="q" placeholder="Search ships" autocomplete="off"><div id="list"></div></aside><section id="view"><p class="hint">Pick a ship on the left.</p></section></main>
<section id="tab-bundles" hidden style="padding:16px 20px;max-width:980px"></section>
<section id="tab-dll" hidden style="padding:16px 20px;max-width:980px"></section>
<section id="tab-tools" hidden style="padding:16px 20px;max-width:1100px"></section>
<section id="tab-settings" hidden style="padding:16px 20px;max-width:980px"></section>
<script>
const $=s=>document.querySelector(s);
const state={ships:[],cur:null,data:null,pending:{},info:{}};
async function api(path,body){const r=await fetch(path,body!==undefined?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
const el=(t,a={},...kids)=>{const e=document.createElement(t);for(const k in a){if(k==='class')e.className=a[k];else if(k.startsWith('on'))e.addEventListener(k.slice(2),a[k]);else if(a[k]!==false&&a[k]!==null&&a[k]!==undefined)e.setAttribute(k,a[k]===true?'':a[k])}for(const c of kids.flat())e.append(c&&c.nodeType?c:document.createTextNode(c??''));return e};
const GROUPS=[
 ['Hull & armour',['MaxHullPoints','ArmorFront','ArmorRear','ArmorLeft','ArmorRight','ArmorTop','ArmorBottom','PlatingSupplyRequired']],
 ['Speed & handling',['MaxVelocity','MaxTurnRate','MaxDRADISRange','CollisionExclusionZoneRadius','MissileCooldownTurns']],
 ['Costs & fleet points',['FleetPointValue','BuildCost','BuildTime','RushCost','XP','Buildable']],
 ['Carrier & munition slots',['FighterSlots','UtilityFighterSlots','MunitionSlots','SupportSlotPriority']],
 ['Class & flags',['Faction','ShipClass','SizeClass','IsCivilian','AlwaysFlagship','AlwaysAllowInCampaign','IncludedInCodex','IncludedInMultiplayer','DLCRequired','DescriptionHeaderID']]];
const key=(s,f)=>s+'|'+f;
function updPend(){const n=Object.keys(state.pending).length;$('#pend').textContent=n?n+' pending change'+(n>1?'s':''):'';$('#apply').disabled=!n;$('#discard').disabled=!n;renderList()}
function renderList(){const q=$('#q').value.toLowerCase();const dirty=new Set(Object.values(state.pending).map(p=>p.ship));const box=$('#list');box.replaceChildren(...state.ships.filter(s=>(s.name+' '+s.faction+' '+s.class).toLowerCase().includes(q)).map(s=>el('div',{class:'ship'+(s.name===state.cur?' on':''),onclick:()=>openShip(s.name)},el('span',{},dirty.has(s.name)?el('span',{class:'dot'},'● '):'',s.name),el('small',{},s.faction[0]+' · '+s.class))))}
function fieldInput(f){const k=key(state.cur,f.name),orig=f.value,pend=state.pending[k];const cur=pend?pend.value:orig;let inp;
 const mark=()=>{const v=read();const same=(f.type==='bool'||f.type==='enum')?String(v)===String(orig):Math.abs(Number(v)-Number(orig))<1e-6*Math.max(1,Math.abs(orig));if(same)delete state.pending[k];else state.pending[k]={ship:state.cur,field:f.name,value:(f.type==='bool'||f.type==='enum')?(f.type==='bool'?!!v:Number(v)):Number(v)};inp.classList.toggle('chg',!same);if(f.type!=='bool')note.textContent=same?'':'was '+orig;updPend()};
 let read;const note=el('span',{class:'orig'});
 if(f.type==='bool'){inp=el('input',{type:'checkbox',onchange:mark});inp.checked=!!cur;read=()=>inp.checked;return el('label',{class:'f chk'},inp,f.name)}
 if(f.type==='enum'){inp=el('select',{onchange:mark},Object.entries(f.options).map(([i,n])=>el('option',{value:i},n+' ('+i+')')));inp.value=String(cur);read=()=>inp.value}
 else{inp=el('input',{type:'number',step:f.type==='int'?'1':'any',value:cur,oninput:mark});read=()=>inp.value===''?NaN:inp.value}
 if(pend)inp.classList.add('chg');if(pend)note.textContent='was '+orig;return el('label',{class:'f'},f.name,inp,note)}

function resNorm(r){return JSON.stringify(r.map(x=>[x.campaign,!!x.on,x.on?Number(x.cost):0,x.on?Number(x.mission):0,x.on?String(x.label):'',x.on?!!x.unlocked:false]))}
function researchCard(d){const rs=d.research,k=key(state.cur,'__res'),card=el('div',{class:'card'});
 if(!rs){card.append(el('h3',{},'Research'),el('p',{class:'hint'},'No research table was found in the level files.'));return card}
 const get=()=>JSON.parse(JSON.stringify(state.pending[k]?state.pending[k].research:rs));
 const put=c=>{if(resNorm(c)===resNorm(rs))delete state.pending[k];else state.pending[k]={ship:state.cur,research:c};updPend();draw()};
 const buildable=()=>{const p=state.pending[key(state.cur,'Buildable')];const f=d.fields.find(x=>x.name==='Buildable');return p?!!p.value:!!(f&&f.value)};
 function draw(){const c=get(),kids=[el('h3',{},'Research (war-room blueprints)'),el('p',{class:'hint'},'Per campaign: can the ship be researched, for how many research points, after which story mission (-1 = any) and whether it is available from the start. A new campaign is needed: an existing save keeps its own research state.')];
  const head=el('div',{class:'row hint',style:'font-weight:600'},el('span',{style:'width:90px'},'campaign'),el('span',{style:'width:90px'},'researchable'),el('span',{style:'width:100px'},'cost (RP)'),el('span',{style:'width:90px'},'mission'),el('span',{style:'width:80px'},'label'),el('span',{},'unlocked at start'));kids.push(head);
  c.forEach((r,i)=>{const set=(f,val)=>{const cc=get();cc[i][f]=val;put(cc)};const off=!r.on;
   kids.push(el('div',{class:'row',style:'margin:3px 0'},el('span',{style:'width:90px'},'Campaign '+r.campaign),
    el('input',{type:'checkbox',style:'width:90px',checked:r.on,onchange:e=>set('on',e.target.checked)}),
    el('input',{type:'number',step:'1',min:'0',value:r.cost,disabled:off,style:'width:100px',onchange:e=>set('cost',Number(e.target.value))}),
    el('input',{type:'number',step:'1',min:'-1',value:r.mission,disabled:off,style:'width:90px',onchange:e=>set('mission',Number(e.target.value))}),
    el('input',{type:'text',maxlength:'12',value:r.label,disabled:off,style:'width:80px',onchange:e=>set('label',e.target.value)}),
    el('input',{type:'checkbox',checked:r.unlocked,disabled:off,onchange:e=>set('unlocked',e.target.checked)})))});
  if(c.some(r=>r.on)&&!buildable())kids.push(el('p',{class:'msg-bad'},'This ship is not marked Buildable (Costs & fleet points), so researching it would not let you build it. Tick Buildable and set a build cost/time.'));
  card.replaceChildren(...kids)}
 draw();return card}
function vetNorm(c){return JSON.stringify(c.map(r=>({rank:r.rank,effects:r.effects.map(e=>[Number(e.effect),Number(e.value)]).sort((a,b)=>a[0]-b[0])})).filter(r=>r.effects.length).sort((a,b)=>a.rank-b.rank))}
function vetCard(d){const v=d.vet,k=key(state.cur,'__vet'),card=el('div',{class:'card'});
 const get=()=>JSON.parse(JSON.stringify(state.pending[k]?state.pending[k].vet:v.current));
 const put=c=>{if(vetNorm(c)===vetNorm(v.current))delete state.pending[k];else state.pending[k]={ship:state.cur,vet:c};updPend();draw()};
 const rankOf=(c,rk)=>{let r=c.find(x=>x.rank===rk);if(!r){r={rank:rk,effects:[]};c.push(r)}return r};
 function draw(){const c=get(),kids=[el('h3',{},'Veterancy - bonuses per rank'),el('p',{class:'hint'},'Each rank can carry several effects. Values are the bonus amount (0.1 = 10%). An empty rank is left out.')];
  for(const rinfo of v.ranks){const r=c.find(x=>x.rank===rinfo.rank)||{rank:rinfo.rank,effects:[]};
   const rows=r.effects.map((e,i)=>el('div',{class:'row',style:'margin:3px 0'},
    el('select',{onchange:ev=>{const cc=get(),rr=rankOf(cc,rinfo.rank);rr.effects[i].effect=Number(ev.target.value);put(cc)}},Object.entries(v.effects).map(([id,n])=>el('option',{value:id,selected:Number(id)===e.effect},n))),
    el('input',{type:'number',step:'any',value:e.value,style:'width:110px',onchange:ev=>{const cc=get(),rr=rankOf(cc,rinfo.rank);rr.effects[i].value=Number(ev.target.value);put(cc)}}),
    el('button',{class:'sec',title:'remove this effect',onclick:()=>{const cc=get(),rr=rankOf(cc,rinfo.rank);rr.effects.splice(i,1);put(cc)}},'x')));
   const used=new Set(r.effects.map(e=>e.effect)),free=Object.keys(v.effects).find(id=>!used.has(Number(id)));
   kids.push(el('div',{style:'margin:10px 0 2px'},el('strong',{},'Rank '+rinfo.rank),el('span',{class:'hint'},'  ('+rinfo.xp+' XP)')),...rows,
    free!==undefined?el('button',{class:'sec',onclick:()=>{const cc=get(),rr=rankOf(cc,rinfo.rank);rr.effects.push({effect:Number(free),value:0});put(cc)}},'+ add effect'):'')}
  card.replaceChildren(...kids)}
 draw();return card}
function renderShip(){const d=state.data;if(!d)return;const by={};d.fields.forEach(f=>by[f.name]=f);const used=new Set();const v=$('#view');v.replaceChildren(el('h2',{style:'margin:0 0 12px'},d.name));
 for(const [title,names] of GROUPS){const fs=names.map(n=>by[n]).filter(Boolean);if(!fs.length)continue;fs.forEach(f=>used.add(f.name));v.append(el('div',{class:'card'},el('h3',{},title),el('div',{class:'grid'},fs.map(fieldInput))))}
 const rest=d.fields.filter(f=>!used.has(f.name)&&f.type!=='ro'),ro=d.fields.filter(f=>f.type==='ro');
 if(rest.length)v.append(el('div',{class:'card'},el('h3',{},'Other editable fields'),el('div',{class:'grid'},rest.map(fieldInput))));
 v.append(el('div',{class:'card'},el('h3',{},'Advantages & penalties'),el('div',{class:'ro'},'Advantages: '+(d.advantages.join(', ')||'none')),el('div',{class:'ro'},'Penalties: '+(d.penalties.join(', ')||'none')),el('p',{class:'hint'},'Advantages and penalties are read only for now (shipdb edits veterancy in the same way).')));
 v.append(researchCard(d));
 v.append(vetCard(d));
 v.append(el('div',{class:'card'},el('h3',{},'Read-only fields'),el('div',{class:'grid'},ro.map(f=>el('div',{class:'ro'},el('span',{class:'hint'},f.name+': '),f.value)))))}
async function openShip(name){state.cur=name;renderList();try{state.data=await api('/api/ship?name='+encodeURIComponent(name));renderShip()}catch(e){$('#view').replaceChildren(el('p',{class:'msg-bad'},e.message))}}
async function loadShips(){state.ships=await api('/api/ships');renderList()}
$('#q').addEventListener('input',renderList);
$('#discard').onclick=()=>{state.pending={};updPend();renderShip()};
$('#apply').onclick=async()=>{const edits=Object.values(state.pending);const b=$('#apply');b.disabled=true;try{const r=await api('/api/apply',{edits,fresh:$('#fresh').checked});state.pending={};await loadShips();await openShip(state.cur);updPend();alert('Applied. '+r.log.slice(0,6).join('\n')+(r.log.length>6?'\n...':'')+'\n\nCopy the files from '+state.info.out+' over the game files (back them up first).')}catch(e){alert('Failed: '+e.message);b.disabled=false}};
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x===b));for(const t of ['ships','bundles','dll','tools','settings'])$('#tab-'+t).hidden=t!==b.dataset.t;if(b.dataset.t==='bundles')loadBundles();if(b.dataset.t==='dll')loadDll();if(b.dataset.t==='tools')loadTools();if(b.dataset.t==='settings')loadSettings()});
let bundleForm=null;
async function loadBundles(){const box=$('#tab-bundles');if(box.dataset.ok)return;
 if(!state.info.ready){box.replaceChildren(el('div',{class:'card'},el('p',{},'Set the paths in the Settings tab first.')));return}
 box.dataset.ok=1;bundleForm=await api('/api/bundle/form');
 const path=el('input',{type:'text',id:'bpath',placeholder:'path to a ship bundle (encrypted game file or decrypted .dec)',style:'flex:1;min-width:280px;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'});
 const pick=el('button',{class:'sec',onclick:async()=>{const r=await pickPath({files:true,start:path.value||state.info.bundle_dir||state.info.home,title:'Choose a ship bundle'});if(r)path.value=r}},'Browse...');
 const sel=el('select',{onchange:e=>{if(e.target.value)path.value=joinPath(state.info.bundle_dir,e.target.value)}},[el('option',{value:''},state.info.bundles.length?'or pick from the asset folder':'(no asset folder set)'),...state.info.bundles.map(b=>el('option',{value:b},b))]);
 const norm=x=>x.toLowerCase().replace(/[^a-z0-9]/g,'');
 if(state.cur){const m=state.info.bundles.find(b=>norm(b.replace(/[.]dec$/,''))===norm(state.cur));if(m){sel.value=m;path.value=joinPath(state.info.bundle_dir,m)}}
 const groups={};bundleForm.forEach(o=>(groups[o.group]=groups[o.group]||[]).push(o));
 box.replaceChildren(el('div',{class:'card'},el('h3',{},'Ship asset (bundle)'),el('div',{class:'row'},path,pick),el('div',{class:'row',style:'margin-top:8px'},sel,el('span',{class:'hint'},'Each run starts from the original bundle and writes to '+state.info.out+'/bundles/. The file matching the open ship is preselected when its name matches.'))),
  el('div',{class:'card'},el('h3',{},'Decrypt / encrypt'),el('div',{class:'row'},...[['decrypt','Decrypt (to '+state.info.work+'/decrypted/)'],['encrypt','Encrypt (to '+state.info.out+'/bundles/)'],['check','Check']].map(([m,l])=>el('button',{class:'sec',onclick:()=>runXor(m)},l)),el('span',{id:'xmsg',class:'hint'})),
   el('p',{class:'hint',style:'margin:6px 0 0'},'Works on the bundle chosen above. Not needed before Run (encrypted bundles are decrypted automatically); use Encrypt for a result made with --no-encrypt.'),el('pre',{id:'xout',hidden:true})));
 for(const [g,os] of Object.entries(groups)){box.append(el('div',{class:'card'},el('h3',{},g),el('div',{class:'grid'},os.map(o=>{let i;
  if(o.kind==='bool'){i=el('input',{type:'checkbox','data-d':o.dest});return el('label',{class:'f chk',title:o.help},i,o.flag)}
  if(o.kind==='choice'){i=el('select',{'data-d':o.dest},[el('option',{value:''},'(default)'),...o.choices.map(c=>el('option',{value:c},c))])}
  else if(o.kind==='list'){i=el('textarea',{'data-d':o.dest,rows:2,placeholder:'one per line, e.g. '+(o.metavar||'fore=1'),style:'padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'})}
  else i=el('input',{type:'text','data-d':o.dest,placeholder:o.default||o.metavar||''});
  return el('label',{class:'f',title:o.help},o.flag,i,el('span',{class:'orig'},o.help.slice(0,110)+(o.help.length>110?'...':'')))}))))}
 box.append(el('div',{class:'row'},el('button',{class:'pri',id:'brun',onclick:runBundle},'Run'),el('span',{id:'bmsg',class:'hint'})),el('pre',{id:'bout',hidden:true}))}
async function runXor(mode){const m=$('#xmsg');m.className='hint';m.textContent='running...';
 try{const r=await api('/api/xor',{bundle:$('#bpath').value,mode});$('#xout').hidden=false;$('#xout').textContent=r.cmd+'\n\n'+r.output;m.textContent=r.ok?'done':'failed';m.className=r.ok?'msg-ok':'msg-bad'}catch(e){m.textContent=e.message;m.className='msg-bad'}}
async function runBundle(){const opts={};document.querySelectorAll('#tab-bundles [data-d]').forEach(i=>{opts[i.dataset.d]=i.type==='checkbox'?i.checked:i.value});
 $('#brun').disabled=true;$('#bmsg').textContent='running...';try{const r=await api('/api/bundle/run',{bundle:$('#bpath').value,options:opts});$('#bout').hidden=false;$('#bout').textContent=r.cmd+'\n\n'+r.output;$('#bmsg').textContent=r.ok?(r.dst?'done -> '+r.dst:'done'):'failed';$('#bmsg').className=r.ok?'msg-ok':'msg-bad'}catch(e){$('#bmsg').textContent=e.message;$('#bmsg').className='msg-bad'}$('#brun').disabled=false}
async function loadDll(){const box=$('#tab-dll');if(!state.info.ready){box.replaceChildren(el('div',{class:'card'},el('p',{},'Set the paths in the Settings tab first.')));return}const fld=(id,l,h)=>el('label',{class:'f'},l,el('input',{type:'number',id,step:'any'}),el('span',{class:'orig'},h));
 if(!box.dataset.ok){box.dataset.ok=1;box.replaceChildren(el('div',{class:'card'},el('h3',{},'Current DLL'),el('pre',{id:'dchk'},'checking...')),
  el('div',{class:'card'},el('h3',{},'Patch'),el('div',{class:'grid'},fld('d_armour','Armour regen factor','Ships built with --armour-regen (armour per tick = hull rate x factor)'),fld('d_revive','Squadron revive (turns)','0 = rebuild at once'),fld('d_rank','Commander max rank','Game default 10'),fld('d_nodes','Command-tree nodes','Game default 4; 10 = one per rank 1-10'),fld('d_step','Ranks between nodes','default 1'),fld('d_first','First node at rank','default 1'),el('label',{class:'f chk',title:'Existing saves pick up a raised MunitionSlots count in battle'},el('input',{type:'checkbox',id:'d_mun'}),'Munition load fix'),el('label',{class:'f chk',title:'Patches normally add to the DLL already in the output folder'},el('input',{type:'checkbox',id:'d_fresh'}),'Start from the original DLL (drops earlier patches)')),
   el('div',{class:'row',style:'margin-top:10px'},el('button',{class:'pri',id:'dgo',onclick:patchDll},'Patch DLL'),el('span',{id:'dmsg',class:'hint'}))),
  el('div',{class:'card'},el('h3',{},'Revert'),el('div',{class:'grid'},...[['r_armour','Armour regen'],['r_revive','Squadron revive'],['r_rank','Max rank (back to 10)'],['r_nodes','Command-tree nodes'],['r_mun','Munition load fix']].map(([id,l])=>el('label',{class:'f chk'},el('input',{type:'checkbox',id}),l))),
   el('div',{class:'row',style:'margin-top:10px'},el('button',{class:'sec',id:'rgo',onclick:revertDll},'Revert ticked (none ticked = all)'),el('span',{id:'rmsg',class:'hint'}))),el('pre',{id:'dout',hidden:true}))}
 try{const r=await api('/api/dll/check');$('#dchk').textContent=r.output+'\n(source: '+r.source+')'}catch(e){$('#dchk').textContent=e.message}}
async function patchDll(){$('#dgo').disabled=true;try{const r=await api('/api/dll/patch',{armour_factor:$('#d_armour').value,revive_turns:$('#d_revive').value,max_rank:$('#d_rank').value,command_nodes:$('#d_nodes').value,command_step:$('#d_step').value,command_first_rank:$('#d_first').value,munition_load:$('#d_mun').checked,fresh:$('#d_fresh').checked});$('#dout').hidden=false;$('#dout').textContent=r.output;$('#dmsg').textContent=r.ok?'written to '+r.dst+' - copy it over BSG_Data/Managed/Assembly-CSharp.dll':'failed';$('#dmsg').className=r.ok?'msg-ok':'msg-bad';loadDll()}catch(e){$('#dmsg').textContent=e.message;$('#dmsg').className='msg-bad'}$('#dgo').disabled=false}

const joinPath=(a,b)=>a.endsWith('/')?a+b:a+'/'+b;
function showTab(t){document.querySelector('nav button[data-t='+t+']').click()}
function pickPath({files=false,start='',title='Choose'}){return new Promise(res=>{
 let cur=start;const back=el('div',{style:'position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:50'});
 const dlg=el('div',{class:'card',style:'width:min(640px,94vw);max-height:80vh;display:flex;flex-direction:column;margin:0'});
 const pth=el('input',{type:'text',style:'flex:1;padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'});
 const list=el('div',{style:'overflow:auto;flex:1;min-height:220px;border:1px solid var(--line);border-radius:6px;margin:8px 0'});
 const done=v=>{back.remove();res(v)};
 async function go(p){try{const d=await api('/api/browse?path='+encodeURIComponent(p)+'&files='+(files?1:0));cur=d.path;pth.value=d.path;
   const rows=d.entries.map(e=>el('div',{class:'ship',onclick:()=>e.dir?go(joinPath(d.path,e.name)):done(joinPath(d.path,e.name))},(e.dir?'[folder] ':'')+e.name));
   list.replaceChildren(...(d.parent?[el('div',{class:'ship',onclick:()=>go(d.parent)},'.. (up one level)')]:[]),...rows,...(d.error?[el('p',{class:'msg-bad'},d.error)]:[]))}
  catch(e){list.replaceChildren(el('p',{class:'msg-bad'},e.message))}}
 pth.addEventListener('keydown',e=>{if(e.key==='Enter')go(pth.value)});
 dlg.append(el('h3',{},title),el('div',{class:'row'},pth,el('button',{class:'sec',onclick:()=>go(pth.value)},'Go')),list,
  el('div',{class:'row'},files?'':el('button',{class:'pri',onclick:()=>done(cur)},'Use this folder'),el('button',{class:'sec',onclick:()=>done(null)},'Cancel'),files?el('span',{class:'hint'},'Click a file to choose it.'):''));
 back.append(dlg);back.addEventListener('mousedown',e=>{if(e.target===back)done(null)});document.body.append(back);go(start||state.info.home)})}async function revertDll(){const m=$('#rmsg');$('#rgo').disabled=true;try{const r=await api('/api/dll/revert',{armour:$('#r_armour').checked,revive:$('#r_revive').checked,max_rank:$('#r_rank').checked,command_nodes:$('#r_nodes').checked,munition_load:$('#r_mun').checked});$('#dout').hidden=false;$('#dout').textContent=r.output;m.textContent=r.ok?'written to '+r.dst:'failed';m.className=r.ok?'msg-ok':'msg-bad';loadDll()}catch(e){m.textContent=e.message;m.className='msg-bad'}$('#rgo').disabled=false}

function loadSettings(){const box=$('#tab-settings'),I=state.info;
 const row=(id,label,hint,val,files)=>{const inp=el('input',{type:'text',id,value:val||'',style:'flex:1;min-width:260px;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)'});
  return el('div',{class:'card'},el('h3',{},label),el('div',{class:'row'},inp,el('button',{class:'sec',onclick:async()=>{const r=await pickPath({files,start:inp.value||I.home,title:label});if(r)inp.value=r}},'Browse...')),el('p',{class:'hint',style:'margin:6px 0 0'},hint))};
 box.replaceChildren(
  row('s_levels','Level data folder','The folder with the level files (level0, level5 ...). Every file that holds the ship table is edited. Results go to the output folder and the originals are never touched.',I.levels,false),
  row('s_dll','Game DLL (Assembly-CSharp.dll)','Gives the field layouts. Use the DLL from the same game version as the level files.',I.dll,true),
  row('s_bundles','Ship asset folder (optional)','Folder with ship bundles for the Bundles tab. Encrypted game bundles are decrypted automatically. Any single bundle file can also be picked there.',I.bundles,false),
  row('s_work','Work folder','Scratch space: decrypted copies of bundles go to its decrypted/ sub-folder.',I.work,false),
  row('s_out','Output folder','Where edited files go: its levels/, dll/ and bundles/ sub-folders, the same places the command-line tools use, so all edits stack.',I.out,false),
  el('div',{class:'row'},el('button',{class:'pri',id:'s_save',onclick:saveSettings},'Save and load'),el('span',{id:'s_msg',class:I.error?'msg-bad':'hint'},I.error||'')))}
async function saveSettings(){const m=$('#s_msg');$('#s_save').disabled=true;m.className='hint';m.textContent='loading the level data (about 15 seconds)...';
 try{state.info=await api('/api/config',{levels:$('#s_levels').value,dll:$('#s_dll').value,bundles:$('#s_bundles').value,out:$('#s_out').value,work:$('#s_work').value});
  state.pending={};state.cur=null;state.data=null;$('#view').replaceChildren(el('p',{class:'hint'},'Pick a ship on the left.'));
  for(const t of ['bundles','dll','tools'])delete $('#tab-'+t).dataset.ok;await loadShips();updPend();m.className='msg-ok';m.textContent='loaded '+state.info.tables+' level file(s) with the ship table.';showTab('ships')}
 catch(e){m.className='msg-bad';m.textContent=e.message}$('#s_save').disabled=false}
async function loadTools(){const box=$('#tab-tools');if(box.dataset.ok)return;
 if(!state.info.ready){box.replaceChildren(el('div',{class:'card'},el('p',{},'Set the paths in the Settings tab first.')));return}
 box.dataset.ok=1;const tools=await api('/api/tools');const groups={};tools.forEach(t=>(groups[t.group]=groups[t.group]||[]).push(t));
 const side=el('div',{style:'min-width:210px'}),form=el('div',{style:'flex:1;min-width:300px'});let cur=null;
 const dl=el('datalist',{id:'shipnames'},state.ships.map(s=>el('option',{value:s.name})));
 function show(t){cur=t;side.querySelectorAll('.ship').forEach(x=>x.classList.toggle('on',x.dataset.id===t.id));
  const vals={};const inputs=t.fields.map(f=>{let i;
   if(f.kind==='bool'){i=el('input',{type:'checkbox'});i.checked=!!f.default;vals[f.name]=()=>i.checked;return el('label',{class:'f chk',title:f.help},i,f.label)}
   if(f.kind==='lines'){i=el('textarea',{rows:'3',style:'padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:13px monospace'});i.value=f.default}
   else if(f.kind.startsWith('choice:')){i=el('select',{},f.kind.slice(7).split(',').map(o=>el('option',{value:o},o)));i.value=f.default}
   else{i=el('input',{type:f.kind==='int'||f.kind==='float'?'number':'text',step:f.kind==='int'?'1':'any',value:f.default,list:f.kind==='ship'?'shipnames':null})}
   vals[f.name]=()=>i.value;
   const row=el('label',{class:'f',title:f.help},f.label,f.kind==='file'?el('div',{class:'row'},i,el('button',{class:'sec',type:'button',onclick:async()=>{const r=await pickPath({files:true,start:i.value||state.info.levels,title:f.label});if(r)i.value=r}},'Browse...')):i);return row});
  const out=el('pre',{hidden:true}),msg=el('span',{class:'hint'});
  const run=el('button',{class:'pri',onclick:async()=>{const v={};for(const k in vals)v[k]=vals[k]();run.disabled=true;msg.className='hint';msg.textContent='running...';out.hidden=true;
    try{const r=await api('/api/tools/run',{id:t.id,values:v,fresh:$('#fresh').checked});out.textContent='$ '+r.cmd+'\n\n'+r.output;out.hidden=false;msg.className=r.ok?'msg-ok':'msg-bad';msg.textContent=r.ok?'done':'the tool reported an error';
     if(r.ok&&t.writes){await loadShips();if(state.cur)await openShip(state.cur)}}
    catch(e){msg.className='msg-bad';msg.textContent=e.message}run.disabled=false}},t.writes?'Run and save':'Run');
  form.replaceChildren(el('div',{class:'card'},el('h3',{},t.group+' - '+t.title),el('p',{class:'hint'},t.help+(t.writes?' Results go to '+state.info.out+' (the same folder as everything else, so edits stack).':'')),el('div',{class:'grid'},inputs),el('div',{class:'row',style:'margin-top:10px'},run,msg)),out,dl)}
 for(const [g,ts] of Object.entries(groups)){side.append(el('div',{class:'hint',style:'margin:10px 0 3px;font-weight:600'},g));ts.forEach(t=>side.append(el('div',{class:'ship','data-id':t.id,onclick:()=>show(t)},t.title)))}
 box.replaceChildren(el('div',{class:'row',style:'align-items:flex-start;gap:20px'},side,form));show(tools[0])}
(async()=>{try{state.info=await api('/api/config');if(state.info.ready)await loadShips();else showTab('settings')}catch(e){$('#view').replaceChildren(el('p',{class:'msg-bad'},e.message))}})();
</script></body></html>
"""

if __name__ == "__main__":
    main()
