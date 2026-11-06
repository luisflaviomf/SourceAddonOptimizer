#include "meshoptimizer.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <new>
#include <set>
#include <stdexcept>
#include <unordered_map>
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
    const std::uint32_t* bone_indices;
    std::size_t bone_count;
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
    std::uint32_t* bone_indices;
    std::size_t vertex_count;
    std::uint32_t* indices;
    std::size_t index_count;
    std::uint32_t* material_ids;
    std::size_t triangle_count;
    float result_error;
    std::uint64_t ownership_cookie;
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
    ErrorBusy = -9,
};

enum class OutputState
{
    Building,
    Owned,
    Destroying,
};

struct OutputRecord
{
    OutputState state = OutputState::Building;
    std::uint64_t cookie = 0;
    float* positions = nullptr;
    float* normals = nullptr;
    float* uvs = nullptr;
    float* weights = nullptr;
    std::uint32_t* bone_indices = nullptr;
    std::uint32_t* indices = nullptr;
    std::uint32_t* material_ids = nullptr;
};

std::mutex g_output_mutex;
std::unordered_map<MaximumMeshOutput*, OutputRecord> g_output_states;
std::atomic<std::uint64_t> g_cookie_counter{
    static_cast<std::uint64_t>(std::chrono::high_resolution_clock::now().time_since_epoch().count()) | 1u};

std::mutex g_test_hook_mutex;
std::condition_variable g_test_hook_condition;
std::uint32_t g_test_pause_point = 0;
std::uint32_t g_test_reached_point = 0;
bool g_test_hook_released = false;
std::atomic<std::uint32_t> g_test_failure{0};

void test_pause(std::uint32_t point) noexcept
{
    try
    {
        std::unique_lock<std::mutex> lock(g_test_hook_mutex);
        if (g_test_pause_point != point)
            return;
        g_test_reached_point = point;
        g_test_hook_condition.notify_all();
        g_test_hook_condition.wait(lock, [point]() {
            return g_test_hook_released || g_test_pause_point != point;
        });
        g_test_pause_point = 0;
        g_test_reached_point = 0;
        g_test_hook_released = false;
        g_test_hook_condition.notify_all();
    }
    catch (...)
    {
    }
}

bool can_multiply(std::size_t a, std::size_t b)
{
    return b == 0 || a <= std::numeric_limits<std::size_t>::max() / b;
}

bool can_add(std::size_t a, std::size_t b)
{
    return a <= std::numeric_limits<std::size_t>::max() - b;
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
    output->bone_indices = nullptr;
    output->vertex_count = 0;
    output->indices = nullptr;
    output->index_count = 0;
    output->material_ids = nullptr;
    output->triangle_count = 0;
    output->result_error = 0.f;
    output->ownership_cookie = 0;
}

class OutputBuildReservation
{
public:
    explicit OutputBuildReservation(MaximumMeshOutput* output) noexcept : output_(output) {}
    OutputBuildReservation(const OutputBuildReservation&) = delete;
    OutputBuildReservation& operator=(const OutputBuildReservation&) = delete;

    ~OutputBuildReservation() noexcept
    {
        rollback();
    }

    void activate() noexcept { active_ = true; }

    void rollback() noexcept
    {
        try
        {
            if (!active_)
                return;
            std::lock_guard<std::mutex> lock(g_output_mutex);
            const auto found = g_output_states.find(output_);
            if (found != g_output_states.end() && found->second.state == OutputState::Building)
            {
                reset_output(output_);
                g_output_states.erase(found);
            }
            active_ = false;
        }
        catch (...)
        {
        }
    }

    bool publish(
        std::unique_ptr<float[]>& positions,
        std::unique_ptr<float[]>& normals,
        std::unique_ptr<float[]>& uvs,
        std::unique_ptr<float[]>& weights,
        std::unique_ptr<std::uint32_t[]>& bone_indices,
        std::unique_ptr<std::uint32_t[]>& indices,
        std::unique_ptr<std::uint32_t[]>& material_ids,
        std::size_t vertex_count,
        std::size_t index_count,
        std::size_t triangle_count,
        float result_error)
    {
        std::lock_guard<std::mutex> lock(g_output_mutex);
        const auto found = g_output_states.find(output_);
        if (found == g_output_states.end() || found->second.state != OutputState::Building)
            return false;
        OutputRecord& record = found->second;
        record.cookie = g_cookie_counter.fetch_add(2, std::memory_order_relaxed);
        record.positions = positions.release();
        record.normals = normals.release();
        record.uvs = uvs.release();
        record.weights = weights.release();
        record.bone_indices = bone_indices.release();
        record.indices = indices.release();
        record.material_ids = material_ids.release();

        output_->positions = record.positions;
        output_->normals = record.normals;
        output_->uvs = record.uvs;
        output_->weights = record.weights;
        output_->bone_indices = record.bone_indices;
        output_->vertex_count = vertex_count;
        output_->indices = record.indices;
        output_->index_count = index_count;
        output_->material_ids = record.material_ids;
        output_->triangle_count = triangle_count;
        output_->result_error = result_error;
        output_->ownership_cookie = record.cookie;
        record.state = OutputState::Owned;
        active_ = false;
        return true;
    }

private:
    MaximumMeshOutput* output_;
    bool active_ = false;
};

struct MaximumSilhouetteBatchInput
{
    std::uint32_t struct_size;
    std::uint32_t width;
    std::uint32_t height;
    std::uint32_t view_count;
    std::uint32_t empty_distance;
    const unsigned char* original_masks;
    std::size_t original_row_stride;
    std::size_t original_view_stride;
    const unsigned char* candidate_masks;
    std::size_t candidate_row_stride;
    std::size_t candidate_view_stride;
};

struct MaximumSilhouetteBatchOutput
{
    std::uint32_t struct_size;
    std::uint32_t p95_distance_squared;
    std::uint64_t distance_count;
    std::uint64_t* intersections;
    std::uint64_t* unions;
    std::size_t view_capacity;
    std::uint32_t* original_boundary_xy;
    std::size_t original_boundary_capacity;
    std::size_t original_boundary_count;
    std::uint32_t* candidate_boundary_xy;
    std::size_t candidate_boundary_capacity;
    std::size_t candidate_boundary_count;
    std::uint32_t* distances_squared;
    std::size_t distance_capacity;
    std::size_t distances_written;
    std::uint64_t* original_boundary_offsets;
    std::uint64_t* candidate_boundary_offsets;
    std::uint64_t* distance_offsets;
    std::size_t offset_capacity;
    std::uint64_t boundary_extraction_ns;
    std::uint64_t distance_calculation_ns;
    std::uint64_t metric_production_ns;
    std::uint64_t allocation_count;
    std::uint64_t allocation_bytes;
    std::uint64_t peak_scratch_bytes;
};

struct MaximumSilhouetteAbiInfo
{
    std::uint32_t struct_size;
    std::uint32_t api_major;
    std::uint32_t api_minor;
    std::uint32_t api_patch;
    std::uint32_t architecture;
    std::uint32_t canonical_view_count;
    std::uint32_t mask_format;
    std::uint32_t calling_convention;
    std::uint32_t pointer_size;
    std::uint32_t size_t_size;
    std::uint32_t uint32_size;
    std::uint32_t uint64_size;
    std::uint32_t input_struct_size;
    std::uint32_t input_struct_alignment;
    std::uint32_t output_struct_size;
    std::uint32_t output_struct_alignment;
    std::uint32_t abi_struct_size;
    std::uint32_t abi_struct_alignment;
    std::uint64_t capabilities;
    char build_id[64];
};

struct SilhouetteScratch
{
    std::vector<std::pair<std::uint32_t, std::uint32_t>> original_boundary;
    std::vector<std::pair<std::uint32_t, std::uint32_t>> candidate_boundary;
    std::vector<std::uint32_t> field;
    std::vector<std::uint32_t> line;
    std::vector<std::uint32_t> transformed;
    std::vector<std::size_t> sites;
    std::vector<double> intersections;
    std::vector<std::uint32_t> distances;
};

thread_local SilhouetteScratch g_silhouette_scratch;

template <typename T>
void reserve_silhouette_buffer(
    std::vector<T>& buffer,
    std::size_t count,
    std::uint64_t& allocation_count,
    std::uint64_t& allocation_bytes)
{
    if (buffer.capacity() >= count)
        return;
    const std::size_t previous = buffer.capacity();
    buffer.reserve(count);
    ++allocation_count;
    allocation_bytes += static_cast<std::uint64_t>(buffer.capacity() - previous) * sizeof(T);
}

std::uint64_t silhouette_scratch_bytes(const SilhouetteScratch& scratch)
{
    return
        static_cast<std::uint64_t>(scratch.original_boundary.capacity()) * sizeof(scratch.original_boundary[0]) +
        static_cast<std::uint64_t>(scratch.candidate_boundary.capacity()) * sizeof(scratch.candidate_boundary[0]) +
        static_cast<std::uint64_t>(scratch.field.capacity()) * sizeof(scratch.field[0]) +
        static_cast<std::uint64_t>(scratch.line.capacity()) * sizeof(scratch.line[0]) +
        static_cast<std::uint64_t>(scratch.transformed.capacity()) * sizeof(scratch.transformed[0]) +
        static_cast<std::uint64_t>(scratch.sites.capacity()) * sizeof(scratch.sites[0]) +
        static_cast<std::uint64_t>(scratch.intersections.capacity()) * sizeof(scratch.intersections[0]) +
        static_cast<std::uint64_t>(scratch.distances.capacity()) * sizeof(scratch.distances[0]);
}

void squared_distance_transform_1d(
    const std::uint32_t* values,
    std::size_t count,
    std::uint32_t infinity,
    std::uint32_t* result,
    std::vector<std::size_t>& sites,
    std::vector<double>& intersections)
{
    std::size_t first = 0;
    while (first < count && values[first] >= infinity)
        ++first;
    if (first == count)
    {
        std::fill_n(result, count, infinity);
        return;
    }

    std::size_t envelope = 0;
    sites[0] = first;
    intersections[0] = -std::numeric_limits<double>::infinity();
    intersections[1] = std::numeric_limits<double>::infinity();
    for (std::size_t coordinate = first + 1; coordinate < count; ++coordinate)
    {
        if (values[coordinate] >= infinity)
            continue;
        std::size_t site = sites[envelope];
        double crossing =
            (static_cast<double>(values[coordinate]) + static_cast<double>(coordinate) * coordinate -
             static_cast<double>(values[site]) - static_cast<double>(site) * site) /
            (2.0 * static_cast<double>(coordinate - site));
        while (crossing <= intersections[envelope])
        {
            --envelope;
            site = sites[envelope];
            crossing =
                (static_cast<double>(values[coordinate]) + static_cast<double>(coordinate) * coordinate -
                 static_cast<double>(values[site]) - static_cast<double>(site) * site) /
                (2.0 * static_cast<double>(coordinate - site));
        }
        ++envelope;
        sites[envelope] = coordinate;
        intersections[envelope] = crossing;
        intersections[envelope + 1] = std::numeric_limits<double>::infinity();
    }

    envelope = 0;
    for (std::size_t coordinate = 0; coordinate < count; ++coordinate)
    {
        while (intersections[envelope + 1] < static_cast<double>(coordinate))
            ++envelope;
        const std::size_t site = sites[envelope];
        const std::size_t delta = coordinate > site ? coordinate - site : site - coordinate;
        const std::uint64_t squared = static_cast<std::uint64_t>(delta) * delta;
        result[coordinate] = static_cast<std::uint32_t>(values[site] + squared);
    }
}

void build_squared_distance_field(
    std::uint32_t width,
    std::uint32_t height,
    std::uint32_t infinity,
    const std::vector<std::pair<std::uint32_t, std::uint32_t>>& boundary,
    SilhouetteScratch& scratch)
{
    const std::size_t cell_count = static_cast<std::size_t>(width) * height;
    scratch.field.resize(cell_count);
    std::fill(scratch.field.begin(), scratch.field.end(), infinity);
    for (const auto& point : boundary)
        scratch.field[static_cast<std::size_t>(point.second) * width + point.first] = 0;

    for (std::size_t y = 0; y < height; ++y)
    {
        const std::size_t start = y * width;
        squared_distance_transform_1d(
            scratch.field.data() + start,
            width,
            infinity,
            scratch.transformed.data(),
            scratch.sites,
            scratch.intersections);
        std::copy_n(scratch.transformed.data(), width, scratch.field.data() + start);
    }
    for (std::size_t x = 0; x < width; ++x)
    {
        for (std::size_t y = 0; y < height; ++y)
            scratch.line[y] = scratch.field[y * width + x];
        squared_distance_transform_1d(
            scratch.line.data(),
            height,
            infinity,
            scratch.transformed.data(),
            scratch.sites,
            scratch.intersections);
        for (std::size_t y = 0; y < height; ++y)
            scratch.field[y * width + x] = scratch.transformed[y];
    }
}

bool binary_mask_and_boundary(
    const unsigned char* mask,
    std::uint32_t width,
    std::uint32_t height,
    std::size_t row_stride,
    std::vector<std::pair<std::uint32_t, std::uint32_t>>& boundary)
{
    boundary.clear();
    for (std::uint32_t y = 0; y < height; ++y)
    {
        const unsigned char* row = mask + static_cast<std::size_t>(y) * row_stride;
        for (std::uint32_t x = 0; x < width; ++x)
        {
            const unsigned char value = row[x];
            if (value != 0 && value != 255)
                return false;
            if (value == 0)
                continue;
            const bool edge =
                x == 0 || y == 0 || x + 1 == width || y + 1 == height ||
                row[x - 1] == 0 || row[x + 1] == 0 ||
                mask[static_cast<std::size_t>(y - 1) * row_stride + x] == 0 ||
                mask[static_cast<std::size_t>(y + 1) * row_stride + x] == 0;
            if (edge)
                boundary.emplace_back(x, y);
        }
    }
    return true;
}

std::uint64_t elapsed_nanoseconds(
    std::chrono::steady_clock::time_point start,
    std::chrono::steady_clock::time_point end)
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count());
}
} // namespace

extern "C" __declspec(dllexport) int maximum_meshopt_version() noexcept
{
    return 10200;
}

extern "C" __declspec(dllexport) int maximum_meshopt_abi_version() noexcept
{
    return 3;
}

extern "C" __declspec(dllexport) int maximum_silhouette_get_abi_info_v1(
    MaximumSilhouetteAbiInfo* output) noexcept
{
    try
    {
        if (output == nullptr)
            return ErrorNullPointer;
        if (output->struct_size != sizeof(MaximumSilhouetteAbiInfo))
            return ErrorStructSize;

        MaximumSilhouetteAbiInfo value{};
        value.struct_size = sizeof(MaximumSilhouetteAbiInfo);
        value.api_major = 1;
        value.api_minor = 0;
        value.api_patch = 0;
        value.architecture = 0x8664;
        value.canonical_view_count = 8;
        value.mask_format = 1;
        value.calling_convention = 1;
        value.pointer_size = sizeof(void*);
        value.size_t_size = sizeof(std::size_t);
        value.uint32_size = sizeof(std::uint32_t);
        value.uint64_size = sizeof(std::uint64_t);
        value.input_struct_size = sizeof(MaximumSilhouetteBatchInput);
        value.input_struct_alignment = alignof(MaximumSilhouetteBatchInput);
        value.output_struct_size = sizeof(MaximumSilhouetteBatchOutput);
        value.output_struct_alignment = alignof(MaximumSilhouetteBatchOutput);
        value.abi_struct_size = sizeof(MaximumSilhouetteAbiInfo);
        value.abi_struct_alignment = alignof(MaximumSilhouetteAbiInfo);
        value.capabilities = 1;
        constexpr char kBuildId[] = "maximum-silhouette-raw-v1-20260722";
        static_assert(sizeof(kBuildId) <= sizeof(value.build_id));
        std::copy_n(kBuildId, sizeof(kBuildId), value.build_id);
        *output = value;
        return 0;
    }
    catch (...)
    {
        return ErrorException;
    }
}

extern "C" __declspec(dllexport) int maximum_silhouette_metrics_raw_batch_v1(
    const MaximumSilhouetteBatchInput* input,
    MaximumSilhouetteBatchOutput* output) noexcept
{
    try
    {
        constexpr std::size_t kCellLimit = std::size_t(1) << 28;
        if (input == nullptr || output == nullptr)
            return ErrorNullPointer;
        if (input->struct_size != sizeof(MaximumSilhouetteBatchInput) ||
            output->struct_size != sizeof(MaximumSilhouetteBatchOutput))
            return ErrorStructSize;
        if (input->width == 0 || input->height == 0 || input->view_count == 0 ||
            input->original_masks == nullptr || input->candidate_masks == nullptr ||
            output->intersections == nullptr || output->unions == nullptr)
            return ErrorNullPointer;
        if (!can_multiply(input->width, input->height) ||
            !can_multiply(input->view_count, static_cast<std::size_t>(input->width) * input->height) ||
            !can_multiply(input->empty_distance, input->empty_distance) ||
            !can_multiply(input->original_row_stride, input->height) ||
            !can_multiply(input->candidate_row_stride, input->height))
            return ErrorCount;
        const std::size_t cell_count = static_cast<std::size_t>(input->width) * input->height;
        const std::size_t all_cells = cell_count * input->view_count;
        if (!can_multiply(all_cells, std::size_t(2)))
            return ErrorCount;
        const std::size_t original_view_span = input->original_row_stride * input->height;
        const std::size_t candidate_view_span = input->candidate_row_stride * input->height;
        const std::uint64_t maximum_distance =
            static_cast<std::uint64_t>(input->width - 1) * (input->width - 1) +
            static_cast<std::uint64_t>(input->height - 1) * (input->height - 1);
        const std::uint64_t empty_distance_squared =
            static_cast<std::uint64_t>(input->empty_distance) * input->empty_distance;
        if (cell_count >= kCellLimit || maximum_distance >= std::numeric_limits<std::uint32_t>::max() ||
            empty_distance_squared > std::numeric_limits<std::uint32_t>::max() ||
            input->original_row_stride < input->width || input->candidate_row_stride < input->width ||
            input->original_view_stride < original_view_span ||
            input->candidate_view_stride < candidate_view_span ||
            output->view_capacity < input->view_count)
            return ErrorCount;
        if (!can_multiply(input->view_count - 1, input->original_view_stride) ||
            !can_multiply(input->view_count - 1, input->candidate_view_stride))
            return ErrorCount;
        const std::size_t original_last_view = (input->view_count - 1) * input->original_view_stride;
        const std::size_t candidate_last_view = (input->view_count - 1) * input->candidate_view_stride;
        if (!can_add(original_last_view, original_view_span) ||
            !can_add(candidate_last_view, candidate_view_span))
            return ErrorCount;

        const bool debug_offsets = output->original_boundary_offsets != nullptr ||
            output->candidate_boundary_offsets != nullptr || output->distance_offsets != nullptr;
        if (debug_offsets &&
            (output->original_boundary_offsets == nullptr || output->candidate_boundary_offsets == nullptr ||
             output->distance_offsets == nullptr || output->offset_capacity < input->view_count + 1))
            return ErrorNullPointer;
        if ((output->original_boundary_xy == nullptr) != (output->original_boundary_capacity == 0) ||
            (output->candidate_boundary_xy == nullptr) != (output->candidate_boundary_capacity == 0) ||
            (output->distances_squared == nullptr) != (output->distance_capacity == 0))
            return ErrorNullPointer;
        if ((output->original_boundary_xy != nullptr || output->candidate_boundary_xy != nullptr ||
             output->distances_squared != nullptr) && !debug_offsets)
            return ErrorNullPointer;

        output->p95_distance_squared = 0;
        output->distance_count = 0;
        output->original_boundary_count = 0;
        output->candidate_boundary_count = 0;
        output->distances_written = 0;
        output->boundary_extraction_ns = 0;
        output->distance_calculation_ns = 0;
        output->metric_production_ns = 0;
        output->allocation_count = 0;
        output->allocation_bytes = 0;
        output->peak_scratch_bytes = 0;
        if (debug_offsets)
        {
            output->original_boundary_offsets[0] = 0;
            output->candidate_boundary_offsets[0] = 0;
            output->distance_offsets[0] = 0;
        }

        SilhouetteScratch& scratch = g_silhouette_scratch;
        reserve_silhouette_buffer(
            scratch.original_boundary, cell_count, output->allocation_count, output->allocation_bytes);
        reserve_silhouette_buffer(
            scratch.candidate_boundary, cell_count, output->allocation_count, output->allocation_bytes);
        reserve_silhouette_buffer(scratch.field, cell_count, output->allocation_count, output->allocation_bytes);
        const std::size_t maximum_dimension = std::max<std::size_t>(input->width, input->height);
        reserve_silhouette_buffer(scratch.line, maximum_dimension, output->allocation_count, output->allocation_bytes);
        reserve_silhouette_buffer(
            scratch.transformed, maximum_dimension, output->allocation_count, output->allocation_bytes);
        reserve_silhouette_buffer(scratch.sites, maximum_dimension, output->allocation_count, output->allocation_bytes);
        reserve_silhouette_buffer(
            scratch.intersections, maximum_dimension + 1, output->allocation_count, output->allocation_bytes);
        reserve_silhouette_buffer(
            scratch.distances, all_cells * 2, output->allocation_count, output->allocation_bytes);
        scratch.line.resize(maximum_dimension);
        scratch.transformed.resize(maximum_dimension);
        scratch.sites.resize(maximum_dimension);
        scratch.intersections.resize(maximum_dimension + 1);
        scratch.distances.clear();
        output->peak_scratch_bytes = silhouette_scratch_bytes(scratch);

        const std::uint32_t infinity = static_cast<std::uint32_t>(maximum_distance + 1);
        for (std::uint32_t view = 0; view < input->view_count; ++view)
        {
            const unsigned char* original = input->original_masks +
                static_cast<std::size_t>(view) * input->original_view_stride;
            const unsigned char* candidate = input->candidate_masks +
                static_cast<std::size_t>(view) * input->candidate_view_stride;

            const auto boundary_start = std::chrono::steady_clock::now();
            if (!binary_mask_and_boundary(
                    original,
                    input->width,
                    input->height,
                    input->original_row_stride,
                    scratch.original_boundary) ||
                !binary_mask_and_boundary(
                    candidate,
                    input->width,
                    input->height,
                    input->candidate_row_stride,
                    scratch.candidate_boundary))
                return ErrorData;
            std::uint64_t intersection = 0;
            std::uint64_t union_count = 0;
            for (std::uint32_t y = 0; y < input->height; ++y)
            {
                const unsigned char* original_row = original + static_cast<std::size_t>(y) * input->original_row_stride;
                const unsigned char* candidate_row = candidate + static_cast<std::size_t>(y) * input->candidate_row_stride;
                for (std::uint32_t x = 0; x < input->width; ++x)
                {
                    intersection += original_row[x] == 255 && candidate_row[x] == 255;
                    union_count += original_row[x] == 255 || candidate_row[x] == 255;
                }
            }
            output->intersections[view] = intersection;
            output->unions[view] = union_count;

            if (output->original_boundary_xy != nullptr)
            {
                if (scratch.original_boundary.size() >
                    output->original_boundary_capacity - output->original_boundary_count)
                    return ErrorCount;
                for (const auto& point : scratch.original_boundary)
                {
                    output->original_boundary_xy[output->original_boundary_count * 2] = point.first;
                    output->original_boundary_xy[output->original_boundary_count * 2 + 1] = point.second;
                    ++output->original_boundary_count;
                }
            }
            if (output->candidate_boundary_xy != nullptr)
            {
                if (scratch.candidate_boundary.size() >
                    output->candidate_boundary_capacity - output->candidate_boundary_count)
                    return ErrorCount;
                for (const auto& point : scratch.candidate_boundary)
                {
                    output->candidate_boundary_xy[output->candidate_boundary_count * 2] = point.first;
                    output->candidate_boundary_xy[output->candidate_boundary_count * 2 + 1] = point.second;
                    ++output->candidate_boundary_count;
                }
            }
            const auto boundary_end = std::chrono::steady_clock::now();
            output->boundary_extraction_ns += elapsed_nanoseconds(boundary_start, boundary_end);

            const auto distance_start = std::chrono::steady_clock::now();
            const std::size_t distance_start_index = scratch.distances.size();
            if (!scratch.original_boundary.empty() || !scratch.candidate_boundary.empty())
            {
                if (scratch.original_boundary.empty() || scratch.candidate_boundary.empty())
                {
                    scratch.distances.push_back(static_cast<std::uint32_t>(empty_distance_squared));
                }
                else
                {
                    build_squared_distance_field(
                        input->width, input->height, infinity, scratch.candidate_boundary, scratch);
                    for (const auto& point : scratch.original_boundary)
                        scratch.distances.push_back(
                            scratch.field[static_cast<std::size_t>(point.second) * input->width + point.first]);
                    build_squared_distance_field(
                        input->width, input->height, infinity, scratch.original_boundary, scratch);
                    for (const auto& point : scratch.candidate_boundary)
                        scratch.distances.push_back(
                            scratch.field[static_cast<std::size_t>(point.second) * input->width + point.first]);
                }
            }
            if (output->distances_squared != nullptr)
            {
                const std::size_t added = scratch.distances.size() - distance_start_index;
                if (added > output->distance_capacity - output->distances_written)
                    return ErrorCount;
                std::copy(
                    scratch.distances.begin() + static_cast<std::ptrdiff_t>(distance_start_index),
                    scratch.distances.end(),
                    output->distances_squared + output->distances_written);
                output->distances_written += added;
            }
            const auto distance_end = std::chrono::steady_clock::now();
            output->distance_calculation_ns += elapsed_nanoseconds(distance_start, distance_end);

            if (debug_offsets)
            {
                output->original_boundary_offsets[view + 1] = output->original_boundary_count;
                output->candidate_boundary_offsets[view + 1] = output->candidate_boundary_count;
                output->distance_offsets[view + 1] = output->distances_written;
            }
        }

        const auto metric_start = std::chrono::steady_clock::now();
        output->distance_count = scratch.distances.size();
        if (!scratch.distances.empty())
        {
            const std::size_t index = std::min(
                scratch.distances.size() - 1,
                std::max<std::size_t>(
                    0,
                    static_cast<std::size_t>(
                        std::ceil(static_cast<double>(scratch.distances.size()) * 0.95)) - 1));
            std::nth_element(
                scratch.distances.begin(),
                scratch.distances.begin() + static_cast<std::ptrdiff_t>(index),
                scratch.distances.end());
            output->p95_distance_squared = scratch.distances[index];
        }
        const auto metric_end = std::chrono::steady_clock::now();
        output->metric_production_ns = elapsed_nanoseconds(metric_start, metric_end);
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

extern "C" __declspec(dllexport) int maximum_meshopt_test_pause_at(std::uint32_t point) noexcept
{
    try
    {
        if (point != 1 && point != 2)
            return 0;
        std::lock_guard<std::mutex> lock(g_test_hook_mutex);
        if (g_test_pause_point != 0 || g_test_reached_point != 0)
            return 0;
        g_test_pause_point = point;
        g_test_hook_released = false;
        return 1;
    }
    catch (...)
    {
        return 0;
    }
}

extern "C" __declspec(dllexport) int maximum_meshopt_test_wait_paused(
    std::uint32_t point, std::uint32_t timeout_ms) noexcept
{
    try
    {
        std::unique_lock<std::mutex> lock(g_test_hook_mutex);
        return g_test_hook_condition.wait_for(
                   lock, std::chrono::milliseconds(timeout_ms),
                   [point]() { return g_test_reached_point == point; })
            ? 1
            : 0;
    }
    catch (...)
    {
        return 0;
    }
}

extern "C" __declspec(dllexport) int maximum_meshopt_test_release(std::uint32_t point) noexcept
{
    try
    {
        std::lock_guard<std::mutex> lock(g_test_hook_mutex);
        if (g_test_reached_point != point)
            return 0;
        g_test_hook_released = true;
        g_test_hook_condition.notify_all();
        return 1;
    }
    catch (...)
    {
        return 0;
    }
}

extern "C" __declspec(dllexport) std::size_t maximum_meshopt_test_registry_count() noexcept
{
    try
    {
        std::lock_guard<std::mutex> lock(g_output_mutex);
        return g_output_states.size();
    }
    catch (...)
    {
        return 0;
    }
}

extern "C" __declspec(dllexport) int maximum_meshopt_test_fail_next(std::uint32_t failure) noexcept
{
    if (failure != 1 && failure != 2)
        return 0;
    std::uint32_t expected = 0;
    return g_test_failure.compare_exchange_strong(expected, failure, std::memory_order_relaxed) ? 1 : 0;
}

extern "C" __declspec(dllexport) void maximum_meshopt_destroy(MaximumMeshOutput* output) noexcept
{
    try
    {
        if (output == nullptr || output->struct_size != sizeof(MaximumMeshOutput))
            return;
        OutputRecord detached;
        {
            std::lock_guard<std::mutex> lock(g_output_mutex);
            const auto owned = g_output_states.find(output);
            if (owned == g_output_states.end() || owned->second.state != OutputState::Owned ||
                owned->second.cookie != output->ownership_cookie)
                return;
            owned->second.state = OutputState::Destroying;
            detached = owned->second;
            reset_output(output);
        }
        test_pause(2);
        delete[] detached.positions;
        delete[] detached.normals;
        delete[] detached.uvs;
        delete[] detached.weights;
        delete[] detached.bone_indices;
        delete[] detached.indices;
        delete[] detached.material_ids;
        {
            std::lock_guard<std::mutex> lock(g_output_mutex);
            const auto found = g_output_states.find(output);
            if (found != g_output_states.end() && found->second.state == OutputState::Destroying)
                g_output_states.erase(found);
        }
    }
    catch (...)
    {
    }
}

extern "C" __declspec(dllexport) int maximum_meshopt_simplify(
    const MaximumMeshInput* input,
    const MaximumMeshOptions* options,
    MaximumMeshOutput* output) noexcept
{
    OutputBuildReservation reservation(output);
    try
    {
        if (input == nullptr || options == nullptr || output == nullptr)
            return ErrorNullPointer;
        if (input->struct_size != sizeof(MaximumMeshInput) ||
            options->struct_size != sizeof(MaximumMeshOptions) ||
            output->struct_size != sizeof(MaximumMeshOutput))
            return ErrorStructSize;
        {
            std::lock_guard<std::mutex> lock(g_output_mutex);
            if (g_output_states.find(output) != g_output_states.end())
                return ErrorBusy;
            reset_output(output);
            const std::uint32_t injected = g_test_failure.exchange(0, std::memory_order_relaxed);
            if (injected == 1)
                throw std::bad_alloc();
            if (injected == 2)
                throw std::runtime_error("injected registry failure");
            g_output_states.emplace(output, OutputRecord{});
            reservation.activate();
        }
        test_pause(1);

        constexpr std::size_t kMeshoptCountLimit = std::size_t(1) << 28;
        if (input->vertex_count == 0 || input->vertex_count >= kMeshoptCountLimit ||
            input->index_count < 3 || input->index_count >= kMeshoptCountLimit || input->index_count % 3 != 0 ||
            input->triangle_count != input->index_count / 3 ||
            input->bone_count == 0 || input->bone_count > std::numeric_limits<std::uint32_t>::max() ||
            !can_multiply(input->vertex_count, 3) || !can_multiply(input->vertex_count, 4) ||
            !can_multiply(input->vertex_count, kAttributeCount))
            return ErrorCount;
        if (input->positions == nullptr || input->normals == nullptr || input->uvs == nullptr ||
            input->weights == nullptr || input->bone_indices == nullptr || input->indices == nullptr || input->material_ids == nullptr ||
            input->vertex_flags == nullptr)
            return ErrorNullPointer;
        if (!std::isfinite(options->target_ratio) || options->target_ratio <= 0.f || options->target_ratio > 1.f ||
            !std::isfinite(options->target_error) || options->target_error < 0.f ||
            options->update_vertices > 2 || (options->meshopt_options & ~kKnownMeshoptOptions) != 0)
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
        for (std::size_t vertex = 0; vertex < input->vertex_count; ++vertex)
            for (unsigned int influence = 0; influence < 4; ++influence)
                if (input->weights[vertex * 4 + influence] > 0.f &&
                    input->bone_indices[vertex * 4 + influence] >= input->bone_count)
                    return ErrorData;

        std::vector<float> positions(input->positions, input->positions + input->vertex_count * 3);
        std::vector<float> attributes(input->vertex_count * kAttributeCount);
        std::vector<std::uint32_t> canonical_bones(input->vertex_count * 4, 0);
        for (std::size_t vertex = 0; vertex < input->vertex_count; ++vertex)
        {
            float* attribute = attributes.data() + vertex * kAttributeCount;
            std::copy_n(input->normals + vertex * 3, 3, attribute);
            normalize3(attribute);
            std::copy_n(input->uvs + vertex * 2, 2, attribute + 3);
            std::map<std::uint32_t, float> combined;
            for (unsigned int influence = 0; influence < 4; ++influence)
            {
                const float weight = std::max(0.f, std::min(1.f, input->weights[vertex * 4 + influence]));
                if (weight > 0.f)
                    combined[input->bone_indices[vertex * 4 + influence]] += weight;
            }
            std::vector<std::pair<std::uint32_t, float>> selected(combined.begin(), combined.end());
            std::sort(selected.begin(), selected.end(), [](const auto& left, const auto& right) {
                return left.second != right.second ? left.second > right.second : left.first < right.first;
            });
            if (selected.size() > 4)
                selected.resize(4);
            float total = 0.f;
            for (const auto& item : selected)
                total += item.second;
            if (!(total > 1e-12f) || !std::isfinite(total))
                return ErrorData;
            std::sort(selected.begin(), selected.end(), [](const auto& left, const auto& right) {
                return left.first < right.first;
            });
            std::fill_n(attribute + 5, 4, 0.f);
            for (std::size_t influence = 0; influence < selected.size(); ++influence)
            {
                canonical_bones[vertex * 4 + influence] = selected[influence].first;
                attribute[5 + influence] = selected[influence].second / total;
            }
        }

        std::vector<unsigned char> vertex_flags(input->vertex_flags, input->vertex_flags + input->vertex_count);
        auto influence_set = [&](std::uint32_t vertex) {
            std::vector<std::uint32_t> result;
            for (unsigned int influence = 0; influence < 4; ++influence)
                if (attributes[vertex * kAttributeCount + 5 + influence] > 0.f)
                    result.push_back(canonical_bones[vertex * 4 + influence]);
            std::sort(result.begin(), result.end());
            result.erase(std::unique(result.begin(), result.end()), result.end());
            return result;
        };
        std::vector<std::set<std::uint32_t>> vertex_materials(input->vertex_count);
        std::vector<std::uint32_t> first_material(input->vertex_count, 0);
        std::vector<unsigned char> material_seen(input->vertex_count, 0);
        std::vector<unsigned char> shared_material(input->vertex_count, 0);
        std::map<std::pair<std::uint32_t, std::uint32_t>, std::uint32_t> edge_material;
        std::map<std::pair<std::uint32_t, std::uint32_t>, bool> material_boundary;
        for (std::size_t triangle = 0; triangle < input->triangle_count; ++triangle)
        {
            const std::uint32_t* tri = input->indices + triangle * 3;
            for (unsigned int corner = 0; corner < 3; ++corner)
            {
                const std::uint32_t vertex = tri[corner];
                vertex_materials[vertex].insert(input->material_ids[triangle]);
                if (!material_seen[vertex])
                {
                    material_seen[vertex] = 1;
                    first_material[vertex] = input->material_ids[triangle];
                }
                else if (first_material[vertex] != input->material_ids[triangle])
                    shared_material[vertex] = 1;
            }
            const auto edges = {edge_key(tri[0], tri[1]), edge_key(tri[1], tri[2]), edge_key(tri[2], tri[0])};
            for (const auto& edge : edges)
            {
                if (options->update_vertices != 2 && influence_set(edge.first) != influence_set(edge.second))
                {
                    vertex_flags[edge.first] |= meshopt_SimplifyVertex_Protect;
                    vertex_flags[edge.second] |= meshopt_SimplifyVertex_Protect;
                }
                const auto found = edge_material.find(edge);
                if (found == edge_material.end())
                    edge_material.emplace(edge, input->material_ids[triangle]);
                else if (found->second != input->material_ids[triangle])
                    material_boundary[edge] = true;
            }
        }
        for (std::size_t vertex = 0; vertex < input->vertex_count; ++vertex)
            if (shared_material[vertex])
                vertex_flags[vertex] |= options->update_vertices == 2
                    ? meshopt_SimplifyVertex_Protect
                    : meshopt_SimplifyVertex_Lock;
        for (const auto& entry : material_boundary)
        {
            vertex_flags[entry.first.first] |= meshopt_SimplifyVertex_Protect;
            vertex_flags[entry.first.second] |= meshopt_SimplifyVertex_Protect;
        }
        if (options->update_vertices == 2)
        {
            std::vector<unsigned int> position_remap(input->vertex_count);
            meshopt_generatePositionRemap(
                position_remap.data(), positions.data(), input->vertex_count, sizeof(float) * 3);
            std::vector<unsigned char> position_flags(input->vertex_count, 0);
            std::vector<std::set<std::uint32_t>> position_materials(input->vertex_count);
            for (std::size_t vertex = 0; vertex < input->vertex_count; ++vertex)
            {
                const std::uint32_t position = position_remap[vertex];
                position_flags[position] |= vertex_flags[vertex];
                position_materials[position].insert(
                    vertex_materials[vertex].begin(), vertex_materials[vertex].end());
            }
            for (std::size_t vertex = 0; vertex < input->vertex_count; ++vertex)
            {
                const std::uint32_t position = position_remap[vertex];
                vertex_flags[vertex] |= position_flags[position];
                if (position_materials[position].size() > 1)
                    vertex_flags[vertex] |= meshopt_SimplifyVertex_Protect;
            }
        }

        std::map<std::uint32_t, std::vector<std::uint32_t>> subsets;
        if (options->update_vertices == 2)
            subsets[0].assign(input->indices, input->indices + input->index_count);
        else
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
            if (options->update_vertices == 1)
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
            if (options->update_vertices == 2)
            {
                for (std::size_t triangle = 0; triangle < result_count / 3; ++triangle)
                {
                    std::set<std::uint32_t> ownership = vertex_materials[subset[triangle * 3]];
                    for (unsigned int corner = 1; corner < 3; ++corner)
                    {
                        std::set<std::uint32_t> intersection;
                        const auto& candidate = vertex_materials[subset[triangle * 3 + corner]];
                        std::set_intersection(
                            ownership.begin(), ownership.end(), candidate.begin(), candidate.end(),
                            std::inserter(intersection, intersection.end()));
                        ownership.swap(intersection);
                    }
                    if (ownership.size() != 1)
                        return ErrorSimplifier;
                    result_materials.push_back(*ownership.begin());
                }
            }
            else
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
        std::unique_ptr<std::uint32_t[]> out_bones(new (std::nothrow) std::uint32_t[input->vertex_count * 4]);
        std::unique_ptr<std::uint32_t[]> out_indices(new (std::nothrow) std::uint32_t[result_indices.size()]);
        std::unique_ptr<std::uint32_t[]> out_materials(new (std::nothrow) std::uint32_t[result_materials.size()]);
        if (!out_positions || !out_normals || !out_uvs || !out_weights || !out_bones || !out_indices || !out_materials)
            return ErrorAllocation;

        std::copy(positions.begin(), positions.end(), out_positions.get());
        for (std::size_t vertex = 0; vertex < input->vertex_count; ++vertex)
        {
            const float* attribute = attributes.data() + vertex * kAttributeCount;
            const float* normal = options->update_vertices == 1 ? attribute : input->normals + vertex * 3;
            const float* uv = options->update_vertices == 1 ? attribute + 3 : input->uvs + vertex * 2;
            const float* weight = options->update_vertices == 1 ? attribute + 5 : input->weights + vertex * 4;
            std::copy_n(normal, 3, out_normals.get() + vertex * 3);
            std::copy_n(uv, 2, out_uvs.get() + vertex * 2);
            std::copy_n(weight, 4, out_weights.get() + vertex * 4);
        }
        std::copy(result_indices.begin(), result_indices.end(), out_indices.get());
        std::copy(result_materials.begin(), result_materials.end(), out_materials.get());
        if (options->update_vertices == 1)
            std::copy(canonical_bones.begin(), canonical_bones.end(), out_bones.get());
        else
            std::copy_n(input->bone_indices, input->vertex_count * 4, out_bones.get());

        if (!reservation.publish(
                out_positions, out_normals, out_uvs, out_weights, out_bones, out_indices,
                out_materials, input->vertex_count, result_indices.size(), result_materials.size(),
                maximum_error))
            return ErrorBusy;
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
