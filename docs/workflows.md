# Editing and installing a mod

## Choose the file that owns the change

| Change | Ship database and level files | Ship prefab bundle | Game DLL |
|---|---|---|---|
| Armour per facing, build cost, fleet points, speed, or ship veterancy | Edit the ship record | | |
| More fighter, support, or munition slots | Set the final slot counts | Add matching launch controls and, where applicable, bays | Optional munition-load fix for existing saves |
| Clone or resize turrets | | Edit the mounts | |
| Micro-FTL, Predictive Augury, or stealth | | Add the ability and its battle-menu control | |
| Hull regeneration | | Add the Hull Resin module | |
| Armour regeneration | | Add or flag Hull Resin | Apply the armour regeneration patch |
| Rebuild destroyed squadrons | | Add or use Munition Factory and enable squadron revive | Apply the squadron revive patch |
| Increase commander ranks | Extend the rank and XP tables | | Raise the rank cap and configure command nodes as needed |

The ship editor's **Hull & armour** fields update the ship database. **Armour regen** under **Ship systems** enables a separate passive behaviour on the prefab. The bundle's **Inspect current bundle** command reports stored prefab settings. Use the ship fields for current database values and the DLL page for active patch settings.

## Edit a ship in the browser

1. Run `python -m deadlock_tools gui` from the project folder. In **Settings**, choose the level data folder, game DLL, and optionally the ship bundle folder. Click **Save and load**.
2. Select a ship. Edit its stats, research entries, or veterancy and click **Apply**. Ship database changes are staged in `out/levels/`.
3. Check **Bundle file** when editing a prefab. The editor selects a matching file by name when it finds one. Use **Browse** to choose another file. **Inspect current bundle** displays mounts, launch action counts, and available module settings.
4. Set the relevant bundle fields on the ship page and click **Save bundle edits**. The edited prefab normally goes to `out/bundles/` under its game filename. The selected bundle is the input for each run, so keep all desired prefab options together when rebuilding from the same input.
5. Check the status and output log after each save. **Database** groups the other table tools by subject. Its write forms display current values from staged files where available, and **Start from the originals** previews source values.

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

## Enable passive armour regeneration

1. Select the ship and inspect its bundle. Under **Ship systems**, enable **Armour regen**. If the prefab needs a Hull Resin module, supply a positive **Hull regen** value, for example `0.2`. Leave **Hull regen** blank when flagging an existing module.
2. Click **Save bundle edits**. If the required DLL patch is missing, enter an armour regeneration factor at the prompt. This factor multiplies the hull repair rate for armour restoration.
3. Check the save result and install the staged bundle and, if generated, the patched DLL.

The same prompt workflow applies to **Squadron revive**. The prefab needs a Munition Factory module. Supply **Munition factory** when adding it and choose a revive delay when prompted. A value of `0` starts the revive as soon as the game can process it.

The editor checks the staged DLL first. An installed patch keeps its current value, which you can change on the **DLL** page. Cancelling the prompt stops the save. An unrecognised DLL build stops the save at the check. If a later DLL write fails after the prefab saves, the result reports the saved bundle and the DLL error.

## Add support abilities

Select a ship, choose its bundle, and enable the desired controls under **Abilities**. The tool uses the stock Defender or Celestra values for blank fields on new modules. Expand an ability to see its settings. For an existing module, open that bundle and enter only the values to change. It looks up `colonialdefender` and `colonialcelestra` in the configured bundle or decrypted work folder. Use **Defender donor** or **Celestra donor** to supply an explicit path. **Inspect current bundle** shows which modules are present and their stored settings.

| Ability | Donor | Recipient requirements |
|---|---|---|
| Turnaround repair bay | Defender | A docking bay and a free position in the Fighters menu. The tool makes a docking-only bay available to the supported fighters. |
| Targeted resupply | Celestra | A position for the Restock Munitions submenu. Both stock restock controls are transferred. |
| Deploy plating | Celestra | A matching shared asset reference for the plating drone. The installed DLL permits the launching ship in the target filter if it meets the standard armour and subsystem checks. Verify self-targeting through the battle UI. |
| Technical support | Defender | A Raptor-compatible launch bay and a Raptor available in battle. |
| Marine reinforcement | Defender | A Raptor-compatible launch bay and a Raptor available in battle. |

Use **Ability submenu** when adding several Actions-menu abilities to a carrier. Turnaround repair stays in the Fighters menu. Save the bundle, install it, and test each action in a skirmish with a suitable target or squadron.

```bash
python -m deadlock_tools weapons colonialmercury --turnaround-repair-bay --targeted-resupply --deploy-plating --technical-support --marine-reinforcement --ability-submenu
```

### Tune Mercury's plating

In **Bundle file**, select your installed Mercury bundle and use **Inspect current bundle** to check its values. Under **Abilities**, enter the new **Plating supply**, **Plating drone load**, **Plating facing cap**, or **Plating rate**, then save the bundle. Blank fields keep their current values. If the source bundle already has the ability, the settings can be edited directly. For a new module, also enable **Deploy plating**.

## Install, verify, and roll back

1. Close the game and back up the destination files and your save.
2. Copy the files for the mod using the mapping below. Keep the game's relative layout and filenames.

| Staged files | Game destination |
|---|---|
| `out/levels/*` | Corresponding files in `BSG_Data/` |
| `out/dll/Assembly-CSharp.dll` | `BSG_Data/Managed/Assembly-CSharp.dll` |
| Encrypted files in `out/bundles/` | The directory containing the equivalent installed ship bundles |
| `work/decrypted/*.dec` or plain `.dec` output | Scratch files for inspection or conversion |

3. Start the game and test the feature where it is used. Use a new campaign for changes to research unlocks. Test added mounts, slots, and abilities in battle.
4. Restore the backed-up destination files to roll back. To build another revision, use a separate output root or inspect the existing staged files before continuing.

### If a change is missing in game

| Symptom | Check |
|---|---|
| Stats look unchanged | Confirm that the staged level files were installed where the game reads them and that the expected ship appears in `shipdb show`. |
| Battle shows fewer launch options than the database | Compare the database totals to the launch actions in **Inspect current bundle**. Verify that the edited encrypted bundle is installed. |
| Armour regeneration or squadron revive is missing | Check the prefab module and flag, then run `dll check` against the DLL actually installed in the game. |
| A bundle tool cannot find a mount, bay, or ability component | Inspect the selected prefab and read the tool's error. Some operations require specific components, geometry, or available radial-menu positions. |
| A later prefab edit dropped earlier changes | Rebuild from the chosen source with the complete option set. |
