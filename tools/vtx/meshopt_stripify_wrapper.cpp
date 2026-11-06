#include "meshoptimizer.h"

#include <cstddef>

#if defined(_WIN32)
#define VTX_EXPORT extern "C" __declspec(dllexport)
#else
#define VTX_EXPORT extern "C"
#endif

VTX_EXPORT std::size_t probe_stripify_bound(std::size_t index_count)
{
    return meshopt_stripifyBound(index_count);
}

VTX_EXPORT std::size_t probe_stripify(
    unsigned int* destination,
    const unsigned int* indices,
    std::size_t index_count,
    std::size_t vertex_count)
{
    // Source VTX v7 has no primitive-restart field. Zero tells
    // meshoptimizer to join strip runs with degenerate triangles instead.
    return meshopt_stripify(destination, indices, index_count, vertex_count, 0);
}

VTX_EXPORT std::size_t probe_unstripify_bound(std::size_t index_count)
{
    return meshopt_unstripifyBound(index_count);
}

VTX_EXPORT std::size_t probe_unstripify(
    unsigned int* destination,
    const unsigned int* indices,
    std::size_t index_count)
{
    return meshopt_unstripify(destination, indices, index_count, 0);
}
