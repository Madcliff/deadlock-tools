# 0.6.2

## Fixes
- **Fighters stopped working once the Mk2 (or any custom jammer) jammed enemies.** `CustomJamming` (0.6.0) built a small array with an initializer. The compiler stores that as hidden data the merge did not copy, so the merged DLL still pointed at the temporary `CustomModules` assembly. Reading the jamming penalty of a jammed ship (fighter attacks, enemy AI) threw "Could not load file or assembly 'CustomModules'". Fixed, and the merge now refuses any DLL that still references `CustomModules`. Rebuild the custom-module DLL (Save ship) and install it.
