# Unreleased changes

## Editor
- **Module subsystems** card and `weapons --module-subsystem MODULE:SOURCE:MIN:MAX`: choose which subsystems drive each module the toolkit adds (Hull Resin, Munition Factory, custom armour repair, drone resupply, squadron replacement, magazine capacity). Each subsystem adds its own % from 0 to full health; the total replaces the stock driver (Engineering for Hull Resin, Armoury for the Munition Factory) or multiplies the custom modules' rate. Needs the rebuilt custom-module DLL.
- The crew-veterancy card is renamed **Crew bonuses from subsystem health**, so it is not confused with the subsystem values.
- **Realign launch bays** (Weapon layout view) and `weapons --realign-bays`: cloned launch bays become mirrored port/starboard pairs in slot order, in a grid beside the stock rear bays, renamed after their side and slot. Bays can also be renamed by hand in the Launch bays layer. See [game/launch-bays.md](game/launch-bays.md).

## Tools
- **Backups no longer go into the work folder.** Replaced files are copied to a new `backups` setting (Settings tab, `config --backups`), by default `work-backups/` beside the work folder. This covers the ship-clone, commander, squadron, bay, bundle-import, bundle-overwrite (no more `.bak` beside the file) and custom-module DLL saves.
- `trench.reset_mounts(src, donor, dst)` puts weapon mounts back to a donor bundle (e.g. the vanilla Mercury) and removes clones; on a scaled hull the turrets stay stock size (Python API).
- `xor decrypt -o DIR` writes to `DIR/decrypted` (it used to ignore `-o` and always write to the work folder).
- The regression tests now run against the vanilla game files (GOG 1.5.113, identical to Steam's). The copies used before carried the ValkyrieInCampaign mod and munition edits. All 60 tests run; none skip.
- `--bay-layout` bay entries also take `name`, `rotation`, `launchPath` and `icon`.
