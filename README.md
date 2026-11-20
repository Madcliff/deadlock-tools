# Deadlock Tools

Modding tools for **Battlestar Galactica: Deadlock**: edit ship bundles (turrets, carrier and munition slots, systems,
abilities), the ship / munition / research / commander tables in the level files, and patch `Assembly-CSharp.dll`.
Everything is also available as a browser GUI. Uses Python and the standard library. Python 3.10 or newer is recommended for the editor.

Select a ship to edit its stats, bundle slots, weapons, systems, and abilities on the same page. Use **Bundle file** to inspect or convert the prefab. The editor selects a matching bundle from the configured folder when it finds one.

## Standalone Windows build

Download `DeadlockTools-Windows-x64.zip` from the [GitHub releases](https://github.com/Madcliff/deadlock-tools/releases). Extract it to a writable folder and double-click `DeadlockTools.exe`. It opens the editor in your default browser and keeps its console open while the editor is running. Settings and staged outputs live beside the executable. For CLI use, run `DeadlockTools.exe --help` from PowerShell.

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

- [Editing and installing a mod](docs/workflows.md) covers the browser editor, file types, slot changes, DLL prompts, testing in game, and rollback.
- [Command-line reference](docs/cli-reference.md) covers all command families, common flags, and examples.
- [Technical findings](docs/technical-findings.md) records the DLL patches, bundle dependencies, carrier UI findings, and verification limits.

## Tools

| Tool | What it does | Writes to |
|---|---|---|
| `config` | show / save the paths every tool uses | `deadlock.json` |
| `gui` | all of the below as forms in your browser (127.0.0.1 only) | same places as the tools |
| `shipdb` | ship stats, veterancy, XP, munitions, research/blueprints, fleet limits, fleet icons, commanders, squadron hull | `out/levels/` |
| `weapons` | edit a ship bundle: clone/resize turrets, slots, hull/armour regen, munition factory, squadron revive, micro-FTL, augury, stealth | `out/bundles/` |
| `dll` | patch `Assembly-CSharp.dll`: armour regen, squadron revive, commander max rank, command-tree nodes, munition load; `check` / `revert` | `out/dll/` |
| `xor` | decrypt / encrypt / check ship bundles | `work/decrypted/`, `out/bundles/` |

## How inputs and outputs work (the same for every tool)

- **Inputs** default to the paths in `deadlock.json`; `--levels`, `--dll`, `--bundles` override them. A bundle can be
  given by name (`colonialmercury`) and is looked up in the bundle folder. Encrypted bundles are decrypted automatically.
- **Outputs** keep the game's own file names, so a folder can be copied straight over the game's files:

  ```
  out/levels/    level0 ... sharedassets9.assets      -> copy over BSG_Data/
  out/dll/       Assembly-CSharp.dll                  -> copy over BSG_Data/Managed/
  out/bundles/   colonialmercury ...                  -> copy over the game's ship bundles
  work/decrypted/  decrypted .dec copies made along the way
  ```

  `-o DIR` picks a different output folder (its `levels/`, `dll/`, `bundles/` sub-folders are used).
- **Edits stack.** `shipdb` and `dll` start from the file already in `out/` if there is one, so separate runs add up;
  `--fresh` starts again from the configured source. `weapons` starts from the selected bundle each time. Include all desired prefab options when rebuilding from the same source.

Back up the game's files before copying the results over them.

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

`python scripts/build_pyz.py` writes `dist/deadlock.pyz`. Copy it anywhere and run `python deadlock.pyz gui` (or any
tool). Its `deadlock.json`, `out/` and `work/` are kept next to the `.pyz`.

## Changes from the old scripts

| Before | Now |
|---|---|
| `python deadlock_shipdb.py set <levels> <dll> ...` (munition/blueprint/fleet commands: `<dll> <levels>`) | `python -m deadlock_tools shipdb set ...` with `--levels/--dll` from settings (the old positional form still works) |
| `patched_out/` relative to the current folder | `out/levels/` |
| `Assembly-CSharp.dll.patched` next to the DLL; separate runs did not combine | `out/dll/Assembly-CSharp.dll`, patches stack |
| `decrypted/`, `encrypted/` next to the script | `work/decrypted/`, `out/bundles/` |
| `deadlock_weapons.py SRC.dec DST --encrypt` | `weapons BUNDLE` (encrypted or not; output encrypted by default, `--no-encrypt` to keep it plain) |
| `patch_munition_load.py` | `dll patch --munition-load` |
| GUI settings in `~/.deadlock_ships.json`, tools embedded in `deadlock_editor.py` | one `deadlock.json`; the GUI uses the package directly |

## License

Deadlock Tools is licensed under the [GNU General Public License, version 2 only](LICENSE).
