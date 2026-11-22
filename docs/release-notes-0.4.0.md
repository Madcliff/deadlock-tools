# 0.4.0

## Added

- Added `ui fix-overflows` to resize construction, research, and battle slot displays, and wrap expanded ship-card slot icons into rows. The command stages Unity asset edits in the configured work tree.
- Added `--turnaround-repair-actions N` to configure 1–8 independent turnaround repair bays. The bundle writer creates one repair module and Fighters action per bay and reconciles existing bundles with mismatched counts.
- Added indexed shortcut IDs, matching key mappings, and a DLL candidate that binds each action to its own repair module and clones the native launch-slot widget per bay.
- Changed Targeted EMP to use one 3D point selection per activation, with configurable charges that refresh each turn; queued points fire as distinct, spaced bursts at turn start.
- Fixed additional repair-slot placement under Unity layout groups by positioning each clone explicitly and reserving row width; rebuilt and installed the updated custom-module DLL in the work tree and GOG game folder.
- Bundle saves in the editor now rebuild custom-module DLL support and repair key mappings into the work tree as one coordinated update. Existing vanilla DLL settings are carried forward, and replaced work files are backed up before promotion.
- Ability grouping now renames and reuses an existing ability-named submenu as `Abilities`, avoiding a duplicate submenu on edited bundles.
- Rebuilt `dist/deadlock.pyz` so the combined editor includes the coordinated update flow.
- Included optional Unity asset editing dependencies in the Windows standalone build.
- Added a Commanders editor for reallocating built-in abilities across subsystem trees, adding/removing tree choices, and editing effect bonuses, display styles, flagship applicability, and localisation IDs. Saves go to the level-data work tree.
- Added a live tree preview that shows the Commander root, all seven subsystem branches, and their assigned ability nodes while editing.
- Added separate scroll areas for the commander preview, subsystem choices, and complete ability/bonus list. The editor reads every ability and bonus record present in the selected level data.
- Added per-ability unlock ranks, persisted with the work tree and applied through an ability-ID-based DLL lookup. The Command-column node schedule remains a separate setting.
- Fixed the commander tree reset action to restore the exact loaded tree mapping, including nonuniform branch sizes.
- Added custom commander abilities as configurable flat or percentage modifiers for supported native ship stats. Saving writes ability/bonus records and fallback-localized strings, then builds the required stat hook into the DLL work copy.
- Added ship copying with the work-folder ship data and bundle as the default donor source, so edited stats and abilities carry into the new ship. The configured base folder remains selectable.
- Corrected linked level/prefab hull and armour edits to use 1:1 values. The editor identifies differing fields and offers either data source for reconciliation.
- Rebuilt the combined `deadlock.pyz` editor with the current ship, commander, bundle, DLL, and UI tools.
- Added both the standalone Windows x64 ZIP and rebuilt `deadlock.pyz` as release downloads.

## Notes

- The UI asset changes should be visually checked in game after installation.
- See [technical findings](technical-findings.md) for the inspected layout components and limits.
