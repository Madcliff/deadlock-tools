# Deadlock Tools 0.3.2

This release improves project file handling and the ship bundle editor.

## Changes

- The work folder is now the single continuing edit location and mirrors the game's `BSG_Data/` layout. Level files, the edited DLL, and ship bundles are staged under their corresponding game directories.
- The editor settings distinguish the source folders for level data and ship bundles, the source DLL, and the work folder. A work copy takes priority over its source; loading a bundle imports it into the work tree.
- Removed the separate output folder from editor settings. Legacy `out/` edits are migrated into the matching work-tree paths without replacing files already there.
- The ship editor dynamically lists bundles from the selected ship-bundle folder and can include imported work copies. Bundle resolution uses the selected source and work tree rather than assumed local folder names.
- Ship system controls now show values read from the loaded bundle, including hull regeneration, armour regeneration, munition factory, and squadron revive.
- Updated the workflow, command-line reference, and technical findings to reflect current file handling and editor behavior.

The project is licensed under the GNU General Public License, version 2 only (`GPL-2.0-only`).
