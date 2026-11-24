# 0.5.0

## Editor
- **One save per tab.** On the Ships tab, **Save ship** (top bar) writes level edits, bundle prefab fields, bundle options and weapon-layout changes in one go. It replaces Apply, Save bundle edits, Overwrite opened bundle and Save weapon layout. Overwriting the opened bundle is now a checkbox under **Saving the bundle**. Save ship and Discard changes appear only on the Ships tab.
- **Level data ↔ bundle table.** Hull, the six armour facings, munition slots and fighter + support launch buttons are listed with both values, ✓/✗, and per-row **Use level** / **Use bundle** buttons. It replaces the old link hint, the repair buttons and the launch-count warning.
- **Commander features moved to the Commanders tab.** Add ranks, the DLL rank cap, command-tree nodes, the rank/XP table and the specialisation-list resets now sit together in **Ranks & command tree** and the subsystem editor. They used to be split across the DLL and Database tabs. Saving adds ranks and patches the DLL cap together, and the card warns when the two differ.
- **Launch bay editor** in the Weapon layout view. An **Edit** selector switches between turrets, squadron launch bays and munition launchers, all drawn in the same projection. Bays: drag or type the position, slot, can launch / can dock, dock range and squadron types (by name), with the launch path drawn. Launchers: position and launch points. The Squadron slots and Munition slots tables remove a slot with its bays and button and lower the ship's level counts to match. Everything is saved by Save ship.
- **Squadron groups no longer change the fighter / support counts.** Groups only split the launch buttons into Fighters submenus, in any number (for example 5,5,4). A total that does not match the slot count is flagged and blocks the save; **Use bundle** in the agreement table no longer reads two groups as fighters + supports.
- **Subsystem crew modifiers card** on the Ships tab: let a subsystem's health add to a crew stat (for example Hangar → squadron repair), with an effect note per row. Saved by Save ship; the custom-module DLL is rebuilt for it.
- **Armoury is listed under Ship subsystems** with the other subsystems. It used to appear among the installed abilities as "Armory munition work rate".
- New [docs/gui-map.md](gui-map.md) maps every tab and control to its endpoint and CLI tool.
- **Copied ships stay addable to fleets after DLL rebuilds.** The custom-module rebuild and fresh DLL patches start from a clean DLL, which dropped the ShipType enum members added for copied ships (adding Colonial Mercury MK2 to a Skirmish fleet threw `KeyNotFoundException`). They now carry those members over from the DLL they replace and add `CustomShip<id>` for any ship-table ID still missing one.

## Fixes
- **The DLL tab works on custom-module DLLs.** Once a ship had been saved with custom modules, `dll check` reported "not the expected build" and Patch DLL / Revert failed, because the repair-shortcut patch looked for fixed byte offsets that the custom-module build moves. The repair shortcut state is now read through the DLL's metadata; on a custom-module DLL it shows as built in, Patch DLL only stages the key mappings, and Revert leaves it in place while reverting the other patches.

## Removed
- Legacy armour regeneration (`weapons --armour-regen`, `dll patch --armour-factor`) and squadron revive (`weapons --squadron-revive`, `dll patch --revive-turns`). Use the custom armour repair and squadron replacement modules. Their check and revert code is gone too: rebuild from an unpatched DLL to drop them. Saving a bundle with a replacement module clears the old prefab flags.

## Tools
- `scripts/add_ship_type_enum.sh` takes several `ID=NAME` pairs (the old `SHIP_ID ENUM_NAME` form still works). New `ship_types` module lists the ShipType enum without Mono.
- **Support-ability donors are picked automatically.** `--turnaround-repair-bay`, `--targeted-resupply`, `--deploy-plating`, `--technical-support` and `--marine-reinforcement` use `colonialdefender` / `colonialcelestra` from the configured bundle folder (stock copy first, then the work folder). `--defender-donor` / `--celestra-donor` and the GUI donor lists now only override that.
- `weapons --bay-layout FILE.json` edits launch bays and munition launchers and removes squadron or munition slots (applied after the other options; the layout revision is checked against the input). `--list-bays` prints the ids it uses.
- `weapons --subsystem-modifier STAT:SOURCE:MIN:MAX` and `--clear-subsystem-modifiers` edit the subsystem crew modifiers; `--inspect` lists them. New modules `subsystem_modifiers` (crew modifiers), `trench` (MK2 bow-gun rows, Python API) and `core` (workspace layer).
- `bundle_ship_stats` reports `munition_actions` and `launch_actions` (prefab button counts).

## Notes
- Not yet checked in game: subsystem crew modifiers, the launch bay editor's bay and launcher edits, and the indexed repair shortcuts on a rebuilt DLL.
- Every editor tab and control is listed in [gui-map.md](gui-map.md); game findings are in [game/](game/).
