#include "meshoptimizer.h"

#include <cstddef>

#if defined(_WIN32)
#define VTX_EXPORT extern "C" __declspec(dllexport)
#else
#define VTX_EXPORT extern "C"
#endif

// Triangle order for strips (model_stream/encoder.py): meshoptimizer reorders the triangles for a
// strip-like vertex cache before meshopt_stripify, which then finds longer strips. The triangles and
// their winding are kept; only their order changes.
VTX_EXPORT void probe_vcache_strip(
    unsigned int* destination,
    const unsigned int* indices,
    std::size_t index_count,
    std::size_t vertex_count)
{
    meshopt_optimizeVertexCacheStrip(destination, indices, index_count, vertex_count);
}
