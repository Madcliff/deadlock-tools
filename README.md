# Deadlock Tools

Modding tools for **Battlestar Galactica: Deadlock**: edit ship bundles (turrets, carrier and munition slots, systems,
abilities), the ship / munition / research / commander tables in the level files, patch `Assembly-CSharp.dll`, and adjust
the game UI layouts for ships with expanded slot lists.
Everything is also available as a browser GUI. Uses Python and the standard library. Python 3.10 or newer is recommended for the editor.

Select a ship to edit its database values and bundle settings on the same page, or use **Load bundle…** to edit a bundle directly without loading a similarly named ship. **Compare with bundle…** shows the editable prefab settings and installed abilities side by side. **Save ship** writes the level record, bundle and weapon layout together. The **Level data ↔ bundle** table flags values the two files must share (hull, armour, slot counts) and fixes them with one click. Every tab and control is listed in [docs/gui-map.md](docs/gui-map.md).

Saving a bundle containing custom modules or turnaround repair actions also rebuilds the matching custom-module DLL and indexed repair shortcut assets into the work tree. The combined update starts from a clean DLL base kept in `work/.deadlock-tools/base/`; it backs up the replaced files to the backups folder (`work-backups/` beside the work folder by default, or the `backups` setting) and stages the DLL and any affected level assets, and does not install them into the game. A configured level-data folder with its matching `Managed/` references is required for this build.

The editor keeps changes in the configured work folder, mirroring `BSG_Data/`: level files at its root, ship bundles in `StreamingAssets/`, and the edited DLL in `Managed/`. A matching work copy takes priority over its configured source. Loading a bundle imports it into this tree.

## Standalone Windows build

Download `DeadlockTools-Windows-x64.zip` from the [GitHub releases](https://github.com/Madcliff/deadlock-tools/releases). Extract it to a writable folder and double-click `DeadlockTools.exe`. It opens the editor in your default browser and keeps its console open while the editor is running. Settings and staged outputs live beside the executable. For CLI use, run `DeadlockTools.exe --help` from PowerShell. The same release also includes `deadlock.pyz`, the rebuilt combined editor for systems with Python installed.

## Quick start

```bash
# once: tell the tools where your game files are (saved in deadlock.json)
python -m deadlock_tools config --levels ../BSG_Data --dll ../Assembly-CSharp.dll --bundles ../decrypted

python -m deadlock_tools gui                                     # the graphical editor
python -m deadlock_tools shipdb show "Colonial Mercury"
python -m deadlock_tools weapons colonialmercury --inspect
python -m deadlock_tools dll check
```

Run any tool with `-h` for its full help. After `pip install -e .`, `deadlock <tool> ...` works from anywhere.

## Guides

- [Unreleased changes](docs/release-notes-unreleased.md) lists the current working-tree additions; [release notes](docs/release-notes-0.6.0.md) cover the latest tagged release.
- [Editing and installing a mod](docs/workflows.md) covers the browser editor, file types, slot changes, DLL prompts, testing in game, and rollback.
- [Command-line reference](docs/cli-reference.md) covers all command families, common flags, and examples.
- [Technical findings](docs/technical-findings.md) records the DLL patches, bundle dependencies, carrier UI findings, and verification limits.
- [Custom ship modules](dll_modules/README.md) documents the experimental module DLL build and current bundle-serialization limitation.

## Tools

| Tool | What it does | Writes to |
|---|---|---|
| `config` | show / save the paths every tool uses | `deadlock.json` |
| `gui` | ship, bundle, DLL, and database editing in your browser (127.0.0.1 only) | configured work folder, mirroring `BSG_Data/` |
| `shipdb` | ship stats, veterancy, XP, munitions, research/blueprints, fleet limits, fleet icons, commanders, squadron hull | `work/BSG_Data/` |
| `weapons` | edit a ship bundle: clone/resize turrets, slots, vanilla systems, custom modules, abilities, and configurable turnaround repair actions | `work/BSG_Data/StreamingAssets/` |
| `dll` | patch `Assembly-CSharp.dll`: commander max rank, command-tree nodes, munition load, indexed repair-bay shortcuts; `check` / `revert` | `work/BSG_Data/Managed/` and `work/BSG_Data/` assets |
| `ui fix-overflows` | size expanded ship slot lists and enable scrolling for the commander skills list, construction, research, and battle panels | `work/BSG_Data/` |
| `xor` | decrypt / encrypt / check ship bundles | `work/.deadlock-tools/decrypted/`, `work/BSG_Data/StreamingAssets/` |

## Command-line inputs and outputs

- **Inputs** default to the paths in `deadlock.json`; `--levels`, `--dll`, `--bundles` override them. A bundle can be
  given by name (`colonialmercury`) and is looked up in the bundle folder. Encrypted bundles are decrypted automatically.
- **Outputs** keep the game's own file names, so a folder can be copied straight over the game's files:

  ```
  work/BSG_Data/                    level0 ... sharedassets9.assets
  work/BSG_Data/Managed/            Assembly-CSharp.dll
  work/BSG_Data/StreamingAssets/    ship bundles
  work/.deadlock-tools/decrypted/   decrypted .dec scratch files
  ```

- The editor's temporary staging folder is hidden inside the work folder; there is no separate output setting.
- **Edits stack.** Tools start from the matching file in `work/` if there is one, so separate runs add up;
  `--fresh` starts again from the configured source. `weapons` starts from the selected bundle each time. Include all desired prefab options when rebuilding from the same source.

The configured work folder is the single continuing edit location. Merge its `BSG_Data/` tree into the game installation when ready. The settings let you choose level data, ship bundles, the DLL, and work folder independently. See [Editing and installing a mod](docs/workflows.md).

The UI asset command uses UnityPy. Install the optional support with `python -m pip install '.[ui-assets]'`; the Windows standalone build and the combined editor (`deadlock.pyz`, which always bundles all its dependencies) include it. Run `python -m deadlock_tools ui fix-overflows` to stage commander skills scrolling, construction, research, battle, deployment, and ship-card layout corrections in `work/BSG_Data/`.

Back up the game's files before copying command-line results over them.

## Project layout

```
deadlock_tools/     the package (python -m deadlock_tools <tool>)
  __main__.py       command dispatcher
  config.py         settings file + output rules shared by every tool
  shipdb.py  weapons.py  dll.py  bundle_xor.py  editor.py (gui)
legacy/             old single-purpose scripts, superseded by the tools above (kept for reference)
scripts/build_pyz.py  builds dist/deadlock.pyz, the whole toolkit as one file
tests/              unit tests + byte-for-byte regression tests
```

## Tests

```bash
python -m unittest -v
```

The regression tests compare every tool's output with outputs recorded from the original scripts. They need game
files: copy `tests/regression_inputs.example.json` to `tests/regression_inputs.json` and fill in the paths (they are
skipped otherwise).

## Single-file version

`python scripts/build_pyz.py` writes `dist/deadlock.pyz`. Rebuild it after changing editor or tool sources so the combined editor stays current. Copy it anywhere and run `python deadlock.pyz gui` (or any
tool). Its `deadlock.json` and `work/` are kept next to the `.pyz`.

## Changes from the old scripts

| Before | Now |
|---|---|
| `python deadlock_shipdb.py set <levels> <dll> ...` (munition/blueprint/fleet commands: `<dll> <levels>`) | `python -m deadlock_tools shipdb set ...` with `--levels/--dll` from settings (the old positional form still works) |
| `patched_out/` relative to the current folder | `work/BSG_Data/` |
| `Assembly-CSharp.dll.patched` next to the DLL; separate runs did not combine | `work/BSG_Data/Managed/Assembly-CSharp.dll`, patches stack |
| `decrypted/`, `encrypted/` next to the script | `work/.deadlock-tools/decrypted/`, `work/BSG_Data/StreamingAssets/` |
| `deadlock_weapons.py SRC.dec DST --encrypt` | `weapons BUNDLE` (encrypted or not; output encrypted by default, `--no-encrypt` to keep it plain) |
| `patch_munition_load.py` | `dll patch --munition-load` |
| GUI settings in `~/.deadlock_ships.json`, tools embedded in `deadlock_editor.py` | one `deadlock.json`; the GUI uses the package directly |

## License

Deadlock Tools is licensed under the [GNU General Public License, version 2 only](LICENSE).
