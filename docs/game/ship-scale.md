# Scaling a ship and laying out its guns

Findings from the original `Assembly-CSharp.dll` (IL read with `ikdasm`) and the Mercury MK2 bundle. Tooling:
`weapons --ship-scale S`, `--scale SELECTOR=S`, `--respace SELECTOR=GAP` (all in the GUI's bundle form too).

## How a ship bundle is put together

The prefab root is a GameObject named after the bundle (e.g. `colonialmercurymk2`) carrying `ShipInfo`,
`ArmourInfo`, `PropulsionInfo`, the subsystems and modules. Its `Transform` has these children: `Actions` (radial
menus), `Model`, `Weapons` (one child per quadrant: `Fore`, `Rear`, `Port`, `Starboard`, `Dorsal`, `Ventral`, each
holding `WeaponMountPoint` objects), `SubSystems`, `WeaponTargets`, `LaunchPoints`, `BoardingPoints`,
`CollisionZones`, `Engines`, `ShipNamePlates`, the wreck mesh and `Pickable`. Extra roots in the same bundle (stealth
effects, decals) aren't part of the ship hierarchy.

## What scales with the root Transform, and what doesn't

| Data | Scales with the root? | Why |
|---|---|---|
| Model, collision zones, launch/boarding points, engines | yes | children of the root |
| Weapon mount **positions** | yes | children of `Weapons/<quadrant>` |
| **Turret size** | yes, unless compensated | `ShipInfo` spawns each turret from `WeaponFactory` at the mount's position/rotation, `set_parent(mount)`, then `localScale = (MeshScale, MeshScale, MeshScale)`, so it inherits every parent scale |
| `WeaponsTarget` hit points and miss points | yes | read through `transform.TransformPoint` |
| `ArmourInfo.quadrantScale` / `quadrantOffset` | yes | the hit position is converted with `InverseTransformPoint` before the quadrant test |
| `WeaponsTarget.missSphereRadius` | **no** | added after `TransformPoint`, a world distance |
| `ShipInfo.CollisionExclusionZoneRadius` | **no** | world distance; deployment spacing reads the **ship database** copy (`ShipDataTableObject.CollisionExclusionZoneRadius`) |

`--ship-scale S` therefore:
1. sets the root scale to S (absolute: 1 = stock, so repeating it changes nothing; non-uniform roots are refused)
2. divides every weapon mount's Transform scale by the change, so turrets keep their size and `MeshScale` keeps meaning
   "turret size"
3. multiplies `CollisionExclusionZoneRadius` and `missSphereRadius` by the change

Also set the ship-database `CollisionExclusionZoneRadius` to the same value (Ships page) so fleets deploy with the
right spacing. Not scaled: the war-room fleet icon model (`FleetModelFactory` in level5/level85) and the hologram
projection; they're separate prefabs.

## Turret spacing

Stock full-size turrets on the Mercury bow are 4.0 game units apart at their closest. Cloned turrets (`<name>_x2`,
`_x3`, made by `--clone`) were packed 2.0 apart at `MeshScale` 0.7. `--respace fore=4` moves the existing clones (originals
stay put) to the nearest free hull spot that is at least the gap from every other turret. Each clone keeps its
original's height above the hull mesh and its rotation, and mirror twins are placed together. The gap is in game
units after `--ship-scale`.

Mercury MK2 (November 2026): hull ×1.25, all 30 bow turrets back to size 1.0, closest pair 4.00:

![Mercury MK2 bow before and after](img/mk2-bow-layout.png)

(Top-down view: circles that overlap belong to turrets at different heights on the bow; the 3D gap is at least 4.0.)
