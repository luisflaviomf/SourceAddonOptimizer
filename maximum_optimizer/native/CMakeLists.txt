cmake_minimum_required(VERSION 3.20)
project(maximum_meshopt_bridge LANGUAGES CXX)

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "maximum meshoptimizer bridge must be built for x64")
endif()

set(MESHOPT_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../third_party/meshoptimizer")
set(MESHOPT_SRC "${MESHOPT_ROOT}/src")

add_library(meshopt_bridge SHARED
    meshopt_bridge.cpp
    "${MESHOPT_SRC}/allocator.cpp"
    "${MESHOPT_SRC}/indexgenerator.cpp"
    "${MESHOPT_SRC}/simplifier.cpp"
)
target_include_directories(meshopt_bridge PRIVATE "${MESHOPT_SRC}")
target_compile_features(meshopt_bridge PRIVATE cxx_std_17)
target_compile_definitions(meshopt_bridge PRIVATE MESHOPTIMIZER_NO_WRAPPERS)

if(MSVC)
    target_compile_options(meshopt_bridge PRIVATE /W4 /WX /EHsc /guard:cf /Brepro)
    target_link_options(meshopt_bridge PRIVATE /guard:cf /DYNAMICBASE /NXCOMPAT /Brepro)
    set_property(TARGET meshopt_bridge PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
else()
    message(FATAL_ERROR "maximum meshoptimizer bridge currently requires MSVC")
endif()
