#!/usr/bin/env bash
# One-time setup of the Linux cross-compile toolchain for SKSE plugins.
#
# Usage: setup.sh [--install] [--native-tests] [--check]
#   --install       install missing system packages with sudo apt-get
#   --native-tests  also require a native C++ compiler for host-side tests
#   --check         stop after the prerequisite check
#
# sudo is used only with --install; everything else goes to $HOME.
set -euo pipefail

TOOLCHAIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "${TOOLCHAIN_VERSIONS_FILE:-$TOOLCHAIN_ROOT/versions.env}"

LLVM_PREFIX="${SETUP_LLVM_PREFIX:-/usr/bin}"
INSTALL=0
NATIVE_TESTS=0
CHECK_ONLY=0

usage() {
	echo "Usage: setup.sh [--install] [--native-tests] [--check]" >&2
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
	local target="$HOME/.local/opt/llvm-mingw"
	# A set but invalid override is an error, never a fallback.
	if [[ -n "${LLVM_MINGW_BIN:-}" ]]; then
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

install_xwin() {
	echo "== 3. xwin"
	local target="$HOME/.local/bin/xwin"
	if xwin_is_pinned "$target"; then
		echo "Found $target"
		return 0
	fi
	if need xwin && xwin_is_pinned xwin; then
		echo "Found $(command -v xwin)"
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

# Placeholders, replaced by the install steps.
not_implemented() {
	echo "$1: not implemented yet" >&2
	return 1
}
install_sysroot() { not_implemented install_sysroot; }
install_vcpkg() { not_implemented install_vcpkg; }
init_submodules() { not_implemented init_submodules; }
print_env() { not_implemented print_env; }

main() {
	while (($#)); do
		case "$1" in
		--install) INSTALL=1 ;;
		--native-tests) NATIVE_TESTS=1 ;;
		--check) CHECK_ONLY=1 ;;
		*)
			usage
			exit 2
			;;
		esac
		shift
	done

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
