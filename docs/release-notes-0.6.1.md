# 0.6.1

## Fixes
- **Skirmish setup lists empty ("Option A").** The 0.6.0 custom-module DLL's fleet-limit code called `string.Split(char, StringSplitOptions)`, which the game's .NET runtime does not have. Reading the fleet caps and ship counts threw `MissingMethodException`, so the Fleet Points and Match Type lists never filled. The per-jammer jamming file (`deadlock-jamming.txt`) failed to load the same way. Rebuild the custom-module DLL (Save ship on a ship with custom modules) and install it.
- The custom modules are now compiled against the game's own `mscorlib`/`System` from its `Managed` folder instead of the system Mono's, so a library call the game lacks fails the build instead of the game.

## Editor
- **Change all turrets of one weapon type to another** in the Weapon layout view (“Change all … to … in …”, with counts per type), and `weapons --swap-weapon [WHERE:]OLD=NEW` on the command line.

## Tools
- `trench.reset_mounts(..., parts=("position",), remove_extra=False)` resets only some mount properties, e.g. positions while keeping weapon types.

## Docs
- Squadron groups: why a carrier's fighters take the same orders (the game's auto-assign option and `DefaultGroup`), and the in-battle Assign Group menu. See [game/squadrons.md](game/squadrons.md#squadron-groups-why-squadrons-take-the-same-orders).
