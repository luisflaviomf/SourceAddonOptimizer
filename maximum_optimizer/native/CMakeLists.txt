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
    "${MESHOPT_SRC}/clusterizer.cpp"
    "${MESHOPT_SRC}/indexanalyzer.cpp"
    "${MESHOPT_SRC}/indexcodec.cpp"
    "${MESHOPT_SRC}/indexgenerator.cpp"
    "${MESHOPT_SRC}/meshletcodec.cpp"
    "${MESHOPT_SRC}/meshletutils.cpp"
    "${MESHOPT_SRC}/opacitymap.cpp"
    "${MESHOPT_SRC}/overdrawoptimizer.cpp"
    "${MESHOPT_SRC}/partition.cpp"
    "${MESHOPT_SRC}/quantization.cpp"
    "${MESHOPT_SRC}/rasterizer.cpp"
    "${MESHOPT_SRC}/simplifier.cpp"
    "${MESHOPT_SRC}/spatialorder.cpp"
    "${MESHOPT_SRC}/stripifier.cpp"
    "${MESHOPT_SRC}/tangentspace.cpp"
    "${MESHOPT_SRC}/vcacheoptimizer.cpp"
    "${MESHOPT_SRC}/vertexcodec.cpp"
    "${MESHOPT_SRC}/vertexfilter.cpp"
    "${MESHOPT_SRC}/vfetchoptimizer.cpp"
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
