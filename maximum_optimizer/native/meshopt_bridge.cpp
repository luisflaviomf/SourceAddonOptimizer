#include "meshoptimizer.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <map>
#include <memory>
#include <new>
#include <utility>
#include <vector>

#if !defined(_WIN32) || !defined(_WIN64)
#error maximum meshoptimizer bridge requires 64-bit Windows
#endif

namespace
{
constexpr unsigned int kAttributeCount = 9;
constexpr unsigned int kKnownMeshoptOptions = (1u << 7) - 1;
constexpr unsigned char kKnownVertexFlags = (1u << 3) - 1;

struct MaximumMeshInput
{
    std::uint32_t struct_size;
    const float* positions;
    const float* normals;
    const float* uvs;
    const float* weights;
    std::size_t vertex_count;
    const std::uint32_t* indices;
    std::size_t index_count;
    const std::uint32_t* material_ids;
    std::size_t triangle_count;
    const unsigned char* vertex_flags;
};

struct MaximumMeshOptions
{
    std::uint32_t struct_size;
    float target_ratio;
    float target_error;
    std::uint32_t meshopt_options;
    std::uint32_t update_vertices;
};

struct MaximumMeshOutput
{
    std::uint32_t struct_size;
    float* positions;
    float* normals;
    float* uvs;
    float* weights;
    std::size_t vertex_count;
    std::uint32_t* indices;
    std::size_t index_count;
    std::uint32_t* material_ids;
    std::size_t triangle_count;
    float result_error;
};

enum ErrorCode
{
    ErrorNullPointer = -1,
    ErrorStructSize = -2,
    ErrorCount = -3,
    ErrorData = -4,
    ErrorOptions = -5,
    ErrorAllocation = -6,
    ErrorSimplifier = -7,
    ErrorException = -8,
};

bool can_multiply(std::size_t a, std::size_t b)
{
    return b == 0 || a <= std::numeric_limits<std::size_t>::max() / b;
}

bool finite_values(const float* values, std::size_t count)
{
    for (std::size_t index = 0; index < count; ++index)
        if (!std::isfinite(values[index]))
            return false;
    return true;
}

void normalize3(float* values)
{
    const float length2 = values[0] * values[0] + values[1] * values[1] + values[2] * values[2];
    if (!(length2 > 1e-20f) || !std::isfinite(length2))
    {
        values[0] = 0.f;
        values[1] = 0.f;
        values[2] = 1.f;
        return;
    }
    const float inverse = 1.f / std::sqrt(length2);
    values[0] *= inverse;
    values[1] *= inverse;
    values[2] *= inverse;
}

bool normalize_weights(float* values)
{
    float total = 0.f;
    for (unsigned int index = 0; index < 4; ++index)
    {
        values[index] = std::max(0.f, std::min(1.f, values[index]));
        total += values[index];
    }
    if (!(total > 1e-12f) || !std::isfinite(total))
        return false;
    for (unsigned int index = 0; index < 4; ++index)
        values[index] /= total;
    return true;
}

std::pair<std::uint32_t, std::uint32_t> edge_key(std::uint32_t a, std::uint32_t b)
{
    return a < b ? std::make_pair(a, b) : std::make_pair(b, a);
}

void reset_output(MaximumMeshOutput* output)
{
    output->positions = nullptr;
    output->normals = nullptr;
    output->uvs = nullptr;
    output->weights = nullptr;
    output->vertex_count = 0;
    output->indices = nullptr;
    output->index_count = 0;
    output->material_ids = nullptr;
    output->triangle_count = 0;
    output->result_error = 0.f;
}
} // namespace

extern "C" __declspec(dllexport) int maximum_meshopt_version()
{
    return 10200;
}

extern "C" __declspec(dllexport) void maximum_meshopt_destroy(MaximumMeshOutput* output)
{
    if (output == nullptr || output->struct_size != sizeof(MaximumMeshOutput))
        return;
    delete[] output->positions;
    delete[] output->normals;
    delete[] output->uvs;
    delete[] output->weights;
    delete[] output->indices;
    delete[] output->material_ids;
    reset_output(output);
}

extern "C" __declspec(dllexport) int maximum_meshopt_simplify(
    const MaximumMeshInput* input,
    const MaximumMeshOptions* options,
    MaximumMeshOutput* output)
{
    if (input == nullptr || options == nullptr || output == nullptr)
        return ErrorNullPointer;
    if (input->struct_size != sizeof(MaximumMeshInput) ||
        options->struct_size != sizeof(MaximumMeshOptions) ||
        output->struct_size != sizeof(MaximumMeshOutput))
        return ErrorStructSize;
    reset_output(output);

    try
    {
        if (input->vertex_count == 0 || input->index_count < 3 || input->index_count % 3 != 0 ||
            input->triangle_count != input->index_count / 3 ||
            !can_multiply(input->vertex_count, 3) || !can_multiply(input->vertex_count, 4) ||
            !can_multiply(input->vertex_count, kAttributeCount))
            return ErrorCount;
        if (input->positions == nullptr || input->normals == nullptr || input->uvs == nullptr ||
            input->weights == nullptr || input->indices == nullptr || input->material_ids == nullptr ||
            input->vertex_flags == nullptr)
            return ErrorNullPointer;
        if (!std::isfinite(options->target_ratio) || options->target_ratio <= 0.f || options->target_ratio > 1.f ||
            !std::isfinite(options->target_error) || options->target_error < 0.f ||
            options->update_vertices > 1 || (options->meshopt_options & ~kKnownMeshoptOptions) != 0)
            return ErrorOptions;
        if (!finite_values(input->positions, input->vertex_count * 3) ||
            !finite_values(input->normals, input->vertex_count * 3) ||
            !finite_values(input->uvs, input->vertex_count * 2) ||
            !finite_values(input->weights, input->vertex_count * 4))
            return ErrorData;

        for (std::size_t index = 0; index < input->index_count; ++index)
            if (input->indices[index] >= input->vertex_count)
                return ErrorData;
        for (std::size_t index = 0; index < input->vertex_count; ++index)
            if ((input->vertex_flags[index] & ~kKnownVertexFlags) != 0)
                return ErrorData;

        std::vector<float> positions(input->positions, input->positions + input->vertex_count * 3);
        std::vector<float> attributes(input->vertex_count * kAttributeCount);
        for (std::size_t vertex = 0; vertex < input->vertex_count; ++vertex)
        {
            float* attribute = attributes.data() + vertex * kAttributeCount;
            std::copy_n(input->normals + vertex * 3, 3, attribute);
            normalize3(attribute);
            std::copy_n(input->uvs + vertex * 2, 2, attribute + 3);
            std::copy_n(input->weights + vertex * 4, 4, attribute + 5);
            if (!normalize_weights(attribute + 5))
                return ErrorData;
        }

        std::vector<unsigned char> vertex_flags(input->vertex_flags, input->vertex_flags + input->vertex_count);
        std::map<std::pair<std::uint32_t, std::uint32_t>, std::uint32_t> edge_material;
        std::map<std::pair<std::uint32_t, std::uint32_t>, bool> material_boundary;
        for (std::size_t triangle = 0; triangle < input->triangle_count; ++triangle)
        {
            const std::uint32_t* tri = input->indices + triangle * 3;
            const auto edges = {edge_key(tri[0], tri[1]), edge_key(tri[1], tri[2]), edge_key(tri[2], tri[0])};
            for (const auto& edge : edges)
            {
                const auto found = edge_material.find(edge);
                if (found == edge_material.end())
                    edge_material.emplace(edge, input->material_ids[triangle]);
                else if (found->second != input->material_ids[triangle])
                    material_boundary[edge] = true;
            }
        }
        for (const auto& entry : material_boundary)
        {
            vertex_flags[entry.first.first] |= meshopt_SimplifyVertex_Protect;
            vertex_flags[entry.first.second] |= meshopt_SimplifyVertex_Protect;
        }

        std::map<std::uint32_t, std::vector<std::uint32_t>> subsets;
        for (std::size_t triangle = 0; triangle < input->triangle_count; ++triangle)
        {
            auto& subset = subsets[input->material_ids[triangle]];
            subset.insert(subset.end(), input->indices + triangle * 3, input->indices + triangle * 3 + 3);
        }

        const float attribute_weights[kAttributeCount] = {1.f, 1.f, 1.f, 1.f, 1.f, 0.5f, 0.5f, 0.5f, 0.5f};
        std::vector<std::uint32_t> result_indices;
        std::vector<std::uint32_t> result_materials;
        result_indices.reserve(input->index_count);
        result_materials.reserve(input->triangle_count);
        float maximum_error = 0.f;

        for (const auto& entry : subsets)
        {
            std::vector<std::uint32_t> subset = entry.second;
            std::size_t target_triangles = static_cast<std::size_t>(
                std::floor(static_cast<double>(subset.size() / 3) * options->target_ratio));
            target_triangles = std::max<std::size_t>(1, std::min(target_triangles, subset.size() / 3));
            const std::size_t target_indices = target_triangles * 3;
            float result_error = 0.f;
            std::size_t result_count = 0;
            if (options->update_vertices != 0)
            {
                result_count = meshopt_simplifyWithUpdate(
                    subset.data(), subset.size(), positions.data(), input->vertex_count, sizeof(float) * 3,
                    attributes.data(), sizeof(float) * kAttributeCount, attribute_weights, kAttributeCount,
                    vertex_flags.data(), target_indices, options->target_error, options->meshopt_options, &result_error);
            }
            else
            {
                std::vector<std::uint32_t> destination(subset.size());
                result_count = meshopt_simplifyWithAttributes(
                    destination.data(), subset.data(), subset.size(), positions.data(), input->vertex_count,
                    sizeof(float) * 3, attributes.data(), sizeof(float) * kAttributeCount, attribute_weights,
                    kAttributeCount, vertex_flags.data(), target_indices, options->target_error,
                    options->meshopt_options, &result_error);
                subset.assign(destination.begin(), destination.begin() + result_count);
            }
            if (result_count == 0 || result_count > subset.size() || result_count % 3 != 0 || !std::isfinite(result_error))
                return ErrorSimplifier;
            subset.resize(result_count);
            result_indices.insert(result_indices.end(), subset.begin(), subset.end());
            result_materials.insert(result_materials.end(), result_count / 3, entry.first);
            maximum_error = std::max(maximum_error, result_error);
        }

        for (std::size_t vertex = 0; vertex < input->vertex_count; ++vertex)
        {
            float* attribute = attributes.data() + vertex * kAttributeCount;
            normalize3(attribute);
            if (!finite_values(attribute + 3, 2) || !normalize_weights(attribute + 5))
                return ErrorSimplifier;
        }

        std::unique_ptr<float[]> out_positions(new (std::nothrow) float[input->vertex_count * 3]);
        std::unique_ptr<float[]> out_normals(new (std::nothrow) float[input->vertex_count * 3]);
        std::unique_ptr<float[]> out_uvs(new (std::nothrow) float[input->vertex_count * 2]);
        std::unique_ptr<float[]> out_weights(new (std::nothrow) float[input->vertex_count * 4]);
        std::unique_ptr<std::uint32_t[]> out_indices(new (std::nothrow) std::uint32_t[result_indices.size()]);
        std::unique_ptr<std::uint32_t[]> out_materials(new (std::nothrow) std::uint32_t[result_materials.size()]);
        if (!out_positions || !out_normals || !out_uvs || !out_weights || !out_indices || !out_materials)
            return ErrorAllocation;

        std::copy(positions.begin(), positions.end(), out_positions.get());
        for (std::size_t vertex = 0; vertex < input->vertex_count; ++vertex)
        {
            const float* attribute = attributes.data() + vertex * kAttributeCount;
            std::copy_n(attribute, 3, out_normals.get() + vertex * 3);
            std::copy_n(attribute + 3, 2, out_uvs.get() + vertex * 2);
            std::copy_n(attribute + 5, 4, out_weights.get() + vertex * 4);
        }
        std::copy(result_indices.begin(), result_indices.end(), out_indices.get());
        std::copy(result_materials.begin(), result_materials.end(), out_materials.get());

        output->positions = out_positions.release();
        output->normals = out_normals.release();
        output->uvs = out_uvs.release();
        output->weights = out_weights.release();
        output->vertex_count = input->vertex_count;
        output->indices = out_indices.release();
        output->index_count = result_indices.size();
        output->material_ids = out_materials.release();
        output->triangle_count = result_materials.size();
        output->result_error = maximum_error;
        return 0;
    }
    catch (const std::bad_alloc&)
    {
        return ErrorAllocation;
    }
    catch (...)
    {
        return ErrorException;
    }
}
