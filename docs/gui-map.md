# GUI functionality map

Where every feature of the browser editor (`python -m deadlock_tools gui`) lives, which server endpoint it calls, and
which command-line tool does the work. Each tab owns one subject, and each tab has **one** button that writes its
subject. Read-only actions (inspect, compare, check) never write.

The header holds the tab bar and **Start from the originals**. That checkbox makes reads and writes start from the
configured source files instead of the work-tree copies, on every tab that edits level data. **Save ship** and
**Discard changes** appear in the header only on the Ships tab.

## Ships

One page per ship that combines the level ship record and the ship bundle (prefab). **Save ship** writes everything
that changed in one go:

| Change | Written by | Endpoint → tool |
|---|---|---|
| Level record fields, research entries, veterancy | level files in `work/BSG_Data/` | `/api/apply` (in-process `shipdb` writer) |
| Bundle prefab fields (hull, armour, subsystems, ability settings) | `work/BSG_Data/StreamingAssets/<bundle>` | `/api/bundle/run` → `weapons --prefab-fields` |
| Bundle options (slots, systems, custom modules, abilities, output) | same bundle | `/api/bundle/run` → `weapons --<option>` |
| Weapon layout (Weapon layout view) | same bundle | `/api/bundle/run` → `weapons --mount-layout` |
| Launch bays, munition launchers, slot removals (Weapon layout view) | same bundle; level slot counts as level edits | `/api/bundle/run` → `weapons --bay-layout` |
| Custom-module DLL, repair-shortcut assets | `work/BSG_Data/Managed/`, `level3`, `resources.assets` | `/api/editor/update` → `dll patch` + module build |

The bundle is written once per save. Prerequisites such as turnaround-repair shortcuts are checked first. Under
**Saving the bundle**, *Save over the opened bundle file* writes over the source file after copying it to the backups folder.
Without it, the save goes into the work tree. **Save ship** reloads the saved bundle and retries a failed DLL/UI
update against that copy. Level edits are staged and verified across every affected file before they are
promoted to the work tree. If level validation fails, the editor keeps those edits pending with the error visible.
**Discard changes** throws away pending level, bundle and layout edits.
To add turnaround repair bays, set **Total turnaround repair bays** under **Installed Abilities → Turnaround Repair Bay**.
The editor creates the paired modules and actions for **Fighters → Repair Bay**.
Use **Group existing bays under Fighters → Repair Bay** and save to move the existing bays with their current total.

| Area | Controls | Endpoint → tool |
|---|---|---|
| Sidebar | Search, **Load bundle…**, **Copy and add a ship…**, **Ship database tools** (opens Database → Ships) | `/api/ships`, `/api/bundle/import`, `/api/ships/clone` |
| Bundle file | path, **Load bundle…**, configured-bundle list, **Compare with bundle…**, **Choose ship folder…**, **Browse folders…**, **Inspect current bundle** | `/api/bundle/import`, `/api/bundle/stats`, `/api/bundle/run` (inspect only) → `weapons --inspect` |
| Decrypt / encrypt | Decrypt, Encrypt, Check, key override | `/api/xor` → `xor` |
| **Level data ↔ bundle** | every value both files hold, side by side, with ✓/✗, per-row **Use level** / **Use bundle**, and *make all match* buttons; *Keep hull & armour linked while editing* | none (stages edits for Save ship); see [game/bundle-level-agreement.md](game/bundle-level-agreement.md) |
| Field groups | level fields; bundle hull & armour and subsystems inside *Hull & armour*; bundle slot controls inside *Carrier & munition slots* | staged for Save ship |
| Bundle option cards | Ship systems, Custom ship modules (including Armour Repair Drones), and Abilities (including Jamming battery settings, Hull Repair Drones, and RSpam AI ship priorities), Weapons, Output | staged for Save ship |
| Drone Resupply | Under **Installed Abilities → Custom drone resupply**, use **Resupply Hull Repair Drones** and **Resupply Armour Repair Drones** independently. | staged for Save ship |
| Flak field | top and side diagram of the zone(s) against the hull (largest hull mesh's bounds, ship scale applied) with drag handles for distance, length and radius; one input per FlakField field with help text; per-turn damage readout at full and 0 Armory health; zone size; Armory FlakDamageRange; stock ships table | `/api/bundle/stats` (read, `flak`); staged for Save ship → `weapons --flak FIELD=VALUE …` |
| Module subsystems | one row per driver: module, driving subsystem, % at 0 and at full health; per-module total and stock driver; **Add subsystem**, **Remove** | `/api/bundle/stats` (read); staged for Save ship → `weapons --module-subsystem … / --clear-module-subsystems` |
| Crew bonuses from subsystem health | one row per modifier: crew stat, source subsystem, bonus at 0 and at full health, effect note; **Add modifier**, **Remove** | `/api/bundle/stats` (read, with the stat catalog); staged for Save ship → `weapons --subsystem-modifier … / --clear-subsystem-modifiers` |
| Research, veterancy, advantages | per-ship research rows and veterancy ranks | staged for Save ship |
| Ship localisation | opens the Localisation tab filtered to the ship | |
| Weapon layout view | **Edit** layer (Turrets / Launch bays / Munition launchers), projection, drag the selected layer's objects, **Reload** | `/api/bundle/layout` (read, includes `bays.read_layout`); staged for Save ship |
| — Turrets | add/clone/reset turrets, weapon, quadrant, angles, mesh scale | `weapons --mount-layout` |
| — Change all … to … in … | swap every turret of one weapon type (with counts) to another, in all quadrants or one | staged as layout edits → `weapons --mount-layout`; CLI equivalent `weapons --swap-weapon` |
| — Launch bays | name, slot, can launch / can dock, dock range, local position and quaternion rotation, squadron types, and editable launch path points with add/remove controls | `weapons --bay-layout` (`bays`) |
| — Munition launchers | position, launch points (add/remove), **Remove launcher** (the last one stays while the ship has munition slots) | `weapons --bay-layout` (`launchers`, `removeLaunchers`) |
| — Realign launch bays | lays the cloned bays out as mirrored pairs in slot order and renames them (staged; review, then Save ship) | `/api/bays/realign` → `bays.realign_spec`; saved as `weapons --bay-layout` |
| — Squadron slots / Munition slots tables | **Remove slot** / **Remove** per row (toggle back with **Keep**); lowers FighterSlots / UtilityFighterSlots / MunitionSlots as level edits | `weapons --bay-layout` (`removeSlots`, `removeMunitions`) |

## DLL

Patches that are not tied to one ship or to the commander tree.

| Control | Endpoint → tool |
|---|---|
| Current DLL (status of every patch) | `/api/dll/check` → `dll check` |
| Repair action shortcuts, Munition load fix, Start from the original DLL → **Patch DLL** | `/api/dll/patch` → `dll patch --repair-bay-actions/--munition-load [--fresh]` |
| Fleet limits: ships per skirmish fleet, extra fleet caps, capital ships per campaign fleet → **Save fleet limits** / **Reset to defaults** | `/api/dll/skirmish`, `/api/dll/skirmish/save` → `deadlock-fleet.txt` (same as `dll patch/revert --skirmish-ships --fleet-caps --campaign-ships`) |
| **Revert ticked patches** (munition load, repair shortcuts) | `/api/dll/revert` → `dll revert --munition-load/--repair-bay-shortcuts` |

Commander rank and node patches are on the Commanders tab. The custom-module and commander DLL code is rebuilt
automatically by Save ship and by Save commander tree.

## Commanders

Everything about commanders: level tables and the DLL limits that must match them.

| Card | Controls | Endpoint → tool |
|---|---|---|
| Ranks & command tree | level rank count vs DLL cap and node status (warns on mismatch); ranks to add, XP growth, fleet-size trait every rank; max rank (follows the rank count), command nodes, step, first rank → **Save ranks & command tree**; **Restore DLL defaults** | `/api/tools/run setcommanders` → `shipdb setcommanders`, then `/api/dll/patch` → `dll patch --max-rank --command-nodes …`; `/api/dll/revert` → `dll revert --max-rank --command-nodes` |
| Rank, XP and leadership table | expands to the read-only listing | `/api/tools/run commanders` → `shipdb commanders` |
| Create a commander ability | stat, flat/percentage, bonus, branch, text | staged for Save commander tree |
| Tree preview + subsystem choices | add/remove/reorder choices; **Reset lists: each subsystem’s own three skills**; **Restore the game’s original lists**; **Restore loaded tree mapping** | the two reset buttons write immediately: `/api/tools/run fixspecs` → `shipdb fixspecs [--revert-specs]` |
| Ability effect values | bonus, unlock rank, display style, flagship, localisation IDs | staged for Save commander tree |
| **Save commander tree** | writes tables, new abilities, localisation, unlock-rank DLL code | `/api/commanders/save` |

## Squadrons

| View | Save button | Endpoint |
|---|---|---|
| Squadron prefabs (fighter count, hull pool, loss thresholds, …) | Save squadron changes | `/api/squadrons`, `/api/squadrons/save` |
| — Abilities card: one collapsible box per ability (Remove / Keep in its header) with its settings (chaff supply, ranges, chances …), **Remove** / **Keep** per ability, **Add ability** (ability + donor squadron; Stealth: battery, cooldown, active at battle start) | Save squadron changes | `/api/squadrons/save` (`abilities`) → `shipdb setsquadronability` |
| — Munitions (in the Abilities card): add (own faction only), move up, remove | Save squadron changes | `/api/squadrons/save` (`munitions`) → `shipdb setsquadronmunitions` |
| — Research card: per campaign researchable, cost, mission, label, unlocked at start | Save squadron changes | `/api/squadrons/save` (`research`) → `shipdb setsquadronresearch` |
| — Launch bays card: launch from the same bays as (squadron), **Add to launch bays** (immediate) | writes carrier bundles | `/api/squadrons/bays` → `shipdb setsquadronbays` |
| **Copy and add a squadron type…** (donor prefab, prefab name, table name, info-panel name, blueprint heading/description, copy the donor's research) | Create squadron type (immediate) | `/api/squadrons/create` → `shipdb addsquadron` |
| Carrier allocations (which squadrons each carrier may field) | Save allocations | `/api/allocations`, `/api/allocations/save` |
| Jamming effects (global speed / evasion / accuracy penalty; per squadron type or ship overrides) | Save jamming effects | `/api/jamming`, `/api/jamming/save` → `shipdb setjamming` |

## Database

The remaining `shipdb` table commands, grouped by subject: Ships (list, inspect, veterancy, XP per rank, copy
veterancy), Munitions, Research (list, bulk add), Fleet limits (campaign fleet limits; skirmish fleet size: Show / Change skirmish fleet size → `shipdb skirmishinfo` / `setskirmishinfo`), Fleet map icons, and Squadrons (bulk hull factor). Write
forms show current values first and have one **Run and save** button. Endpoint: `/api/tools`, `/api/tools/run` →
`shipdb <command>`. The commander commands (`commanders`, `setcommanders`, `fixspecs`) are on the Commanders tab.

## Localisation

Search, edit and add strings in `resources.assets`: **Load file**, **Add string**, **Save string**. Endpoint:
`/api/localisation`.

## Armaments

Weapon and munition definitions in the level data: edit, clone, **Save weapon** / **Save munition** / **Save clone**. Munitions: **Combat** section (damage, blast, irradiation, speed, range, accuracy, turn rate; **Save combat stats** → `/api/munitions/combat/save` → `shipdb setmunitionstats`), **Copy as a new munition…** (name, displayed name, blueprint heading/description, researchable like the donor) → `/api/munitions/create` → `shipdb addmunition`.
Endpoint: `/api/armaments`.

## Settings

Level-data folder, ship-bundle folder, DLL file and work folder: **Save and load**. Endpoint: `/api/config`. Saved in
`deadlock.json`.

## CLI features with no GUI control

None. When you add a CLI option, add its control to the tab that owns its subject (table above) and update this map.

## Automatic, no control needed

- **ShipType enum members** (`ship_types.py`): every DLL rebuild (Save ship's custom-module update, DLL tab patches started
  from the original) puts back the enum members that copied ships need. Without them, adding such a ship to a fleet throws
  `KeyNotFoundException`.

## Library code not yet reachable from the CLI or the GUI

- `subsystem_modifiers.py` (subsystem crew modifiers in a bundle; the DLL side `CustomSubsystemModifiers` is already
  merged by the custom-module build) and `trench.py` (bow guns in the mouth trench, made for the Mercury MK2). Both have
  tests. When they get CLI options, add their controls to the Ships tab bundle cards and the weapon layout view.
