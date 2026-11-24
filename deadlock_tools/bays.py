"""Read and edit a ship bundle's squadron launch bays and munition slots (for the GUI's visual bay editor).

How the game wires them (Assembly-CSharp.dll, see docs/game/launch-bays.md):
  * ShipInfo spawns one FighterSquadron per carrier slot and binds them to the LaunchFightersAction buttons BY ORDER:
    it collects every LaunchFightersAction under the ship (hierarchy order) and gives button i the squadron of slot i.
  * A squadron launches from the LaunchPoint whose SlotNo is its slot (the tool's notes: falls back to any compatible
    bay). LaunchPoint also holds CanLaunch / CanDock / DockRange / LaunchCompatibility (squadron types) and the
    launch path (LaunchPoints, local offsets).
  * Munition slots are the FireMissileAction buttons in the Munitions menu (and its submenus), in order.

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
                     "position": tr["m_LocalPosition"], "matrix": w.transform_matrix(tr["m_Father"]["m_PathID"], view.v),
                     "icon": view.v(icon)["m_LocalPosition"] if icon and icon in prefab.by_pid and prefab.by_pid[icon].classid == 4 else None})
    def buttons(script):
        return [{"id": str(pid), "menu": view.path(view.v(pid)["m_GameObject"]["m_PathID"]), "radial": view.v(pid)["radialMenuPosition"]}
                for pid in view.ordered(script, root)]
    layout = w.bundle_mount_layout(path)
    return {"revision": w.layout_revision(bundle), "bays": bays, "launchButtons": buttons("LaunchFightersAction"),
            "munitionButtons": buttons("FireMissileAction"), "hull": layout["hull"],
            "slots": sorted({b["slot"] for b in bays if b["canLaunch"]})}


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
    """spec = {revision, bays: [{id, position?, slot?, canLaunch?, canDock?, dockRange?, compat?}],
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
        if "position" in row:
            t_pid = view.transform_of(lp["m_GameObject"]["m_PathID"]); tr = fields(t_pid)
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
            "bays": len(check["bays"]), "launchButtons": len(check["launchButtons"]), "munitionButtons": len(check["munitionButtons"])}
