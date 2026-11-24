# Values the level data and the ship bundle must agree on

A ship exists twice in the game files. Its **level ship record** sits in the `ShipDataTable` of the level files
(`BSG_Data/level*`). Its **prefab** sits in the encrypted ship bundle (`BSG_Data/StreamingAssets/<ship>`). Several
values are stored in both places. The game reads some from one place and some from the other, so they must be equal or
the ship behaves inconsistently.

| Value | Level record field | Bundle (prefab) | What breaks when they differ |
|---|---|---|---|
| Hull | `MaxHullPoints` | `ShipInfo.MaxHullPoints` | Displayed hull and the hull used in battle disagree |
| Armour, per facing | `ArmorFront/Rear/Left/Right/Top/Bottom` | `ArmourInfo.defaultArmourFront/Rear/Left/Right/Top/Bottom` | Same as hull, per facing |
| Munition slots | `MunitionSlots` | number of `FireMissileAction` components (battle munition buttons) | Loadout screen and battle radial menu show different slot counts. Existing saves also need the DLL munition-load fix |
| Fighter + support slots | `FighterSlots` + `UtilityFighterSlots` | number of `LaunchFightersAction` components, grouped under the Fighters menu's sub-menus (`squadron_groups`) | Squadrons that cannot be launched, or launch buttons with no squadron behind them |

The relationship is 1:1. A modded Mercury once had prefab values exactly 4× its level values (800 hull vs 200). That was
a mismatch in the mod, not a game conversion factor (see technical-findings.md).

## How the editor handles it

The Ships tab shows a **Level data ↔ bundle** table under the bundle file card. For each row it lists the level
value, the bundle value (or the value Save ship will build, marked *on save*) and ✓/✗. **Use level** and **Use bundle**
copy one side to the other. While *Keep hull & armour linked while editing* is on, editing hull or armour on either side
updates the other. Editing a level slot count fills in the matching bundle slot control, so Save ship rebuilds the
prefab's buttons to match.

Launch buttons are counted across all Fighters sub-menus. When the bundle has exactly two groups, **Use bundle** splits
them into fighter and support counts. Otherwise it keeps the support count and gives the rest to fighters.
