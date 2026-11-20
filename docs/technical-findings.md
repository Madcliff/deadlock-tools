# Deadlock modding findings

These findings describe the Unity 2018.4 Deadlock files inspected during development. Check the exact build and selected files before applying a patch. Test a candidate mod in a local skirmish before using it in a campaign.

## What each file controls

| Source | Data observed | How to inspect |
|---|---|---|
| `BSG_Data/level*` | Ship records, build costs, slot totals, research, fleet limits, and commander tables. Multiple scenes carry copies. | `shipdb show`, `shipdb fleetinfo`, `shipdb commanders`, and the Database page |
| `sharedassets9.assets` | Squadron hull pools, fighter counts, and fighter-loss thresholds in the inspected files. | `shipdb squadrons` |
| Ship bundle | Unity prefab components, weapon mounts, attached ship systems, launch actions, bay positions, and battle menus. | `weapons BUNDLE --inspect` |
| `Assembly-CSharp.dll` | Rules that interpret prefab settings, battle actions, and campaign saves. The database tool also reads managed metadata to decode level tables. | `dll check` |
| `battle_shared` | Shared assets referenced by ship prefabs, including the Deploy Plating drone. | Inspect the serialized CAB object and the ship bundle's external references and preload table |

The modded Mercury provides a concrete example. Its inspected level records had 200 hull and armour values of 180 front, 100 rear, 140 port and starboard, 100 top, and 120 bottom. The installed modified prefab had 800 hull and armour values of 720, 400, 560, 560, 400, and 480 respectively. These are observations from different files. Confirm the active values in the particular game mode you are testing.

## Managed DLL patches

| Patch | What it changes | Related data |
|---|---|---|
| Armour regeneration | Adds armour restoration scaled by the hull repair rate and configured factor. | The bundle marks Hull Resin for armour regeneration using the sign of its serialized repair rate. |
| Squadron revive | Lets a flagged ship's Munition Factory rebuild destroyed squadrons after the configured delay. | The recipient needs the factory module and the revive flag. |
| Munition load | Extends the load-time check so a saved munition list shorter than the configured slot total is rebuilt. | Level records set the slot total, and the prefab supplies battle launch actions. Review existing loadouts after loading. |
| Commander rank cap | Changes the maximum rank used by the game. | Extend the commander rank and XP tables separately. |
| Command nodes | Adjusts the hard-coded command-node count and rank spacing. | The command-tree display and commander data should be tested together. |

The DLL tool checks expected byte patterns or offsets before editing. Unsupported builds can fail even when the managed class names match. Staged patches build on the output DLL by default. Use `dll check PATH` to inspect the installed DLL specifically. The ship editor can prompt for the two DLL patches required by prefab armour regeneration and squadron revive.

## Bundle editing and dependencies

The tool reads the game's XOR-wrapped UnityFS bundles and the serialized type trees inside them. An ability transfer can require several pieces: a serialized module, its action component, a free menu position, ownership links back to the correct GameObjects, and external assets. A copied reference may exist in the file and still be unresolved in Unity if the prefab's preload metadata omits its dependencies.

### Deploy Plating

- Stock donor values observed in Celestra were 400 total supply, 40 per drone load, 50 maximum plating, application rate 0.2, and deployment range 750.
- Mercury's battle log recorded accepted Deploy Plating commands followed by a null argument to Unity's drone prefab instantiation. The object existed in the installed `battle_shared` CAB. The initial transfer had omitted its external preload entries.
- The repaired transfer carries the donor's matching external preload dependencies and updates the prefab's preload window. A comparison of the repaired Mercury verified the shared object references and preserved the other prefab objects and resource bytes.
- The inspected DLL's initial plating target filter permits the owning ship if it satisfies the usual eligibility checks. That static finding does not establish that the complete selection and drone sequence succeeds in battle.

### Carrier slots and display

The database supplies fighter and utility counts. The prefab contains the actual launch actions, bay compatibility, and optional icon transforms. Keep these in agreement. Slot IDs start at zero, with fighter slots before utility slots. The inspected installed Mercury had five fighter slots and one utility slot, but some fighter positions pointed to support-only bay compatibility lists. The staged correction aligned the lists while retaining a Raptor-compatible utility bay and moved the two extra icon markers. The build and deployment screens still need visual verification for a given configuration.

The installed Mercury bundle can also have a full Fighters radial menu. Adding the turnaround repair action alone then fails for lack of a free position. A staged rebuild using the level record's 5 fighter and 1 support slots, grouped into separate 5/1 submenus, created six launch actions and bays and added the repair action successfully. This is a prefab-level validation; confirm icon spacing and the campaign build/deployment presentation in game.

### Creating a new ship

The existing `shipdb addblueprint` command only adds research entries for a ship record that already exists. A complete new ship needs a distinct record and ID in every level table that contains `ShipDataTable`, matching campaign unlock entries, a ship bundle prefab, and localisation keys. The bundle also needs valid mount, slot, ability, and shared-asset references. Copying a bundle alone does not register a buildable ship. The current database writer edits existing records and does not yet insert a new record, so do not use `addblueprint` as a ship-cloning operation.

### Localisation and in-game screen layout

The installed game includes Unity's Localization module, but the current toolkit has not identified or decoded the game's active string-table/catalogue assets. Ship `DescriptionHeaderID` values are references, not editable text. Localisation editing should wait until the active tables, key format, and asset dependencies are confirmed, then write staged copies with round-trip checks.

The known Mercury build and deployment display issue is tied to the relationship between level slot counts and bundle launch actions, bay compatibility, and icon positions. The tool can align those bundle structures, but this repository has no validated patch for the build, deployment, or research screen layout itself. Verify screen changes in the running game before treating them as fixed; database values and serialized prefab checks alone cannot confirm the UI presentation.

### Donor abilities and menu capacity

The donor-based options use local game assets. Defender supplies turnaround repair, technical support, and reinforcement. Celestra supplies targeted munition resupply and plating. Turnaround repair needs a suitable docking bay. The Defender's Raptor-related actions need an appropriate bay and squadron. Resupply has two stock buttons in its submenu. The tool uses the existing Abilities submenu where available and checks for a free position. Serialized component checks establish that links are present in the output, while skirmish testing establishes whether the game executes the action.

See [Editing and installing a mod](workflows.md) for procedures and [Command-line reference](cli-reference.md) for commands.
