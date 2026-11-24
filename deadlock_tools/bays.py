"""Read and edit a ship bundle's squadron launch bays and munition slots (for the GUI's visual bay editor).

How the game wires them (Assembly-CSharp.dll, see docs/game/launch-bays.md):
  * ShipInfo spawns one FighterSquadron per carrier slot and binds them to the LaunchFightersAction buttons BY ORDER:
    it collects every LaunchFightersAction under the ship (hierarchy order) and gives button i the squadron of slot i.
  * A squadron launches from the LaunchPoint whose SlotNo is its slot (the tool's notes: falls back to any compatible
    bay). LaunchPoint also holds CanLaunch / CanDock / DockRange / LaunchCompatibility (squadron types) and the
    launch path (LaunchPoints, local offsets).
  * Munition slots are the FireMissileAction buttons in the Munitions menu (and its submenus), in order
    (FireMissileAction.Slot = its index among GetComponentsInChildren<FireMissileAction>).
  * Munition launchers are MissileLauncher components (MK2: Weapons/MissileLauncherFore/Port/Starboard), each with
    LaunchPoints (local offsets). FireMissileAction collects GetComponentsInChildren<MissileLauncher>() from the ship
    root and logs an error (the button is dead) when there are none, so at least one launcher must remain.

Removing slot k therefore removes every bay with SlotNo k, renumbers the bays above it, removes the k-th launch button
and lowers the ship's FighterSlots / UtilityFighterSlots by one (the caller updates the level data).
"""
import copy

from deadlock_tools import weapons as w


def _open(path):
    bundle = w.read_bundle(path)
    offset, size, *_ = bundle["nodes"][0]
    return bundle, w.SerFile(bundle["data"][offset:offset + size])


class _View:
    """cached reads of a prefab with helpers for scripts, GameObjects and transforms"""
    def __init__(self, prefab):
        self.p = prefab
        self.cache = {}
        self.scripts = {o.pid: self.v(o.pid)["m_ClassName"] for o in prefab.objs if o.classid == 115}

    def v(self, pid):
        if pid not in self.cache:
            self.cache[pid] = self.p.read(self.p.by_pid[pid])[0]
        return self.cache[pid]

    def script(self, pid):
        return self.scripts.get(self.v(pid).get("m_Script", {}).get("m_PathID"))

    def components(self, go):
        return [ref["component"]["m_PathID"] for ref in self.v(go)["m_Component"] if ref["component"]["m_PathID"] in self.p.by_pid]

    def transform_of(self, go):
        return next(pid for pid in self.components(go) if self.p.by_pid[pid].classid == 4)

    def ship_root(self):
        ships = [o.pid for o in self.p.objs if o.classid == 114 and self.script(o.pid) == "ShipInfo"]
        go = max((self.v(s)["m_GameObject"]["m_PathID"] for s in ships), key=lambda g: len(self.v(g)["m_Component"]))
        return go, self.transform_of(go)

    def walk(self, transform):
        """GameObjects under transform, depth first in child order (the order GetComponentsInChildren uses)"""
        go = self.v(transform)["m_GameObject"]["m_PathID"]
        yield go
        for child in self.v(transform)["m_Children"]:
            if child["m_PathID"] in self.p.by_pid:
                yield from self.walk(child["m_PathID"])

    def ordered(self, script, root_transform):
        out = []
        for go in self.walk(root_transform):
            for pid in self.components(go):
                if self.p.by_pid[pid].classid == 114 and self.script(pid) == script:
                    out.append(pid)
        return out

    def path(self, go):
        names, t = [], self.transform_of(go)
        while t:
            names.append(self.v(self.v(t)["m_GameObject"]["m_PathID"])["m_Name"]); t = self.v(t)["m_Father"]["m_PathID"]
        return "/".join(reversed(names[:-1]))


def read_layout(path):
    """bays, launch buttons and munition buttons of a bundle, plus hull points for drawing (same space as mount layouts)"""
    bundle, prefab = _open(path)
    view = _View(prefab)
    _ship_go, root = view.ship_root()
    bays = []
    for pid in view.ordered("LaunchPoint", root):
        lp = view.v(pid); go = lp["m_GameObject"]["m_PathID"]; tr = view.v(view.transform_of(go))
        icon = lp["iconPosition"]["m_PathID"]
        bays.append({"id": str(pid), "name": view.v(go)["m_Name"], "path": view.path(go), "slot": lp["SlotNo"],
                     "canLaunch": bool(lp["CanLaunch"]), "canDock": bool(lp["CanDock"]), "dockRange": lp["DockRange"],
                     "compat": list(lp["LaunchCompatibility"]), "launchPath": lp["LaunchPoints"],
                     "position": tr["m_LocalPosition"], "rotation": tr["m_LocalRotation"],
                     "matrix": w.transform_matrix(tr["m_Father"]["m_PathID"], view.v),
                     "icon": view.v(icon)["m_LocalPosition"] if icon and icon in prefab.by_pid and prefab.by_pid[icon].classid == 4 else None})
    def buttons(script):
        return [{"id": str(pid), "menu": view.path(view.v(pid)["m_GameObject"]["m_PathID"]), "radial": view.v(pid)["radialMenuPosition"]}
                for pid in view.ordered(script, root)]
    launchers = []
    for pid in view.ordered("MissileLauncher", root):
        ml = view.v(pid); go = ml["m_GameObject"]["m_PathID"]; tr = view.v(view.transform_of(go))
        launchers.append({"id": str(pid), "name": view.v(go)["m_Name"], "path": view.path(go), "position": tr["m_LocalPosition"],
                          "rotation": tr["m_LocalRotation"], "launchPoints": ml["LaunchPoints"],
                          "matrix": w.transform_matrix(tr["m_Father"]["m_PathID"], view.v)})
    layout = w.bundle_mount_layout(path)
    return {"revision": w.layout_revision(bundle), "bays": bays, "launchers": launchers, "launchButtons": buttons("LaunchFightersAction"),
            "munitionButtons": buttons("FireMissileAction"), "hull": layout["hull"],
            "slots": sorted({b["slot"] for b in bays if b["canLaunch"]})}


def realign_spec(layout, rows=None, step_y=3.0, step_z=4.0):
    """Bay edits that lay a ship's cloned launch bays out as mirrored port/starboard pairs.

    The templates are the launch+dock bays without "_slot" in their name (Mercury: PortRear / StarboardRear). Every
    launch bay named after a template (the template and its clones, PortRear_slot4, ...) takes part. Sorted by slot,
    consecutive slots form pairs: the lower slot on the port side (x < 0), the higher one mirrored to starboard. Pair k
    goes to a grid that starts at the port template: column k % columns moves step_z along the ship, row k // columns
    moves step_y up (local units of the bays' parent). Each bay takes its side's template rotation, launch path and
    icon offset (the icon marker is a child of the bay, so it moves with it). Returns (bay rows, notes)."""
    bays = layout["bays"]
    templates = [b for b in bays if b["canLaunch"] and b["canDock"] and "_slot" not in b["name"]]
    port = next((b for b in templates if b["position"]["x"] < 0), None)
    starboard = next((b for b in templates if b["position"]["x"] > 0), None)
    if not port or not starboard:
        raise ValueError("realigning needs a port and a starboard launch+dock bay to copy (Mercury: PortRear / StarboardRear)")
    names = tuple(t["name"] for t in templates)
    group = sorted((b for b in bays if b["canLaunch"] and b["name"].startswith(names)), key=lambda b: (b["slot"], b["name"]))
    if len({b["slot"] for b in group}) != len(group):
        raise ValueError("two realigned bays share a slot; give each slot one launch bay first")
    pairs = [group[i:i + 2] for i in range(0, len(group), 2)]
    rows = rows or (1 if len(pairs) <= 3 else 2)
    columns = -(-len(pairs) // rows)
    base = port["position"]
    out, notes = [], []
    for k, pair in enumerate(pairs):
        y, z = base["y"] + step_y * (k // columns), base["z"] + step_z * (k % columns)
        for bay, template, sign in zip(pair, (port, starboard), (-1, 1)):
            row = {"id": bay["id"], "position": {"x": sign * abs(template["position"]["x"]), "y": y, "z": z},
                   "rotation": template["rotation"], "launchPath": template["launchPath"],
                   # name it after its side and slot (StarboardRear_slot7); the stock bays keep their names
                   "name": template["name"] if bay["id"] == template["id"] else "%s_slot%d" % (template["name"], bay["slot"])}
            if template["icon"] is not None and bay["icon"] is not None:
                row["icon"] = template["icon"]
            out.append(row)
            notes.append("slot %d: %s, %s at y %.2f, z %.2f" % (bay["slot"], row["name"], "port" if sign < 0 else "starboard", y, z))
    moved = {row["id"]: row["position"] for row in out}
    spots = [(b["name"], moved.get(b["id"], b["position"])) for b in bays]
    closest = min(((sum((p[a] - q[a]) ** 2 for a in "xyz") ** 0.5, n, m) for i, (n, p) in enumerate(spots) for m, q in spots[i + 1:]
                   if not (moved.get(next(b["id"] for b in bays if b["name"] == n)) is None and
                           moved.get(next(b["id"] for b in bays if b["name"] == m)) is None)), default=None)
    if closest:
        notes.append("closest pair involving a moved bay: %s / %s, %.2f units apart" % (closest[1], closest[2], closest[0]))
    return out, notes


def _pptrs(value, out):
    """every local PPtr path id inside a parsed object"""
    if isinstance(value, dict):
        if set(value) >= {"m_FileID", "m_PathID"} and value.get("m_FileID") == 0 and value.get("m_PathID"):
            out.append(value["m_PathID"])
        for v in value.values():
            _pptrs(v, out)
    elif isinstance(value, list):
        for v in value:
            _pptrs(v, out)
    return out


def apply(src, dst, spec, encrypt=True):
    """spec = {revision, bays: [{id, position?, rotation?, launchPath?, icon?, slot?, canLaunch?, canDock?, dockRange?, compat?}],
               removeSlots: [slot numbers], removeMunitions: [FireMissileAction ids]}
    Writes dst and returns {log, removedSlots, removedMunitions} so the caller can lower the ship's slot counts."""
    bundle, prefab = _open(src)
    if spec.get("revision") and spec["revision"] != w.layout_revision(bundle):
        raise ValueError("The bundle changed since it was loaded. Reload the bay layout before saving.")
    view = _View(prefab)
    _ship_go, root = view.ship_root()
    replaced, removed, log = {}, set(), []
    edited = {}                                     # pid -> parsed fields being edited

    def fields(pid):
        if pid not in edited:
            edited[pid] = copy.deepcopy(view.v(pid))
        return edited[pid]

    bays = {pid: view.v(pid) for pid in view.ordered("LaunchPoint", root)}
    for row in spec.get("bays") or []:
        pid = int(row["id"])
        if pid not in bays:
            raise ValueError("unknown launch bay %s" % row["id"])
        lp = fields(pid); name = view.v(lp["m_GameObject"]["m_PathID"])["m_Name"]
        for key, field, cast in (("slot", "SlotNo", int), ("dockRange", "DockRange", float)):
            if key in row and row[key] != lp[field]:
                log.append("%s: %s %s -> %s" % (name, field, lp[field], row[key])); lp[field] = cast(row[key])
        for key, field in (("canLaunch", "CanLaunch"), ("canDock", "CanDock")):
            if key in row and bool(row[key]) != bool(lp[field]):
                log.append("%s: %s -> %s" % (name, field, bool(row[key]))); lp[field] = type(lp[field])(bool(row[key]))
        if "compat" in row:
            compat = sorted({int(x) for x in row["compat"]})
            if compat != sorted(lp["LaunchCompatibility"]):
                log.append("%s: launch types %s -> %s" % (name, lp["LaunchCompatibility"], compat)); lp["LaunchCompatibility"] = compat
        if "position" in row or "rotation" in row:
            t_pid = view.transform_of(lp["m_GameObject"]["m_PathID"]); tr = fields(t_pid)
            if "position" in row:
                new = {axis: float(row["position"][axis]) for axis in "xyz"}
                if any(abs(new[a] - tr["m_LocalPosition"][a]) > 1e-6 for a in "xyz"):
                    log.append("%s: moved to %.2f, %.2f, %.2f" % (name, new["x"], new["y"], new["z"])); tr["m_LocalPosition"] = new
            if "rotation" in row:
                new = {axis: float(row["rotation"][axis]) for axis in "xyzw"}
                if any(abs(new[a] - tr["m_LocalRotation"][a]) > 1e-6 for a in "xyzw"):
                    log.append("%s: rotation set" % name); tr["m_LocalRotation"] = new
        if row.get("name"):
            go = lp["m_GameObject"]["m_PathID"]; gf = fields(go)
            if gf["m_Name"] != row["name"]:
                log.append("%s: renamed to %s" % (gf["m_Name"], row["name"])); gf["m_Name"] = str(row["name"]); name = gf["m_Name"]
        if "launchPath" in row:
            path = [{axis: float(p[axis]) for axis in "xyz"} for p in row["launchPath"]]
            if not path:
                raise ValueError("%s needs at least one launch path point" % name)
            if path != lp["LaunchPoints"]:
                log.append("%s: launch path set (%d point(s))" % (name, len(path))); lp["LaunchPoints"] = path
        if row.get("icon") is not None:
            icon = lp["iconPosition"]["m_PathID"]
            if not icon or icon not in prefab.by_pid or prefab.by_pid[icon].classid != 4:
                raise ValueError("%s has no icon marker to move" % name)
            it = fields(icon); new = {axis: float(row["icon"][axis]) for axis in "xyz"}
            if any(abs(new[a] - it["m_LocalPosition"][a]) > 1e-6 for a in "xyz"):
                log.append("%s: icon marker at %.2f, %.2f, %.2f" % (name, new["x"], new["y"], new["z"])); it["m_LocalPosition"] = new

    launchers = {pid: view.v(pid) for pid in view.ordered("MissileLauncher", root)}
    for row in spec.get("launchers") or []:
        pid = int(row["id"])
        if pid not in launchers:
            raise ValueError("unknown munition launcher %s" % row["id"])
        ml = fields(pid); name = view.v(ml["m_GameObject"]["m_PathID"])["m_Name"]
        if "launchPoints" in row:
            points = [{axis: float(p[axis]) for axis in "xyz"} for p in row["launchPoints"]]
            if not points:
                raise ValueError("%s needs at least one launch point" % name)
            if points != ml["LaunchPoints"]:
                log.append("%s: %d launch point(s) -> %d" % (name, len(ml["LaunchPoints"]), len(points))); ml["LaunchPoints"] = points
        if "position" in row:
            t_pid = view.transform_of(ml["m_GameObject"]["m_PathID"]); tr = fields(t_pid)
            new = {axis: float(row["position"][axis]) for axis in "xyz"}
            if any(abs(new[a] - tr["m_LocalPosition"][a]) > 1e-6 for a in "xyz"):
                log.append("%s: moved to %.2f, %.2f, %.2f" % (name, new["x"], new["y"], new["z"])); tr["m_LocalPosition"] = new

    def remove_subtree(transform):
        go = view.v(transform)["m_GameObject"]["m_PathID"]
        for child in view.v(transform)["m_Children"]:
            if child["m_PathID"] in prefab.by_pid: remove_subtree(child["m_PathID"])
        removed.update(view.components(go)); removed.add(go)

    def detach(transform):
        parent = view.v(transform)["m_Father"]["m_PathID"]
        if parent:
            pf = fields(parent); pf["m_Children"] = [c for c in pf["m_Children"] if c["m_PathID"] != transform]

    def remove_component(pid):
        go = view.v(pid)["m_GameObject"]["m_PathID"]; gf = fields(go)
        gf["m_Component"] = [c for c in gf["m_Component"] if c["component"]["m_PathID"] != pid]
        removed.add(pid)

    # remove slots, highest first so the renumbering stays simple
    launch_buttons = view.ordered("LaunchFightersAction", root)
    removed_slots = sorted({int(k) for k in spec.get("removeSlots") or []}, reverse=True)
    for k in removed_slots:
        hits = [pid for pid in bays if pid not in removed and fields(pid)["SlotNo"] == k]
        if not hits:
            raise ValueError("no launch bay has slot %d" % k)
        for pid in hits:
            lp = fields(pid); go = lp["m_GameObject"]["m_PathID"]; t = view.transform_of(go)
            icon = lp["iconPosition"]["m_PathID"]
            detach(t); remove_subtree(t)
            if icon and icon in prefab.by_pid and prefab.by_pid[icon].classid == 4 and icon not in removed:
                detach(icon); remove_subtree(icon)
            log.append("removed bay %s (slot %d)" % (view.v(go)["m_Name"], k))
        for pid in bays:
            if pid not in removed and fields(pid)["SlotNo"] > k:
                fields(pid)["SlotNo"] -= 1
        live = [b for b in launch_buttons if b not in removed]
        if k >= len(live):
            raise ValueError("slot %d has no launch button (the ship has %d)" % (k, len(live)))
        remove_component(live[k])
        log.append("removed launch button %d (%s)" % (k, view.path(view.v(live[k])["m_GameObject"]["m_PathID"])))

    remove_launchers = [int(x) for x in spec.get("removeLaunchers") or []]
    for pid in remove_launchers:
        if pid not in launchers:
            raise ValueError("unknown munition launcher %s" % pid)
    if remove_launchers and len(set(remove_launchers)) >= len(launchers) and view.ordered("FireMissileAction", root):
        raise ValueError("the ship must keep at least one munition launcher while it has munition slots "
                         "(FireMissileAction finds launchers with GetComponentsInChildren<MissileLauncher> and fails with none)")
    for pid in remove_launchers:
        go = view.v(pid)["m_GameObject"]["m_PathID"]; t = view.transform_of(go)
        if len([c for c in view.components(go) if prefab.by_pid[c].classid == 114]) == 1:
            detach(t); remove_subtree(t)                         # the GameObject only carries the launcher
        else:
            remove_component(pid)
        log.append("removed munition launcher %s" % view.v(go)["m_Name"])

    munitions = view.ordered("FireMissileAction", root)
    removed_munitions = [int(x) for x in spec.get("removeMunitions") or []]
    for pid in removed_munitions:
        if pid not in munitions:
            raise ValueError("unknown munition button %s" % pid)
        log.append("removed munition button %d (%s)" % (munitions.index(pid), view.path(view.v(pid)["m_GameObject"]["m_PathID"])))
        remove_component(pid)
    # a submenu this edit emptied (it had actions besides Back, now it has none): drop the button that opens it
    def actions(go, gone):
        return [c["component"]["m_PathID"] for c in view.v(go)["m_Component"]
                if c["component"]["m_PathID"] in prefab.by_pid and prefab.by_pid[c["component"]["m_PathID"]].classid == 114
                and c["component"]["m_PathID"] not in gone and view.script(c["component"]["m_PathID"]) != "BackAction"]
    if removed_slots or removed_munitions:
        for pid in [o.pid for o in prefab.objs if o.classid == 114 and view.script(o.pid) == "SubMenuAction"]:
            target = view.v(pid).get("SubMenu", {}).get("m_PathID")
            if pid in removed or not target or target not in prefab.by_pid or prefab.by_pid[target].classid != 1: continue
            if actions(target, set()) and not actions(target, removed):
                remove_component(pid); log.append("removed the button opening the now-empty %s menu" % view.v(target)["m_Name"])

    # the AssetBundle's preload table lists every object of the prefab: drop removed entries, fix the containers
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

    # nothing that stays may point at a removed object
    dangling = []
    for o in prefab.objs:
        if o.pid in removed: continue
        refs = _pptrs(edited.get(o.pid) or view.v(o.pid), [])
        bad = [r for r in refs if r in removed]
        if bad: dangling.append("class %d object %d -> %s" % (o.classid, o.pid, bad[:3]))
    if dangling:
        raise ValueError("removing would leave references to deleted objects: %s" % "; ".join(dangling[:5]))
    for pid, value in edited.items():
        if pid not in removed:
            replaced[pid] = prefab.write(prefab.by_pid[pid], value)
    cab = prefab.rebuild(replaced=replaced, removed=removed)
    (_O1, _S1, F1, N1), (O2, S2, F2, N2) = bundle["nodes"][0], bundle["nodes"][1]
    ress = bundle["data"][O2:O2 + S2]
    tmp = dst + ".tmp"
    w.write_bundle(tmp, bundle, [(F1, N1, cab), (F2, N2, ress)])
    check = read_layout(tmp)                          # verify by re-reading
    with open(tmp, "rb") as fh:
        data = fh.read()
    import os
    with open(dst, "wb") as fh:
        fh.write(w.xor_bytes(data) if encrypt else data)
    os.remove(tmp)
    return {"log": log or ["no changes"], "removedSlots": removed_slots, "removedMunitions": len(removed_munitions),
            "removedLaunchers": len(remove_launchers), "launchers": len(check["launchers"]),
            "bays": len(check["bays"]), "launchButtons": len(check["launchButtons"]), "munitionButtons": len(check["munitionButtons"])}
