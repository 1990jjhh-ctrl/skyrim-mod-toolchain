#!/usr/bin/env bash
# One-time setup of the Linux cross-compile toolchain for SKSE plugins.
#
# Usage: setup.sh [--install] [--native-tests] [--check]
#        setup.sh --print-locations
#   --install          install missing system packages with sudo apt-get
#   --native-tests     also require a native C++ compiler for host-side tests
#   --check            stop after the prerequisite check
#   --print-locations  print install locations and cache keys as key=value
#                      lines, then exit; runs nothing else; use it alone
#
# sudo is used only with --install; everything else goes to $HOME.
set -euo pipefail

TOOLCHAIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSIONS_FILE="${TOOLCHAIN_VERSIONS_FILE:-$TOOLCHAIN_ROOT/versions.env}"
# shellcheck source=/dev/null
source "$VERSIONS_FILE"

LLVM_PREFIX="${SETUP_LLVM_PREFIX:-/usr/bin}"
# Default install locations, used by the install steps and print_locations.
LLVM_MINGW_MANAGED="$HOME/.local/opt/llvm-mingw"
XWIN_BIN_DEFAULT="$HOME/.local/bin/xwin"
SYSROOT_DEFAULT="$HOME/xwin-out"
XWIN_CACHE_DEFAULT="$HOME/xwin-cache"
VCPKG_MANAGED="$HOME/.local/share/vcpkg"
# Arguments of `xwin splat` that shape the sysroot's content; they feed the
# sysroot cache key, so a change here invalidates cached sysroots.
SPLAT_ARGS=(--include-debug-libs --use-winsysroot-style --preserve-ms-arch-notation)
INSTALL=0
NATIVE_TESTS=0
CHECK_ONLY=0
PRINT_LOCATIONS=0

usage() {
	echo "Usage: setup.sh [--install] [--native-tests] [--check]" >&2
	echo "       setup.sh --print-locations" >&2
}

need() { command -v "$1" >/dev/null 2>&1; }

# Accepts `name` on PATH or a versioned `<prefix>/name-*` (Debian's clang-cl-21).
need_llvm() {
	need "$1" && return 0
	local versioned
	for versioned in "$LLVM_PREFIX/$1"-*; do
		[[ -x "$versioned" ]] && return 0
	done
	return 1
}

missing_commands() {
	local tool
	# zip/unzip and pkg-config: vcpkg; xz: the llvm-mingw tarball;
	# wine: runs the fxc2 shader stand-in for directxtk; sha256sum: downloads.
	for tool in git curl tar xz zip unzip pkg-config cmake ninja wine python3 sha256sum; do
		need "$tool" || echo "$tool"
	done
	for tool in clang-cl lld-link llvm-rc llvm-mt; do
		need_llvm "$tool" || echo "$tool"
	done
	if ((NATIVE_TESTS)); then
		need c++ || echo "c++"
	fi
	return 0
}

check_prereqs() {
	echo "== 1. System tools"
	local -a missing packages
	mapfile -t missing < <(missing_commands)
	((${#missing[@]})) || return 0

	packages=(git curl xz-utils zip unzip pkg-config cmake ninja-build wine python3 coreutils clang lld llvm)
	((NATIVE_TESTS)) && packages+=(build-essential)

	if ((INSTALL)); then
		if ! need apt-get; then
			echo "--install supports apt only; install ${packages[*]} yourself." >&2
			exit 1
		fi
		# A fresh distrobox has an empty package index.
		sudo apt-get update
		sudo apt-get install -y --no-install-recommends "${packages[@]}"
		mapfile -t missing < <(missing_commands)
		((${#missing[@]})) || return 0
	fi

	echo "Missing: ${missing[*]}"
	echo "Debian/Ubuntu: sudo apt-get install -y --no-install-recommends ${packages[*]}"
	echo "Or re-run with --install. Then re-run this script."
	exit 1
}

# Temp dirs of the running install step; removed on any exit, including errors.
CLEANUP_DIRS=()
cleanup() {
	((${#CLEANUP_DIRS[@]})) && rm -rf "${CLEANUP_DIRS[@]}"
	return 0
}
trap cleanup EXIT

# make_temp_dir <parent>: sets TEMP_DIR to a new directory inside <parent>.
make_temp_dir() {
	mkdir -p "$1"
	TEMP_DIR="$(mktemp -d "$1/.setup.XXXXXX")"
	CLEANUP_DIRS+=("$TEMP_DIR")
}

# fetch_verified <url> <sha256> <dest>: download; on a mismatch remove <dest>, exit 1.
fetch_verified() {
	local url="$1" sha256="$2" dest="$3"
	rm -f "$dest"
	echo "Downloading $url"
	if ! curl -fL --retry 3 -o "$dest" "$url"; then
		rm -f "$dest"
		echo "Download failed: $url" >&2
		exit 1
	fi
	if ! printf '%s  %s\n' "$sha256" "$dest" | sha256sum -c --quiet - >/dev/null; then
		rm -f "$dest"
		echo "sha256 mismatch for $url (expected $sha256); refusing to install it." >&2
		exit 1
	fi
}

install_llvm_mingw() {
	echo "== 2. llvm-mingw"
	local compiler=x86_64-w64-mingw32-clang++
	local target="$LLVM_MINGW_MANAGED"
	# The user's own llvm-mingw is never modified; one at the managed location is.
	# A set but invalid override is an error, never a fallback.
	if llvm_mingw_is_user_owned; then
		if [[ ! -x "$LLVM_MINGW_BIN/$compiler" ]]; then
			echo "LLVM_MINGW_BIN=$LLVM_MINGW_BIN has no $compiler; fix or unset it." >&2
			exit 1
		fi
		echo "Using LLVM_MINGW_BIN=$LLVM_MINGW_BIN"
		return 0
	fi
	# The stamp names the installed release, so a pin bump reinstalls.
	local stamp="$target/.toolchain-version"
	if [[ -x "$target/bin/$compiler" && -f "$stamp" && "$(<"$stamp")" == "$LLVM_MINGW_VERSION" ]]; then
		echo "Found $target"
		return 0
	fi

	local tarball="llvm-mingw-${LLVM_MINGW_VERSION}-ucrt-ubuntu-22.04-x86_64.tar.xz"
	make_temp_dir "$(dirname "$target")"
	local tmp="$TEMP_DIR"
	fetch_verified \
		"https://github.com/mstorsjo/llvm-mingw/releases/download/${LLVM_MINGW_VERSION}/${tarball}" \
		"$LLVM_MINGW_SHA256" "$tmp/$tarball"
	mkdir "$tmp/extract"
	tar -xf "$tmp/$tarball" -C "$tmp/extract" --strip-components=1
	if [[ ! -x "$tmp/extract/bin/$compiler" ]]; then
		echo "The llvm-mingw tarball has no bin/$compiler." >&2
		exit 1
	fi
	# Replace a partial install left by an interrupted run.
	rm -rf "$target"
	mv "$tmp/extract" "$target"
	printf '%s\n' "$LLVM_MINGW_VERSION" >"$stamp"
	echo "Installed $target"
}

xwin_is_pinned() {
	[[ "$("$1" --version 2>/dev/null)" == "xwin $XWIN_VERSION" ]]
}

# Prints the path of the pinned xwin (the install location first, then PATH).
find_pinned_xwin() {
	if xwin_is_pinned "$XWIN_BIN_DEFAULT"; then
		echo "$XWIN_BIN_DEFAULT"
	elif need xwin && xwin_is_pinned xwin; then
		command -v xwin
	else
		return 1
	fi
}

install_xwin() {
	echo "== 3. xwin"
	local target="$XWIN_BIN_DEFAULT" found
	if found="$(find_pinned_xwin)"; then
		echo "Found $found"
		return 0
	fi

	local dir="xwin-${XWIN_VERSION}-x86_64-unknown-linux-musl"
	make_temp_dir "$(dirname "$target")"
	local tmp="$TEMP_DIR"
	fetch_verified \
		"https://github.com/Jake-Shadle/xwin/releases/download/${XWIN_VERSION}/${dir}.tar.gz" \
		"$XWIN_SHA256" "$tmp/xwin.tar.gz"
	tar -xzf "$tmp/xwin.tar.gz" -C "$tmp" --strip-components=1 "$dir/xwin"
	chmod +x "$tmp/xwin"
	mv "$tmp/xwin" "$target"
	echo "Installed $target"
}

install_sysroot() {
	echo "== 4. Windows SDK and MSVC CRT sysroot"
	local out
	out="$(sysroot_dir)"
	# Written after a successful splat, so an interrupted one is redone. A newer
	# xwin does not re-splat a stamped sysroot; delete the stamp to force it.
	local stamp="$out/.splat-complete"
	if [[ -f "$stamp" ]]; then
		echo "Found $out"
		return 0
	fi
	local xwin
	if ! xwin="$(find_pinned_xwin)"; then
		echo "No xwin $XWIN_VERSION found; run install_xwin first." >&2
		exit 1
	fi
	local cache="$XWIN_CACHE_DEFAULT"
	mkdir -p "$cache"
	echo "Downloading the MSVC CRT and Windows SDK. You accept Microsoft's license by continuing."
	"$xwin" --accept-license --http-retry 10 --cache-dir "$cache" splat \
		--output "$out" "${SPLAT_ARGS[@]}"
	: >"$stamp"
}

vcpkg_baseline() {
	python3 -I -c '
import json
import sys

with open(sys.argv[1]) as config:
    print(json.load(config)["default-registry"]["baseline"])
' "${TOOLCHAIN_VCPKG_CONFIG:-$TOOLCHAIN_ROOT/vcpkg-configuration.json}"
}

# same_directory <a> <b>: true when both are the same existing directory or the same path.
same_directory() {
	[[ "$1" == "$2" ]] && return 0
	[[ -d "$1" && -d "$2" ]] || return 1
	[[ "$(cd "$1" && pwd -P)" == "$(cd "$2" && pwd -P)" ]]
}

install_vcpkg() {
	echo "== 5. vcpkg"
	local baseline
	baseline="$(vcpkg_baseline)"
	# The user's own vcpkg is never modified; the managed one is.
	if [[ -n "${VCPKG_ROOT:-}" ]] && ! same_directory "$VCPKG_ROOT" "$VCPKG_MANAGED"; then
		if [[ ! -x "$VCPKG_ROOT/vcpkg" ]]; then
			echo "VCPKG_ROOT=$VCPKG_ROOT has no bootstrapped vcpkg; fix or unset it." >&2
			exit 1
		fi
		local head
		head="$(git -C "$VCPKG_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
		if [[ "$head" != "$baseline" ]]; then
			echo "Warning: VCPKG_ROOT is at $head, not the baseline $baseline; left unchanged." >&2
		fi
		echo "Using VCPKG_ROOT=$VCPKG_ROOT"
		return 0
	fi

	local dir="$VCPKG_MANAGED"
	local url="${VCPKG_GIT_URL:-https://github.com/microsoft/vcpkg}"
	if [[ ! -d "$dir/.git" ]]; then
		mkdir -p "$(dirname "$dir")"
		git clone --no-checkout "$url" "$dir"
	fi
	if ! git -C "$dir" cat-file -e "$baseline^{commit}" 2>/dev/null; then
		git -C "$dir" remote get-url origin >/dev/null 2>&1 || git -C "$dir" remote add origin "$url"
		git -C "$dir" fetch origin
	fi
	local changed=0
	# A --no-checkout clone can sit at the baseline with an empty tree.
	if [[ "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)" != "$baseline" ||
		! -f "$dir/bootstrap-vcpkg.sh" ]]; then
		git -C "$dir" checkout --detach "$baseline"
		changed=1
	fi
	if ((changed)) || [[ ! -x "$dir/vcpkg" ]]; then
		"$dir/bootstrap-vcpkg.sh" -disableMetrics
	else
		echo "Found $dir at the baseline"
	fi
}

init_submodules() {
	echo "== 6. Submodules"
	git -C "$TOOLCHAIN_ROOT" submodule update --init --recursive
}

# True when LLVM_MINGW_BIN names an llvm-mingw other than the managed one.
llvm_mingw_is_user_owned() {
	[[ -n "${LLVM_MINGW_BIN:-}" ]] && ! same_directory "$LLVM_MINGW_BIN" "$LLVM_MINGW_MANAGED/bin"
}

# Prints the bin directory setup uses: the user's own as given, else the managed one.
llvm_mingw_bin_dir() {
	if llvm_mingw_is_user_owned; then
		echo "$LLVM_MINGW_BIN"
	else
		echo "$LLVM_MINGW_MANAGED/bin"
	fi
}

sysroot_dir() { echo "${XWIN_SYSROOT:-$SYSROOT_DEFAULT}"; }
vcpkg_root_dir() { echo "${VCPKG_ROOT:-$VCPKG_MANAGED}"; }

# sha256_of_stdin: prints the hex digest of stdin; fails when it can't compute one.
sha256_of_stdin() {
	local digest
	digest="$(sha256sum)" || return 1
	digest="${digest%% *}"
	[[ "$digest" =~ ^[0-9a-f]{64}$ ]] || return 1
	echo "$digest"
}

# The single definition of every install location and cache key, as key=value
# lines (the value is everything after the first "="). Needs only sha256sum
# and python3; downloads and installs nothing. Prints nothing and fails when
# any value can't be computed. llvm_mingw is the managed directory (what the
# tools cache holds); llvm_mingw_bin is the bin directory setup actually uses.
print_locations() {
	local tools_key sysroot_key baseline
	tools_key="$(sha256_of_stdin <"$VERSIONS_FILE")" || return 1
	sysroot_key="$(printf '%s\n' "$XWIN_VERSION" "${SPLAT_ARGS[@]}" | sha256_of_stdin)" || return 1
	baseline="$(vcpkg_baseline)" || return 1
	[[ "$baseline" =~ ^[0-9a-f]{40}$ ]] || {
		echo "Invalid vcpkg baseline: $baseline" >&2
		return 1
	}
	printf '%s=%s\n' \
		llvm_mingw "$LLVM_MINGW_MANAGED" \
		llvm_mingw_bin "$(llvm_mingw_bin_dir)" \
		xwin_bin "$XWIN_BIN_DEFAULT" \
		sysroot "$(sysroot_dir)" \
		xwin_cache "$XWIN_CACHE_DEFAULT" \
		vcpkg_root "$(vcpkg_root_dir)" \
		vcpkg_archives "${VCPKG_DEFAULT_BINARY_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/vcpkg/archives}" \
		tools_key "$tools_key" \
		sysroot_key "$sysroot_key" \
		vcpkg_baseline "$baseline"
}

print_env() {
	local xwin_dir
	xwin_dir="$(dirname "$XWIN_BIN_DEFAULT")"
	# Keep $HOME symbolic so the profile line works on any machine.
	xwin_dir="${xwin_dir/#"$HOME"/\$HOME}"
	cat <<EOF

Setup done. Add to your shell profile:
  export VCPKG_ROOT="$(vcpkg_root_dir)"
  export XWIN_SYSROOT="$(sysroot_dir)"
  export LLVM_MINGW_BIN="$(llvm_mingw_bin_dir)"
  export PATH="$xwin_dir:\$PATH"
EOF
}

main() {
	while (($#)); do
		case "$1" in
		--install) INSTALL=1 ;;
		--native-tests) NATIVE_TESTS=1 ;;
		--check) CHECK_ONLY=1 ;;
		--print-locations) PRINT_LOCATIONS=1 ;;
		*)
			usage
			exit 2
			;;
		esac
		shift
	done
	if ((PRINT_LOCATIONS)); then
		# Exclusive: the action calls it before any tool exists.
		if ((INSTALL || NATIVE_TESTS || CHECK_ONLY)); then
			usage
			exit 2
		fi
		print_locations || exit 1
		return 0
	fi

	check_prereqs
	((CHECK_ONLY)) && return 0
	install_llvm_mingw
	install_xwin
	install_sysroot
	install_vcpkg
	init_submodules
	print_env
}

# Tests source this file to call single steps.
if [[ "${1:-}" == --source-only ]]; then
	return 0
fi

main "$@"
