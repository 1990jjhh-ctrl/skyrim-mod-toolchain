#!/usr/bin/env bash
# Compiles every .psc in a folder with the Creation Kit's Papyrus compiler, run
# under Proton's Wine (system Wine lacks the Wine Mono the compiler needs), and
# normalizes the .pex it writes (scripts/normalize_pex.py).
#
#   SKYRIM_DIR=<game folder> PROTON_DIR=<Proton>/files \
#     scripts/papyrus.sh <source-dir> <output-dir> [<import-dir>...]
#
# SKYRIM_DIR is the Skyrim Special Edition folder with the Creation Kit
# installed: it holds "Papyrus Compiler/" and the base game's script sources in
# "Data/Source/Scripts/". PROTON_DIR is the files/ folder of a Proton release,
# e.g. Proton - Experimental. The compiler imports the source dir, then the
# import dirs in the order given, then the base game's sources.
#
# The game folder is only read. The Wine prefix, the compiler's log and its raw
# output live below ${XDG_CACHE_HOME:-~/.cache}/skyrim-mod-toolchain/papyrus;
# the output dir receives the normalized .pex only when every script compiled.
#
# Exit status: 0 when every script compiled, 1 on a failed compile or a
# missing input, 2 on bad usage.
set -euo pipefail

usage() {
	echo "usage: papyrus.sh <source-dir> <output-dir> [<import-dir>...]" >&2
	exit 2
}

fail() {
	echo "papyrus: $*" >&2
	exit 1
}

if [[ $# -lt 2 ]]; then
	usage
fi
TOOLCHAIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[[ -n "${SKYRIM_DIR:-}" ]] || fail "SKYRIM_DIR not set: the Skyrim Special Edition folder with the Creation Kit installed"
[[ -n "${PROTON_DIR:-}" ]] || fail "PROTON_DIR not set: the files/ folder of Proton, e.g. <Steam>/steamapps/common/Proton - Experimental/files"
GAME="$(realpath -m -- "$SKYRIM_DIR")"
COMPILER="$GAME/Papyrus Compiler/PapyrusCompiler.exe"
BASE_SOURCES="$GAME/Data/Source/Scripts"
WINE="$PROTON_DIR/bin/wine"
for path in "$COMPILER" "$BASE_SOURCES/TESV_Papyrus_Flags.flg" "$WINE"; do
	[[ -e "$path" ]] || fail "not found: $path"
done

inside_game() {
	[[ "$1" == "$GAME" || "$1" == "$GAME"/* ]]
}

SOURCE="$(realpath -m -- "$1")"
OUTPUT="$(realpath -m -- "$2")"
shift 2
IMPORTS=("$SOURCE")
for dir in "$@"; do
	[[ -d "$dir" ]] || fail "not a directory: $dir"
	IMPORTS+=("$(realpath -- "$dir")")
done
IMPORTS+=("$BASE_SOURCES")

[[ -d "$SOURCE" ]] || fail "not a directory: $SOURCE"
for dir in "${IMPORTS[@]}"; do
	# The compiler splits its import list at semicolons.
	[[ "$dir" != *";"* ]] || fail "a semicolon in a path can't be passed to the compiler: $dir"
done
if inside_game "$OUTPUT"; then
	fail "the output dir is inside the game folder: $OUTPUT"
fi

mapfile -t SCRIPTS < <(find "$SOURCE" -maxdepth 1 -type f -iname '*.psc' | sort)
[[ ${#SCRIPTS[@]} -gt 0 ]] || fail "no .psc files in $SOURCE"

CACHE="$(realpath -m -- "${XDG_CACHE_HOME:-$HOME/.cache}/skyrim-mod-toolchain/papyrus")"
if inside_game "$CACHE"; then
	fail "the cache dir is inside the game folder: $CACHE"
fi
export WINEPREFIX="$CACHE/wineprefix" WINEDEBUG=-all
mkdir -p "$WINEPREFIX"
RAW="$(mktemp -d "$CACHE/out.XXXXXX")"
trap 'rm -rf -- "$RAW"' EXIT
LOG="$CACHE/compile.log"

import_list=""
for dir in "${IMPORTS[@]}"; do
	import_list+="${import_list:+;}Z:$dir"
done

# Wine maps the host root to Z:. -all compiles every .psc in the folder. The
# compiler exits 0 even when a script fails, so its summary line decides.
if ! "$WINE" "$COMPILER" "Z:$SOURCE" -all -f="TESV_Papyrus_Flags.flg" \
	-i="$import_list" -o="Z:$RAW" 2>&1 | tee "$LOG"; then
	fail "the compiler exited with an error, see $LOG"
fi
grep -Eq "(^|[^0-9])${#SCRIPTS[@]} succeeded, 0 failed" "$LOG" ||
	fail "not all ${#SCRIPTS[@]} scripts compiled, see $LOG"

mapfile -t COMPILED < <(find "$RAW" -maxdepth 1 -type f -iname '*.pex' | sort)
[[ ${#COMPILED[@]} -eq ${#SCRIPTS[@]} ]] ||
	fail "the compiler wrote ${#COMPILED[@]} .pex for ${#SCRIPTS[@]} scripts, see $LOG"

# Zero the compile times and drop the user and machine names, so a recompile
# of the same source gives the same bytes.
python3 "$TOOLCHAIN/scripts/normalize_pex.py" "${COMPILED[@]}"
mkdir -p "$OUTPUT"
mv -f -- "${COMPILED[@]}" "$OUTPUT/"
echo "papyrus: ${#COMPILED[@]} scripts compiled into $OUTPUT"
