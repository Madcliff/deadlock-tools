# Unreleased changes

## Removed
- Legacy armour regeneration (`weapons --armour-regen`, `dll patch --armour-factor`) and squadron revive (`weapons --squadron-revive`, `dll patch --revive-turns`). Use the custom armour repair and squadron replacement modules. Their check and revert code is gone too: rebuild from an unpatched DLL to drop them. Saving a bundle with a replacement module clears the old prefab flags.
