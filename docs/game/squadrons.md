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
