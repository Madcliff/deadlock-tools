# Munitions

How Battlestar Galactica: Deadlock stores munition types (missiles, torpedoes, mines, nukes ...), and how the toolkit
copies one into a new type (`shipdb addmunition`, Armaments tab → Munitions → **Copy as a new munition…**,
`deadlock_tools/munition_types.py`). Stats of any munition are edited with `shipdb setmunition` or on the Armaments tab.

## Where a munition type lives

| Place | What it holds |
|---|---|
| `MissileDataTable` (level5, 6, 7, 8, 85, 9) | one entry per type: name, id, text IDs, guidance, faction, salvo size, `MaxMissiles`, launch delays, costs, `shipExclusionList`, DLC, AoE, velocity, range, damage |
| `MissileImageDataTable` (same six levels) | `Icon`, `IconTop` per munition. Several tables share this layout (weapon and other icons), so the munition one is the table whose ids are the munition ids |
| `MissileFactory` (level9) | munition id → missile prefab, a `PPtr(6, path id)` into `sharedassets9.assets` |
| missile prefab (`sharedassets9.assets`) | the projectile GameObject tree: `Missile`, a mover (`MissileMoverUnguided` …), `MissileDetonator`, mesh, particles, audio, `Rigidbody`, colliders. `Missile.missileType` is the id the game looks up the munition's stats with (`MissileDataTable.Find`); nothing sets it at runtime (only the F3D demo launcher writes it), so every munition type needs its own prefab |
| research (level5, level85) | `WarRoomDataTables.BlueprintInfo[campaign].MunitionUnlocks`, the same entry layout as `SquadronUnlocks` (id, mission, RP cost, label, unlocked); see [squadrons.md](squadrons.md#research). Squadron-only munitions (Raptor Torpedo, Assault Raptor Guided, Scorpion Rocket) have none |
| `Assembly-CSharp.dll` | the munition enum (obfuscated `LJPOLGLDKBA`, anchor `ColonialRaptorTorpedo = 12`, vanilla 0–29). Campaign stock data (`ShipStockData`, `Dictionary<munition, MunitionStockData>`) iterates it with `Enum.GetValues` |

Text: `descriptionID` is the name in the `default` localisation table (115 "Rocket" for the Raptor Torpedo);
`descriptionHeaderID` / `descriptionBodyID` are in `blueprint_descriptions` (-1 for squadron-only munitions).

A squadron fires the munitions listed in its `SquadronDataTable.MunitionSlots` (see
[squadrons.md](squadrons.md#munitions)); a capital ship gets them through the research tree and the munition table's
faction, DLC and exclusion list.

## New munition types

`create_munition` copies a donor:

1. clones the donor's missile prefab tree in `sharedassets9.assets` (new object ids, internal pointers remapped,
   shared meshes/materials/audio kept) and sets the copy's `Missile.missileType` to the new id;
2. appends a copy of the donor's `MissileDataTable` and `MissileImageDataTable` rows (new name and id) to all six levels;
3. adds `MissileFactory` id → the new prefab in level9;
4. optionally copies the donor's `MunitionUnlocks` research entries (never "unlocked at start") and writes new
   name/heading/description strings;
5. the caller adds the munition enum member (`ship_types.add(..., munition=True)`); DLL rebuilds keep munition members
   like ship and squadron ones (`CustomMunition<id>` for an id with none).

The new munition starts with the donor's stats and visuals; change the stats afterwards. Untested in game.

## Combat stats

Hits use the missile prefab's `Missile.damage` (16 reads): `HullDamage`, `ArmourDamage`, `SubSystemDamage`, `Range`
(blast radius; 0 = direct hit), `SubsystemSpreadDamage` / `SubsystemSpreadCount`, `AppliesIrradiatedStatus`,
`IrradiatedTurns`, `IrradiatedPercentage`. `Missile.DisplayDamage` (unused) and `MissileDataTable.DisplayDamage` (the
UI's "listed" damage, 1 read) are copies. Speed is `Missile.maxVelocity` (17 reads; the table's `MaxVelocity` 3, a
prediction helper). Range is two values: `MissileDataTable.Range` is the firing range targeting uses (26 reads:
`FireMissileAction`, the multi-munition button ...), `Missile.range` how far the missile flies. The mover component holds
`maxTurnRate` and, on guided munitions (`MissileMoverTargetSeeking`), `accuracyPC`. Stock examples: Colonial Guided
6/6 hull/armour, speed 50, accuracy 0.8, turn 60; Colonial Torpedo 4/4, speed 100, unguided turn 0.6; Raptor Torpedo 4/4,
speed 50, range 4000.

Every stock munition has its own prefab (30 MissileFactory entries, 30 prefabs). `shipdb munitionstats` /
`setmunitionstats MUNITION hull=8 armour=6 blast=30 speed=70 range=5000 ...` and the Armaments munition editor's
**Combat** section write the prefab and keep the table copies (and both ranges) equal.

