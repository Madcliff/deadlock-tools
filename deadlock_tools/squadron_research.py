"""Squadron research: which squadron types a campaign researches, at what RP cost and after which story mission.

The research tree is WarRoomDataTables.BlueprintInfo in level5 and level85 (identical copies; edit both). It has one
entry per campaign (0 Deadlock, 1 Quorum, 2 Sin and Sacrifice, 3 Galactica Rebirth, 4 Ghost Fleet, 5 Armistice), each
with CapitalShipUnlocks, MunitionUnlocks, SquadronUnlocks and BattlestarShipUnlocks. A SquadronUnlocks entry
(BlueprintSquadronUnlock, the same generic CHDPKHPONLG<T> as the ship entries) holds:

    ODOMECMDGKJ  squadron type id          GEIBBHBCKAM  story mission required (-1 = none)
    GKIMOHIEFNI  research cost (RP)        NPFHKIHKKIC  label on the research node
    HJGFMHOLHPG  unlocked from the start   COPOFKMOPIH  second flag (always false in stock data)

The research panel rebuilds its list from these entries every time (GBEPGGLDNMC.KPJBEHEGPHJ), so added entries show in
existing campaigns too. A squadron's state (GBEPGGLDNMC.IKIHCCPCAAC): Purchased when flagged unlocked or already in the
save's CampaignState.UnlockedSquadrons; Unavailable when the type is missing from the SquadronDataTable or its DLC is
not owned; Available once the required story mission is completed; Locked otherwise. Buying it adds it to
UnlockedSquadrons, which is what the carrier slot picker checks in a campaign. The "unlocked" flag is applied to
UnlockedSquadrons when a campaign starts, so it only takes effect in a new campaign.
"""
import struct

from deadlock_tools import shipdb as sd

KEY = "SquadronUnlocks"
MUNITION_KEY = "MunitionUnlocks"     # same entry layout: id, mission, cost, label, unlocked, flag
CAMPAIGNS = {0: "Deadlock", 1: "Quorum", 2: "Sin and Sacrifice", 3: "Galactica Rebirth", 4: "Ghost Fleet", 5: "Armistice"}
F_ID, F_MISSION, F_COST, F_LABEL, F_UNLOCKED, F_FLAG2 = ("ODOMECMDGKJ", "GEIBBHBCKAM", "GKIMOHIEFNI", "NPFHKIHKKIC",
                                                         "HJGFMHOLHPG", "COPOFKMOPIH")


def _plain(u):
    return {"id": u[F_ID], "mission": u[F_MISSION], "cost": u[F_COST], "label": u[F_LABEL],
            "unlocked": bool(u[F_UNLOCKED]), "flag2": bool(u[F_FLAG2])}


def read(S, data, key=KEY):
    """{campaign: [entry dicts]} of the squadron research lists in one level file, or None without the table"""
    try:
        _pid, _off, _size, res = sd.load_wardata(S, data)
    except LookupError:
        return None
    return {it["Campaign"]: [_plain(u) for u in it[key]] for it in res["BlueprintInfo"]}


def entries_for(S, data, type_id, key=KEY):
    """{campaign: entry} for one squadron type"""
    found = read(S, data, key) or {}
    return {c: e for c, items in found.items() for e in items if e["id"] == int(type_id)}


def _check_layout(mono, res, key=KEY):
    """pack_unlock must reproduce every stock squadron entry byte for byte, or nothing is edited"""
    for ci, it in enumerate(res["BlueprintInfo"]):
        _cpos, _first, end = sd.list_span(res, ci, key)
        els = it[key]
        for j, u in enumerate(els):
            start = u["_off"][F_ID][0]
            stop = els[j + 1]["_off"][F_ID][0] if j + 1 < len(els) else end
            if mono[start:stop] != sd.pack_unlock(u[F_ID], u[F_MISSION], u[F_COST], u[F_LABEL], u[F_UNLOCKED], u[F_FLAG2]):
                raise ValueError("squadron research entry layout differs from what this tool expects (campaign %s #%d); "
                                 "not editing" % (it["Campaign"], j))


def apply(S, data, type_id, changes, key=KEY):
    """Edit one squadron type's research entries in one level file.

    changes: {campaign: {"cost": RP, "mission": id or -1, "label": text, "unlocked": bool} to add or update,
              or None to remove the type from that campaign}. Missing keys of an existing entry are kept; a new entry
    defaults to cost 600, mission -1, an empty label, not unlocked. Returns (new bytes, log); a file without the
    research table is returned unchanged."""
    data = bytes(data)
    try:
        pid, off, size, res = sd.load_wardata(S, data)
    except LookupError:
        return data, []
    mono = bytearray(data[off:off + size])
    _check_layout(bytes(mono), res, key)
    type_id = int(type_id)
    campaigns = {it["Campaign"]: ci for ci, it in enumerate(res["BlueprintInfo"])}
    jobs, log = [], []
    for campaign, change in changes.items():
        campaign = int(campaign)
        if campaign not in campaigns:
            raise ValueError("no campaign %d in the research table (campaigns: %s)" % (campaign, sorted(campaigns)))
        ci = campaigns[campaign]
        items = [_plain(u) for u in res["BlueprintInfo"][ci][key]]
        old = next((e for e in items if e["id"] == type_id), None)
        name = CAMPAIGNS.get(campaign, "campaign %d" % campaign)
        if change is None:
            if old is None:
                continue
            items = [e for e in items if e["id"] != type_id]
            log.append("%s: removed from research" % name)
        else:
            entry = dict(old or {"id": type_id, "mission": -1, "cost": 600, "label": "", "unlocked": False, "flag2": False})
            for field in ("cost", "mission"):
                if change.get(field) is not None:
                    number = float(change[field])
                    if not number.is_integer():
                        raise ValueError("%s must be a whole number" % field)
                    entry[field] = int(number)
            if change.get("label") is not None:
                entry["label"] = str(change["label"])
                if "\0" in entry["label"] or len(entry["label"].encode("utf-8")) > 32:
                    raise ValueError("a research label must be at most 32 bytes")
            if change.get("unlocked") is not None:
                entry["unlocked"] = change["unlocked"] in (True, 1, "1", "true", "True", "yes")
            if entry["cost"] < 0 or entry["mission"] < -1:
                raise ValueError("cost must be 0 or more and mission -1 (none) or a story mission id")
            if old == entry:
                continue
            if old is None:
                items.append(entry)
                log.append("%s: researchable (cost %d RP, mission %s%s)" % (name, entry["cost"],
                           entry["mission"] if entry["mission"] >= 0 else "none", ", unlocked at start" if entry["unlocked"] else ""))
            else:
                items = [entry if e["id"] == type_id else e for e in items]
                log.append("%s: research cost %d -> %d RP, mission %s -> %s, label %r -> %r, unlocked %s -> %s"
                           % (name, old["cost"], entry["cost"], old["mission"], entry["mission"], old["label"],
                              entry["label"], old["unlocked"], entry["unlocked"]))
        cpos, first, end = sd.list_span(res, ci, key)
        body = b"".join(sd.pack_unlock(e["id"], e["mission"], e["cost"], e["label"], e["unlocked"], e["flag2"]) for e in items)
        jobs.append((cpos, first, end, len(items), body))
    if not jobs:
        return data, log
    for cpos, first, end, count, body in sorted(jobs, reverse=True):        # back to front keeps offsets valid
        mono[first:end] = body
        struct.pack_into("<i", mono, cpos, count)
    result = sd.resize_object(data, pid, bytes(mono))
    # verify: the edited lists read back as intended and every other list is unchanged
    before = read(S, data, key)
    after = read(S, result, key)
    if after is None or set(after) != set(before):
        raise ValueError("verification failed: the research table did not read back")
    for campaign, items in after.items():
        change = changes.get(campaign, changes.get(str(campaign), "keep"))
        others_before = [e for e in before[campaign] if e["id"] != type_id]
        if [e for e in items if e["id"] != type_id] != others_before:
            raise ValueError("verification failed: other research entries changed in campaign %d" % campaign)
        mine = [e for e in items if e["id"] == type_id]
        if change is None and mine or change not in (None, "keep") and len(mine) != 1:
            raise ValueError("verification failed for campaign %d" % campaign)
    _p, o2, s2, r2 = sd.load_wardata(S, result)
    _check_layout(result[o2:o2 + s2], r2, key)
    return result, log


def apply_all(S, levels, type_id, changes, key=KEY):
    """apply() to every level file that has the research table. Returns ({file: bytes}, log)"""
    out, log = {}, []
    for name, data in levels.items():
        new, lines = apply(S, data, type_id, changes, key)
        if new != data:
            out[name] = new
            if not log:
                log = lines
    if out:
        log = ["%s research (%s): %s" % ("squadron" if key == KEY else "munition", ", ".join(sorted(out)), "; ".join(log))]
    return out, log


def copy_entries(S, levels, donor_id, type_id, label=None, unlocked=False, key=KEY):
    """give `type_id` the donor's research entries (same campaigns, cost and mission; `unlocked` and `label` set)"""
    donor = {}
    for data in levels.values():
        donor = entries_for(S, data, donor_id, key)
        if donor:
            break
    if not donor:
        return {}, []
    changes = {c: {"cost": e["cost"], "mission": e["mission"], "label": e["label"] if label is None else label,
                   "unlocked": unlocked} for c, e in donor.items()}
    return apply_all(S, levels, type_id, changes, key)


def cli(a):
    """shipdb squadronresearch [TYPE]
    shipdb setsquadronresearch TYPE CAMPAIGNS [cost=RP] [mission=ID|-1] [label=TEXT] [unlocked=yes|no]
    shipdb setsquadronresearch TYPE CAMPAIGNS remove          CAMPAIGNS: 0,2,5 or all"""
    import os
    from deadlock_tools import squadron_table as st
    S = sd.Schema(a.dll)
    folder = a.level0 if os.path.isdir(a.level0) else os.path.dirname(a.level0)
    sources = {}
    for f_ in sd.files_of(folder):
        base = os.path.basename(f_)
        if base.startswith("level") and not base.endswith(".resS"):
            sources[base] = sd.chain_source(a, f_, a.out, quiet=a.cmd == "squadronresearch")
    levels = {base: open(path, "rb").read() for base, path in sources.items()}
    table = next((st.find(S, d)[3] for d in levels.values() if st.find(S, d)), None)
    research = next((r for r in (read(S, d) for d in levels.values()) if r), None)
    if table is None or research is None:
        raise SystemExit("the level folder needs the SquadronDataTable and the research table (level5 / level85)")
    names = {e["id"]: e["name"] for e in table["objects"]}
    rest = list(a.args)
    if a.cmd == "squadronresearch":
        ids = [st.entry(table, rest[0])["id"]] if rest else sorted(names)
        for type_id in ids:
            rows = {c: e for c, items in research.items() for e in items if e["id"] == type_id}
            print("%-28s %-4d %s" % (names.get(type_id, type_id), type_id, "; ".join(
                "%s: %d RP, mission %s%s%s" % (CAMPAIGNS.get(c, c), e["cost"], e["mission"] if e["mission"] >= 0 else "none",
                                             ", label " + e["label"] if e["label"] else "", ", unlocked at start" if e["unlocked"] else "")
                for c, e in sorted(rows.items())) or "not researched in any campaign"))
        return
    if len(rest) < 2:
        raise SystemExit(cli.__doc__)
    try:
        type_id = st.entry(table, rest[0])["id"]
    except ValueError as e:
        raise SystemExit(str(e))
    campaigns = sorted(research) if rest[1].lower() == "all" else [int(x) for x in rest[1].split(",") if x.strip()]
    if rest[2:] == ["remove"]:
        change = None
    else:
        change = {}
        for item in rest[2:]:
            key, sep, value = item.partition("=")
            if not sep or key not in ("cost", "mission", "label", "unlocked"):
                raise SystemExit("expected cost=, mission=, label= or unlocked=, got %r\n%s" % (item, cli.__doc__))
            change[key] = value if key in ("label", "unlocked") else int(value)
            if key == "unlocked":
                change[key] = value.lower() in ("1", "true", "yes", "on")
    try:
        out, log = apply_all(S, levels, type_id, {c: change for c in campaigns})
    except ValueError as e:
        raise SystemExit(str(e))
    os.makedirs(a.out, exist_ok=True)
    for base, data in out.items():
        with open(os.path.join(a.out, base), "wb") as stream:
            stream.write(data)
    print("\n".join("[ok] " + line for line in log) or "[ok] no change")
    if out:
        print("\nwrote %s to %s/. Added entries show in existing campaigns; 'unlocked at start' needs a new campaign."
              % (", ".join(sorted(out)), a.out))
