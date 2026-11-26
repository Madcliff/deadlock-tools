# Command-line reference

Run commands from the project directory with `python -m deadlock_tools`, or use `deadlock` after an editable installation. Run any tool with `-h` for its flags and defaults.

## Settings and output

```bash
python -m deadlock_tools config --levels "/path/to/BSG_Data" --dll "/path/to/BSG_Data/Managed/Assembly-CSharp.dll" --bundles "/path/to/ship-bundles"
python -m deadlock_tools config
python -m deadlock_tools gui --no-browser
```

- `deadlock.json` shares paths between the GUI and CLI. `--levels`, `--dll`, and `--bundles` override the relevant paths for tools that accept those options.
- The configured work folder is the continuing edit location. It mirrors `BSG_Data/`, with level files at the root, the DLL in `Managed/`, and ship bundles in `StreamingAssets/`. The editor has no separate output folder.
- Table and DLL edits build on a staged file of the same name where available. `--fresh` uses the configured source for the operation. A fresh write can replace the staged copy it targets.
- Bundle edits read the selected path or bundle name. Keep related prefab edits in a single invocation when rebuilding from the same source.
- Relative paths supplied on the command line are resolved from your working directory. Relative settings-file paths resolve from the project directory or the directory containing the single-file build.

## Ship database

Run `python -m deadlock_tools shipdb COMMAND [ARGS]`. Provide `--levels` with the complete level folder for normal play so the write reaches each applicable copy of the table. Use `--levels path/to/level0` to inspect one scene. Squadron data is read from an assets file such as `sharedassets9.assets` and can also be located by scanning the level folder.

| Command | Effect |
|---|---|
| `list` | List ships and key stats. |
| `show SHIP` | Show a ship's fields, advantages, penalties, and veterancy. Use the exact field names when editing. |
| `set SHIP FIELD VALUE` | Change a scalar field such as `ArmorFront` or `BuildCost`. |
| `vet [SHIP]` | Show XP requirements and rank bonuses. |
| `setvet SHIP RANK EFFECT VALUE` | Update an existing effect. |
| `addvet SHIP RANK EFFECT VALUE` | Add or replace an effect. |
| `removevet SHIP RANK EFFECT` | Remove an effect. Use `all` in place of the effect to clear the rank. |
| `copyvet TARGET SOURCE` | Replace the target ship's rank bonuses with the source ship's. |
| `setxp RANK XP` | Change the shared fleet crew XP requirement for a rank. |
| `munitions [NAME]` | List munition types or show one exact name. |
| `setmunition NAME FIELD VALUE [FIELD VALUE ...]` | Update values such as `MaxMissiles` and `StandardSalvoSize`. |
| `blueprints` | List campaign research unlocks. |
| `addblueprint SHIP [OPTIONS]` | Add a research unlock and make the ship buildable. Use `--like`, `--cost`, `--campaigns`, `--mission`, `--label`, `--list`, `--unlocked`, or `--keep-build-stats` to control the result. |
| `fleetinfo` | Read campaign fleet limits. |
| `setfleetinfo FIELD VALUE [FIELD VALUE ...]` | Set limits such as `MaxShips` and `StartingFleetPointCap`. |
| `skirmishinfo` | Show the ships allowed per skirmish / multiplayer fleet (`SkirmishDataTables.maxShips` in level6/7/8/9; stock 10, consoles 8). |
| `setskirmishinfo maxShips N` | Set it (1-99; also `maxShipsConsole`). See [game/skirmish.md](game/skirmish.md). |
| `fleeticons [--factory FACTORY]` | List the fleet icon table. |
| `fleeticon TARGETS SOURCE [--factory FACTORY] [--add-missing]` | Copy a prefab reference to one or more targets. `--add-missing` creates absent factory entries for cloned ships. |
| `commanders` | Show ranks, XP, leadership intervals, traits, and specialisations. |
| `setcommanders [--add N] [--xp-growth F] [--every-level]` | Extend the commander rank tables. Pair this with a DLL rank-cap patch. |
| `fixspecs [--revert-specs]` | Repair the commander specialisation lists or restore the shipped Tactician list. |
| `squadrons [NAME]` | List every squadron (fighters, hull pool, per-fighter hull, max speed, loss thresholds), or show every editable field of one. |
| `setsquadron NAME FIELD VALUE ...` | Set `MaxShipCount` (1–16; resizes thresholds and formation), `ShipLoss 2,4,8,...`, `HullFactor`, `FighterHull`, or any squadron field (`MaxVelocity`, `MaxHullPoints`, `RetreatDistance`, `launchVelocity` ...). Match part of a name or use `all`. See [docs/game/squadrons.md](game/squadrons.md). |
| `squadronabilities [SQUADRON]` | Abilities on each squadron prefab (menu and slot), and which squadrons each ability can be copied from. Needs the optional UnityPy support and the game's `globalgamemanagers.assets` (`--game-data`) and `Managed` folder (`--managed`); both default to the levels folder. |
| `setsquadronability SQUADRON add\|remove ABILITY [--donor SQUADRON]` | Add an ability (`board`, `disarm`, `boost_firewalls`, `hack`, `jamming`, `chaff`, `paint_target`, `bomb`, `fire_missile`) by copying it and its helper component from a donor prefab into the squadron's Abilities submenu, or remove one. `setsquadronability SQUADRON set ABILITY field=value ...` changes an ability's settings (e.g. `set chaff maxStock=20`; `squadronabilities` lists them). `stealth` is built in instead of copied: `--battery N` (default 100), `--cooldown TURNS` (default 1), `--stealth-active`; it needs a custom-module DLL with `CustomStealthFallback` (any build from now on), and the command says when the target DLL lacks it. Keeps the table's info-panel `AbilityIDs` in step; Fire munitions also gives a squadron without munitions the donor's. Writes `sharedassets9.assets` and the level files. |
| `addsquadron DONOR NewPrefabNameSquadron [--table-name N] [--display-name T] [--heading T] [--description T] [--type-id N]` | New squadron type: copies the donor prefab in `sharedassets9.assets`, its `SquadronDataTable` entry (all six copies), registers the prefab in level9's `SquadronFactory`, adds optional localisation strings, and adds a squadron enum member to the DLL (written to the output's `dll`/`Managed` folder). |
| `setsquadronbays PREFAB [--launch-like PREFAB]` | Add a squadron type to the launch bays of every carrier that may carry it, on each bay that takes the `--launch-like` squadron (default: the stock squadron its name contains). Writes the carrier bundles to the output's `bundles` folder. `addsquadron` does this for the donor automatically. |
| `addmunition DONOR "New name" [--display-name T] [--heading T] [--description T] [--munition-id N] [--no-research]` | New munition type: copies the donor's missile prefab (new `missileType`), its munition and icon table rows (six levels), its `MissileFactory` entry, optionally its research entries and new text, and adds a munition enum member to the DLL (output `dll` folder). Edit its stats with `setmunition`. See [docs/game/munitions.md](game/munitions.md). |
| `jamming` / `setjamming global\|squadron ID\|ship ID speed=.. evade=.. accuracy=..` | Show or set jamming penalties: `global` edits `DataTables.JammingPenalty` in level5/85/9; `squadron` / `ship` writes a per-jammer override to `deadlock-jamming.txt` in the output bundles folder (value `-` = back to global; needs the custom-module DLL's CustomJamming). |
| `munitionstats MUNITION` / `setmunitionstats MUNITION STAT=VALUE ...` | Show or set a munition's combat stats on its missile prefab: `hull`, `armour`, `subsystem`, `blast`, `spread_damage`, `spread_count`, `irradiate`, `irradiate_turns`, `irradiate_percent`, `speed`, `range`, `accuracy` (guided only), `turn_rate`. The table's listed values follow. |
| `squadronmunitions [TYPE]` / `setsquadronmunitions TYPE ID[,ID...]\|none` | Show or set the munition types a squadron type carries (slot order). Munitions must match the squadron's faction. The fighter's `MunitionSlots` in the ship database follows. When another squadron type shares the fighter with a different count, the squadron gets its own copy of the fighter (ship row, factory entries, bundle copy to the output's `bundles` folder, ShipType enum member to its `dll` folder). |
| `squadronresearch [TYPE]` / `setsquadronresearch TYPE CAMPAIGNS [cost=RP] [mission=ID] [label=T] [unlocked=yes\|no]` / `setsquadronresearch TYPE CAMPAIGNS remove` | Show or edit which campaigns research a squadron type (`CAMPAIGNS`: `0,2,5` or `all`; 0 Deadlock … 5 Armistice), its RP cost, the story mission required first (-1 = none), its node label and whether it starts unlocked. Edits both research tables (level5, level85). Only the named keys change on an existing entry. |
| `allocations [CARRIER]` | Squadron types each carrier may put in its fighter and utility slots (from `SquadronDataTable`). |
| `setallocation TYPE allow\|deny CARRIER[,CARRIER]` | Allow or deny a squadron type on capital ships (edits its `shipExclusionList` in all six level copies). `setallocation TYPE FIELD VALUE ...` edits `IsUtility`, `Faction`, `DLCRequired`, `PointValue`, costs, `XP`, `DefaultGroup`, `MoveOnLaunch`, list flags. |

## Ship prefab bundles

Run `python -m deadlock_tools weapons BUNDLE [OPTIONS]`. Use a file path or a name from the bundle folder. Plain and encrypted UnityFS inputs are accepted. Saved output is encrypted by default. Use `--no-encrypt` for a plain copy.

| Task | Options |
|---|---|
| Inspect the prefab or list only its mounts | `--inspect`, `--list-mounts` |
| Duplicate or resize mounts | `--copies`, `--clone SELECTOR=N`, `--scale SELECTOR=S`, `--clone-scale` |
| Respace cloned mounts | `--respace SELECTOR=GAP` (move existing clones so every turret is at least GAP game units apart; originals stay put) |
| Scale the whole ship | `--ship-scale S` (absolute, 1 = stock; turrets keep their size). See [docs/game/ship-scale.md](game/ship-scale.md) |
| Control turret placement | `--layout offset\|mid\|free`, `--offset`, `--gap`, `--min-gap`, `--no-mirror`, `--no-hull-follow` |
| Add carrier and munition controls | `--fighter-slots`, `--utility-slots`, `--munition-slots`, `--bay-offset`, `--icon-offset`, `--slot-parts`, `--split-squadrons`, `--squadron-groups` (submenu sizes in slot order, any number of groups; they must add up to FighterSlots + UtilityFighterSlots and never change those counts) |
| Edit launch bays, munition launchers, remove slots | `--list-bays` (ids), `--bay-layout FILE.json`, `--realign-bays` (cloned bays as mirrored port/starboard pairs). See [docs/game/launch-bays.md](game/launch-bays.md) |
| Align bay types with database slot order or move a marker | `--align-launch-bays` with both slot totals, `--bay-icon SLOT=X,Y,Z` (repeatable) |
| Passive ship systems | `--hull-regen` (positive, hull only), `--munition-factory` |
| Flak field | `--flak FIELD=VALUE` (repeatable), editing the ship's FlakField: `BaseDamagePerTick.HullDamage` / `ArmourDamage` / `SubSystemDamage` / `SubsystemSpreadCount` / `SubsystemSpreadDamage` / `AppliesIrradiatedStatus` / `IrradiatedTurns` / `IrradiatedPercentage` / `Range`, `FlakRange.Min` / `FlakRange.Max`, `FlakAreaLength`, `FlakAreaRadius`, `FlakExplosionsPerTick`. Unknown fields, negative values and a Max below Min are refused; a Min above Max raises Max to Min + 1. Only ships that already have flak. `--inspect` lists the values. See [game/flak.md](game/flak.md) |
| Jamming (Cylon Wardriver's Jamming mode) | `--jamming`, `--jam-range` (default 500), `--jam-missile-chance` (0-1, default 0.6); run again to change the settings. `--jam-speed`, `--jam-evade`, `--jam-accuracy` (number or `global`): the penalties this ship's jamming gives, written to `deadlock-jamming.txt` next to the output bundle (needs the custom-module DLL's CustomJamming) |
| Custom ship modules | `--custom-armour-repair`, `--custom-squadron-replacement`, `--custom-drone-resupply`, `--custom-magazine-capacity`, `--custom-emp-pulse`, `--custom-targeted-emp` |
| Module subsystems (which subsystems drive Hull Resin, Munition Factory and the custom modules) | `--module-subsystem MODULE:SOURCE:MIN:MAX` (repeatable; replaces the ship's list), `--clear-module-subsystems`. `--inspect` lists them. See [docs/game/subsystems.md](game/subsystems.md#module-subsystems) |
| Subsystem crew modifiers | `--subsystem-modifier STAT:SOURCE:MIN:MAX` (repeatable; replaces the ship's list), `--clear-subsystem-modifiers`. `--inspect` lists them. See [docs/game/subsystems.md](game/subsystems.md) |
| Micro-FTL | `--mini-jump`, `--jump-range`, `--jump-delay`, `--jump-duration`, `--jump-cooldown` |
| Predictive Augury | `--augury`, `--augury-uses` |
| Stealth | `--stealth ORION_BUNDLE`, `--stealth-battery`, `--stealth-start`, `--stealth-mesh` |
| Defender and Celestra support abilities | `--turnaround-repair-bay`, `--targeted-resupply`, `--deploy-plating`, `--technical-support`, `--marine-reinforcement` |
| Override support donor bundles | `--defender-donor PATH`, `--celestra-donor PATH` (default: `colonialdefender` / `colonialcelestra` from the configured bundle folder, then the work folder) |
| Tune Deploy Plating | `--plating-supply`, `--plating-drone-load`, `--plating-facing-cap`, `--plating-rate` |
| Group abilities in the battle menu | `--ability-submenu` |

```bash
python -m deadlock_tools weapons colonialmercury --inspect
python -m deadlock_tools weapons colonialmercury --clone fore=1 --clone-scale 0.7 --mini-jump
python -m deadlock_tools weapons colonialmercury --hull-regen 0.2 --custom-armour-repair
python -m deadlock_tools weapons colonialmercury --custom-targeted-emp --ability-submenu
python -m deadlock_tools weapons colonialmercurymk2 --subsystem-modifier squadron_repair:hangar:0:0.5 --subsystem-modifier 201::1:2
```

In `--subsystem-modifier`, STAT is a crew-stat number or name (`102`, `squadron_repair`) and SOURCE is `0`–`6` or a
subsystem name (`hangar`, `Tech Bay`). An empty SOURCE means the stat's own subsystem (201 → Armoury). MIN is the bonus
at 0 source health and MAX the bonus at full health, in veterancy `BonusAmount` units (0.5 = +50% for percentage stats).

The last two commands are a separate recipe. Combine the desired prefab options in one `weapons` call to keep both sets of edits when using the same input. Micro-FTL needs a compatible prefab, including an FTL effect and Tech Lab. Stealth needs the Orion bundle as a donor. The tool reports missing components and radial-menu capacity errors.

### Support settings

| Ability | Options |
|---|---|
| Turnaround repair | `--repair-bay-fraction` (0–1), `--repair-bay-relaunch-offset X,Y,Z` |
| Targeted resupply | `--resupply-range`, `--resupply-swap-time`, `--resupply-cooldown` |
| Deploy Plating | `--plating-range`, `--plating-supply`, `--plating-drone-load`, `--plating-facing-cap`, `--plating-rate` |
| Technical support | `--technical-support-range`, `--technical-support-spare-parts`, `--technical-support-parts-rate`, `--technical-support-boost`, `--technical-support-resupply-seconds`, `--technical-support-minimum-repair`, `--technical-support-unlimited-parts on/off` |
| Marine reinforcement | `--marines-per-raptor` |

Supply only the options you want to change. When adding an ability to a new bundle, include its ability flag. Use `--inspect` to read the settings back.

Custom module flags append the matching MonoScript and MonoBehaviour records to the ship bundle. Armour repair clears Hull Resin's negative-rate armour flag and preserves positive hull repair. Squadron replacement clears the `ShipInfo` flag used by the legacy Munition Factory revive patch while keeping the Munition Factory itself for ordinary resupply. The matching custom-module DLL must be installed before launching the game. Drone resupply defaults to 1 plating per battle tick (120 per 120-tick turn at the base rate); ships can configure their own rate in the editor.

Experimental EMP adds a battle action and configurable ship module. Activating it queues the pulse for the start of the next turn; `detonationDelayTicks` adds a configurable delay after that event (default 0). The default range is 1,000 units and cooldown is one turn. It reduces enemy subsystem maximum health by 8 for 240 ticks, stuns enemy squadrons for 360 ticks (`disableSquadronTicks`), applies the game's native jamming evade penalty for 360 ticks (`fighterEvadeDebuffTicks`), and immediately destroys hostile missiles in range. Its target scan includes child fighter ships, which the game's regular radius query omits. The detonation notification reports fighter squadrons stunned, fighters jammed, and missiles destroyed. The 360-tick duration matches the vanilla Cylon Virus Mine's default jamming field; durations remain independently configurable. Team and faction checks exclude friendly ships and missiles. It does not alter munition capacity or stockpiles.

Instant Plating also queues its effect: activation applies the configured extra armour at the start of the next turn, with `activationDelayTicks` as an optional additional delay (default 0). Both abilities can be cancelled by activating the queued action again before the turn starts; once the start event occurs, the delay/effect cannot be cancelled. Both delay settings are simulation ticks: 10 ticks per second and 120 ticks per 12-second turn. The EMP fighter-stun duration remains independently configurable.

Use `--turnaround-repair-actions N` to add or reconcile 1–8 independent turnaround repair bays. The tool writes one
repair module and one Fighters action per bay; the matching DLL creates a launch-slot button per module and assigns
indexed shortcuts. To install the ability on a new bundle, also pass `--turnaround-repair-bay` (the Defender donor is picked automatically).

## UI layout

Run `python -m deadlock_tools ui fix-overflows` to stage Unity UI layout fixes in the configured work tree. For the commander detail panel, it adds a vertical `ScrollRect` to `SkillsWidgetList`, links the existing list component, and clips overflow at the content panel. This allows the skill entries to scroll while the commander profile fields remain fixed. The command
sets ship slot lists to resize vertically as their contents gain rows, wraps ship-card icons into two rows, and makes
the battle ship squadron panel tall enough for two rows. It edits `level5`, `level85`, `level9`, and
`sharedassets5.assets` under `work/BSG_Data/`.
Python users need the optional UnityPy support: `python -m pip install '.[ui-assets]'`. The Windows standalone archive
includes this support.

## DLL patches

Run `python -m deadlock_tools dll check` to inspect the staged DLL or configured source. An explicit DLL path inspects that file directly.

| Patch option | Purpose | Revert option |
|---|---|---|
| `--max-rank N` | Set the commander cap. | `--max-rank` |
| `--command-nodes N` | Set the number of command nodes. Optionally set `--command-step` and `--command-first-rank`. | `--command-nodes` |
| `--munition-load` | Rebuild an existing save's munition list when it is shorter than the ship's new slot count. | `--munition-load` |
| `--skirmish-ships N` | Ships per skirmish / multiplayer fleet (1–99), overriding the level data's `maxShips` (stock 10). Written to `StreamingAssets/deadlock-fleet.txt`, read by the custom-module DLL. | `--skirmish-ships` alone |
| `--fleet-caps A,B` | Extra skirmish PvP / co-op fleet-point cap choices (default `32000,64000`; `none` for none). The largest is also the fleet save limit. Same file. | `--fleet-caps` alone |
| `--campaign-ships N` | Capital ships a campaign fleet takes into battle (1–99), overriding `FleetInfo.MaxShips` (stock 7). The single support slot is fixed by the game. Same file. | `--campaign-ships` alone |
| `--repair-bay-actions N` | Assign indexed shortcuts and stage matching keyboard/localization rows in `level3` and `resources.assets` (1–16). The custom-module DLL adds the matching launch-slot widgets. | `--repair-bay-shortcuts` |

```bash
python -m deadlock_tools dll patch --max-rank 20 --command-nodes 10
python -m deadlock_tools dll patch --repair-bay-actions 2 --levels /path/to/BSG_Data
python -m deadlock_tools dll check
python -m deadlock_tools dll revert --munition-load
python -m deadlock_tools dll revert --repair-bay-shortcuts --levels /path/to/BSG_Data
```

The legacy armour-regeneration (`--armour-factor`) and squadron-revive (`--revive-turns`) patches were removed
completely, including their check and revert. Use the custom armour repair and squadron replacement modules. To get rid
of the old patches, rebuild from an unpatched DLL. The editor's custom-module build always starts from
`work/.deadlock-tools/base/Assembly-CSharp.dll`, so that file must be a clean copy.

`dll revert` with no patch flags reverts all supported patches. The `dll armour`, `dll revive`, and `dll munition` subcommands also provide single-patch operations with `--check` and `--revert`. Repair shortcut changes also stage the relevant key mappings and descriptions. The tool checks for expected code patterns before patching.

## Bundle conversion

```bash
python -m deadlock_tools xor check colonialmercury
python -m deadlock_tools xor decrypt colonialmercury
python -m deadlock_tools xor encrypt work/.deadlock-tools/decrypted/colonialmercury.dec
```

`xor` also accepts a folder for recursive conversion. Conversion uses the configured work tree: decrypted scratch files go under `work/.deadlock-tools/decrypted/`, while encrypted bundles are written into `work/BSG_Data/StreamingAssets/`. Use `--key HEX` when you have a specific key to use. Decryption creates `.dec` files. Encryption drops the `.dec` suffix from the output name.
