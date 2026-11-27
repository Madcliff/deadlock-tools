# Fighter squadrons

How Battlestar Galactica: Deadlock stores and uses fighter squadrons. Findings from the game files and the
original `Assembly-CSharp.dll` (IL read with `ikdasm`). Edited with `shipdb squadrons/setsquadron` and the GUI's
**Squadrons** tab (`deadlock_tools/squadrons.py`).

## Where they live

All 16 squadron prefabs are in **`BSG_Data/sharedassets9.assets`** (no other asset file has any). Each is a
GameObject named `<Faction><Type>Squadron` with these components:

| Component | What it holds |
|---|---|
| `Transform` | position (the squadron root) |
| `UniqueId` | persistent ID |
| `FighterSquadron` | fighter count, loss thresholds, formation, behaviour ranges, missile defence |
| `ShipInfo` | the shared hull pool, handling, DRADIS, flags |
| `SquadronMovementAgent` | launch speed, boarding/landing/collision ranges (not on every Cylon squadron) |
| `PropulsionInfo` | `MinVelocity` / `MaxVelocity` |
| `WeaponsTarget` | hit points per quadrant, miss points, `missSphereRadius` |
| `OrbitCameraOverrideInfo`, `Recordable`, `DRADISRangeRenderer` | camera, replay, DRADIS ring |
| `AIBrainColonialFighter` (Colonial) / Cylon brain | AI behaviour |

Five Cylon squadrons (Bomber, Heavy Raider, Raider Prototype, Scorpion, Wardriver) have 4 of the 5 editable
components. `FighterSquadron.ShipType` is the **fighter's ship-database ID** (e.g. 31 = Colonial Viper Mk VII, whose
weapons and veterancy are in the level files' ShipDataTable). `SquadronShipType` is a separate squadron enum, not a
ship ID. How many squadrons a carrier launches is the carrier's `FighterSlots` / `UtilityFighterSlots` in the ship
database.

## Fighter count and losses

- `MaxShipCount`: fighters in a full squadron. `ShipLoss`: a `float[]` of exactly `MaxShipCount - 1` hull values;
  the DLL logs `"{0} - ShipLoss/MaxShipCount mismatch"` otherwise. `FormationOffset`: one `Vector3` slot per fighter.
- **`FighterSquadron.CalculateActiveFighters`** (the rule the game uses):
  - hull ≤ 0 → 0 fighters
  - hull == `MaxHullPoints` → `MaxShipCount` (all of them)
  - otherwise → 1 + the number of `ShipLoss` values strictly below the current hull
  
  So **the order of the thresholds does not matter** (Taipan `[12, 8, 4]` and Bomber `[16, 12, 8]` are stored
  descending; Vipers and Raiders ascending), and a threshold equal to the hull is legal (Raider Mk I: 52 with a 52
  hull pool). Low thresholds mean fighters survive until the pool is nearly gone.
- **`FighterSquadron.Spawn`**: a squadron relaunched with fewer fighters gets its starting hull from `ShipLoss[n]`
  (`SetInitialHullPoints`), so the thresholds also set the hull of a partial squadron.
- The largest stock squadron has 16 fighters (Cylon Raiders); the editor caps counts at 16. Larger counts are
  untested in game.

## Stock values (unmodded `sharedassets9.assets`)

| Squadron | Fighters | Hull | Speed min–max | DRADIS | Loss thresholds |
|---|---|---|---|---|---|
| ColonialAssaultRaptor | 3 | 30 | 0.63–3.0 | 400 | 8, 16 |
| ColonialRaptor | 2 | 24 | 0.63–3.6 | 400 | 8 |
| ColonialSingleRaptor | 1 | 30 | 0.63–3.8 | 400 | – |
| ColonialSweeper | 2 | 24 | 0.63–3.4 | 400 | 8 |
| ColonialTaipan | 4 | 32 | 0.63–3.6 | 400 | 12, 8, 4 |
| ColonialViperMkII | 8 | 34 | 0.91–3.8 | 200 | 2, 4, 8, 12, 18, 24, 32 |
| ColonialViperMkI | 10 | 40 | 0.78–3.2 | 200 | 2, 4, 8, 12, 16, 20, 24, 28, 32 |
| ColonialViperMkVII | 10 | 60 | 0.91–3.8 | 200 | 2, 4, 8, 12, 18, 24, 32, 40, 48 |
| CylonBomber | 4 | 36 | 0.63–3.4 | 200 | 16, 12, 8 |
| CylonHeavyRaider | 2 | 32 | 0.63–2.6 | 200 | 12 |
| CylonModernRaider | 16 | 56 | 1.13–3.6 | 200 | 2, 4, 8, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 46, 52 |
| CylonRaiderMkI | 16 | 52 | 0.84–3.6 | 200 | same as Modern Raider |
| CylonRaiderPrototype | 12 | 48 | 0.69–3.0 | 200 | 2, 4, 8, 12, 16, 20, 24, 30, 36, 42, 48 |
| CylonScorpion | 1 | 36 | 0.78–3.0 | 400 | – |
| CylonWardriver | 2 | 24 | 0.78–3.8 | 400 | 12 |
| SagittaronViperMkI | 10 | 40 | 0.78–3.2 | 200 | same as Viper Mk I |

All have `MaxTurnRate` 1. (The work tree's Viper Mk VII had `MaxVelocity` 3.8 at the time of writing.)

## How the editor changes them

- Scalar fields (36 across `FighterSquadron`, `ShipInfo`, `PropulsionInfo`, `SquadronMovementAgent`,
  `WeaponsTarget`) are rewritten in place; the file size does not change.
- A fighter-count change rewrites `ShipLoss` and `FormationOffset` together and resizes the `FighterSquadron` object
  (`shipdb.resize_object`). New thresholds follow the old curve's shape, scaled to the hull pool; new formation slots
  continue the V outwards (±1 sideways, 0.8 back per pair).
- With "scale thresholds" on (the default, and the old `HullFactor` behaviour), changing the hull pool scales the
  thresholds by the same factor, so fighters are lost at the same fractions of the pool.
- Every save is read back and verified, and only the edited objects change: tested on all 16 squadrons (+3 fighters
  and back gives a byte-identical file).

## Squadron types and carrier allocations (`SquadronDataTable`)

Which squadron types a capital ship may carry is **not** stored on the carrier. It's in the level-data
**`SquadronDataTable`**, which has identical copies in `level5`, `level6`, `level7`, `level8`, `level85` and `level9`
(edit them all). There is one `SquadronDataTableObject` per squadron type; its `id` is the squadron enum (the same
number as the prefab's `FighterSquadron.SquadronShipType`).

| Field | Meaning |
|---|---|
| `name`, `id` | display name, squadron enum value |
| `ShipType` | the fighter's ship-database ID |
| `shipExclusionList` | ship-database IDs of the capital ships that may **not** carry this type |
| `IsUtility` | may fill utility slots |
| `Faction` | 0 Colonial, 1 Cylon, 2 Sagittaron Separatist |
| `DLCRequired` | DLC enum: -1 None (base game), 0 Firepower, 1 SGUCampaign, 2 Survival, 3 SinAndSacrifice, 4 Rebirth, 5 GhostFleet, 6 Armistice, 7 ModernShipsPack (internal names). Stock: assault raptor and scorpion 1, taipan and cylon bomber 4, Viper Mk VII and Modern Raider 7 |
| `MunitionSlots`, `AbilityIDs` | munitions and abilities the squadron brings (raptors) |
| `PointValue`, `SupplyCostPerFighter`, `SurvivalCostPerFighter`, `XP` | fleet points and campaign costs |
| `DefaultGroup`, `MoveOnLaunch` | launch behaviour |
| `includedInMultiplayer`, `IncludedInCodex` | list membership; types with neither are story-only (delivery/messenger/engineer/signals raptors ...) |
| `MaxShipCount`, `MaxHullPoints` | a level-side copy of the prefab values, used by the UI; the GUI keeps them in step when you save squadron prefabs |
| `descriptionID`, `descriptionHeaderID`, `descriptionBodyID`, `Icon`, `WeaponType` | text IDs, icon sprite, weapon type |

The table also has global squadron-behaviour settings: `dradisBlipTicks` 120, `squadronToCapitalSlowDownSpeed` 2,
`squadronWeaponRangeAmount` 0.8, `squadronToSquadronMax/MinRange` 120/5, `squadron1v1Distance` 150, defend and
separation tuning.

**Allocation rule** (`JNLHLGGFCGH.NMGCJCCKOIC(SquadronDataTable, DRMManager, GameData, List<squadron>, FleetShip, faction, bool, bool utilitySlot)`,
which builds the choices for a carrier slot): a type is offered when **all** of these hold:

1. `DLCRequired == -1` or that DLC is authorised
2. for a **utility** slot, `IsUtility` is set (fighter slots accept utility types as well)
3. `JNLHLGGFCGH.BGNPDHJFGII(GameData, entry, bool)` passes (the unlock check: campaign unlocks/blueprints; it also
   hides the story-only raptors)
4. `Faction` equals the fleet's faction
5. the carrier's `FleetShip.ShipType` is **not** in `shipExclusionList`

Missiles follow the same pattern: `MissileDataTableObject` has its own `shipExclusionList`, `Faction` and
`DLCRequired`.

How many squadrons a carrier launches is `FighterSlots` / `UtilityFighterSlots` on the ship in `ShipDataTable`.
A fleet ship in a save stores its chosen types per slot in `FleetShip.ActiveSquadrons` (with `SquadronGroupNumbers` and
`SquadronHullHealths`), so changing allocations doesn't alter squadrons already assigned in a save; re-pick them in
the war room.

Stock exclusions: only three types have any. Colonial Viper Mk VII excludes ship IDs 0, 1, 2, 5, 11, 12, 15, 24, 26,
27 (every pre-Modern-Ships carrier). Cylon Modern Raider excludes 100, 101, 102, 104, 112, 116, 118, 120, 123. Single
Raptor and Sagittaron Viper Mk I exclude 11 (Daidalos).

Edited with `shipdb allocations` / `setallocation` and the GUI's **Squadrons → Carrier allocations** view
(`deadlock_tools/squadron_table.py`). Exclusion lists are variable length, so the table object is resized; every save
is read back, and any entry that wasn't edited must come back unchanged.

## Squadron groups (why squadrons take the same orders)

A launched squadron joins a squadron group when the game option **Auto-assign squadrons to groups**
(`GameConfig.AutoAssignSquadronsToGroups`, `"AutoAssignSquadronsToGroups"` in `Documents/Black Lab Games/BSG/config.txt`)
is on. The group is its type's `SquadronDataTable.DefaultGroup` (`JNLHLGGFCGH.KPABCKPJGHN`); with the option off,
or `DefaultGroup` 0, it joins none. Each fleet ship keeps one group number per squadron slot in
`FleetShip.SquadronGroupNumbers` (built with every slot at -1, "no group").

A group takes orders together. Stock `DefaultGroup` is 1 for the fighters (Viper Mk I, Mk II and Mk VII; Raider
prototype, Mk I and modern) and 0 for everything else. Copied types keep their donor's value, so
"Viper MkVII (Missile)" is 1 too. A carrier with ten fighter slots therefore puts all ten squadrons in group 1, and
one order moves them all: the battle log shows one `MoveToLocation` line per squadron for a single order.

To command them separately:

- **In battle:** each fighter squadron's action menu has an **Assign Group** submenu (radial position 2).
  It holds seven `AssignSquadronGroupAction` buttons:

  | Button | `targetGroup` | Text id | Radial position |
  |---|---|---|---|
  | No group | 0 | 304, "remove" | 1 |
  | Red | 1 | 881 | 4 |
  | Blue | 2 | 882 | 5 |
  | Yellow | 3 | 883 | 6 |
  | Green | 4 | 884 | 7 |
  | Black | 5 | 1507 | 2 |
  | White | 6 | 1508 | 3 |

  Copied types keep the submenu (Viper MkVII (Missile) has it).
- **Turn the game option off.** Squadrons then launch with no group.
- **Set Default group to 0** on the Squadrons tab for the type. This applies on every carrier.

## Reviving destroyed squadrons

The stock game brings a destroyed squadron back only in **Survival**. `ShipInfo.ReviveSquadron()` returns at once
unless `BattleManager.BattleType` is 6 (battle types: 0 StoryMission, 1 ResourceMission, 2 CampaignRandomEncounter,
3 MultiplayerSkirmish_PvP, 4 MultiplayerSkirmish_CoOp, 5 SingleplayerSkirmish, 6 Survival, 7 Tutorial). Survival's
two static helpers are its only callers. For a dead squadron (not alive, or `FighterSquadron.Status` 5 Destroyed) it
does this:

1. sets `Status` to 1 (Unlaunched, i.e. docked);
2. publishes game event 26 (`FightersDocked`) with the squadron's GameObject;
3. zeroes the squadron's `Force.ConstantForce`;
4. sets hull to `MaxHealth`.

Only a squadron that is still alive gets `RestoreFighters()`. `FighterSquadron.Status` values: 0 Unknown,
1 Unlaunched, 2 Launching, 3 Launched, 4 Recalled, 5 Destroyed, 6 Boarded, 7 BoardingRetreat.
`DestructableAssetInfo.IsAlive` is just `hullHealth.currentHealth > 0`, and `SetInitialHullPoints(h)` sets current
hull directly.

When a squadron dies, `BattleManager` ("Dealing with a dead ship") takes it out of the battle's ship list and its
fleet's list, records its UID, and keeps its GameObject in the hulk list (`GetHulksForFaction`). Nothing destroys the
object. It stays in its carrier's `ShipInfo.Children`, so it can be revived.

`CustomSquadronReplacementModule` (`dll_modules/`) first called `ReviveSquadron()`. Outside Survival that did nothing,
so a destroyed squadron never came back: the module filled its counter, saw the squadron still dead, and retried
every tick. Seen 2026-11-25 in a battle with the Colonial Mercury MK2: `ColonialRaptorSquadron_0031` died 312 s in
and was not replaced over the remaining ~600 s. The module now does steps 1–3 itself and then sets the hull to one
fighter's share (the smallest loss threshold). The rebuild now calls the native ship revive before applying that
one-fighter hull value. It clears the squadron's temporary disable, queued orders, and targets, and clears jamming,
ship disable, and pending weapon targets on the squadron and its individual craft. The squadron returns to AutoFire.
Then it finishes the landing teardown as before (removes the squadron
from the simulation, registers the docked fighters, sets the action to 0, deactivates the GameObject), so the
carrier's normal hangar repair (`RepairCrew.FighterRepairRate`) refills it from there. Status, `Force` and the
`FightersDocked` publish use the game's docked state and event. Gun batteries are collected at the start of each
simulation turn from active, living ships. A squadron relaunched after that collection can miss that turn.

## Where a squadron type lives

A squadron type is spread over four places. "Copy and add a squadron type" (`shipdb addsquadron`) writes all of them:

| Place | What it holds |
|---|---|
| `sharedassets9.assets` | the **prefab**: a GameObject tree (root `<Name>Squadron` with the components above; children `Actions`, `Weapon`, `Engines` ...). Abilities live here |
| `level9` `SquadronFactory` | squadron id → prefab pointer (`PPtr(6, path id)`, file 6 = `sharedassets9.assets`); `BattleShipSpawner` calls `SquadronFactory.Create(id)` |
| `SquadronDataTable` (six level copies) | one entry per type: faction, fighter ship type, munitions, info-panel ability names, costs, text IDs |
| `Assembly-CSharp.dll` | the squadron enum (obfuscated `MEAAHENKKPG`; anchor `ColonialRaptorSquadron = 2`, vanilla highest 114). Campaign stock data (`ShipStockData`) iterates it with `Enum.GetValues`, so new ids get a member |

The individual fighters are **not** in the prefab: they come from the ship bundle of the fighter ship type
(`SquadronDataTable.ShipType` / `FighterSquadron.ShipType`, e.g. 9 → `ShipFactory` → bundle `colonialraptor`). A new
squadron type that keeps its donor's fighter needs no new bundle. Fighter weapons and veterancy are in that fighter's
ship-database row, which every squadron type using the fighter shares.

Text: `descriptionID` is in the `default` localisation table (the name in the info panel), `descriptionHeaderID` and
`descriptionBodyID` in `blueprint_descriptions`.

Offering: in skirmish a type is offered when `includedInMultiplayer` is set (`JNLHLGGFCGH.BGNPDHJFGII`); in a
campaign only when its id is in the save's `CampaignState.UnlockedSquadrons`, which research fills (next section).

## Abilities

Abilities are action components on the prefab, each with a `radialMenuPosition` (0–7) and, mostly, a helper
component on the prefab root:

| Ability | Action | Helper | Info-panel text ID | Stock carriers |
|---|---|---|---|---|
| Board target | `BoardAction` | – | 34 | Raptor, Single Raptor, Heavy Raider |
| Disarm | `DisarmAction` | `DisarmAgent` | 833 | Raptor, Sweeper, Wardriver |
| Boost firewalls | `BoostFirewallsAction` | – | 967 | Raptor, Single Raptor |
| Hack target | `HackTargetAction` | `HackModule` | 441 | Wardriver |
| Jamming mode | `JammingAction` | `Jammer` | 995 | Wardriver |
| Deploy chaff | `ChaffCloudAction` | `ChaffCloudAgent` | 33 | Sweeper |
| Paint target | `PaintTargetAction` | `PaintTargetModule` | 971 | Assault Raptor |
| Bomb target | `BombTargetAction` | `BombTargetModule` | – | Cylon Bomber |
| Fire munitions | `FireMissileAction` | `MissileLauncher` (child of `Weapon`) | 232 Fire Munitions / 25 Fire Rockets | Raptor, Assault Raptor, Scorpion |

Settings (on the helper component; Abilities card inputs, `shipdb setsquadronability SQ set ABILITY field=value`):

| Ability | Fields (stock) |
|---|---|
| Deploy chaff | `ChaffCloudAgent.maxStock` 10 = the chaff supply (set full in `Awake`, refilled on `FightersDocked`, i.e. landing), `releaseInterval` 80 ticks, `minRangeFromPreviousClouds` 50 |
| Disarm | `DisarmAgent.disarmRange` 500 |
| Hack target | `HackModule.HackStrength` 0.6, `HackRange` 300 |
| Jamming mode | `Jammer.jamRange` 150, `jamMissileChance` 0.6 |
| Paint target | `PaintTargetModule.accuracyBuff` 15, `range` 400 |
| Bomb target | `BombTargetModule.bombRange` 1, `bombVerticalOffset` 0.5, `moveOutRange` 150, `missileType` 26 (the munition dropped) |
| Stealth | `StealthModule.batteryMax`, `destealthCooldownTurns`, `activeByDefault` |

Board, Boost firewalls and Fire munitions have no settings of their own. A helper is per prefab, so settings change one
squadron type only.

Not covered: the Scorpion's `SentryTargetPriorityAction` (+ `SentryAgent`, `StabilisationModule`) and the
reinforcement raptor's `RaptorChangeReinforcementTargetAction` (+ `ReinforcementMarines`).

The main menu always uses 0 Recall, 1 Move, 2 Assign group (a `SubMenuAction`, `descriptionID` 880), 3 Defend at
location, 4 Select target, 5 Defend; 6 and 7 are free. The Raptor's abilities sit in a submenu: a `SubMenuAction`
(slot 7, `descriptionID` 21 "Abilities", `Icon` 8) whose `SubMenu` points at the child GameObject `Actions/Abilities`,
which holds a `BackAction` in slot 0 and the ability actions in 1–7. `SquadronDataTable.AbilityIDs` are only text IDs
for the info panel (`ONLMCNHFBIO` passes them to `TextRepository.GetItem`). Several stock squadrons carry a
`DisarmAgent` without a `DisarmAction` (Taipan, the story raptors), so the toolkit leaves it in place when Disarm is
removed; other helpers go with the last action that uses them.

The toolkit copies an ability (and its helper, and for Fire munitions the donor's `MissileLauncher` GameObject) from a
donor prefab into the squadron's Abilities submenu, creating the submenu in main slot 7 (or 6) when missing. When the main
menu is full (Assault Raptor: Fire munitions 6, Paint target 7; Wardriver: Hack 6, Jamming 7), the ability in slot 7
(or 6; Fire munitions stays if it can) moves into the new submenu's slot 1 and the submenu button takes its slot.

## Munitions

`SquadronDataTable.MunitionSlots` lists the munition types (MissileDataTable ids, the same as capital ships use) a
squadron spawns with; `BattleShipSpawner` hands it to `ShipInfo.ConfigureMunitions`. Its filter
(`KBHEGPGCPJC.DNNMFPCFEBB`) logs `Mismatch between ShipInfo munition slots ...` when the count differs from the
fighter ship row's `MunitionSlots`, and swaps a munition of another faction for the faction default (`Ship faction
{0} is trying to use munition type ...`). Both only log; every slot still loads. Firing them takes **one `FireMissileAction` per munition**: at battle start
`MJDBEJECJMN.OBEGIIMCLHP` pairs the squadron's `GetComponentsInChildren<FireMissileAction>()` (hierarchy order) with its
munitions, giving button *i* munition *i* (`set_MissileType`, `set_Slot`) for `min(buttons, munitions)` pairs. A
munition without a button never shows. The toolkit keeps the count equal: extra buttons are copies of the first, put in
the Abilities submenu (after the main-menu one in hierarchy order), and a squadron given munitions without any button
gets the Fire munitions ability and a MissileLauncher. Stock data always matches
(Raptor, Single Raptor and Delivery Raptor: Raptor Torpedo 12; Assault Raptor: 20; Scorpion: 19). The toolkit refuses
foreign munitions and keeps the fighter's count in step.

When the new count would clash with another squadron type on the same fighter (e.g. giving a copy of the Raptor two
munitions while the Raptor and Single Raptor keep one on fighter 9), the squadron first gets **its own fighter**:
a copy of the fighter's ship-database row in every level file with a ship table (level0, 1, 3, 5, 6, 7, 8, 85, 9,
97), its `ProjectionFactory` (all six scene levels), `ShipImageDataTable` and `ShipFactory` (level5, 85, 9) entries,
a copy of the fighter bundle with the new `ShipInfo.ShipType` and its own CAB names (`<type name>fighter<id>`), and a
`CustomShip<id>` ShipType member in the DLL. The squadron prefab's `ShipInfo.ShipType` and `FighterSquadron.ShipType`
and its table entry's `ShipType` then point at the copy. Fighters have no `FleetModelFactory` entry.


## Prefab file format notes

`sharedassets9.assets` has no type trees. MonoBehaviours are read with trees generated from the DLLs (UnityPy +
TypeTreeGeneratorAPI). All 979 objects of the 21 squadron prefabs round-trip byte for byte. The generated
MonoBehaviour header leaves out the alignment after `m_Enabled`, so `m_Script` reads as garbage (the later fields line
up again); script names are taken from the raw bytes (file id and path id at offset 16) and resolved against
`globalgamemanagers.assets`.

## Research

Campaigns unlock squadron types through the research tree: `WarRoomDataTables.BlueprintInfo` in **level5** and
**level85** (identical copies). It has one entry per campaign (`Campaign`: 0 Deadlock, 1 Quorum, 2 Sin and Sacrifice,
3 Galactica Rebirth, 4 Ghost Fleet, 5 Armistice) with four lists: `CapitalShipUnlocks`, `MunitionUnlocks`,
`SquadronUnlocks`, `BattlestarShipUnlocks`. `BlueprintSquadronUnlock` uses the same generic entry
(`CHDPKHPONLG<T>`) as the ship entries, with obfuscated fields:

| Field | Meaning |
|---|---|
| `ODOMECMDGKJ` | squadron type id |
| `GEIBBHBCKAM` | story mission that must be completed first (-1 = none) |
| `GKIMOHIEFNI` | cost in research points |
| `NPFHKIHKKIC` | short label on the research node |
| `HJGFMHOLHPG` | unlocked from the start |
| `COPOFKMOPIH` | second flag (false in every stock entry) |

Stock: Viper Mk I and Raptor unlocked at start (not in Quorum), Viper Mk II 800 RP, Sweeper 400, Assault Raptor 600
and, from Galactica Rebirth, Taipan 600 (mission 39 there). In Deadlock and Quorum the Mk II, Sweeper and Assault
Raptor need missions 5, 6 and 17. The Viper Mk VII and Modern Raider have no entries (skirmish only).

The research panel rebuilds its list from these entries every time it opens (`GBEPGGLDNMC.KPJBEHEGPHJ`), so added
entries show in existing campaigns. A squadron entry's state (`GBEPGGLDNMC.IKIHCCPCAAC`, enum Locked / Available /
Purchased / Unavailable): **Purchased** when flagged unlocked or already in `UnlockedSquadrons`; **Unavailable** when
the type is missing from the `SquadronDataTable` or its DLC is not owned; **Available** once the required mission is
completed (`BICIKLMBCLG`: `LastStoryMissionCompleted` or `StoryMissionsCompleted`); **Locked** otherwise. Buying it
adds it to `UnlockedSquadrons`. The unlocked flag is copied into `UnlockedSquadrons` when a campaign starts
(`CampaignStartingResourcesInfo`), so in an existing campaign a newly flagged entry shows as purchased but is not
offered in carrier slots until a new campaign.

Edited with `shipdb squadronresearch` / `setsquadronresearch` and the Research card (`deadlock_tools/squadron_research.py`).

## Stealth

Stealth is `StealthModule` (battery, drain/recharge, cooldown) plus `StealthAction` (the button). Stock carriers: the
Colonial Orion (and copies such as the user's Mercury MK2). The Orion's `StealthModule` serializes `batteryMax` 100,
`destealthCooldownTurns` 1, `activeByDefault` 1 and two effect GameObjects (`stealthEffect`, toggled with
`SetActive`; `changeStealthEffect`, passed to `Instantiate` with no null check on every stealth change).
`StealthAction` serializes only `ActionBase`'s `radialMenuPosition` / `hideInMenu`; it looks for a TechLab only on
capital ships and guards its "Tech Bay Offline" text against a missing one. Text 1534 is "Stealth".

The rates come from the ship's **Tech Bay** (`TechLab`, found in `StealthModule.Awake` with
`ShipInfo.GetSubSystem<TechLab>()`): per battle tick the battery loses `TechLab.StealthDrainRate / 10` while stealthed
and gains `StealthRechargeRate / 10` otherwise, and stealth is cancelled when `TechLab.IsOffline`. The rates evaluate
`stealthDrainRate` (FloatCurveRange Start 8 → End 0.1, Power 5) and `stealthRechargeRate` (FloatRange 0.5–2) at the
Tech Bay's health; at full health that is drain 0.1 and recharge 2. Without a TechLab `Awake` only logs "Could not find
TechLab on ship", but every rate or IsOffline call then throws. Squadrons have no subsystems, so:

* The custom-module DLL's `CustomStealthFallback` (`dll_modules/`, injected by `InjectStealthFallbackHooks` in
  `scripts/merge_custom_modules.cs`) turns the TechLab rate getters (including the decoy copies `CMGEDHHPHFB` and
  `AIACKIPACHL`) and the `IsOffline` call in `StealthModule` and `StealthBatteryWidget` (10 call sites) into static
  calls that return the stock value with a TechLab and the full-health values (drain 0.1, recharge 2, never offline)
  without one. Every custom-module build includes it; ships with a Tech Bay behave as before.
* `sharedassets9.assets` has no type entry for either script (no squadron used them), and both MonoScripts are in
  `globalgamemanagers.assets` (StealthModule 905, StealthAction 2618). A new MonoBehaviour type there needs a
  script-type entry (file 1 = globalgamemanagers, the MonoScript's path id) and a type whose script ID is
  **MD4(class name + "Assembly-CSharp.dll")** and whose type hash is the MonoScript's **`m_PropertiesHash`**
  (checked against DisarmAgent and Jammer). The toolkit adds both, then the two components; the effects are two empty,
  inactive child GameObjects (`StealthEffect`, `ChangeStealthEffect`), so there is no shimmer.

Untested in game: whether enemies stop targeting a stealthed squadron and which squadron commands cancel stealth
(`StealthAction.IsCommandAllowedInStealth`).

UnityPy note: an object is read from its place in the source file; only saving uses the edited `data`. The toolkit
points every object it writes at a reader over its new bytes, so in-session reads (and clones) see the edit.

## Jamming on capital ships

`Jammer` acts on what is around it: every squadron reference in it is to the ship being jammed (enemy squadrons in
`jamRange`, tracked in `jameeSquadronIDList`; capital ships it finds are treated differently), and it rolls
`jamMissileChance` against missiles in range. Nothing assumes its own ship is a squadron, so it works on a capital
ship. `JammingAction` finds its Jammer with a parent search (button in an `Actions` child, Jammer on the root, as on the
Wardriver); its `HackTargetAction` link is null-checked everywhere. The jam sound plays through the root's
`AudioSource` only when there is one (`PlayOneShot` is guarded); the Wardriver's clip (path 1174) is in
`sharedassets9.assets`, which a ship bundle cannot reference, so a ship's jammer has no clip.

No ship bundle contains either script, so `weapons --jamming` writes both into the bundle the way the custom modules
are written: a MonoScript record (`Assembly-CSharp.dll`, no namespace) and a type tree per class. Jammer's serialized
fields: `jamRange` float, `jamMissileChance` float, `jamAudioClip` `PPtr<$AudioClip>`, `visualiseLayer` string
(Wardriver: `UI_3D`), `assignToSelf` bool. `JammingAction` serializes only `ActionBase`'s `radialMenuPosition` /
`hideInMenu`. Type-tree conventions copied from the game's own bundles: strings `string -1 / 0x8000` → `Array 0x4001`
→ `size`, `data` (`0x1`) as colonialcelestra's `predefinedName`; object pointers 12 bytes with `m_FileID` / `m_PathID`
at `0x800001` as the Orion's StealthModule effects. Default range 500 (the Wardriver's 150 is squadron-sized), missile
chance 0.6.

**Turning it on needs the custom-module DLL.** The Jamming button queues a `Jam` (33) or `JamCancel` (34) command;
`BattleSimulation` runs them in `PGJCKKJJNKA` / `LANLGKDOLED`, which call `ShipInfo.Jammer.set_JamActive(true/false)`
only when the ship's `ShipInfo.IsProxy` is set. `IsProxy` is never written by code: it is a prefab field, 1 on squadron
prefabs and 0 on capital ships. On a stock DLL a capital ship's Jam order is accepted, shown as pending (the button
lights because the command exists), then does nothing, so the button reads "off" again next turn (the game log still
lists `<ship> - Jam` each time). `InjectJammingHooks` swaps that `IsProxy` read for `CustomJamming.CanCommandJam` (IsProxy
or a Jammer on the ship) in every method that then calls `set_JamActive` (the two handlers plus an unused decoy).
`ShipInfo.Tick` calls `Jammer.Tick` for any ship. While `jamActive` it jams enemy ships
that are `IsProxy` (squadrons, not capital ships) and in range, and rolls against guided missiles. It switches off on
`JamCancel`, a Hack order, or its ship's death.

**Capital jammer battery.** The custom DLL charges 10 power per 120 battle ticks while a capital ship's jammer is
active. A ship with Stealth, such as the Mercury Mk II, spends its existing battery charge. Stealth recharge pauses
while jamming, and simultaneous stealth still has its own drain. A capital ship without Stealth starts with a
separate 100-power reserve and recovers 24 power per 120 ticks while jamming is off. The reserve lives on the battle
instance and has no HUD meter. Empty batteries shut jamming off and block activation until there is enough charge
for a tick. Squadron and missile jammers keep their existing behavior.

The jammer checks for the ship's Stealth component at runtime, so the order in which you add the abilities does
not affect battery sharing. **Save ship** prepares the custom DLL for bundles with a Jammer, including ships with
no other custom modules.

Ships → Abilities → Jamming exposes battery drain, reserve capacity and reserve recharge. These per-ship overrides
live in `deadlock-jamming.txt` as `drain`, `reserve` and `recharge`. Drain and recharge are measured per 120
battle ticks. A zero drain makes jamming free. Reserve and reserve recharge apply only when there is no Stealth
module. Clearing an override restores the default.

## Launch bays

A carrier launches a squadron only from a `LaunchPoint` whose `LaunchCompatibility` (a list of squadron types) contains
the squadron's type: `MJDBEJECJMN.FindLaunchPoint(type)` checks `Contains`. The lists live in each carrier's ship bundle
(vanilla Colonial carriers: fighter bays `0, 1` (+ `113` with the Modern Ships pack), utility bays `2, 3, 4, 5`, some
with `104`). A new squadron type is in none of them, so it is offered in a carrier slot but never launches.
"Copy and add a squadron type" / `shipdb addsquadron` therefore also adds the new type to every bay that takes the
donor, on every carrier allowed to carry it (same faction, fighter or utility slots, not on its `shipExclusionList`;
bundle names from `ShipFactory`). For an existing type: the Launch bays card or
`shipdb setsquadronbays PREFAB [--launch-like PREFAB]` (default: the stock squadron whose name the prefab name contains).
Carriers excluded later through the allocation grid keep the entry; carriers allowed later need the action again.

## Jamming effects

A `Jammer` flags squadrons and fighters in `jamRange` `IsJammed` (and records the team: `LastTeamJammedBy`) and rolls
`jamMissileChance` to `Missile.Redirect` missiles. What a jammed ship suffers is one global struct,
`DataTables.JammingPenalty` (level5, level85, level9): `MaxVelocityPenalty` -1.2 (read in the fleet ship class's
`get_MaxVelocity`), `EvadePenalty` -15 (14 reads in the hit calculation) and `AccuracyPenalty` 0 (read in
`WeaponInfo`, for the jammed ship's own weapons). The ship-info tooltip shows the same values. The game does not record
which jammer jammed a ship, so stock jamming has one effect for every jammer.

Edited globally with the Squadrons tab's **Jamming effects** view or `shipdb setjamming global evade=-20 ...`.
Per jammer: the custom-module DLL's `CustomJamming` (`dll_modules/`, injected by `InjectJammingHooks`) routes the
Jammer's 8 `set_IsJammed` calls through `SetJammed(target, value, jammer)`, which remembers the jamming ship, and in
every method that checks `IsJammed` and then reads a penalty field (16 reads; not the tooltip) turns the check into
`IsJammed(ship)` and the read into `MaxVelocity / Evade / Accuracy(ref info)`. Those return the jammer's own value from
`StreamingAssets/deadlock-jamming.txt` (`squadron <type id> evade=-25 ...`, `ship <ship id> speed=-2 ...`), else the
global value. The jammer key is `FighterSquadron.SquadronShipType` for a squadron, `ShipInfo.ShipType` for a ship.
Untested in game.

