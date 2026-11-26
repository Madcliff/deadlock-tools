# Unreleased changes

## Editor
- **Change all turrets of one weapon type to another** in the Weapon layout view (“Change all … to … in …”, with counts per type), and `weapons --swap-weapon [WHERE:]OLD=NEW` on the command line.

## Tools
- `trench.reset_mounts(..., parts=("position",), remove_extra=False)` resets only some mount properties, e.g. positions while keeping weapon types.
