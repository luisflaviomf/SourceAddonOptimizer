# meshoptimizer v1.2 (third_party/meshoptimizer) as a plain C-API DLL for model_screen (ctypes).
cmake_minimum_required(VERSION 3.15)
project(meshopt_simplify CXX)
file(GLOB SRC "${CMAKE_CURRENT_LIST_DIR}/../../third_party/meshoptimizer/src/*.cpp")
add_library(meshopt_simplify SHARED ${SRC})
target_compile_definitions(meshopt_simplify PRIVATE "MESHOPTIMIZER_API=__declspec(dllexport)")
set_property(TARGET meshopt_simplify PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")
