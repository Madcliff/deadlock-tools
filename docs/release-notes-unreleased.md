# Unreleased changes

## Editor
- **Copied ships stay addable to fleets after DLL rebuilds.** The custom-module rebuild and fresh DLL patches start from a clean DLL, which dropped the ShipType enum members added for copied ships (adding Colonial Mercury MK2 to a Skirmish fleet threw `KeyNotFoundException`). They now carry those members over from the DLL they replace and add `CustomShip<id>` for any ship-table ID still missing one.

## Removed
- Legacy armour regeneration (`weapons --armour-regen`, `dll patch --armour-factor`) and squadron revive (`weapons --squadron-revive`, `dll patch --revive-turns`). Use the custom armour repair and squadron replacement modules. Their check and revert code is gone too: rebuild from an unpatched DLL to drop them. Saving a bundle with a replacement module clears the old prefab flags.

## Tools
- `scripts/add_ship_type_enum.sh` takes several `ID=NAME` pairs (the old `SHIP_ID ENUM_NAME` form still works). New `ship_types` module lists the ShipType enum without Mono.
