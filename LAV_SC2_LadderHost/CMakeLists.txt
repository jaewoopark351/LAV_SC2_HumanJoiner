cmake_minimum_required(VERSION 3.6)

if (POLICY CMP0091)
    cmake_policy(SET CMP0091 NEW)
endif ()

project(Sc2LadderServer)

#20260709_kpopmodder: Match MicroMachine's prebuilt s2client-api libs, which were built with the static MSVC runtime.
if (MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif ()

# Use bin as the directory for all executables.
set(EXECUTABLE_OUTPUT_PATH ${PROJECT_BINARY_DIR}/bin)
set(LIBRARY_OUTPUT_PATH ${PROJECT_BINARY_DIR}/bin)

# Windows builds subdirectories Debug/Release.
# These variables will overwrite that and put binaries in bin.
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY_DEBUG ${PROJECT_BINARY_DIR}/bin)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY_DEBUG ${PROJECT_BINARY_DIR}/bin)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_DEBUG ${PROJECT_BINARY_DIR}/bin)

set(CMAKE_LIBRARY_OUTPUT_DIRECTORY_RELEASE ${PROJECT_BINARY_DIR}/bin)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY_RELEASE ${PROJECT_BINARY_DIR}/bin)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE ${PROJECT_BINARY_DIR}/bin)

set(CMAKE_LIBRARY_OUTPUT_DIRECTORY_MINSIZEREL ${PROJECT_BINARY_DIR}/bin)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY_MINSIZEREL ${PROJECT_BINARY_DIR}/bin)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL ${PROJECT_BINARY_DIR}/bin)

set(CMAKE_LIBRARY_OUTPUT_DIRECTORY_RELWITHDEBINFO ${PROJECT_BINARY_DIR}/bin)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY_RELWITHDEBINFO ${PROJECT_BINARY_DIR}/bin)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO ${PROJECT_BINARY_DIR}/bin)

# Build with c++14 support, required by sc2api.
set(CMAKE_CXX_STANDARD 14)

# Allow creating filters for projects in visual studio.
set_property(GLOBAL PROPERTY USE_FOLDERS ON)

# Disable building of examples in the sc2api submodule.
set(BUILD_API_EXAMPLES OFF CACHE INTERNAL "" FORCE)

# Disable building of tests in the sc2api submodule.
set(BUILD_API_TESTS OFF CACHE INTERNAL "" FORCE)

#20260709_kpopmodder: Allow local builds to reuse an existing cpp-sc2 checkout or prebuilt libs when the git submodule is missing.
set(LAV_SC2_API_ROOT "" CACHE PATH "Optional path to an existing cpp-sc2/s2client-api checkout")
set(LAV_SC2_API_PREBUILT_ROOT "" CACHE PATH "Optional path to a prebuilt cpp-sc2 build directory containing bin/*.lib and generated headers")
if (LAV_SC2_API_PREBUILT_ROOT AND LAV_SC2_API_ROOT)
    set(SC2_API_SOURCE_DIR "${LAV_SC2_API_ROOT}")
    set(SC2_API_GENERATED_DIR "${LAV_SC2_API_PREBUILT_ROOT}/generated")
    foreach(SC2_IMPORTED_TARGET sc2api sc2lib sc2utils sc2protocol civetweb libprotobuf ipv6-parse)
        add_library(${SC2_IMPORTED_TARGET} STATIC IMPORTED GLOBAL)
        set_target_properties(${SC2_IMPORTED_TARGET} PROPERTIES IMPORTED_LOCATION "${LAV_SC2_API_PREBUILT_ROOT}/bin/${SC2_IMPORTED_TARGET}.lib")
    endforeach()
elseif (EXISTS "${PROJECT_SOURCE_DIR}/cpp-sc2/CMakeLists.txt")
    set(SC2_API_SOURCE_DIR "${PROJECT_SOURCE_DIR}/cpp-sc2")
    set(SC2_API_GENERATED_DIR "${PROJECT_BINARY_DIR}/cpp-sc2/generated")
    add_subdirectory("cpp-sc2")
elseif (LAV_SC2_API_ROOT AND EXISTS "${LAV_SC2_API_ROOT}/CMakeLists.txt")
    set(SC2_API_SOURCE_DIR "${LAV_SC2_API_ROOT}")
    set(SC2_API_GENERATED_DIR "${PROJECT_BINARY_DIR}/cpp-sc2/generated")
    add_subdirectory("${LAV_SC2_API_ROOT}" "${PROJECT_BINARY_DIR}/cpp-sc2")
else ()
    message(FATAL_ERROR "cpp-sc2 submodule is missing. Set LAV_SC2_API_ROOT to an existing s2client-api checkout.")
endif ()
add_subdirectory("src")
add_subdirectory("tests")

# set Sc2LadderServer as startup project
set_property(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR} PROPERTY VS_STARTUP_PROJECT Sc2LadderServer)

# set folders/subfolders
# there might be a better way to do this than
# manually setting all these, but it works...
set_target_properties(sc2api PROPERTIES FOLDER sc2api)
set_target_properties(sc2lib PROPERTIES FOLDER sc2api)
set_target_properties(sc2protocol PROPERTIES FOLDER sc2api)
set_target_properties(sc2utils PROPERTIES FOLDER sc2api)

if (TARGET civetweb-c-library)
    set_target_properties(civetweb-c-library PROPERTIES FOLDER sc2api/contrib)
endif ()
set_target_properties(libprotobuf PROPERTIES FOLDER sc2api/contrib)
if (TARGET libprotobuf-lite)
    set_target_properties(libprotobuf-lite PROPERTIES FOLDER sc2api/contrib)
endif ()
if (TARGET libprotoc)
    set_target_properties(libprotoc PROPERTIES FOLDER sc2api/contrib)
endif ()
if (TARGET protoc)
    set_target_properties(protoc PROPERTIES FOLDER sc2api/contrib)
endif ()

# SC2 API doesn't build SDL and ipv6 libs on OS X.
if (NOT APPLE)
    if (TARGET sc2renderer)
    set_target_properties(sc2renderer PROPERTIES FOLDER sc2api/utilities)
endif ()
    set_target_properties(ipv6-parse PROPERTIES FOLDER sc2api/contrib)
    if (TARGET SDL2main)
    set_target_properties(SDL2main PROPERTIES FOLDER sc2api/contrib)
endif ()
    if (TARGET SDL2-static)
    set_target_properties(SDL2-static PROPERTIES FOLDER sc2api/contrib)
endif ()
endif ()
