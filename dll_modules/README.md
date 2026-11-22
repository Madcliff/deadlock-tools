# Custom ship modules

These C# sources can be merged into a candidate `Assembly-CSharp.dll` with
`scripts/build_custom_modules.sh`. Pass the configured game's `Managed`
directory, source DLL, and candidate output path. The script does not install
the DLL or edit bundles. Build against the same game version that will load
the candidate; do not ship the standalone compiled assembly, since Unity
resolves these MonoBehaviours from `Assembly-CSharp.dll`.

`CustomCommanderStatModifiers` is a static runtime helper, not a bundle
component. The commander editor creates unique ability IDs that encode one of
the native ship-stat calculation paths, a flat/percentage mode, and flagship
scope. The merge script injects the helper into FleetCommander bonus returns;
it reads the selected ID and bonus amount from the level DataTables. Saving a
custom commander ability builds the helper into the work-tree DLL while
preserving the current DLL as its input.

The four module classes and the indexed repair-slot helper compile and merge
into a candidate copy of the installed DLL:

- `CustomArmourRepairModule`: repairs armour once per battle tick. It has its
  own base rate, modifier rate, and `FEDNAJLFKEF` modifier-field selector.
- `CustomSquadronReplacementModule`: tracks each destroyed child squadron
  independently and restores it after its configured number of battle ticks.
- `CustomDroneResupplyModule`: adds a configured amount of plating reserve
  each battle tick, using an independently selected crew modifier. New modules
  default to 1 plating per tick (120 per 120-tick turn at a 1× modifier).
- `CustomMagazineCapacityModule`: scales each munition slot's starting load
  during battle setup and patches the per-ship maximum read by the HUD and
  resupply clamp. Vanilla missile-table capacities remain unchanged. The
  module has its own multiplier and crew modifier selector; new modules
  default to 10x.
- `CustomRepairBaySlots`: pairs each repair action with its own module, clones
  the native battle-panel slot widget for each bay, explicitly positions the
  extra buttons under layout groups, expands the slot row as needed, and assigns
  each button's indexed shortcut function.
- `CustomEmpPulseModule` and `CustomEmpPulseAction`: queue an enemy-only pulse
  for turn start, apply temporary subsystem damage and fighter stun, and destroy
  enemy missiles in range. Fighters also receive the game's native jamming
  flag, which applies the configured JammingPenalty evade reduction for a
  separate configurable duration. Active missiles come from `MissileEventProxy` (with
  an active-scene fallback); `BattleManager.NonShipDestructables` does not
  contain the game's active missile collection. The pulse reports its missile
  destruction count in battle.
- `CustomTargetedEmpModule` and `CustomTargetedEmpAction`: open a fresh native
  single-point PCM selector for each configurable detonation point (four by
  default), letting the player set each point and its height independently.
  The whole salvo is queued for turn start and shares one cooldown. Its editor
  fields include map range, blast radius, delay, subsystem effect, fighter
  stun duration, fighter evade-debuff duration, and charges per cooldown. Cancelling any selection cancels the
  incomplete salvo.
- `CustomInstantPlatingModule` and `CustomInstantPlatingAction`: queue a
  configurable plating restoration for turn start.

The ship editor shows each module's configured base rate and its derived rate
per turn. In the installed GOG `BattleSimulation`, the game defines 10 ticks
per second, 120 ticks per round, and a 12-second round. The editor uses those
values for its seconds conversion. Playback speed can change wall-clock time.
Squadron replacement time is shown at a 1x crew modifier rate, and the selected
crew-veterancy field can change the actual rate in play.

The Munition Factory component has no serialized work-rate field of its own.
Its speed is configured on the ship's `Armory.MunitionResupplyRange`, which the
editor exposes as damaged (`Min`) and full-effectiveness (`Max`) endpoints.
The editor displays each endpoint as a multiplier and derived munition-work
rate per turn, plus the approximate per-second equivalent. The configured
Mercury endpoints currently read 25 and 100.

An earlier custom-module candidate passed a startup smoke check through
managed assembly loading, `GameData::Awake`, the startup scene, and a request
for `battle_shared`. The indexed repair-slot and magazine-capacity candidate
was installed to the GOG game on 2026-11-21. The replaced DLL and Mercury
bundle are backed up at `.deadlock-backups/repair-bay-slots-install-20261121/`.
Steam was not modified. The new slot and magazine behaviors still need an
in-game battle check.

The bundle writer constructs per-class MonoScript and serialized MonoBehaviour
type records from the bundle's existing Unity 2018.4 header and the module's
scalar settings. The resulting Mercury bundle is installed to the GOG install's
`BSG_Data/StreamingAssets/colonialmercury` for a post-reboot test; the original
is backed up at `.deadlock-backups/custom-modules-install-20261121/colonialmercury`.
The generated bundle reopened successfully in the tool. Armour repair makes
Hull Resin positive again; squadron replacement clears the ShipInfo flag that
enabled the old Munition Factory revive patch. Keep the Munition Factory
component for ordinary resupply.

Drone resupply uses reflection to reach the game's private reserve field
because its public API only refills to full; that needs an exact-build guard
before it is safe to ship. The magazine hook is injected into
`ShipInfo.GetMunitionsSlotStatusData` and `ShipInfo.ResupplyMunitions`, in
addition to using `ChangeMunitionSlot` for the starting load. The combined
candidate is `work/candidates/Assembly-CSharp-repair-slots.dll`; ammo counts,
resupply, and save/load still need in-game checks.

Once attached to a bundle, the ship editor recognizes each custom module's
serialized settings: its base rate or multiplier, modifier rate, and named
crew-veterancy selector. These fields are validated against the supported
game's `FEDNAJLFKEF` enum. The Mercury drone module uses a base rate of 1
plating per battle tick.

The custom module writer avoids relying on generated MonoBehaviour header
nodes. This matters because the current UnityPy TypeTreeGenerator integration
has a reported issue where it can place `m_Script` three bytes early. See the
[UnityPy issue](https://github.com/K0lb3/UnityPy/issues/388).

The installed test DLL backup is at
`.deadlock-backups/modules-smoke-20261121/Assembly-CSharp.dll` relative to the
game installation directory.

The 2026-11-21 EMP targeting correction includes capital ships in the shared
EMP target query and replaces linked multi-point PCM input with sequential
independent 3D point selections. The 32,000 fleet-cap option is included in
skirmish PvP and co-op choices. The combined candidate was installed in both
the work overlay and GOG install; the prior DLL is backed up under
`.deadlock-backups/emp-3d-selector-fix-20261121/Managed/Assembly-CSharp.dll`.
The subsequent fighter-jamming update applies the native `IsJammed` flag to
enemy fighter targets, using the game's configured JammingPenalty evade value.
Stun and evade-jam durations are independent configurable tick fields. Both
default to 360 ticks (three turns), matching the vanilla Cylon Virus Mine's
jamming-field duration. The Mercury bundle was updated to serialize those settings. The installed DLL
SHA-256 is
`2bb49a653e2627ce5d2bf985dcd86214719b18b49bc79bdb7a3ddbc889cb6d06`, and the
bundle SHA-256 is
`c8e31fd5f78f491a02aefebffcec6aa0057e10df36c07abdf8b8b06a1e3c0cc0`.
The combined editor was rebuilt as `dist/deadlock.pyz` with SHA-256
`80e73a9270203089b3b3965137bae2a7906f060d9df57aa7c720860364e74572`.
In-game verification of the independent height picks and EMP damage remains
necessary.

For a manual one-type merge, the script takes
`TARGET SOURCE OUTPUT REFERENCES TYPE_NAME`. Each step writes a new DLL so the
input remains untouched. The build script performs the four merges in order
and writes only the requested candidate output.
