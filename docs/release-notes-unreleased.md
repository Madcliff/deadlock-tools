# Unreleased changes

## Editor
- **Realign launch bays** (Weapon layout view) and `weapons --realign-bays`: cloned launch bays become mirrored port/starboard pairs in slot order, in a grid beside the stock rear bays, renamed after their side and slot. Bays can also be renamed by hand in the Launch bays layer. See [game/launch-bays.md](game/launch-bays.md).

## Tools
- `trench.reset_mounts(src, donor, dst)` puts weapon mounts back to a donor bundle (e.g. the vanilla Mercury) and removes clones; on a scaled hull the turrets stay stock size (Python API).
- `xor decrypt -o DIR` writes to `DIR/decrypted` (it used to ignore `-o` and always write to the work folder).
- The regression tests now run against the vanilla game files (GOG 1.5.113, identical to Steam's). The copies used before carried the ValkyrieInCampaign mod and munition edits. All 60 tests run; none skip.
- `--bay-layout` bay entries also take `name`, `rotation`, `launchPath` and `icon`.
