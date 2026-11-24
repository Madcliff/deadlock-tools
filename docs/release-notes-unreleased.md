# Unreleased changes

## Editor
- **One save per tab.** On the Ships tab, **Save ship** (top bar) writes level edits, bundle prefab fields, bundle options and weapon-layout changes in one go. It replaces Apply, Save bundle edits, Overwrite opened bundle and Save weapon layout. Overwriting the opened bundle is now a checkbox under **Saving the bundle**. Save ship and Discard changes appear only on the Ships tab.
- **Level data ↔ bundle table.** Hull, the six armour facings, munition slots and fighter + support launch buttons are listed with both values, ✓/✗, and per-row **Use level** / **Use bundle** buttons. It replaces the old link hint, the repair buttons and the launch-count warning.
- **Commander features moved to the Commanders tab.** Add ranks, the DLL rank cap, command-tree nodes, the rank/XP table and the specialisation-list resets now sit together in **Ranks & command tree** and the subsystem editor. They used to be split across the DLL and Database tabs. Saving adds ranks and patches the DLL cap together, and the card warns when the two differ.
- New [docs/gui-map.md](gui-map.md) maps every tab and control to its endpoint and CLI tool.

- **Copied ships stay addable to fleets after DLL rebuilds.** The custom-module rebuild and fresh DLL patches start from a clean DLL, which dropped the ShipType enum members added for copied ships (adding Colonial Mercury MK2 to a Skirmish fleet threw `KeyNotFoundException`). They now carry those members over from the DLL they replace and add `CustomShip<id>` for any ship-table ID still missing one.

## Removed
- Legacy armour regeneration (`weapons --armour-regen`, `dll patch --armour-factor`) and squadron revive (`weapons --squadron-revive`, `dll patch --revive-turns`). Use the custom armour repair and squadron replacement modules. Their check and revert code is gone too: rebuild from an unpatched DLL to drop them. Saving a bundle with a replacement module clears the old prefab flags.

## Tools
- `scripts/add_ship_type_enum.sh` takes several `ID=NAME` pairs (the old `SHIP_ID ENUM_NAME` form still works). New `ship_types` module lists the ShipType enum without Mono.
- `bundle_ship_stats` reports `munition_actions` and `launch_actions` (prefab button counts).
