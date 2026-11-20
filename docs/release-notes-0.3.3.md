# Deadlock Tools 0.3.3

The 0.3.2 GitHub release was built from an earlier commit and did not include the subsequent ship editor and work-folder updates. This release aligns the published version with the current source.

## Changes

- Make the configured work folder the continuing edit location, mirroring the game's `BSG_Data/` hierarchy for level files, ship bundles, and the edited DLL.
- Remove the separate output folder from editor settings, migrate existing staged edits, and use work copies as the default when reopening a project.
- Load and compare ship bundles directly, populate bundle choices from configured sources, and display the loaded bundle's ship-system values.
- Update workflows, command-line reference, and technical findings for the current editor and file handling.
- Refresh the Windows release build and smoke check for the current `work` setting.

The project is licensed under the GNU General Public License, version 2 only (`GPL-2.0-only`).
