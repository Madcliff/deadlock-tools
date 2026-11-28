#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 GAME_MANAGED INPUT_ASSEMBLY_CSHARP OUTPUT_CANDIDATE_DLL" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
managed_dir="$(cd "$1" && pwd)"
input_dll="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
output_dll="$(mkdir -p "$(dirname "$3")" && cd "$(dirname "$3")" && pwd)/$(basename "$3")"
cecil="/usr/lib/mono/gac/Mono.Cecil/0.11.1.0__0738eb9f132ed756/Mono.Cecil.dll"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/deadlock-commander.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

for assembly in Assembly-CSharp.dll Assembly-CSharp-firstpass.dll UnityEngine.dll UnityEngine.CoreModule.dll UnityEngine.UI.dll; do
  [[ -f "$managed_dir/$assembly" ]] || { echo "Missing game reference: $managed_dir/$assembly" >&2; exit 1; }
done
[[ -f "$input_dll" ]] || { echo "Missing input DLL: $input_dll" >&2; exit 1; }
[[ -f "$cecil" ]] || { echo "Mono.Cecil 0.11.1 was not found at $cecil" >&2; exit 1; }

mcs -target:library -out:"$work_dir/CommanderModifiers.dll" \
  -r:"$managed_dir/Assembly-CSharp.dll" \
  -r:"$managed_dir/Assembly-CSharp-firstpass.dll" \
  -r:"$managed_dir/UnityEngine.dll" \
  -r:"$managed_dir/UnityEngine.CoreModule.dll" \
  -r:"$managed_dir/UnityEngine.UI.dll" \
  "$repo_dir/dll_modules/CustomCommanderStatModifiers.cs"
mcs -out:"$work_dir/merge_custom_modules.exe" -r:"$cecil" \
  "$repo_dir/scripts/merge_custom_modules.cs"
args=("$input_dll" "$work_dir/CommanderModifiers.dll" "$output_dll" "$managed_dir" CustomCommanderStatModifiers)
if grep -aq CustomCommanderStatModifiers "$input_dll"; then args+=(--update); fi
mono "$work_dir/merge_custom_modules.exe" "${args[@]}"
echo "Created commander modifier DLL candidate: $output_dll"
