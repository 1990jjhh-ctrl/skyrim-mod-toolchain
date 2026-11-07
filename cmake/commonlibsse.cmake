# Builds CommonLibSSE-NG from the toolchain's submodule and makes
# add_commonlibsse_plugin() available.
#
# Set ENABLE_SKYRIM_SE / ENABLE_SKYRIM_AE / ENABLE_SKYRIM_VR (and any other
# CommonLibSSE-NG option) before including this file; they are left to the
# project.
include_guard(GLOBAL)

set(BUILD_TESTS OFF CACHE BOOL "Build the CommonLibSSE-NG tests")

get_filename_component(_toolchain_root "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
add_subdirectory(
	"${_toolchain_root}/lib/commonlibsse-ng"
	"${CMAKE_BINARY_DIR}/commonlibsse-ng"
	EXCLUDE_FROM_ALL
)

# CommonLibSSE-NG's own CMakeLists only includes this file on the prebuilt-binary
# path, so a from-source consumer includes it itself.
get_target_property(_commonlib_src_dir CommonLibSSE SOURCE_DIR)
include("${_commonlib_src_dir}/cmake/CommonLibSSE.cmake")

unset(_toolchain_root)
unset(_commonlib_src_dir)
