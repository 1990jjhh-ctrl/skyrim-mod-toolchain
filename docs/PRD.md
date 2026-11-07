# skyrim-mod-toolchain — Product Requirements Document

**Status:** Draft · **Author:** Joel Hirsbrunner · **Date:** 2026-11-07

## 1. Summary

skyrim-mod-toolchain is the shared toolchain for cross-compiling SKSE plugins
with CommonLibSSE-NG on a Linux host. It replaces the setup, build-helper,
format/lint, style and CI code that skyrim-mcp, trials-of-mastery and
beyond-mastery each carry today, and becomes the one place that pins the tool
versions, CommonLibSSE-NG and the vcpkg baseline. The repo is public.

## 2. Problem statement

The three projects started from the same setup and copied it. The copies have
drifted:

- Fixes made in one copy are missing in the others. In trials-of-mastery and
  beyond-mastery an interrupted xwin download is never retried, a partial vcpkg
  clone breaks setup, and a freshly installed xwin is not found in the same run.
  skyrim-mcp has all three fixes.
- Pinned versions (llvm-mingw, xwin) are repeated in each project's setup and
  again in CI, where they are copied or parsed out of the setup script.
- The projects build against different CommonLibSSE-NG commits.

Every toolchain change has to be made three times, and a missed copy fails
later, on a fresh machine or in CI.

## 3. Goals & success metrics

| Goal | Signal that it was met |
|---|---|
| One-command setup | On a fresh Ubuntu 24.04, cloning any of the three projects, running one setup command and then the project's build produces its DLLs. |
| Pins in one place | Changing a tool version, the CommonLibSSE-NG version or the vcpkg baseline is one edit in this repo; projects pick it up by updating the submodule, and their CI uses the new version without further edits. |
| No duplicated copies | None of the three projects keeps its own copy of the setup script, vcpkg triplet, static-runtime check, format/lint scripts, `.clang-format`/`.clang-tidy`, or toolchain install steps in CI. |

## 4. Target users & use cases

- **Primary:** the author, building and maintaining skyrim-mcp,
  trials-of-mastery and beyond-mastery on Linux and in GitHub Actions.
- **Secondary:** other SKSE modders on Linux who find the public repo. They
  get documentation good enough to use it unaided; there is no support
  commitment.

Key scenarios:

1. Set up a new machine (Ubuntu, or an Ubuntu distrobox on a Steam Deck) and
   build a project.
2. Bump a pinned version once and roll it out to all projects.
3. Run CI for a project with the toolchain installed and cached the same way
   as locally.
4. Start a new SKSE plugin project on the toolchain.

## 5. Scope

### In scope (v1)

- Setup script that checks, and on request installs, the host prerequisites
  and installs the user-level tools.
- Build helpers: vcpkg triplet, static-runtime check, CMake helper for
  building CommonLibSSE-NG from source, base CMake preset.
- Format and lint scripts.
- Shared `.clang-format` and `.clang-tidy`.
- Reusable GitHub Action that installs and caches the toolchain.
- The pinned CommonLibSSE-NG version and vcpkg baseline.
- Migrating skyrim-mcp, trials-of-mastery and beyond-mastery onto the
  toolchain and deleting their copies.

### Out of scope / non-goals

- Windows or macOS hosts. Linux host only.
- Packaging or releasing mods (Nexus/FOMOD packages, Papyrus compilation, ESP
  generation). These stay in the projects.
- Skyrim VR or SE-1.5.97-specific work. The toolchain adds nothing for these
  runtimes; a project that enables SE in CommonLibSSE-NG keeps working.
- Per-project vcpkg dependency lists and the list of DLLs a project builds.
  These stay in the projects.

## 6. Requirements

| ID | Requirement | Priority |
|---|---|---|
| R1 | Setup shall check all host prerequisites and list the missing ones with the install command for Ubuntu/Debian. | Must |
| R2 | Setup shall install missing system packages with `sudo` only when passed `--install`; by default it shall not use `sudo`. | Must |
| R3 | Setup shall install the user-level tools (llvm-mingw, xwin and its sysroot, vcpkg) at pinned versions, without root. | Must |
| R4 | Setup shall be safe to re-run and shall resume after an interrupted download or clone. | Must |
| R5 | Setup shall reject a set but invalid tool location override instead of falling back to another installation. | Must |
| R6 | Each pinned version (tools, CommonLibSSE-NG, vcpkg baseline) shall be defined exactly once in this repo and read from there by setup, build helpers and the CI action. | Must |
| R7 | The toolchain shall provide the vcpkg triplet that links the C++ runtime statically, and a check that fails the build if a plugin DLL imports the dynamic C++ runtime. | Must |
| R8 | The toolchain shall provide a CMake helper and base preset that let a project build CommonLibSSE-NG from source and cross-compile its plugin with clang-cl. | Must |
| R9 | The toolchain shall provide format and lint scripts that take the project's source folders as arguments and use the pinned clang-format/clang-tidy, with a check mode for CI. | Must |
| R10 | The toolchain shall provide shared `.clang-format` and `.clang-tidy` configs. | Must |
| R11 | The toolchain shall provide a reusable GitHub Action that installs and caches the toolchain at the pinned versions. | Must |
| R12 | Projects shall consume the toolchain as a git submodule pinned to a commit or tag, and reference the CI action by tag. | Must |
| R13 | skyrim-mcp, trials-of-mastery and beyond-mastery shall build locally and in CI through the toolchain, with their duplicated files removed. | Must |
| R14 | Setup shall support native unit-test prerequisites (host C++ compiler, Python) as an option for projects that need them. | Should |
| R15 | The README shall document running setup inside an Ubuntu distrobox on SteamOS. | Should |
| R16 | The README shall document how to start a new SKSE plugin project on the toolchain. | Could |

## 7. Constraints & dependencies

- Supported hosts: Ubuntu/Debian (including the GitHub Actions `ubuntu-24.04`
  runner) and Steam Deck via an Ubuntu distrobox.
- Depends on CommonLibSSE-NG, xwin (which requires accepting Microsoft's
  license for the MSVC CRT and Windows SDK), vcpkg, llvm-mingw and Wine.
- License: GPL-3.0-or-later, matching the three projects and CommonLibSSE-NG.

## 8. Assumptions & risks

| Assumption or risk | Likelihood / impact | Mitigation |
|---|---|---|
| All three projects can build against the same CommonLibSSE-NG version. trials-of-mastery pins `8eadded` today, the others v13.1.0. | Medium / High | Move trials-of-mastery to the shared version during migration; projects can stay on an older toolchain tag until they are ready. |
| Lockstep pins force a project to upgrade before it is ready. | Medium / Medium | Projects pin the submodule; a bump reaches a project only when it updates the submodule. |
| vcpkg expects its baseline configuration in the project root, which may conflict with the toolchain owning it. | Resolved | Projects symlink the configuration; see section 9, question 1. |
| Wine and the xwin sysroot behave the same inside a distrobox on SteamOS. | Low / Medium | Verify on a Deck before marking R15 done. |
| Public users expect support. | Low / Low | README states there is no support commitment. |

## 9. Open questions

None open. Resolved questions and their answers:

1. **How does a project use the toolchain's vcpkg baseline when vcpkg reads
   its configuration from the project root?** Each project's
   `vcpkg-configuration.json` is a git symlink to the toolchain's file. vcpkg
   follows the symlink (tested 2026-11-07), version constraints in the
   project's `vcpkg.json` keep working, and symlinks are fine because the host
   is Linux only.
2. **Which static-runtime check becomes the shared one?** The Python PE
   import parser from trials-of-mastery, which needs no other file. The unit
   tests from beyond-mastery (`tests/test_runtime_deps.py`) move along with it,
   adapted to that version.
3. **How are toolchain tags versioned?** Semantic versioning by the impact on
   projects: a major release when a project has to change something (for
   example a renamed script or option, or a CommonLibSSE-NG bump that breaks
   the build); a minor release for new features and pin bumps that need no
   project changes; a patch release for fixes.

## 10. Milestones

1. Toolchain repo with setup, pins, build helpers, format/lint and style
   configs.
2. Reusable CI action.
3. Migrate beyond-mastery, then skyrim-mcp, then trials-of-mastery (which
   also moves to the shared CommonLibSSE-NG version).
