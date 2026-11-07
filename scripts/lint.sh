#!/usr/bin/env bash
# Runs clang-tidy (.clang-tidy) on the compile-database entries under the given
# paths.
#
#   scripts/lint.sh <build-dir> <path>...
#
# <build-dir> holds the compile_commands.json of a configured build. Paths are
# relative to the current directory. Only sources the build compiles are
# checked: the compile database has no entry for the others.
#
# Uses llvm-mingw's clang-tidy, which setup.sh installs at a pinned version, so
# every machine reports the same findings. Override with
# CLANG_TIDY=/path/to/clang-tidy.
#
# Exit status: 0 clean, 1 findings, 2 usage error, missing compile database or
# missing clang-tidy.
set -euo pipefail

if [[ $# -lt 2 ]]; then
	echo "usage: lint.sh <build-dir> <path>..." >&2
	exit 2
fi
BUILD="$1"
shift

if [[ ! -f "$BUILD/compile_commands.json" ]]; then
	echo "no $BUILD/compile_commands.json; configure the build first" >&2
	exit 2
fi

CT="${CLANG_TIDY:-}"
if [[ -z "$CT" ]]; then
	for candidate in "${LLVM_MINGW_BIN:-$HOME/.local/opt/llvm-mingw/bin}/clang-tidy" "$(command -v clang-tidy || true)"; do
		if [[ -n "$candidate" && -x "$candidate" ]]; then
			CT="$candidate"
			break
		fi
	done
fi
if [[ -z "$CT" || ! -x "$CT" ]]; then
	echo "clang-tidy not found; run scripts/setup.sh or set CLANG_TIDY" >&2
	exit 2
fi

for path in "$@"; do
	if [[ ! -e "$path" ]]; then
		echo "lint: no such path: $path" >&2
		exit 2
	fi
done

# clang-tidy reads a copy of the compile database that holds only the entries
# under the given paths, without the arguments that use the precompiled header
# (/Yu, /Fp): the build's clang-cl wrote the PCH, and clang-tidy, a different
# clang, rejects its format. The forced include (/FI) of the PCH header stays,
# so sources still see what it includes.
DB_DIR="$(mktemp -d)"
trap 'rm -rf "$DB_DIR"' EXIT

# The database holds absolute paths (or paths relative to each entry's
# directory), so compare resolved ones. Run outside a process substitution so a
# failure (such as a malformed database) is seen.
selected="$(python3 -c '
import json, os, shlex, sys

build, out, *roots = sys.argv[1:]
roots = [os.path.realpath(root) for root in roots]
PCH_USE = ("/Yu", "/Fp")


def selected(path):
    return any(path == root or path.startswith(root + os.sep) for root in roots)


with open(os.path.join(build, "compile_commands.json")) as db:
    entries = json.load(db)
files = set()
kept = []
for entry in entries:
    path = os.path.realpath(os.path.join(entry["directory"], entry["file"]))
    if not selected(path):
        continue
    files.add(path)
    command = entry.pop("command", None)
    arguments = entry.get("arguments") or shlex.split(command)
    entry["arguments"] = [arg for arg in arguments if not arg.startswith(PCH_USE)]
    kept.append(entry)
with open(os.path.join(out, "compile_commands.json"), "w") as db:
    json.dump(kept, db, indent=1)
print("\n".join(sorted(files)))
' "$BUILD" "$DB_DIR" "$@")" || {
	echo "lint: cannot read $BUILD/compile_commands.json" >&2
	exit 2
}
FILES=()
if [[ -n "$selected" ]]; then
	mapfile -t FILES <<<"$selected"
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
	echo "lint: 0 files"
	exit 0
fi

version="$("$CT" --version | grep -m1 -o 'version [0-9.]*')"
if "$CT" -p "$DB_DIR" --quiet "${FILES[@]}"; then
	echo "lint: ${#FILES[@]} files clean ($version)"
else
	echo "lint: findings above" >&2
	exit 1
fi
