#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "Usage: $0 GAME_MANAGED INPUT_DLL OUTPUT_DLL [--squadron|--munition] ID=NAME [ID=NAME ...]" >&2
  echo "       $0 GAME_MANAGED INPUT_DLL OUTPUT_DLL SHIP_ID ENUM_NAME" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
managed_dir="$(cd "$1" && pwd)"
input_dll="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
output_dll="$(mkdir -p "$(dirname "$3")" && cd "$(dirname "$3")" && pwd)/$(basename "$3")"
cecil="/usr/lib/mono/gac/Mono.Cecil/0.11.1.0__0738eb9f132ed756/Mono.Cecil.dll"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/deadlock-ship-enum.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

[[ -f "$managed_dir/UnityEngine.CoreModule.dll" ]] || { echo "Missing game reference: $managed_dir/UnityEngine.CoreModule.dll" >&2; exit 1; }
[[ -f "$input_dll" ]] || { echo "Missing input DLL: $input_dll" >&2; exit 1; }
[[ -f "$cecil" ]] || { echo "Mono.Cecil 0.11.1 was not found at $cecil" >&2; exit 1; }

mcs -out:"$work_dir/add_ship_type_enum.exe" -r:"$cecil" "$repo_dir/scripts/add_ship_type_enum.cs"
BSG_MANAGED_DIR="$managed_dir" mono "$work_dir/add_ship_type_enum.exe" \
  "$input_dll" "$output_dll" "${@:4}"
