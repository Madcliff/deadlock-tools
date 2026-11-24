# Launch bays, slot buttons and munition launchers

How Battlestar Galactica: Deadlock wires a carrier's squadron slots and a ship's munition slots to objects in the
ship prefab. Findings from the original `Assembly-CSharp.dll` and the Colonial Mercury / Mercury Mk II bundles.
Read and edited by `deadlock_tools/bays.py`, `weapons --list-bays` / `--bay-layout`, and the **Launch bays** and
**Munition launchers** layers of the GUI's Weapon layout view.

## Squadron slots

The level ship record says how many squadrons a ship carries (`FighterSlots`, then `UtilityFighterSlots`). Slots are
numbered from 0, fighter slots first. In the prefab:

| Component | What it does |
|---|---|
| `LaunchFightersAction` | One button per slot in the Fighters radial menu (or its submenus). `ShipInfo` collects every `LaunchFightersAction` under the ship in hierarchy order and gives button *i* the squadron of slot *i*. |
| `LaunchPoint` | A bay. `SlotNo` is the slot it serves; several bays may share a slot (Mercury: a launch tube and a separate docking bay). `CanLaunch` / `CanDock` say whether squadrons leave or return there, `DockRange` how close they must come, `LaunchCompatibility` which squadron types (SquadronShipType, the SquadronDataTable id) it accepts, and `LaunchPoints` the launch path as local offsets. `iconPosition` points at the build-screen icon marker. |

A squadron launches from a `LaunchPoint` with its slot number that can launch it; the game falls back to any
compatible bay. The button count must match the level total (see [bundle-level-agreement.md](bundle-level-agreement.md)).

Fighters submenus (`--squadron-groups`) only split the buttons into pages, in slot order. They can have any number of
groups, and they say nothing about which slots are fighters or supports; that is decided by the level counts.

Removing slot *k* (`removeSlots`) removes every bay with `SlotNo` *k* and its icon marker, lowers the `SlotNo` of the
bays above it by one, and removes the *k*-th launch button. The level counts must then drop by one: `FighterSlots` if
*k* < `FighterSlots`, otherwise `UtilityFighterSlots`. The GUI stages that level edit; the CLI leaves it to you.

## Munition slots

Each munition slot is a `FireMissileAction` button in the Munitions radial menu. Its `Slot` is its index among
`GetComponentsInChildren<FireMissileAction>()`, so hierarchy order decides the numbering. A radial holds 8 positions:
7 munition buttons and Back. `--munition-slots` adds a "Munitions (continued)" submenu page (and further chained
pages) when more are needed. There is no option to group munitions into chosen submenus.

Removing a munition button (`removeMunitions`) also removes the submenu button of a page it leaves empty. Lower the
level `MunitionSlots` to match (the GUI does).

## Munition launchers

`MissileLauncher` components are where munitions leave the ship (Mercury Mk II: `Weapons/MissileLauncherFore`,
`MissileLauncherPort`, `MissileLauncherStarboard`). Each has `LaunchPoints`, local offsets. `FireMissileAction`
collects `GetComponentsInChildren<MissileLauncher>()` from the ship root and logs an error, leaving the button dead,
when there are none. So a ship with munition slots must keep at least one launcher; `bays.apply` refuses otherwise.

## Realigning cloned bays

`--fighter-slots` / `--utility-slots` clone a launch bay for every added slot and shift each clone by `--bay-offset`
(default 0, 3, 4: up and forward). On the Mercury Mk II that stacked the clones upwards out of the flight pod (the pod
spans y −14 to −2 and z −49 to 37 in the bays' frame) and, after several rounds of edits, left 9 of the 12 rear bays
on the port side, four pairs of them on the same spot.

`weapons --realign-bays` (GUI: **Realign launch bays** in the Weapon layout view) rebuilds that group:

* The templates are the launch+dock bays without `_slot` in the name (Mercury: `PortRear` / `StarboardRear`). Every
  launch bay whose name starts with a template name takes part.
* Sorted by slot, consecutive slots pair up: the lower slot on the port side, the higher one mirrored to starboard.
* Pair *k* goes to a grid starting at the stock bay: column *k* mod *c* steps 4 units along the ship, row *k* div *c*
  steps 3 units up, with one row for up to three pairs and two rows above that (Mk II: z −39.29 / −35.29 / −31.29 at
  y −7.09 and −4.09, all inside the pod).
* Each bay takes its side's template rotation, launch path (`LaunchPoints`) and icon offset. The build-screen icon
  marker (`Icon`) is a child of its bay, so it moves with it.
* Bays are renamed after their side and slot (`StarboardRear_slot7`); the stock bays keep their names. The game
  finds bays by component and slot number, not by name.

Mercury Mk II result (2026-11-24): slots 2–13 as six mirrored pairs, 8 bays per side, closest moved pair 3.0 units
apart; vipers bays and the fore docking bays unchanged.

## The `--bay-layout` spec

```json
{
  "revision": "sha256 of the bundle data, from --list-bays' source or the GUI (optional)",
  "bays": [{"id": "...", "name": "PortRear_slot4", "position": {"x": 0, "y": 0, "z": 0},
            "rotation": {"x": 0, "y": 1, "z": 0, "w": 0}, "launchPath": [{"x": -1, "y": 0, "z": -3}],
            "icon": {"x": 0.5, "y": 4.28, "z": 5.0}, "slot": 2, "canLaunch": true, "canDock": true,
            "dockRange": 3.0, "compat": [0, 1, 5, 113]}],
  "launchers": [{"id": "...", "position": {"x": 0, "y": 0, "z": 0}, "launchPoints": [{"x": 0, "y": 0, "z": 1}]}],
  "removeSlots": [13],
  "removeMunitions": ["FireMissileAction id"],
  "removeLaunchers": ["MissileLauncher id"]
}
```

Ids are path ids printed by `weapons --list-bays`. Every key is optional. Positions are local to the object's parent.
Edits run before removals, so `removeSlots` uses the slot numbers after any `slot` edits. The spec is applied after
the other `weapons` options in the same run; removing slots while `--fighter-slots` / `--munition-slots` add slots in
the same run is refused. The result is re-read and checked for references to removed objects before it is written.
