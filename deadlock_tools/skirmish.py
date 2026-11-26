"""Fleet-limit DLL options: ships per skirmish / multiplayer fleet, the extra fleet-point caps, and capital ships per
campaign fleet.

The custom-module DLL's CustomSkirmishFleetCap reads StreamingAssets/deadlock-fleet.txt:

    ships 20
    fleet_caps 32000 64000
    campaign_ships 14

`ships` replaces SkirmishDataTables.maxShips (level data, stock 10) wherever the game reads it; `fleet_caps` are added
to the PvP and co-op fleet-cap choices (default 32,000 and 64,000 when the line is missing); `campaign_ships` replaces
WarRoomDataTables.FleetInfo.MaxShips (capital ships a campaign fleet takes into battle, stock 7; the one support slot is
fixed). Set with `dll patch --skirmish-ships N --fleet-caps A,B --campaign-ships N` or the DLL page. See
docs/game/skirmish.md.
"""
import os

FILE = "deadlock-fleet.txt"
OLD_FILE = "deadlock-skirmish.txt"          # name used before release; read if FILE is missing
DEFAULT_CAPS = (32000, 64000)
MAX_SHIPS = 99
MAX_CAP = 1000000


def read(path):
    """{"ships": int or None, "fleet_caps": tuple or None} (None = not set: level data / default caps)"""
    out = {"ships": None, "fleet_caps": None, "campaign_ships": None}
    if path and not os.path.isfile(path) and os.path.isfile(os.path.join(os.path.dirname(path), OLD_FILE)):
        path = os.path.join(os.path.dirname(path), OLD_FILE)
    if not path or not os.path.isfile(path):
        return out
    for raw in open(path, encoding="utf-8"):
        parts = raw.split("#", 1)[0].split()
        if not parts:
            continue
        if parts[0] in ("ships", "campaign_ships") and len(parts) > 1 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
        elif parts[0] == "fleet_caps":
            out["fleet_caps"] = tuple(int(v.replace(",", "")) for v in parts[1:] if v.replace(",", "").isdigit())
    return out


def parse_caps(text):
    """'32000,64000' / '32000 64000' -> (32000, 64000); '' or 'none' -> () (no extra caps)"""
    text = str(text).strip()
    if text.lower() in ("", "none", "-"):
        return ()
    caps = []
    for item in text.replace(",", " ").split():
        if not item.isdigit():
            raise ValueError("fleet caps are whole numbers, got %r" % item)
        value = int(item)
        if not 1000 <= value <= MAX_CAP:
            raise ValueError("a fleet cap must be between 1000 and %d, got %d" % (MAX_CAP, value))
        caps.append(value)
    return tuple(sorted(set(caps)))


def check_ships(value, what="ships per skirmish fleet"):
    value = int(value)
    if not 1 <= value <= MAX_SHIPS:
        raise ValueError("%s must be between 1 and %d" % (what, MAX_SHIPS))
    return value


def render(settings):
    lines = ["# fleet limits for the custom-module DLL (CustomSkirmishFleetCap); written by deadlock-tools",
             "# ships N: ships per skirmish / multiplayer fleet (missing: the level data's maxShips, stock 10)",
             "# fleet_caps A B ...: extra fleet-point cap choices (missing: 32000 64000)",
             "# campaign_ships N: capital ships per campaign fleet (missing: the level data's FleetInfo.MaxShips, stock 7)"]
    if settings.get("ships"):
        lines.append("ships %d" % settings["ships"])
    if settings.get("fleet_caps") is not None:
        lines.append("fleet_caps " + " ".join(str(v) for v in settings["fleet_caps"]))
    if settings.get("campaign_ships"):
        lines.append("campaign_ships %d" % settings["campaign_ships"])
    return "\n".join(lines) + "\n"


def describe(settings):
    ships = settings.get("ships")
    caps = settings.get("fleet_caps")
    return ["skirmish ships per fleet: %s" % (ships if ships else "from the level data"),
            "extra fleet caps: %s" % (", ".join(map(str, caps)) if caps else "none" if caps == () else
                                     "default (%s)" % ", ".join(map(str, DEFAULT_CAPS))),
            "campaign capital ships per fleet: %s" % (settings.get("campaign_ships") or "from the level data")]


def update(path, ships=None, fleet_caps=None, clear_ships=False, clear_caps=False, campaign_ships=None, clear_campaign=False):
    """merge changes into the settings file at path; returns (settings, log lines). The file is removed when empty."""
    settings = read(path)
    log = []
    if clear_ships and settings["ships"] is not None:
        log.append("skirmish ships per fleet %s -> level data" % settings["ships"]); settings["ships"] = None
    if clear_caps and settings["fleet_caps"] is not None:
        log.append("extra fleet caps -> default (%s)" % ", ".join(map(str, DEFAULT_CAPS))); settings["fleet_caps"] = None
    if clear_campaign and settings["campaign_ships"] is not None:
        log.append("campaign capital ships per fleet %s -> level data" % settings["campaign_ships"]); settings["campaign_ships"] = None
    if campaign_ships is not None:
        campaign_ships = check_ships(campaign_ships, "capital ships per campaign fleet")
        if settings["campaign_ships"] != campaign_ships:
            log.append("campaign capital ships per fleet %s -> %d" % (settings["campaign_ships"] or "level data", campaign_ships))
            settings["campaign_ships"] = campaign_ships
    if ships is not None:
        ships = check_ships(ships)
        if settings["ships"] != ships:
            log.append("skirmish ships per fleet %s -> %d" % (settings["ships"] or "level data", ships)); settings["ships"] = ships
    if fleet_caps is not None:
        if settings["fleet_caps"] != tuple(fleet_caps):
            log.append("extra fleet caps -> %s" % (", ".join(map(str, fleet_caps)) or "none")); settings["fleet_caps"] = tuple(fleet_caps)
    old = os.path.join(os.path.dirname(path), OLD_FILE)
    if os.path.isfile(old):
        os.remove(old)
    if settings["ships"] is None and settings["fleet_caps"] is None and settings["campaign_ships"] is None:
        if os.path.isfile(path):
            os.remove(path)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(render(settings))
    return settings, log
