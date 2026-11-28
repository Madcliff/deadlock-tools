#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 GAME_MANAGED SOURCE_ASSEMBLY_CSHARP OUTPUT_CANDIDATE_DLL" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
managed_dir="$(cd "$1" && pwd)"
source_dll="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
output_dll="$(mkdir -p "$(dirname "$3")" && cd "$(dirname "$3")" && pwd)/$(basename "$3")"
cecil="/usr/lib/mono/gac/Mono.Cecil/0.11.1.0__0738eb9f132ed756/Mono.Cecil.dll"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/deadlock-custom-modules.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

for assembly in mscorlib.dll System.dll System.Core.dll Assembly-CSharp.dll Assembly-CSharp-firstpass.dll UnityEngine.dll UnityEngine.CoreModule.dll UnityEngine.UI.dll UnityEngine.TextRenderingModule.dll UnityEngine.UIModule.dll; do
  [[ -f "$managed_dir/$assembly" ]] || { echo "Missing game reference: $managed_dir/$assembly" >&2; exit 1; }
done
[[ -f "$source_dll" ]] || { echo "Missing source DLL: $source_dll" >&2; exit 1; }
[[ -f "$cecil" ]] || { echo "Mono.Cecil 0.11.1 was not found at $cecil" >&2; exit 1; }

# Compile against the game's own framework (Managed/mscorlib.dll, System*.dll), not the system Mono's newer one:
# a method the game's runtime lacks (e.g. string.Split(char, StringSplitOptions)) is then a build error instead of a
# MissingMethodException in game.
mcs -target:library -nostdlib -out:"$work_dir/CustomModules.dll" \
  -r:"$managed_dir/mscorlib.dll" -r:"$managed_dir/System.dll" -r:"$managed_dir/System.Core.dll" \
  -r:"$managed_dir/Assembly-CSharp.dll" \
  -r:"$managed_dir/Assembly-CSharp-firstpass.dll" \
  -r:"$managed_dir/UnityEngine.dll" \
  -r:"$managed_dir/UnityEngine.CoreModule.dll" \
  -r:"$managed_dir/UnityEngine.UI.dll" \
  -r:"$managed_dir/UnityEngine.TextRenderingModule.dll" -r:"$managed_dir/UnityEngine.UIModule.dll" \
  "$repo_dir/dll_modules/CustomArmourRepairModule.cs" \
  "$repo_dir/dll_modules/CustomArmourRepairDrones.cs" \
  "$repo_dir/dll_modules/CustomArmourRepairDroneModule.cs" \
  "$repo_dir/dll_modules/CustomArmourRepairDroneAction.cs" \
  "$repo_dir/dll_modules/CustomSquadronReplacementModule.cs" \
  "$repo_dir/dll_modules/CustomCampaignRefit.cs" \
  "$repo_dir/dll_modules/CustomDroneResupplyModule.cs" \
  "$repo_dir/dll_modules/CustomMagazineCapacityModule.cs" \
  "$repo_dir/dll_modules/CustomRepairBaySlots.cs" \
  "$repo_dir/dll_modules/CustomInstantPlatingModule.cs" \
  "$repo_dir/dll_modules/CustomInstantPlatingAction.cs" \
  "$repo_dir/dll_modules/CustomEmpPulseModule.cs" \
  "$repo_dir/dll_modules/CustomEmpPulseAction.cs" \
  "$repo_dir/dll_modules/CustomTargetedEmpModule.cs" \
  "$repo_dir/dll_modules/CustomTargetedEmpAction.cs" \
  "$repo_dir/dll_modules/CustomVortexCrushModule.cs" \
  "$repo_dir/dll_modules/CustomVortexCrushAction.cs" \
  "$repo_dir/dll_modules/CustomCommanderStatModifiers.cs" \
  "$repo_dir/dll_modules/CustomSkirmishFleetCap.cs" \
  "$repo_dir/dll_modules/CustomBattleHud.cs" \
  "$repo_dir/dll_modules/CustomSubsystemModifiers.cs" \
  "$repo_dir/dll_modules/CustomModuleSubsystems.cs" \
  "$repo_dir/dll_modules/CustomStealthFallback.cs" \
  "$repo_dir/dll_modules/CustomJamming.cs"

mcs -out:"$work_dir/merge_custom_modules.exe" -r:"$cecil" \
  "$repo_dir/scripts/merge_custom_modules.cs"

current="$source_dll"
types=(CustomModuleSubsystems CustomArmourRepairModule CustomSquadronReplacementModule CustomArmourRepairDroneModule CustomCampaignRefit CustomDroneResupplyModule CustomMagazineCapacityModule CustomRepairBaySlots CustomBattleHud CustomInstantPlatingModule CustomInstantPlatingAction CustomEmpPulseModule CustomEmpPulseAction CustomTargetedEmpModule CustomTargetedEmpAction CustomVortexCrushModule CustomVortexCrushAction CustomCommanderStatModifiers CustomSkirmishFleetCap CustomSubsystemModifiers CustomStealthFallback CustomJamming CustomArmourRepairDroneAction CustomArmourRepairDrones)
for index in "${!types[@]}"; do
  next="$work_dir/merged-$index.dll"
  mono "$work_dir/merge_custom_modules.exe" "$current" "$work_dir/CustomModules.dll" \
    "$next" "$managed_dir" "${types[$index]}"
  current="$next"
done

cp "$current" "$output_dll"
echo "Created candidate DLL: $output_dll"
echo "This script does not install the DLL or modify any game bundle."
