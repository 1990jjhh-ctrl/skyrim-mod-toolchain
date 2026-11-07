# skyrim-mod-toolchain

A shared toolchain for cross-compiling SKSE plugins with CommonLibSSE-NG on a Linux host. Goals, scope and requirements are in [docs/PRD.md](docs/PRD.md).

## Setup

Run once per machine, from the CMake source directory of a project that has the toolchain as a submodule at `lib/skyrim-mod-toolchain` (see [Use it in a project](#use-it-in-a-project)):

```sh
git clone --recursive <your project>
cd <your project's CMake source directory>
lib/skyrim-mod-toolchain/scripts/setup.sh
```

If you cloned without `--recursive`, run `git submodule update --init --recursive` first: setup initialises the toolchain's own submodules, but the toolchain submodule must exist before its setup script can run. Then add the exports setup prints to your shell profile and build the project (step 6 of [Use it in a project](#use-it-in-a-project)).

A standalone clone of this repository (`scripts/setup.sh`) is only for working on the toolchain itself.

Options:

- `--install`: install missing system packages with `sudo apt-get` (Debian and Ubuntu only). Without it, setup prints the missing tools and the `apt-get` command, then stops. The compiler must be at least `CLANG_MIN_VERSION` in [versions.env](versions.env); `--install` installs it and links the tools by their plain names into `/usr/local/bin`. It needs a distribution that packages clang-`CLANG_MIN_VERSION` (Ubuntu 24.04 does; Ubuntu 22.04 and Debian 12 don't).
- `--native-tests`: also require a native C++ compiler, for host-side tests.
- `--check`: stop after the prerequisite check, before installing llvm-mingw, xwin, the sysroot and vcpkg. Combined with `--install`, the missing system packages are still installed.

Setup installs, in order, only what is missing, and everything but the system packages goes under `$HOME`. Re-run it after updating the toolchain submodule, so llvm-mingw, xwin and vcpkg follow the new pins.

| Step | What | Where |
| --- | --- | --- |
| 1 | System packages (check only, unless `--install`) | system |
| 2 | llvm-mingw | `~/.local/opt/llvm-mingw` (or your `LLVM_MINGW_BIN`) |
| 3 | xwin | `~/.local/bin/xwin` |
| 4 | Windows SDK and MSVC CRT sysroot (accepts Microsoft's license) | `~/xwin-out` (or your `XWIN_SYSROOT`) |
| 5 | vcpkg at the pinned baseline | `~/.local/share/vcpkg` |
| 6 | The toolchain's own submodules | the toolchain checkout |

If `VCPKG_ROOT` already points to a vcpkg elsewhere, setup leaves it alone and only warns when it is not at the pinned baseline.

Downloads are verified against pinned checksums. Tool versions are in [versions.env](versions.env), the vcpkg baseline is in [vcpkg-configuration.json](vcpkg-configuration.json).

Setup ends by printing the `export` lines (`VCPKG_ROOT`, `XWIN_SYSROOT`, `LLVM_MINGW_BIN`, `PATH`) to add to your shell profile. Projects need them to configure a build.

## Use it in a project

[tests/consumer](tests/consumer) is a complete, working project. Copy from it rather than from the steps below, but delete its `lib/skyrim-mod-toolchain` symlink, which points back to this repository, before adding the real submodule.

1. **Add the toolchain as a submodule at `lib/skyrim-mod-toolchain` under the CMake source directory**, the directory that holds the project's `CMakePresets.json` and `vcpkg.json` (the repository root, or a subdirectory such as `plugin/`). The path is required: the shared preset locates its triplet, ports and CommonLibSSE-NG relative to the project source directory, because CMake expands `${fileDir}` against the including file. Then `git submodule update --init --recursive`. Pin the submodule to a release tag; how tags are versioned is in [docs/PRD.md#9-open-questions](docs/PRD.md#9-open-questions) (question 3).
2. **Symlink the shared files** into the CMake source directory: `vcpkg-configuration.json`, `.clang-format` and `.clang-tidy`, each to its file in `lib/skyrim-mod-toolchain/`. Keep the project's own `vcpkg.json` for its dependencies.
3. **Include the preset** from the project's `CMakePresets.json` (`lib/skyrim-mod-toolchain/cmake/CMakePresets.json`) and inherit the hidden preset `skse-clangcl` in your own presets. The preset sets the compiler, the static runtime and the warning flags; see [cmake/CMakePresets.json](cmake/CMakePresets.json).
4. **Include the CommonLibSSE-NG helper** in `CMakeLists.txt` after `project()`, with `include(lib/skyrim-mod-toolchain/cmake/commonlibsse.cmake)`. Set `ENABLE_SKYRIM_SE`, `ENABLE_SKYRIM_AE` and `ENABLE_SKYRIM_VR` yourself first. Then call `add_commonlibsse_plugin()`.
5. **Give the plugin a precompiled header** that includes `<RE/Skyrim.h>` and `<SKSE/SKSE.h>` and has `using namespace std::literals;`. The generated plugin source needs it ([tests/consumer/src/PCH.h](tests/consumer/src/PCH.h)).
6. **Build:** `cmake --preset <your preset>` and `cmake --build --preset <your preset>`, with the exports from setup in the environment.
7. **CI:** after checking out with `submodules: recursive`, set the toolchain up with the action from the submodule, `uses: ./lib/skyrim-mod-toolchain/.github/actions/setup`. If the CMake source directory is not the repository root, include it in the path and pass it as `source-dir` (for `plugin/`: `uses: ./plugin/lib/skyrim-mod-toolchain/.github/actions/setup` with `source-dir: plugin`). The action caches the toolchain and the vcpkg packages. The `cross-build` job in [.github/workflows/ci.yml](.github/workflows/ci.yml) is a working example; the inputs and outputs are in [.github/actions/setup/action.yml](.github/actions/setup/action.yml).

Checks to run on a project, with its own source folders:

- `lib/skyrim-mod-toolchain/scripts/check_runtime_deps.py <plugin.dll>...` fails the build if a DLL imports the redistributable C++ runtime. Exit statuses: [scripts/check_runtime_deps.py](scripts/check_runtime_deps.py).
- `lib/skyrim-mod-toolchain/scripts/format.sh [--check] <path>...` formats the C++ sources under the paths with the pinned clang-format; `--check` only reports.
- `lib/skyrim-mod-toolchain/scripts/lint.sh <build-dir> <path>...` runs clang-tidy on the sources under the paths. The build directory of the shared preset is `build/<preset name>`.

## Steam Deck

Run setup inside an Ubuntu 24.04 distrobox, not on SteamOS itself (from your project, as in [Setup](#setup)):

```sh
distrobox create --name skse --image ubuntu:24.04
distrobox enter skse
lib/skyrim-mod-toolchain/scripts/setup.sh --install
```

`--install` runs `apt-get update` first, which a fresh distrobox needs.

## Tests

`scripts/test.sh` runs the unit tests and shellcheck. The cross build of `tests/consumer`, and lint on it, run only when the toolchain is set up (`VCPKG_ROOT` and the sysroot exist); set `REQUIRE_CROSS_BUILD=1` to fail instead of skipping when it cannot run.

## Contributing

Branches, commits and pull requests: [CONTRIBUTING.md](CONTRIBUTING.md).

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
