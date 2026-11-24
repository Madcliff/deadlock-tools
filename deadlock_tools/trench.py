"""Lay a ship's bow guns out in rows along the mouth trench of its bow (made for the Mercury MK2's alligator-head bow).

How it reads the bow (docs/game/ship-scale.md, "Bow guns in the mouth trench"):
  * Every stock (non-clone) turret of the group sits on a "lip": the hull surface nearest to it. Stock turrets of one lip
    share the surface height, a small height offset from it and a rotation. The two lips holding the most stock turrets
    are the mouth: the higher one is the upper jaw's underside, the lower one the trench floor (the lower jaw's top).
    Stock turrets on any other lip (the MK2's belly pair) are moved into the mouth.
  * Rows run across the ship (along x) and are `gap` game units apart. The first floor row goes through the front stock
    floor turrets; further floor rows step back by `gap`. Behind the open front of the mouth only the side grooves (the
    jaw line) still have trench floor, so those rows hold one mirrored pair each and run back along the jaw. Upper rows
    start just ahead of the first floor row (where the 3D gap to it is `gap`) and step forward under the snout.
  * Inside a row turrets are `gap` apart and mirror-symmetric (x <-> -x); a spot only counts if the lip surface is there
    within 0.25 local units across a 0.5-unit neighbourhood (so turrets keep off the edges and walls).
  * Turrets fill the spots from the centre of the mouth outwards. Stock turrets that already lie on a spot stay exactly
    where they are. Each turret snaps to its lip with the lip's stock height offset, rotation and InitialAngles, and gets
    MeshScale 1.0. More turrets than spots: the extra clones (highest _xN first) are removed with their GameObjects.

Distances are in game units (the ship's root scale applied); positions are written in the mounts' local frame.
"""
import copy
import math
import os
import re

from deadlock_tools import weapons as w
from deadlock_tools.bays import _pptrs

CLONE = re.compile(r"_x(\d+)$")
LIP_TOLERANCE = 0.25        # a spot belongs to a lip if the surface is within this of the lip height (local units)
EDGE_MARGIN = 0.5           # the lip must continue this far around a spot (local units)
STEP = 0.05                 # sampling step along a row (local units)


def _open(path):
    bundle = w.read_bundle(path)
    offset, size, *_ = bundle["nodes"][0]
    prefab = w.SerFile(bundle["data"][offset:offset + size])
    resource = next((bundle["data"][o:o + s] for o, s, _f, name in bundle["nodes"] if name.endswith(".resS")), b"")
    return bundle, prefab, resource


def _apply(M, p):
    return tuple(M[r][0] * p[0] + M[r][1] * p[1] + M[r][2] * p[2] + M[r][3] for r in range(3))


def _mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) + (A[i][3] if j == 3 else 0) for j in range(4)] for i in range(3)]


def _selected(name, quad, selector):
    sel = selector.lower()
    return sel in ("all", quad) or name.lower().startswith(sel)


def _mounts(prefab, value):
    out = []
    for mount in w.weapon_mounts(prefab, value):
        owner, transform = w.mount_objects(prefab, value, mount)
        mb, tr = value(mount.pid), value(transform.pid)
        q = mb["Quadrant"]
        p = tr["m_LocalPosition"]
        parent = tr["m_Father"]["m_PathID"]
        out.append({"mount": mount, "owner": owner, "transform": transform, "name": value(owner.pid)["m_Name"],
                    "quad": w.MOUNT_QUADRANTS[q] if 0 <= q < len(w.MOUNT_QUADRANTS) else str(q),
                    "pos": (p["x"], p["y"], p["z"]), "rot": dict(tr["m_LocalRotation"]), "angles": dict(mb["InitialAngles"]),
                    "scale": mb["MeshScale"], "parent": parent, "matrix": w.transform_matrix(parent, value)})
    return out


def _hull_frame(prefab, value):
    """world matrix of the mesh build_hull_query measures in (the biggest non-wreck mesh, as hull_geometry picks it)"""
    best = None
    for o in prefab.objs:
        if o.classid != 33: continue
        mesh_pid = value(o.pid)["m_Mesh"]["m_PathID"]
        if not mesh_pid or mesh_pid not in prefab.by_pid: continue
        mesh = value(mesh_pid)
        if "wreck" in mesh["m_Name"].lower() or not mesh["m_SubMeshes"]: continue
        if best is None or mesh["m_VertexData"]["m_VertexCount"] > best[0]:
            best = (mesh["m_VertexData"]["m_VertexCount"], value(o.pid)["m_GameObject"]["m_PathID"])
    if best is None:
        raise ValueError("the bundle has no readable hull mesh")
    go = value(best[1])
    tp = next(c["component"]["m_PathID"] for c in go["m_Component"]
              if c["component"]["m_PathID"] in prefab.by_pid and prefab.by_pid[c["component"]["m_PathID"]].classid == 4)
    return w.transform_matrix(tp, value)


def _scale(M):
    return math.sqrt(sum(M[r][0] ** 2 for r in range(3)))


def _is_identity(M, tol=1e-3):
    return all(abs(M[r][c] - (1.0 if r == c else 0.0)) < tol for r in range(3) for c in range(4))


class Bow:
    """the hull and mounts of one bundle, in the frame of the selected mounts"""

    def __init__(self, prefab, resource, selector="fore"):
        self.prefab = prefab
        cache = {}
        def value(pid):
            if pid not in cache: cache[pid] = prefab.read(prefab.by_pid[pid])[0]
            return cache[pid]
        self.value = value
        self.hq = w.build_hull_query(prefab, resource, value)
        if self.hq is None:
            raise ValueError("the bundle has no readable hull mesh")
        self.mounts = _mounts(prefab, value)
        self.group = [m for m in self.mounts if _selected(m["name"], m["quad"], selector)]
        if not self.group:
            raise ValueError("no turrets match %r" % selector)
        parents = {m["parent"] for m in self.group}
        if len(parents) != 1:
            raise ValueError("the %s turrets hang under %d different parents; a trench layout needs one" % (selector, len(parents)))
        self.frame = self.group[0]["matrix"]                          # mount frame -> world
        hull_world = _hull_frame(prefab, value)
        to_hull = _mul(w.inverse_transform(hull_world), self.frame)  # mount frame -> hull mesh frame
        if not _is_identity(to_hull):
            raise ValueError("the %s mounts are not in the hull mesh's frame; the trench layout does not handle that" % selector)
        self.unit = _scale(self.frame)                               # game units per local unit
        self.others = [_apply(m["matrix"], m["pos"]) for m in self.mounts if m not in self.group]

    def world(self, p):
        return _apply(self.frame, p)

    def surface(self, x, z, height):
        """the hull height at (x, z) nearest to height, or None"""
        ys = self.hq(x, z)
        return min(ys, key=lambda y: abs(y - height)) if ys else None

    def on_lip(self, x, z, height):
        """surface height if the lip at height covers (x, z) and EDGE_MARGIN around it, else None"""
        here = None
        for dx, dz in ((0, 0), (EDGE_MARGIN, 0), (-EDGE_MARGIN, 0), (0, EDGE_MARGIN), (0, -EDGE_MARGIN)):
            s = self.surface(x + dx, z + dz, height)
            if s is None or abs(s - height) > LIP_TOLERANCE: return None
            if dx == dz == 0: here = s
        return here


def find_lips(bow):
    """cluster the group's stock turrets by the hull surface they sit on; returns (upper, floor) lip dicts"""
    stock = [m for m in bow.group if not CLONE.search(m["name"])]
    clusters = []
    for m in sorted(stock, key=lambda m: m["name"]):
        x, y, z = m["pos"]
        s = bow.surface(x, z, y)
        if s is None: continue
        for c in clusters:
            if abs(c["height"] - s) < 0.4:
                c["members"].append((m, s)); c["height"] = sum(v for _, v in c["members"]) / len(c["members"]); break
        else:
            clusters.append({"height": s, "members": [(m, s)]})
    mouth = sorted(clusters, key=lambda c: -len(c["members"]))[:2]
    if len(mouth) < 2 or len(mouth[1]["members"]) < 2:
        raise ValueError("could not find two lips with stock turrets (found %d)" % len(clusters))
    lips = []
    for c in sorted(mouth, key=lambda c: -c["height"]):
        first = c["members"][0][0]
        offsets = sorted(m["pos"][1] - s for m, s in c["members"])
        lips.append({"height": c["height"], "offset": offsets[len(offsets) // 2] if len(offsets) % 2 else sum(offsets[len(offsets) // 2 - 1:len(offsets) // 2 + 1]) / 2,
                     "rot": first["rot"], "angles": first["angles"], "stock": [m for m, _ in c["members"]]})
    lips[0]["name"], lips[1]["name"] = "upper", "floor"
    return lips[0], lips[1]


def _intervals(bow, lip, z):
    """runs of x >= 0 where the lip covers (x, z) and (-x, z); a run touching 0 is returned as starting at 0"""
    xmax = max(abs(bow.hq.xlo), abs(bow.hq.xhi))
    runs, start, last = [], None, None
    for i in range(int(xmax / STEP) + 2):
        x = i * STEP
        ok = bow.on_lip(x, z, lip["height"]) is not None and bow.on_lip(-x, z, lip["height"]) is not None
        if ok and start is None: start = x
        if ok: last = x
        if not ok and start is not None:
            runs.append((start, last)); start = None
    if start is not None: runs.append((start, last))
    return runs


def _row_xs(runs, g, stock_xs):
    """x >= 0 of the spots in one row (mirrored by the caller); 0 means one spot on the centre line"""
    xs = []
    for a, b in runs:
        if a == 0:
            best = None
            phases = [0.0] + [g / 2 + k * STEP for k in range(int(g / 2 / STEP))] + [x % g for x in stock_xs if g / 2 <= x % g < g]
            for s in phases:
                if s > b: continue
                pts = [s + k * g for k in range(int((b - s) / g + 1e-9) + 1)]
                count = len(pts) * 2 - (1 if s == 0 else 0)
                matched = sum(any(abs(p - x) < 0.3 for p in pts) for x in stock_xs)
                key = (count, matched)
                if best is None or key > best[0]: best = (key, [(s, pts)])
                elif key == best[0]: best[1].append((s, pts))
            if best:
                ties = best[1]
                if best[0][1]:                                          # stock turrets on the row: the phase nearest them
                    err = lambda pts: sum(min(abs(p - x) for p in pts) for x in stock_xs if min(abs(p - x) for p in pts) < 0.3)
                    xs += min(ties, key=lambda t: (round(err(t[1]), 6), t[0]))[1]
                else:
                    xs += ties[len(ties) // 2][1]                       # the middle of the equally good phases
        else:
            n = int((b - a) / g + 1e-9) + 1
            first = (a + b) / 2 - (n - 1) * g / 2
            xs += [first + k * g for k in range(n)]
    return xs


def plan(bow, gap=4.0, upper_rows=1, floor_rows=None):
    """spots for the group: list of {pos, lip, row, priority}, plus the lips and row z values"""
    upper, floor = find_lips(bow)
    g = gap / bow.unit
    front = max(m["pos"][2] for m in floor["stock"])
    z_floor = [m["pos"][2] for m in floor["stock"] if m["pos"][2] > front - 1.0]
    z0 = sum(z_floor) / len(z_floor)
    dy = abs((upper["height"] + upper["offset"]) - (floor["height"] + floor["offset"]))
    z_up = z0 + math.sqrt(max(g * g - dy * dy, 0.0)) + 1e-3
    near = [m["pos"][2] for m in upper["stock"] if z_up <= m["pos"][2] <= z_up + 0.5]
    if near: z_up = min(near)
    need = len(bow.group)

    spots = []
    def add_row(lip, z, row):
        stock_xs = [abs(m["pos"][0]) for m in lip["stock"] if abs(m["pos"][2] - z) < 0.5]
        xs = _row_xs(_intervals(bow, lip, z), g, stock_xs)
        for x in xs:
            for sx in ((x,) if x == 0 else (x, -x)):
                s = bow.on_lip(sx, z, lip["height"])
                spots.append({"pos": (sx, s + lip["offset"], z), "lip": lip["name"], "row": row,
                              "priority": math.hypot(abs(sx), z - z0)})
        return len(xs)

    rows = []
    for k in range(upper_rows):
        z = z_up + k * g
        if add_row(upper, z, "upper %d" % (k + 1)): rows.append(("upper %d" % (k + 1), z))
    k = 0
    while floor_rows is None or k < floor_rows:
        z = z0 - k * g
        if not add_row(floor, z, "floor %d" % (k + 1)): break
        rows.append(("floor %d" % (k + 1), z)); k += 1
        if floor_rows is None and len(spots) >= need + 2: break
        if k > 200: break
    return {"spots": spots, "upper": upper, "floor": floor, "rows": rows, "gap_local": g, "front_z": z0}


def _choose(bow, spots, count, gap):
    """take up to count spots, centre first, mirrored pairs together, all >= gap game units from each other and other turrets"""
    units = {}
    for s in spots:
        units.setdefault((round(abs(s["pos"][0]), 4), s["pos"][2], s["lip"]), []).append(s)
    order = sorted(units.values(), key=lambda u: (u[0]["priority"], -u[0]["pos"][0]))
    def fits(unit, world):
        pts = [bow.world(s["pos"]) for s in unit]
        if any(math.dist(a, b) < gap - 1e-6 for i, a in enumerate(pts) for b in pts[i + 1:]): return False
        return all(math.dist(p, q) >= gap - 1e-6 for p in pts for q in world)
    def greedy(units):
        taken, world = [], list(bow.others)
        for unit in units:
            if len(taken) + len(unit) > count: continue
            if fits(unit, world):
                taken += unit; world += [bow.world(s["pos"]) for s in unit]
            if len(taken) == count: break
        return taken
    taken = greedy(order)
    if len(taken) < count and any(len(u) == 1 for u in order):
        # a centre-line spot can leave an odd turret with only pairs left: try without centre spots
        pairs = greedy([u for u in order if len(u) == 2])
        if len(pairs) > len(taken): taken = pairs
    return taken


def _twins(group):
    """mirror pairs of mounts: stock twins by mirrored position, clones by their originals' twins and suffix"""
    stock = {m["name"]: m for m in group if not CLONE.search(m["name"])}
    twin = {}
    for m in stock.values():
        x, y, z = m["pos"]
        t = next((o for o in stock.values() if o is not m and abs(o["pos"][0] + x) < 0.3 and abs(o["pos"][1] - y) < 0.3
                  and abs(o["pos"][2] - z) < 0.3), None)
        if t: twin[m["name"]] = t["name"]
    names = {m["name"] for m in group}
    for m in group:
        hit = CLONE.search(m["name"])
        if hit:
            base = m["name"][:hit.start()]
            if base in twin and twin[base] + hit.group(0) in names: twin[m["name"]] = twin[base] + hit.group(0)
    return twin


def layout_plan(bow, gap=4.0, upper_rows=1, floor_rows=None):
    """where every mount of the group goes: {placements: {name: (pos, lip)}, remove: [names], rows, lips}"""
    p = plan(bow, gap, upper_rows, floor_rows)
    taken = _choose(bow, p["spots"], len(bow.group), gap)
    by_name = {m["name"]: m for m in bow.group}
    placements, free = {}, list(taken)
    # stock turrets already on a spot stay exactly where they are (if the spacing still holds)
    for m in sorted(bow.group, key=lambda m: m["name"]):
        if CLONE.search(m["name"]): continue
        spot = min(free, key=lambda s: math.dist(s["pos"], m["pos"]), default=None)
        if spot is None or math.dist(spot["pos"], m["pos"]) > 0.3: continue
        others = [bow.world(s["pos"]) for s in taken if s is not spot] + bow.others
        keep = m["pos"] if all(math.dist(bow.world(m["pos"]), q) >= gap - 1e-6 for q in others) else spot["pos"]
        spot["pos"] = keep
        placements[m["name"]] = (keep, spot["lip"]); free.remove(spot)
    # the rest: originals first, then clones by suffix; mirror twins onto mirror spots (+x gets the starboard twin)
    twin = _twins(bow.group)
    def order(m):
        hit = CLONE.search(m["name"])
        return (int(hit.group(1)) if hit else 0, m["name"])
    queue = [m["name"] for m in sorted(bow.group, key=order) if m["name"] not in placements]
    units = {}
    for s in free:
        units.setdefault((round(abs(s["pos"][0]), 4), s["pos"][2], s["lip"]), []).append(s)
    for unit in sorted(units.values(), key=lambda u: u[0]["priority"]):
        unit.sort(key=lambda s: -s["pos"][0])
        if len(unit) == 2 and queue:
            a = queue.pop(0)
            b = twin.get(a) if twin.get(a) in queue else (queue[0] if queue else None)
            if b is None:
                placements[a] = (unit[0]["pos"], unit[0]["lip"]); continue
            queue.remove(b)
            plus, minus = (a, b) if by_name[a]["pos"][0] >= by_name[b]["pos"][0] else (b, a)
            placements[plus] = (unit[0]["pos"], unit[0]["lip"]); placements[minus] = (unit[1]["pos"], unit[1]["lip"])
        elif queue:
            placements[queue.pop(0)] = (unit[0]["pos"], unit[0]["lip"])
    remove = [n for n in queue]
    if any(not CLONE.search(n) for n in remove):
        raise ValueError("the trench only holds %d turrets; even without clones the %d stock ones do not fit"
                         % (len(taken), sum(1 for m in bow.group if not CLONE.search(m["name"]))))
    return {"placements": placements, "remove": remove, "rows": p["rows"], "upper": p["upper"], "floor": p["floor"],
            "gap_local": p["gap_local"], "spots": len(p["spots"])}


def trench_layout(src, dst, selector="fore", gap=4.0, upper_rows=1, floor_rows=None, encrypt=None):
    """Lay out the turrets matching selector (a quadrant, 'all' or a name prefix) in rows along the bow's mouth trench and
    write dst. upper_rows: rows on the upper jaw's underside; floor_rows: rows on the trench floor (None = as many as the
    turrets need, running back along the side grooves). encrypt: None = like src. Returns a report dict."""
    bundle, prefab, resource = _open(src)
    if encrypt is None:
        with open(src, "rb") as fh: encrypt = not fh.read(7) == b"UnityFS"
    bow = Bow(prefab, resource, selector)
    lp = layout_plan(bow, gap, upper_rows, floor_rows)
    lips = {"upper": lp["upper"], "floor": lp["floor"]}
    replaced, removed, edited, log = {}, set(), {}, []
    value = bow.value

    def fields(pid):
        if pid not in edited: edited[pid] = copy.deepcopy(value(pid))
        return edited[pid]

    by_name = {m["name"]: m for m in bow.group}
    moved = 0
    for name, (pos, lip_name) in lp["placements"].items():
        m, lip = by_name[name], lips[lip_name]
        tr = fields(m["transform"].pid)
        new = {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}
        if any(abs(new[a] - tr["m_LocalPosition"][a]) > 1e-6 for a in "xyz"): moved += 1
        tr["m_LocalPosition"] = new
        tr["m_LocalRotation"] = dict(lip["rot"])
        mb = fields(m["mount"].pid)
        mb["MeshScale"] = type(mb["MeshScale"])(1.0)
        mb["InitialAngles"] = dict(lip["angles"])
    log.append("%d turret(s) placed in %d row(s), %d moved, all at MeshScale 1.0" % (len(lp["placements"]), len(lp["rows"]), moved))

    # remove the extra clones' GameObjects (as bays.apply removes launch bays)
    def remove_subtree(transform):
        go = value(transform)["m_GameObject"]["m_PathID"]
        for child in value(transform)["m_Children"]:
            if child["m_PathID"] in prefab.by_pid: remove_subtree(child["m_PathID"])
        removed.update(c["component"]["m_PathID"] for c in value(go)["m_Component"]); removed.add(go)
    for name in lp["remove"]:
        t = by_name[name]["transform"].pid
        parent = value(t)["m_Father"]["m_PathID"]
        if parent:
            pf = fields(parent); pf["m_Children"] = [c for c in pf["m_Children"] if c["m_PathID"] != t]
        remove_subtree(t)
    if lp["remove"]:
        log.append("removed %d extra clone(s): %s" % (len(lp["remove"]), ", ".join(lp["remove"])))
        for ab in [o for o in prefab.objs if o.classid == 142]:
            abv = fields(ab.pid); table = abv["m_PreloadTable"]
            keep = [i for i, ref in enumerate(table) if not (ref["m_FileID"] == 0 and ref["m_PathID"] in removed)]
            if len(keep) != len(table):
                new_index = {old: new for new, old in enumerate(keep)}
                for entry in abv["m_Container"]:
                    info = entry["second"]; start, size = info["preloadIndex"], info["preloadSize"]
                    kept = [i for i in range(start, start + size) if i in new_index]
                    info["preloadIndex"] = new_index[kept[0]] if kept else 0; info["preloadSize"] = len(kept)
                abv["m_PreloadTable"] = [table[i] for i in keep]
        dangling = []
        for o in prefab.objs:
            if o.pid in removed: continue
            bad = [r for r in _pptrs(edited.get(o.pid) or value(o.pid), []) if r in removed]
            if bad: dangling.append("class %d object %d -> %s" % (o.classid, o.pid, bad[:3]))
        if dangling:
            raise ValueError("removing would leave references to deleted objects: %s" % "; ".join(dangling[:5]))

    for pid, v in edited.items():
        if pid not in removed: replaced[pid] = prefab.write(prefab.by_pid[pid], v)
    cab = prefab.rebuild(replaced=replaced, removed=removed)
    (_o1, _s1, f1, n1), (o2, s2, f2, n2) = bundle["nodes"][0], bundle["nodes"][1]
    tmp = dst + ".tmp"
    w.write_bundle(tmp, bundle, [(f1, n1, cab), (f2, n2, bundle["data"][o2:o2 + s2])])
    try:
        report = check(tmp, selector, gap)
        with open(tmp, "rb") as fh: data = fh.read()
        with open(dst, "wb") as fh: fh.write(w.xor_bytes(data) if encrypt else data)
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    used = {}
    for name, (pos, _lip) in lp["placements"].items():
        row = next(n for n, z in lp["rows"] if abs(z - pos[2]) < 0.6 and n.startswith(_lip))
        used.setdefault(row, []).append(name)
    report.update({"log": log, "rows": [{"name": n, "z": round(z, 3), "turrets": sorted(used[n])} for n, z in lp["rows"] if n in used],
                   "removed": lp["remove"], "moved": moved, "encrypted": bool(encrypt)})
    return report


def check(path, selector="fore", gap=4.0):
    """verify a layout: MeshScale, closest pair (3D, game units, also to other turrets), mirror symmetry, distance to the
    nearest hull surface; re-reads the bundle with weapons.bundle_mount_layout. Raises ValueError on a failure."""
    layout = w.bundle_mount_layout(path)                     # must re-read
    _bundle, prefab, resource = _open(path)
    bow = Bow(prefab, resource, selector)
    pts = [bow.world(m["pos"]) for m in bow.group]
    closest = min((math.dist(a, b) for i, a in enumerate(pts) for b in pts[i + 1:]), default=float("inf"))
    to_others = min((math.dist(a, q) for a in pts for q in bow.others), default=float("inf"))
    scales = sorted({round(m["scale"], 6) for m in bow.group})
    unmirrored = [m["name"] for m in bow.group if abs(m["pos"][0]) > 0.05 and not any(
        abs(o["pos"][0] + m["pos"][0]) < 0.05 and abs(o["pos"][1] - m["pos"][1]) < 0.05 and abs(o["pos"][2] - m["pos"][2]) < 0.05
        for o in bow.group)]
    off_lip = 0.0
    for m in bow.group:
        x, y, z = m["pos"]
        s = bow.surface(x, z, y)
        off_lip = max(off_lip, abs(y - s) if s is not None else float("inf"))
    problems = []
    if scales != [1.0]: problems.append("MeshScale %s" % scales)
    if closest < gap - 1e-3: problems.append("closest pair %.3f < %.2f" % (closest, gap))
    if to_others < gap - 1e-3: problems.append("%.3f from another turret group" % to_others)
    if unmirrored: problems.append("not mirrored: %s" % ", ".join(unmirrored))
    if off_lip > 0.3: problems.append("a turret is %.2f from the hull surface" % off_lip)
    if problems:
        raise ValueError("trench layout check failed: " + "; ".join(problems))
    return {"turrets": len(bow.group), "closest": round(closest, 3), "closest_other": round(to_others, 3),
            "max_off_surface": round(off_lip, 3), "mesh_scales": scales, "mounts_total": len(layout["mounts"])}


def draw_comparison(before, after, out, selector="fore", titles=("Before", "After")):
    """top-down + side view of the group in two bundles, drawn with Pillow (not a package dependency)"""
    from PIL import Image, ImageDraw
    views = []
    for path in (before, after):
        _bundle, prefab, resource = _open(path)
        bow = Bow(prefab, resource, selector)
        views.append(bow)
    px = 16                                               # pixels per game unit
    xs = [p for bow in views for m in bow.group for p in (bow.world(m["pos"]),)]
    xlo, xhi = min(p[0] for p in xs) - 4, max(p[0] for p in xs) + 4
    zlo, zhi = min(p[2] for p in xs) - 5, max(p[2] for p in xs) + 6
    ylo, yhi = min(p[1] for p in xs) - 4, max(p[1] for p in xs) + 4
    pw, ph_top, ph_side = int((xhi - xlo) * px), int((zhi - zlo) * px), int((yhi - ylo) * px)
    W, H = 2 * pw + 60, ph_top + ph_side + 110
    img = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(img)
    for col, (bow, title) in enumerate(zip(views, titles)):
        ox = 20 + col * (pw + 20)
        top = (ox, 40, ox + pw, 40 + ph_top); side = (ox, 60 + ph_top, ox + pw, 60 + ph_top + ph_side)
        d.text((ox, 12), title, fill="black")
        d.rectangle(top, outline="#bbbbbb"); d.rectangle(side, outline="#bbbbbb")
        def T(p): return (ox + (p[0] - xlo) * px, 40 + (zhi - p[2]) * px)
        def S(p): return (ox + (zhi - p[2]) * px * pw / ((zhi - zlo) * px), 60 + ph_top + (yhi - p[1]) * px)
        # hull: sample the surfaces inside the box
        inv = w.inverse_transform(bow.frame)
        step = 0.5 / bow.unit
        lx0, _, lz0 = _apply(inv, (xlo, 0, zlo)); lx1, _, lz1 = _apply(inv, (xhi, 0, zhi))
        x = min(lx0, lx1)
        while x <= max(lx0, lx1):
            z = min(lz0, lz1)
            while z <= max(lz0, lz1):
                ys = bow.hq(x, z)
                if ys:
                    wp = bow.world((x, max(ys), z)); d.point(T(wp), fill="#c8c8c8")
                    if abs(x) < 6 / bow.unit:
                        for y in ys:
                            sp = S(bow.world((x, y, z)))
                            if side[1] <= sp[1] <= side[3] and side[0] <= sp[0] <= side[2]: d.point(sp, fill="#c8c8c8")
                z += step
            x += step
        r = 2.0 * px                                      # half the stock 4.0 gap
        for m in bow.group:
            p = bow.world(m["pos"]); stock = not CLONE.search(m["name"])
            colour = "#1f3a5f" if stock else "#c0392b"
            cx, cy = T(p); d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=colour, width=2)
            d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=colour)
            sx, sy = S(p); d.ellipse((sx - 3, sy - 3, sx + 3, sy + 3), fill=colour)
        d.text((ox + 4, 44), "top-down, bow at the top", fill="#666666")
        d.text((side[0] + 4, side[1] + 4), "side view (bow on the left), |x| < 6 hull slice", fill="#666666")
    d.text((20, H - 40), "16 px per game unit. Dark = stock turret, red = clone. Circles = half the stock 4.0 gap: "
           "circles that overlap in the top view are turrets at different heights.", fill="black")
    img.save(out)
    return out
