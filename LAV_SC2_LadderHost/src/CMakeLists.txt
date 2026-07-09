# Source files
file(GLOB SOURCES_SC2LADDERSERVER "sc2ladderserver/*.cpp" "sc2ladderserver/*.h")
file(GLOB SOURCES_LAVHUMANVSBOT "lavhumanvsbot/*.cpp" "lavhumanvsbot/*.h")
file(GLOB SOURCES_SC2LADDERCORE "sc2laddercore/*.cpp" "sc2laddercore/*.h")

# Include directories
include_directories(SYSTEM
    ${PROJECT_SOURCE_DIR}/src/sc2laddercore
    ${SC2_API_SOURCE_DIR}/include
    ${SC2_API_SOURCE_DIR}/contrib/protobuf/src
        ${SC2_API_SOURCE_DIR}/contrib/civetweb/include
${SC2_API_GENERATED_DIR}
    ${PROJECT_SOURCE_DIR}/rapidjson
)

if (WIN32)
    include_directories(SYSTEM
        ${SC2_API_SOURCE_DIR}/src/sc2utils/
    )
endif ()

# Link directories
link_directories(${PROJECT_BINARY_DIR} ${PROJECT_BINARY_DIR}/cpp-sc2/bin)


# Create the executable.
add_executable(Sc2LadderServer ${SOURCES_SC2LADDERSERVER})
#20260709_kpopmodder: Small local launcher for LAV human-vs-Changeling tests without Qt/SC2AIApp UI automation.
add_executable(LavHumanVsBot ${SOURCES_LAVHUMANVSBOT})
add_library(Sc2LadderCore ${SOURCES_SC2LADDERCORE})
target_link_libraries(Sc2LadderServer
    Sc2LadderCore
)
target_link_libraries(LavHumanVsBot
    Sc2LadderCore sc2api sc2lib sc2utils sc2protocol civetweb libprotobuf ipv6-parse
)
target_link_libraries(Sc2LadderCore
    sc2api sc2lib sc2utils sc2protocol civetweb libprotobuf ipv6-parse
)


# Set working directory as the project root
set_target_properties(Sc2LadderServer PROPERTIES VS_DEBUGGER_WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}")