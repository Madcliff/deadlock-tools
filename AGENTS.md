# Notes for coding agents

Deadlock Tools is a modding toolkit for *Battlestar Galactica: Deadlock* (Unity 2018.4, Mono). It edits level files,
ship and squadron bundles, and `Assembly-CSharp.dll`. It is also a browser GUI. Start with `README.md`.

**If `HANDOFF.local.md` exists in this folder, read it first.** It is untracked and holds this machine's paths, the
current state of the work and the open items.

## Layout

- `deadlock_tools/`: the package. `python -m deadlock_tools <config|xor|weapons|shipdb|dll|ui|gui>`.
  - `editor.py`: the GUI. One Python file; the page is the `PAGE` raw string. It shells out to the same CLI tools.
  - `weapons.py`: ship bundles.
  - `shipdb.py`: level tables.
  - `dll.py`: DLL patches and DLL-page options.
  - `squadron_types.py`, `munition_types.py`, `jamming.py`, `flak.py`, `skirmish.py`: feature modules.
- `dll_modules/*.cs`: custom-module C# compiled into the game DLL by `scripts/build_custom_modules.sh` +
  `scripts/merge_custom_modules.cs` (Mono.Cecil). Their hooks into game code are injected in `merge_custom_modules.cs`.
- `docs/`:
  - `cli-reference.md`: every CLI option.
  - `gui-map.md`: every GUI control → endpoint → CLI tool.
  - `game/*.md`: reverse-engineered game internals.
  - `release-notes-unreleased.md`: user-facing changes since the last release.
- `tests/`: `unittest`. Many tests need the gitignored `tests/regression_inputs.json` (see the `.example`). They are
  skipped without it.

## Rules the owner set

1. **Every CLI feature needs a GUI control, and the other way round.** Update `docs/gui-map.md` and
   `docs/cli-reference.md` in the same change.
2. **Document game internals as you learn them**, in `docs/game/*.md` or `docs/technical-findings.md`, in the same
   change: which file holds what, field meanings, limits, behaviour, gotchas.
3. **User-facing changes go in `docs/release-notes-unreleased.md`.**
4. **Tests use vanilla data only.** `regression_inputs.json` must point at clean copies, never at the game's or the
   work tree's modded `BSG_Data`.
5. **Never write the vanilla reference copies or the game folder without being asked.**
   - Edits go to the work tree (`work/BSG_Data`, which mirrors `BSG_Data`).
   - Backups go to `work-backups/`, never inside the work folder.
   - Installing into the game is a separate, explicit step: back up, copy, then compare the trees.
6. Commit, push and release only when asked.

## Building and testing

```bash
python -m unittest discover -s tests          # full suite; prefix PYTHONPATH with the UnityPy folder (HANDOFF.local.md)
python scripts/build_pyz.py                   # dist/deadlock.pyz; rebuild after any change, the GUI user runs it
bash scripts/build_custom_modules.sh MANAGED SOURCE_DLL OUT_DLL   # custom-module DLL candidate
```

The editor's **Save ship** rebuilds the work-tree DLL through `Project.editor_update(bundle)` in `editor.py`. That path:

- starts from the clean base DLL;
- carries over the existing DLL patches and the added ship / squadron / munition enum members (`keep_ship_types`);
- backs up the replaced files.

Call it directly to rebuild the DLL without the GUI.

## Writing custom modules (`dll_modules/*.cs`): hard-won rules

- **No array initializers** (`int[] a = { 1, 2 };`). The compiler can store their data in
  `<PrivateImplementationDetails>`, which the merge does not copy. The game then throws "Could not load file or
  assembly 'CustomModules'" wherever that code runs. Allocate the array and assign the elements. The merge now fails
  the build if any reference into `CustomModules` remains.
- **Only use library calls the game's runtime has.** The build compiles with `-nostdlib` against the game's own
  `Managed/mscorlib.dll`, `System.dll`, `System.Core.dll` and the UnityEngine modules. Before that,
  `string.Split(char, StringSplitOptions)` compiled and then threw `MissingMethodException` in game.
- Game types and members are obfuscated (e.g. `ShipInfo.FOIHMGKCNFN`). Find real methods by disassembling the
  vanilla DLL (`ikdasm`/`monodis`; both crash on merged DLLs, so inspect those with a small Cecil program instead).
  Many methods have identical decoy copies: hook every copy that matches, as the existing injections do.
- Runtime-tunable values live in small `StreamingAssets/*.txt` files the modules read: `deadlock-jamming.txt`,
  `deadlock-fleet.txt`. Those files must be installed alongside the DLL.

## Releases

1. Move the unreleased notes to `docs/release-notes-X.Y.Z.md` and reset the unreleased file.
2. Bump `deadlock_tools/__init__.py` `__version__` and the README's release-notes link.
3. Commit "Prepare Deadlock Tools vX.Y.Z", push `main`, then
   `gh release create vX.Y.Z --target main --title "Deadlock Tools X.Y.Z" --notes-file docs/release-notes-X.Y.Z.md`.

Publishing a release triggers `.github/workflows/windows.yml`. It builds `DeadlockTools.exe`, smoke-tests it and
attaches `DeadlockTools-Windows-x64.zip` and `deadlock.pyz` to the release; check that both builds pass.
