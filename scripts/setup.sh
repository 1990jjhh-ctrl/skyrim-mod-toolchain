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

# Placeholders, replaced by the install steps.
not_implemented() {
	echo "$1: not implemented yet" >&2
	return 1
}
install_llvm_mingw() { not_implemented install_llvm_mingw; }
install_xwin() { not_implemented install_xwin; }
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

main "$@"
