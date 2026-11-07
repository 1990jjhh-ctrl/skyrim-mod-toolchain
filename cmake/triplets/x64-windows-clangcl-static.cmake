# Static-CRT twin of CommonLibSSE-NG's examples/linux-cross-compile triplet.
#
# The plugin links the C++ runtime statically (/MT) so it never depends on the
# player's msvcp140.dll -- see scripts/check_runtime_deps.py in this toolchain
# for why that matters. Every dependency has to match, or the link fails on the
# runtime mismatch.
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE static)
set(VCPKG_LIBRARY_LINKAGE static)

# The plugin only ever builds Release, so skip the Debug half of every port.
set(VCPKG_BUILD_TYPE release)

# vcpkg turns VCPKG_CRT_LINKAGE into /MT only inside its own windows.cmake
# toolchain, and vcpkg_cmake_configure.cmake uses that toolchain only when no
# toolchain is chainloaded. This triplet chainloads, so without the options
# below VCPKG_CRT_LINKAGE would be silently ignored and every port would build
# /MD. CMP0091 NEW makes CMAKE_MSVC_RUNTIME_LIBRARY take effect even in ports
# whose cmake_minimum_required predates it.
set(VCPKG_CMAKE_CONFIGURE_OPTIONS
	"-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded"
	"-DCMAKE_POLICY_DEFAULT_CMP0091=NEW")

# Do not set VCPKG_CMAKE_SYSTEM_NAME (see the upstream triplet for why); the
# chainloaded toolchain does the real targeting.
set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/../../lib/commonlibsse-ng/cmake/toolchain-linux-clangcl.cmake")
