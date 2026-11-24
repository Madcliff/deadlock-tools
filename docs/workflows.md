# Editing and installing a mod

## Choose the file that owns the change

| Change | Ship database and level files | Ship prefab bundle | Game DLL |
|---|---|---|---|
| Armour per facing, build cost, fleet points, speed, or ship veterancy | Edit the ship record | | |
| More fighter, support, or munition slots | Set the final slot counts | Add matching launch controls and, where applicable, bays | Optional munition-load fix for existing saves |
| Clone or resize turrets | | Edit the mounts | |
| Micro-FTL, Predictive Augury, or stealth | | Add the ability and its battle-menu control | |
| Hull regeneration | | Add the Hull Resin module | |
| Armour regeneration | | Add the custom armour repair module | Custom-module DLL (rebuilt automatically on save) |
| Rebuild destroyed squadrons | | Add the custom squadron replacement module | Custom-module DLL (rebuilt automatically on save) |
| Increase commander ranks | Extend the rank and XP tables | | Raise the rank cap and configure command nodes as needed |

The ship editor's **Hull & armour** fields come from the level ship database, while **Bundle hull & armour** comes from the ship prefab. Corresponding hull and armour values sync 1:1. If they disagree, the editor offers **Use level data as source** and **Use bundle as source**; choose which file has the values you want to keep. Click **Apply** in the top bar to save the correction to the work tree. **Armour regen** under **Ship systems** is a separate passive behaviour on the prefab.

## Edit a ship in the browser

1. Run `python -m deadlock_tools gui` from the project folder. In **Settings**, choose the default level-data folder, ship-bundle folder, DLL file, and work folder. Click **Save and load**.
2. The work folder mirrors the game's `BSG_Data/` tree. A same-named file there takes priority over the configured source. Ship database changes are saved in the work tree's `BSG_Data/` root.
3. Check **Bundle file** when editing a prefab. The editor selects a matching work copy first, then a matching file in the configured bundle folder. Use **Load bundle…** to import a chosen file into the work tree. **Inspect current bundle** displays mounts, launch action counts, and available module settings.
4. Set the relevant bundle fields on the ship page and click **Save bundle edits**. The edited prefab goes to `BSG_Data/StreamingAssets/` in the work folder. **Overwrite opened bundle** replaces that work copy after confirmation and keeps a backup beside it.
5. Check the status and output log after each save. **Database** groups the other table tools by subject. Its write forms display current values from staged files where available, and **Start from the originals** previews source values.

## Copy and add a ship

1. On the **Ships** page, choose **Copy and add a ship…**.
2. Select a donor ship and its bundle, then enter a unique database name, in-game display name, new bundle filename, and blueprint text. Choose the capital-ship or battlestar research list.
3. Create the ship. The editor copies the donor's full ship record into each ship table, allocates a new ID, adds the ship to campaign research, registers it in `FleetModelFactory`, `ProjectionFactory`, `ShipImageDataTable`, and the battle `ShipFactory`, and adds the ID to the working DLL's `ShipType` enum. The enum is needed for fleet-placement collision checks; `ShipFactory` maps the type to its battle bundle so it can spawn in combat. It creates fresh localization entries and copies the bundle into `work/BSG_Data/StreamingAssets/` using a lowercase filename for case-sensitive game installs. It also renames the bundle internal AssetBundle name, prefab container path, and referenced prefab root to match that key, which the game uses for `LoadAsset<GameObject>`. It sets `ShipInfo.ShipType` to the new database ID so the in-game ship record identifies the clone rather than its donor. The copied ship record retains the donor's preview art bundle, so its research card uses the donor artwork.

## Commander trees

The **Commanders** page loads every ability definition and bonus found in the configured level data. Edit subsystem choices, each ability's bonus/display settings, and its unlock rank. The tree preview, subsystem choice editor, and ability list have separate scroll areas. **Restore loaded tree mapping** returns to the exact mapping from the loaded files. New custom abilities support flat and percentage ship-stat modifiers; saving writes the ability and bonus records, localization, and required work-tree DLL changes. When a tree has more than three choices, saving also installs the extended rank lookup even if those choices use their default ranks.
4. Select the new ship and edit its fields and copied bundle as needed. The donor preview art and its `PreviewAssetBundle` reference are retained. The clone is a starting point; check its mounts, abilities, shared assets, and game presentation before using it in a campaign.

Creation is additive and writes to the work tree. It uses the effective ship records (including prior work-tree edits), and rejects duplicate ship names and bundle filenames.

## Add munition slots to Colonial Mercury

1. Open **Carrier & munition slots** on Colonial Mercury's ship page.
2. Set the database field **MunitionSlots** to the desired total, for example `4`, and click **Apply**.
3. Set **Munition slots** under **Bundle slot controls** to the same total and click **Save bundle edits**. For fighter bays, supply both the fighter and utility totals in the bundle controls, including either value that stays the same.
4. Install both the level files and the bundle. If an existing campaign save still uses its old munition list, apply **Munition load fix** on the DLL page and reload the campaign with the patched DLL installed. The fix rebuilds lists that are shorter than the configured slot count. Review the affected ships' loadouts after loading.

The CLI example assumes the source has fewer than four slots. Check both current counts before choosing the new total.

```bash
python -m deadlock_tools shipdb set "Colonial Mercury" MunitionSlots 4
python -m deadlock_tools weapons colonialmercury --munition-slots 4
# Optional for an existing save
python -m deadlock_tools dll patch --munition-load
```

## Diagnose crowded or mismatched carrier controls

Use **Inspect current bundle** to compare the number of launch actions and the numbered bay markers with the ship database totals. Slot numbering starts at zero. Fighter slots come first, followed by utility slots.

- **Align launch bays** updates the bay compatibility lists to match the fighter and utility totals in the bundle controls. Use the same totals as the database.
- **Bay icon** sets the marker coordinates relative to its launch bay. Enter one `SLOT=X,Y,Z` line per marker. This also works on bundles with extra bays already installed. For example, `4=12.5,4.28,8.99` moves the marker for slot 4. Use the inspector and a game preview to tune the spacing.
- **Split squadrons** groups the battle launch buttons. **Ability submenu** groups the added Actions-menu abilities. These organise the battle radial menus.

## Armour regeneration and squadron replacement

Use **Custom armour repair** and **Custom squadron replacement** under **Custom ship modules**. The old sign-flag armour
regeneration and Munition Factory squadron revive were removed. Saving a bundle that still carries those old flags
clears them once the replacement module is installed, and `dll revert --armour --revive` removes the old patches from a
DLL made by an older version.

## Add support abilities

Select a ship, choose its bundle, and enable the desired controls under **Abilities**. Choose donor bundles from the live list for the configured ship-bundle folder; no fallback folders or donor filenames are assumed. Expand an ability to see its settings. For an existing module, open that bundle and enter only the values to change. **Inspect current bundle** shows which modules are present and their stored settings.

| Ability | Donor | Recipient requirements |
|---|---|---|
| Turnaround repair bay | Defender | A docking bay and a free position in the Fighters menu. The tool makes a docking-only bay available to the supported fighters. |
| Targeted resupply | Celestra | A position for the Restock Munitions submenu. Both stock restock controls are transferred. |
| Deploy plating | Celestra | A matching shared asset reference for the plating drone. The installed DLL permits the launching ship in the target filter if it meets the standard armour and subsystem checks. Verify self-targeting through the battle UI. |
| Technical support | Defender | A Raptor-compatible launch bay and a Raptor available in battle. |
| Marine reinforcement | Defender | A Raptor-compatible launch bay and a Raptor available in battle. |

Use **Ability submenu** when adding several Actions-menu abilities to a carrier. Turnaround repair stays in the Fighters menu. Save the bundle, install it, and test each action in a skirmish with a suitable target or squadron.

Experimental EMP queues when activated and emits its pulse at the start of the next turn; configure the extra tick delay and fighter stun duration in the ship editor. The pulse immediately destroys in-range enemy missiles. Instant Plating queues at activation and applies at turn start; its optional tick delay is configured independently. Activate either queued action again before turn start to cancel it. Once the start event fires, cancellation is no longer available. A delay of 0 means the effect occurs at the turn boundary. There are 10 simulation ticks per second and 120 ticks per turn.

Targeted EMP uses the native PCM 3D point selector separately for every burst. Its **Map points per cooldown** field defaults to four: choose a point, adjust its height, then repeat for the second, third, and fourth points. Every point has an independent position and height; the salvo queues only after all configured points are selected. Cancelling a point selection discards the incomplete salvo. At turn start, each selected point gets its own EMP burst using the configured delay. **Targeting range** controls how far away the points can be, and **EMP blast radius** controls the area around each point. The salvo shares one cooldown, which begins when it detonates. The ship editor exposes these values alongside subsystem damage, recovery, fighter stun duration, and fighter evade debuff duration. The evade debuff uses the game's normal jamming flag and configured evade penalty. You can add it to a bundle with `--custom-targeted-emp`. Both EMP modules query capital ships as well as smaller ships, so their subsystem effect can reach battleships.

```bash
python -m deadlock_tools weapons colonialmercury --turnaround-repair-bay --targeted-resupply --deploy-plating --technical-support --marine-reinforcement --custom-emp-pulse --ability-submenu
```

### Tune Mercury's plating

In **Bundle file**, select your installed Mercury bundle and use **Inspect current bundle** to check its values. Under **Abilities**, enter the new **Plating supply**, **Plating drone load**, **Plating facing cap**, or **Plating rate**, then save the bundle. Blank fields keep their current values. If the source bundle already has the ability, the settings can be edited directly. For a new module, also enable **Deploy plating**.

## Install, verify, and roll back

1. Close the game and back up the destination files and your save.
2. Copy the files for the mod using the mapping below. Keep the game's relative layout and filenames.

| Staged files | Game destination |
|---|---|
| Work folder `BSG_Data/*` | Corresponding files in the game's `BSG_Data/` tree |
| Work folder `BSG_Data/Managed/Assembly-CSharp.dll` | Game's `BSG_Data/Managed/Assembly-CSharp.dll` |
| Work folder `BSG_Data/StreamingAssets/*` | Corresponding installed ship bundles |
| Work folder `.deadlock-tools/` | Editor staging and conversion scratch; do not merge into the game |

3. Start the game and test the feature where it is used. Use a new campaign for changes to research unlocks. Test added mounts, slots, and abilities in battle.
4. Restore the backed-up destination files to roll back. Work copies remain the editor's defaults for the next session.

### If a change is missing in game

| Symptom | Check |
|---|---|
| Stats look unchanged | Confirm that the staged level files were installed where the game reads them and that the expected ship appears in `shipdb show`. |
| Battle shows fewer launch options than the database | Compare the database totals to the launch actions in **Inspect current bundle**. Verify that the edited encrypted bundle is installed. |
| Armour repair or squadron replacement is missing | Check that the custom module is listed under **Installed abilities**, and that the custom-module DLL from the work tree is installed in the game. |
| A bundle tool cannot find a mount, bay, or ability component | Inspect the selected prefab and read the tool's error. Some operations require specific components, geometry, or available radial-menu positions. |
| A later prefab edit dropped earlier changes | Rebuild from the chosen source with the complete option set. |
