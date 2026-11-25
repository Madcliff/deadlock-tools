# Subsystems and crew stats

How Battlestar Galactica: Deadlock turns subsystem health and crew veterancy into ship stats, and how the
toolkit's **subsystem crew modifiers** add to them. Findings from the original `Assembly-CSharp.dll`
(`Assembly-CSharp.dll.old`, IL read with `ikdasm`). Edited with `deadlock_tools/subsystem_modifiers.py`.
The DLL side is `dll_modules/CustomSubsystemModifiers.cs`.

## Subsystem components

Each ship prefab has one GameObject holding the subsystem MonoBehaviours. All of them derive from `SubSystemBase`
(itself an `EventReceiverBehaviour`). On the Colonial Mercury that GameObject is a child of the one holding
`ShipInfo`.

| Class | Display name | Stat group |
|---|---|---|
| `FireControl` | Fire Control | 0–2 |
| `Engineering` | Engineering | 100–103 |
| `Armory` | Armoury | 200–204 |
| `CIC` | CIC | 300–302 |
| `Hangar` | Hangar | 400–401 |
| `BlackLab.BSG.Navigation` | Navigation | 500–502 |
| `TechLab` | Tech Bay | 600–607 |

`RepairCrew` is not a subsystem (it extends `MonoBehaviourEx`).

### Health to effect

A subsystem setting such as `FireControl.TurretRangeRange` is a `FloatRange {Min, Max}` in percent. Stock getters
evaluate it on the subsystem's health through a helper class (`KFGFMNDEPHL`, built from the subsystem's `Health`):

```
effect = value * FloatRange.Evaluate(health.HealthPC) / 100
FloatRange.Evaluate(t) = Min + (Max - Min) * t      // unclamped
```

So `Min` applies at 0 health and `Max` at full health. `SubSystemBase.get_HealthPC` returns the boosted health
fraction, so it can be above 1 while a boost is active. Its property is named `JFGBMFEOOCJ` in the obfuscated
metadata, which is the name C# code compiled against the DLL has to use.

## Crew stats (`FEDNAJLFKEF`)

Crew veterancy bonuses come from `ShipDataTable.GetCrewVeterancyBonus(ShipInfo ship, FEDNAJLFKEF stat,
BattleManager battle)`. It returns the `BonusAmount` of the ship's current veterancy rank for that stat, or 0 when
the ship is null, crew veterancy is off (`BattleManager.CrewVeterancyEnabled`), the ship type has no veterancy
table, or the rank has no effect for the stat. The amount is not a multiplier. The game applies it in one of two
ways:

- `CalculateCrewVeterancyPercentageBonus(ship, stat, battle, value)` returns `value * (1 + bonus)`, or `value`
  when the bonus is exactly 0.
- `CalculateCrewVeterancyFixedBonus(ship, stat, battle, value)` returns `value + bonus`, or `value` when it is 0.

`SubSystemBase.INJINIKABHG(stat)` returns `GetCrewVeterancyBonus(...)`, or 1 when the table or ship is null. It is
used only by `get_SubsystemEffectInfoList` (the subsystem tooltip rows). The 14 `INJINIKABHG` call sites are
3 tooltip getters (`Armory` 202, `FireControl` 1, `Hangar` 401) and 11 obfuscator decoy copies of them, which
are never called and pass junk stat values (0xa5, -29, 0x89, ...). Patching `INJINIKABHG` would therefore change
tooltips only. The toolkit hooks `GetCrewVeterancyBonus` instead, since every consumer goes through it.

The enum has 28 values (the editor's `CREW_MODIFIER_FIELDS` lists the first 25). The table shows every stat and
the gameplay code that reads it in this build. **Setup** means the value is read once (Start, spawn, mission
resume or a subsystem event) and cached. **Live** means it is read each time it is used.

| Stat | Name | Read by | Mode | When |
|---|---|---|---|---|
| 0 | Turret range | — | — | — |
| 1 | Turret accuracy | `WeaponInfo` setup (`Start`, `OnLoad`) | fixed | setup |
| 2 | Munition salvo size | `FireControl.GetMunitionReload` (from `FireMissileAction`) | percent | live |
| 100 | Damage reduction | `Engineering` (`Start`, `OnGameEvent`) | percent of 1 | setup |
| 101 | Subsystem repair | — | — | — |
| 102 | Squadron repair | `Engineering.GetSquadronRepairRate` (from `RepairCrew.DoFighterRepairs`) | percent | live |
| 103 | Hull regeneration | — | — | — |
| 200 | Marine armour | — | — | — |
| 201 | Marine strength | `ShipInfo.SetCommander` on `MaxCombatantStrength` (at spawn) | fixed | setup |
| 202 | Flak damage | `FlakField.Start` / `OnResumeMission` | percent of 1 | setup |
| 203 | Flak range | — | — | — |
| 204 | Boarding strength | — | — | — |
| 300 | Posture effect | — | — | — |
| 301 | Processing power | — | — | — |
| 302 | System overclock | — | — | — |
| 400 | Squadron launch | — | — | — |
| 401 | Squadron evade | `ShipInfo` `Start` / `OnResumeMission`, read from the squadron's **mothership** | fixed | setup |
| 500 | Pitch control | — | — | — |
| 501 | Yaw control | `Navigation` (`Start`, `OnGameEvent`) | percent of 1 | setup |
| 502 | Maximum distance | `Navigation` (`Start`, `OnGameEvent`) | percent of 1 | setup |
| 600 | DRADIS range | — | — | — |
| 601 | Firewall regeneration | — | — | — |
| 602 | Hacking strength | — | — | — |
| 603 | Micro-FTL range | — | — | — |
| 604 | Munition resupply rate | — | — | — |
| 605 | Firewall strength | `Firewall` (`Start`, `OnResumeMission`) | fixed | setup |
| 606 | Stealth drain rate | — | — | — |
| 607 | Stealth recharge rate | — | — | — |

"—" means no stock code reads the stat. A bonus on it has no effect unless one of this toolkit's custom modules
reads it through its crew-modifier selector. `CustomArmourRepairModule`, `CustomDroneResupplyModule`,
`CustomSquadronReplacementModule` and `CustomMagazineCapacityModule` all call
`CalculateCrewVeterancyPercentageBonus` with their configured `modifierField`. Other decoy sites (`Engineering`
112, `FlakField` 0x93/0x72, `Navigation` 0xb5 and the `ShipDataTable.PLKAIEIFNJK` wrapper) use values outside the
enum and are never reached.

## Subsystem crew modifiers

`CustomSubsystemModifiers` is a MonoBehaviour on the GameObject that holds `ShipInfo`. Its four serialized vectors
are parallel arrays, one entry per index:

| Field | Type | Meaning |
|---|---|---|
| `stats` | `int[]` | target `FEDNAJLFKEF` value |
| `sources` | `int[]` | source subsystem: 0 Fire Control, 1 Engineering, 2 Armoury, 3 CIC, 4 Hangar, 5 Navigation, 6 Tech Bay |
| `bonusMin` | `float[]` | bonus at 0 source health |
| `bonusMax` | `float[]` | bonus at full source health |

The merge script (`InjectSubsystemModifierHooks` in `scripts/merge_custom_modules.cs`) rewrites each of the six
returns of `GetCrewVeterancyBonus` to `return CustomSubsystemModifiers.Apply(ship, stat, result)`. `Apply` returns
`result` unchanged when the ship is null or has no component. Otherwise it adds
`bonusMin + (bonusMax - bonusMin) * source.HealthPC` for every entry whose stat matches, which is the stock
`FloatRange.Evaluate` on the source subsystem's health. The source is found once per ship with
`GetComponentsInChildren<SubSystemBase>(true)`. Ships without the component behave exactly as stock.

The bonus uses the same unit as a veterancy rank's `BonusAmount`. For percent stats, 0.25 is +25%. For fixed
stats it is an absolute amount added to the value. Because the hook is inside `GetCrewVeterancyBonus`:

- The bonus applies even when crew veterancy is off or the ship has no rank effect for the stat, since the
  callers apply any non-zero result. It adds to any veterancy bonus the ship already has.
- Tooltips built with `INJINIKABHG` show it too.
- For **setup** stats the bonus is taken at the source's health when the game reads it (normally full health, so
  `bonusMax`). Later damage changes only the **live** stats.
- Squadron evade (401) is read with the squadron's mothership, so an entry on a carrier affects its squadrons.

Edit them with `weapons --subsystem-modifier STAT:SOURCE:MIN:MAX` (repeatable, replaces the list) or
`--clear-subsystem-modifiers`, or in the GUI's **Subsystem crew modifiers** card on the Ships tab (saved by Save ship,
which also rebuilds the custom-module DLL when a bundle carries the component).

The Colonial Mercury Mk II round trip (add, edit, remove, re-add) is covered by `tests/test_subsystem_modifiers.py`.
The patched DLL has been built and checked in IL, but it has not been tested in game yet.

## Which subsystem drives each module

Passive modules read one subsystem setting through the same health evaluation (`value * Range.Evaluate(HealthPC) / 100`):

| Module | Per tick | Stock driver |
|---|---|---|
| `RegenerativeHullResinModule` (Hull Resin) | `RepairHull(hullPerTick * Engineering.HullRepairRateRange.Evaluate(health) / 100)` while hull is below maximum | Engineering `HullRepairRateRange` (the same range also drives the repair crew's hull repair) |
| `MunitionFactoryModule` | work `Armory.MunitionResupplyRange.Evaluate(health) / 100` | Armoury `MunitionResupplyRange` |
| Custom armour repair / drone resupply / squadron replacement / magazine capacity | base value × crew-veterancy weighting | none |

Read from the vanilla DLL: `RegenerativeHullResinModule.DJMKBDNFBGM` (the tick `OnGameEvent` calls) calls
`Engineering.GetHullRepairRate(hullPerTick)`; `MunitionFactoryModule.DJMKBDNFBGM` calls `Armory.GetMunitionResupplyRate()`.
The other copies of those methods are obfuscator decoys that call renamed getters and never run. Crew veterancy's
"HullRegen" stat (103) is read by neither, so its rank bonuses have no effect on Hull Resin in the stock game.

## Module subsystems

`CustomModuleSubsystems` (`dll_modules/CustomModuleSubsystems.cs`, `deadlock_tools/module_subsystems.py`) lets
several subsystems drive one of those modules. It sits on the GameObject that holds `ShipInfo`, with four parallel
vectors: `modules` (0 Hull Resin, 1 Munition Factory, 2 custom armour repair, 3 drone resupply, 4 squadron
replacement, 5 magazine capacity), `sources` (0 Fire Control … 6 Tech Bay), `rateMin`, `rateMax`. A module with at
least one entry uses

    factor % = sum of rateMin + (rateMax - rateMin) * source.HealthPC   over its entries (never below 0)

* Hull Resin: hull per tick = `hullPerTick × factor / 100` instead of Engineering's range.
* Munition Factory: work per tick = `factor / 100` instead of the Armoury's range.
* Custom modules: their rate (or the magazine multiplier) is multiplied by `factor / 100`, after the crew weighting.

A module without entries is unchanged. The merge script (`InjectModuleSubsystemHooks`) wraps every real-name call to
`Engineering.GetHullRepairRate` inside `RegenerativeHullResinModule` and to `Armory.GetMunitionResupplyRate` inside
`MunitionFactoryModule`; the custom modules call `CustomModuleSubsystems.Factor` themselves. Edit with
`weapons --module-subsystem MODULE:SOURCE:MIN:MAX` / `--clear-module-subsystems` or the **Module subsystems** card on
the Ships tab. Built and checked in IL (2026-11-25); not yet tested in game.

