# Command-line reference

Run commands from the project directory with `python -m deadlock_tools`, or use `deadlock` after an editable installation. Run any tool with `-h` for its flags and defaults.

## Settings and output

```bash
python -m deadlock_tools config --levels "/path/to/BSG_Data" --dll "/path/to/BSG_Data/Managed/Assembly-CSharp.dll" --bundles "/path/to/ship-bundles"
python -m deadlock_tools config
python -m deadlock_tools gui --no-browser
```

- `deadlock.json` shares paths between the GUI and CLI. `--levels`, `--dll`, and `--bundles` override the relevant paths for tools that accept those options.
- For commands that accept `-o DIR`, it selects an output root. The tool appends `levels/`, `dll/`, or `bundles/` as appropriate. Configure a separate output root for independent builds.
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
| `fleeticons [--factory FACTORY]` | List the fleet icon table. |
| `fleeticon TARGETS SOURCE [--factory FACTORY]` | Copy a prefab icon from a source to one or more comma-separated targets. |
| `commanders` | Show ranks, XP, leadership intervals, traits, and specialisations. |
| `setcommanders [--add N] [--xp-growth F] [--every-level]` | Extend the commander rank tables. Pair this with a DLL rank-cap patch. |
| `fixspecs [--revert-specs]` | Repair the commander specialisation lists or restore the shipped Tactician list. |
| `squadrons` | Show fighter count, hull pool, per-fighter hull, and loss thresholds. |
| `setsquadron NAME FIELD VALUE` | Set `HullFactor`, `MaxHullPoints`, or `FighterHull`. Match part of a name or use `all`. |

## Ship prefab bundles

Run `python -m deadlock_tools weapons BUNDLE [OPTIONS]`. Use a file path or a name from the bundle folder. Plain and encrypted UnityFS inputs are accepted. Saved output is encrypted by default. Use `--no-encrypt` for a plain copy.

| Task | Options |
|---|---|
| Inspect the prefab or list only its mounts | `--inspect`, `--list-mounts` |
| Duplicate or resize mounts | `--copies`, `--clone SELECTOR=N`, `--scale SELECTOR=S`, `--clone-scale` |
| Control turret placement | `--layout offset\|mid\|free`, `--offset`, `--gap`, `--min-gap`, `--no-mirror`, `--no-hull-follow` |
| Add carrier and munition controls | `--fighter-slots`, `--utility-slots`, `--munition-slots`, `--bay-offset`, `--icon-offset`, `--slot-parts`, `--split-squadrons`, `--squadron-groups` |
| Align bay types with database slot order or move a marker | `--align-launch-bays` with both slot totals, `--bay-icon SLOT=X,Y,Z` (repeatable) |
| Passive ship systems | `--hull-regen`, `--armour-regen`, `--munition-factory`, `--squadron-revive` |
| Micro-FTL | `--mini-jump`, `--jump-range`, `--jump-delay`, `--jump-duration`, `--jump-cooldown` |
| Predictive Augury | `--augury`, `--augury-uses` |
| Stealth | `--stealth ORION_BUNDLE`, `--stealth-battery`, `--stealth-start`, `--stealth-mesh` |
| Defender and Celestra support abilities | `--turnaround-repair-bay`, `--targeted-resupply`, `--deploy-plating`, `--technical-support`, `--marine-reinforcement` |
| Override support donor bundles | `--defender-donor PATH`, `--celestra-donor PATH` |
| Tune Deploy Plating | `--plating-supply`, `--plating-drone-load`, `--plating-facing-cap`, `--plating-rate` |
| Group abilities in the battle menu | `--ability-submenu` |

```bash
python -m deadlock_tools weapons colonialmercury --inspect
python -m deadlock_tools weapons colonialmercury --clone fore=1 --clone-scale 0.7 --mini-jump
python -m deadlock_tools weapons colonialmercury --hull-regen 0.2 --armour-regen
python -m deadlock_tools dll patch --armour-factor 1
```

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

## DLL patches

Run `python -m deadlock_tools dll check` to inspect the staged DLL or configured source. An explicit DLL path inspects that file directly.

| Patch option | Purpose | Revert option |
|---|---|---|
| `--armour-factor F` | Enable armour regeneration for flagged Hull Resin modules. | `--armour` |
| `--revive-turns T` | Rebuild destroyed squadrons on a flagged ship with Munition Factory. | `--revive` |
| `--max-rank N` | Set the commander cap. | `--max-rank` |
| `--command-nodes N` | Set the number of command nodes. Optionally set `--command-step` and `--command-first-rank`. | `--command-nodes` |
| `--munition-load` | Rebuild an existing save's munition list when it is shorter than the ship's new slot count. | `--munition-load` |

```bash
python -m deadlock_tools dll patch --armour-factor 1 --revive-turns 0
python -m deadlock_tools dll check
python -m deadlock_tools dll revert --revive
```

`dll revert` with no patch flags reverts all supported patches. The `dll armour`, `dll revive`, and `dll munition` subcommands also provide single-patch operations with `--check` and `--revert`. The tool checks for expected code patterns before patching.

## Bundle conversion

```bash
python -m deadlock_tools xor check colonialmercury
python -m deadlock_tools xor decrypt colonialmercury
python -m deadlock_tools xor encrypt work/decrypted/colonialmercury.dec
```

`xor` also accepts a folder for recursive conversion. Use `--work DIR` to choose the scratch root for decryption, `-o DIR` to choose the root for encrypted output, or `--key HEX` when you have a specific key to use. Decryption creates `.dec` files. Encryption drops the `.dec` suffix from the output name.
