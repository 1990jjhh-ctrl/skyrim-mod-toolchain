cmake_minimum_required(VERSION 3.25)

project(ToolchainConsumer VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(ENABLE_SKYRIM_SE ON CACHE BOOL "" FORCE)
set(ENABLE_SKYRIM_AE ON CACHE BOOL "" FORCE)
set(ENABLE_SKYRIM_VR OFF CACHE BOOL "" FORCE)

# lib/skyrim-mod-toolchain is a symlink to the repo root here; a real project
# has the toolchain as a submodule at that path.
include(lib/skyrim-mod-toolchain/cmake/commonlibsse.cmake)

add_commonlibsse_plugin(ToolchainConsumer SOURCES src/main.cpp)

# The plugin's generated sources include CommonLibSSE headers that need the
# standard library already included, so every plugin needs a PCH like this.
target_precompile_headers(ToolchainConsumer PRIVATE src/PCH.h)
