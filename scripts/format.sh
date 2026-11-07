#!/usr/bin/env bash
# Formats the C++ sources (*.h, *.cpp) under the given paths with clang-format,
# using the project's .clang-format.
#
#   scripts/format.sh <path>...           fix in place
#   scripts/format.sh --check <path>...   report violations and exit non-zero;
#                                         changes nothing
#
# Paths are relative to the current directory, so editors can turn reported
# violations into links. Symlinks are not followed.
#
# Uses llvm-mingw's clang-format, which setup.sh installs at a pinned version:
# formatting output differs between clang-format versions, so every machine
# should run the same one. Override with CLANG_FORMAT=/path/to/clang-format.
#
# Exit status: 0 clean, 1 violations, 2 usage error or clang-format missing.
set -euo pipefail

usage() {
	echo "usage: format.sh [--check] <path>..." >&2
	exit 2
}

check=0
if [[ "${1:-}" == "--check" ]]; then
	check=1
	shift
fi
if [[ $# -eq 0 ]]; then
	usage
fi

CF="${CLANG_FORMAT:-}"
if [[ -z "$CF" ]]; then
	for candidate in "${LLVM_MINGW_BIN:-$HOME/.local/opt/llvm-mingw/bin}/clang-format" "$(command -v clang-format || true)"; do
		if [[ -n "$candidate" && -x "$candidate" ]]; then
			CF="$candidate"
			break
		fi
	done
fi
if [[ -z "$CF" || ! -x "$CF" ]]; then
	echo "clang-format not found; run scripts/setup.sh or set CLANG_FORMAT" >&2
	exit 2
fi

for path in "$@"; do
	if [[ ! -e "$path" ]]; then
		echo "format: no such path: $path" >&2
		exit 2
	fi
done

mapfile -d '' -t FILES < <(find "$@" -type f \( -name '*.h' -o -name '*.cpp' \) -print0 | sort -z)

# clang-format without file arguments reads stdin.
if [[ ${#FILES[@]} -eq 0 ]]; then
	echo "format: 0 files"
	exit 0
fi
version="$("$CF" --version | sed 's/ (.*//')"

if [[ $check -eq 1 ]]; then
	if "$CF" --dry-run --Werror "${FILES[@]}"; then
		echo "format: ${#FILES[@]} files clean ($version)"
	else
		echo "format: violations found; run scripts/format.sh to fix" >&2
		exit 1
	fi
else
	"$CF" -i "${FILES[@]}"
	echo "format: ${#FILES[@]} files formatted ($version)"
fi
