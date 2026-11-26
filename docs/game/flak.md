# Flak field

Read from the stock `Assembly-CSharp.dll` (1.5.113) and the stock ship bundles. Edit a ship's flak with
`weapons --flak FIELD=VALUE` or the **Flak field** card on the Ships tab (see [cli-reference](../cli-reference.md)).

## Components

- **`FlakField`** (MonoBehaviour on the ship root, next to `ShipInfo`) holds every setting:
  - `BaseDamagePerTick`: a `MissileDamage`, the same struct missiles use: `HullDamage`, `ArmourDamage`,
    `SubSystemDamage`, `Range`, `SubsystemSpreadDamage`, `SubsystemSpreadCount`, `AppliesIrradiatedStatus`,
    `IrradiatedTurns`, `IrradiatedPercentage`;
  - `FlakRange` (`FloatRange` Min / Max);
  - `FlakAreaLength`, `FlakAreaRadius`, `FlakExplosionsPerTick`;
  - `flakHitAudio` and `flakEffectsTemplate` (sound and effect references).
- **`FlakFieldAction`**: the radial button (Mercury: Actions menu, position 6). It opens the quadrant selector:
  port, starboard, or off. The choice is a battle command; `FlakField.ChangePosition(quadrant)` applies it (`-1` = off).
- **`Armory`** subsystem:
  - `FlakDamageRange` (Min 20, Max 100 on almost every ship) is a percentage of the base damage, evaluated on the
    Armory's health (`Health.HealthPC` → `FloatRange.Evaluate`). `Armory.FlakDamagePercent` returns 0 when the
    Armory is offline or the ship has no FlakField.
  - `FlakRangeRange` (code default 40, NaN in the Mercury bundle) is read by nothing (`Armory.get_FlakRange` has no
    callers).

## What happens each battle tick

`ShipInfo.Tick` → `FlakField.Tick`, while a quadrant is active:

1. If there is no Armory, the game logs "Armory is null!" and stops; `Start` also warns "{ship} has flak but no
   armory!". If the Armory is offline, nothing happens.
2. Bursts: `FlakExplosionsPerTick × Armory health` (0-1) are added to a counter. Each whole burst plays a detonation
   effect and is recorded for the replay. **This is visual only**: damage does not depend on it. When the product is
   0, a random roll against Armory health still gives one burst.
3. Damage (`BGNPCICLNIB`):
   - The zone is a capsule cast along the ship's forward axis, centred `FlakRange.Min` to the side: −x (port) for
     quadrant 0, +x (starboard) otherwise. It is `FlakAreaLength` long with radius `FlakAreaRadius`, on the
     Pickable and Missile layers. Each hit is then kept only inside the ellipsoid (local x/r, y/r,
     z/(Length/2 + r)) with squared length ≤ 1.
   - Every ship hit, except the flak ship itself, is damaged **once per tick**. A fighter counts as its squadron
     (`ShipInfo.Proxy`); boarded squadrons are skipped. The damage is
     `BaseDamagePerTick × crew bonus × Armory.FlakDamagePercent`, applied with `ApplyMissileDamage`.
   - There is **no team check** in `FlakField` or in either `ApplyMissileDamage`, so friendly ships inside the zone
     appear to be hit too. This has not been confirmed in game.
   - Missiles inside the zone (a destructible whose check passes) each have a **30% chance per tick** to be
     destroyed.
   - At most one hit sound plays every 4 seconds.

## Scaled ships

The zone is placed in the ship root's local space (`transform.TransformPoint`, `worldToLocalMatrix`). So on a ship
whose root is scaled by s (`weapons --ship-scale`), the centre distance and the length scale with the ship. The capsule
radius is a world distance and does not scale, while the ellipsoid clip does (s·r sideways, s·(L/2 + r) fore-aft).
The zone is the overlap of the two. At s = 1 the ellipsoid lies inside the capsule, so the zone is just the
ellipsoid. `apply_ship_scale` leaves the FlakField alone, so a 1.25× ship's flak sits 25% further out with the same
width. The Ships tab diagram draws this overlap in world units.

## Field by field

| Field | Effect |
|---|---|
| `BaseDamagePerTick.HullDamage` | hull damage per tick per ship in the zone. Stock 1.0, which is 120 per turn at full Armory health |
| `BaseDamagePerTick.ArmourDamage` | armour damage per tick. Stock 0.02 (2.4 per turn) |
| `BaseDamagePerTick.SubSystemDamage` | subsystem damage per tick (stock 0) |
| `BaseDamagePerTick.SubsystemSpreadCount` | extra subsystems hit |
| `BaseDamagePerTick.SubsystemSpreadDamage` | **ignored**: the damage scaling (`MissileDamage` × factor) writes `SubSystemDamage × factor` into it, and flak always scales |
| `BaseDamagePerTick.AppliesIrradiatedStatus`, `IrradiatedTurns`, `IrradiatedPercentage` | irradiation on hit, as nuclear munitions do (stock off) |
| `BaseDamagePerTick.Range` | not used by flak |
| `FlakRange.Min` | distance from the centre line to the middle of the zone |
| `FlakRange.Max` | not used: `ChangePosition` clamps the distance into the range and then overwrites it with Min. The tool keeps Max ≥ Min |
| `FlakAreaLength` | zone length fore-aft. It reaches Length/2 + Radius from the middle |
| `FlakAreaRadius` | zone radius sideways and vertically |
| `FlakExplosionsPerTick` | visual bursts per tick at full Armory health |

**Crew bonus.** Stat 202 ("Flak damage") is read once, in `Start` and `OnResumeMission`, through
`ShipDataTable.CalculateCrewVeterancyPercentageBonus(ship, 202, …, 1)`, which gives 1 × (1 + bonus). Stat 203
("Flak range") is read by no stock code.

## Stock ships

Only these eight bundles have a FlakField; no Cylon or Sagittaron ship does. All have hull 1.0 and armour 0.02 per
tick, and no subsystem damage or irradiation.

| Ship | Distance (Min) | Max | Length | Radius | Bursts/tick | Armory FlakDamageRange |
|---|---|---|---|---|---|---|
| Artemis | 100 | 101 | 110 | 45 | 1.33 | 20-100 |
| Battlestar (Galactica) | 120 | 121 | 155 | 70 | 1.4 | 20-100 |
| Defender | 70 | 71 | 70 | 30 | 1.33 | 20-100 |
| Heracles | 60 | 61 | 90 | 45 | 1.4 | 20-100 |
| Jupiter Mk II | 120 | 121 | 160 | 75 | 1.4 | 20-100 |
| Mercury | 100 | 101 | 110 | 45 | 1.33 | 20-100 |
| Minerva | 100 | 101 | 90 | 35 | 1.33 | 20-90 |
| Valkyrie | 60 | 61 | 75 | 35 | 1.33 | 20-100 |

Adding flak to a ship that lacks it is not supported yet: it needs the FlakField, a FlakFieldAction button and the
effect and audio references, like the stealth copy from the Orion.
