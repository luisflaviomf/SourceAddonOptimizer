from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import hashlib
import heapq
import math
import random
from typing import Iterable, Sequence

from PIL import Image, ImageChops, ImageDraw

from .contracts import RegionBudget, RegionMetrics, ValidationDecision
from .regions import SmdRegion
from .smd import SmdTriangle, SmdVertex


Vector2 = tuple[float, float]
Vector3 = tuple[float, float, float]
Matrix3x4 = tuple[float, ...]
Pose = tuple[tuple[int, Matrix3x4], ...]


@dataclass(frozen=True)
class MetricContract:
    canonical_views: tuple[Vector3, ...]
    silhouette_resolution: int
    sample_count: int
    seed: str
    poses: tuple[Pose, ...] = ()

    def __post_init__(self) -> None:
        if not self.canonical_views or any(len(view) != 3 for view in self.canonical_views):
            raise ValueError("metric contract requires 3D canonical views")
        if self.silhouette_resolution < 64 or self.silhouette_resolution > 2048:
            raise ValueError("silhouette resolution is out of range")
        if self.sample_count < 64 or self.sample_count > 65536:
            raise ValueError("sample count is out of range")
        if type(self.seed) is not str or not self.seed:
            raise ValueError("metric seed is empty")
        for pose in self.poses:
            if len({bone for bone, _matrix in pose}) != len(pose):
                raise ValueError("pose contains duplicate bones")
            if any(len(matrix) != 12 or not all(math.isfinite(value) for value in matrix) for _bone, matrix in pose):
                raise ValueError("pose matrix must be a finite 3x4 transform")


@dataclass(frozen=True)
class _Sample:
    point: Vector3
    normal: Vector3
    uv: Vector2
    influences: tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class _TriangleData:
    triangle: SmdTriangle
    bounds_min: Vector3
    bounds_max: Vector3
    centroid: Vector3


@dataclass(frozen=True)
class _BvhNode:
    bounds_min: Vector3
    bounds_max: Vector3
    indices: tuple[int, ...]
    left: "_BvhNode | None" = None
    right: "_BvhNode | None" = None


@dataclass(frozen=True)
class _Nearest:
    distance: float
    sample: _Sample
    triangle_index: int


@dataclass(frozen=True)
class _SilhouetteReference:
    right: Vector3
    up: Vector3
    frame: tuple[float, float, float, float]
    mask: Image.Image
    boundary: tuple[tuple[int, int], ...]
    boundary_tree: "_KdNode | None"


@dataclass(frozen=True)
class PreparedRegionReference:
    original: SmdRegion
    contract: MetricContract
    diagonal: float
    triangle_data: tuple[_TriangleData, ...]
    bvh: _BvhNode
    samples: tuple[_Sample, ...]
    silhouettes: tuple[_SilhouetteReference, ...]


def _add(left: Vector3, right: Vector3) -> Vector3:
    return tuple(left[index] + right[index] for index in range(3))  # type: ignore[return-value]


def _sub(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
    return tuple(left[index] - right[index] for index in range(len(left)))


def _mul(value: Sequence[float], factor: float) -> tuple[float, ...]:
    return tuple(component * factor for component in value)


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(left[index] * right[index] for index in range(len(left)))


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _length(value: Sequence[float]) -> float:
    return math.sqrt(_dot(value, value))


def _normalized(value: Vector3) -> Vector3:
    length = _length(value)
    if length <= 1e-15:
        return (0.0, 0.0, 1.0)
    return tuple(component / length for component in value)  # type: ignore[return-value]


def _triangle_area(triangle: SmdTriangle) -> float:
    first, second, third = (vertex.position for vertex in triangle.vertices)
    return _length(_cross(_sub(second, first), _sub(third, first))) * 0.5  # type: ignore[arg-type]


def _influences(vertex: SmdVertex) -> dict[int, float]:
    combined: dict[int, float] = {}
    for influence in vertex.influences:
        if influence.weight > 0.0:
            combined[influence.bone] = combined.get(influence.bone, 0.0) + influence.weight
    total = sum(combined.values())
    if total <= 1e-12:
        raise ValueError("mesh contains a zero-sum skin vertex")
    return {bone: weight / total for bone, weight in combined.items()}


def _interpolate_triangle(triangle: SmdTriangle, barycentric: tuple[float, float, float]) -> _Sample:
    point = tuple(sum(triangle.vertices[index].position[axis] * barycentric[index] for index in range(3)) for axis in range(3))
    normal = _normalized(tuple(sum(triangle.vertices[index].normal[axis] * barycentric[index] for index in range(3)) for axis in range(3)))
    uv = tuple(sum(triangle.vertices[index].uv[axis] * barycentric[index] for index in range(3)) for axis in range(2))
    weights: dict[int, float] = {}
    for index, vertex in enumerate(triangle.vertices):
        for bone, weight in _influences(vertex).items():
            weights[bone] = weights.get(bone, 0.0) + weight * barycentric[index]
    total = sum(weights.values())
    influence_tuple = tuple(sorted((bone, weight / total) for bone, weight in weights.items() if weight > 1e-12))
    return _Sample(point, normal, uv, influence_tuple)  # type: ignore[arg-type]


def _samples(region: SmdRegion, count: int, seed: str) -> tuple[_Sample, ...]:
    areas = tuple(_triangle_area(triangle) for triangle in region.triangles)
    if any(area <= 1e-15 for area in areas):
        raise ValueError("mesh contains a degenerate triangle")
    cumulative = []
    running = 0.0
    for area in areas:
        running += area
        cumulative.append(running)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    result = []
    for _index in range(count):
        triangle_index = min(bisect_left(cumulative, rng.random() * running), len(areas) - 1)
        root = math.sqrt(rng.random())
        second = rng.random()
        barycentric = (1.0 - root, root * (1.0 - second), root * second)
        result.append(_interpolate_triangle(region.triangles[triangle_index], barycentric))
    return tuple(result)


def _triangle_data(region: SmdRegion) -> tuple[_TriangleData, ...]:
    result = []
    for triangle in region.triangles:
        positions = tuple(vertex.position for vertex in triangle.vertices)
        bounds_min = tuple(min(position[axis] for position in positions) for axis in range(3))
        bounds_max = tuple(max(position[axis] for position in positions) for axis in range(3))
        centroid = tuple(sum(position[axis] for position in positions) / 3.0 for axis in range(3))
        result.append(_TriangleData(triangle, bounds_min, bounds_max, centroid))
    return tuple(result)


def _bounds_for_indices(data: Sequence[_TriangleData], indices: Sequence[int]) -> tuple[Vector3, Vector3]:
    return (
        tuple(min(data[index].bounds_min[axis] for index in indices) for axis in range(3)),
        tuple(max(data[index].bounds_max[axis] for index in indices) for axis in range(3)),
    )  # type: ignore[return-value]


def _build_bvh(data: Sequence[_TriangleData], indices: tuple[int, ...]) -> _BvhNode:
    bounds_min, bounds_max = _bounds_for_indices(data, indices)
    if len(indices) <= 8:
        return _BvhNode(bounds_min, bounds_max, indices)
    extents = tuple(bounds_max[axis] - bounds_min[axis] for axis in range(3))
    axis = max(range(3), key=lambda value: extents[value])
    ordered = tuple(sorted(indices, key=lambda index: (data[index].centroid[axis], index)))
    midpoint = len(ordered) // 2
    return _BvhNode(
        bounds_min,
        bounds_max,
        (),
        _build_bvh(data, ordered[:midpoint]),
        _build_bvh(data, ordered[midpoint:]),
    )


def _bounds_distance_squared(point: Vector3, bounds_min: Vector3, bounds_max: Vector3) -> float:
    return sum(
        (bounds_min[axis] - point[axis]) ** 2 if point[axis] < bounds_min[axis]
        else (point[axis] - bounds_max[axis]) ** 2 if point[axis] > bounds_max[axis]
        else 0.0
        for axis in range(3)
    )


def _closest_barycentric(point: Vector3, triangle: SmdTriangle) -> tuple[Vector3, tuple[float, float, float]]:
    a, b, c = (vertex.position for vertex in triangle.vertices)
    ab = _sub(b, a)
    ac = _sub(c, a)
    ap = _sub(point, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a, (1.0, 0.0, 0.0)
    bp = _sub(point, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return b, (0.0, 1.0, 0.0)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        return _add(a, _mul(ab, v)), (1.0 - v, v, 0.0)  # type: ignore[arg-type]
    cp = _sub(point, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return c, (0.0, 0.0, 1.0)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        return _add(a, _mul(ac, w)), (1.0 - w, 0.0, w)  # type: ignore[arg-type]
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and d4 - d3 >= 0.0 and d5 - d6 >= 0.0:
        edge = _sub(c, b)
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return _add(b, _mul(edge, w)), (0.0, 1.0 - w, w)  # type: ignore[arg-type]
    denominator = 1.0 / (va + vb + vc)
    v, w = vb * denominator, vc * denominator
    return _add(a, _add(_mul(ab, v), _mul(ac, w))), (1.0 - v - w, v, w)  # type: ignore[arg-type]


def _nearest(point: Vector3, data: Sequence[_TriangleData], root: _BvhNode) -> _Nearest:
    queue: list[tuple[float, int, _BvhNode]] = []
    counter = 0
    heapq.heappush(queue, (0.0, counter, root))
    best_squared = math.inf
    best: _Nearest | None = None
    while queue:
        lower_bound, _order, node = heapq.heappop(queue)
        if lower_bound > best_squared:
            continue
        if node.indices:
            for index in node.indices:
                closest, barycentric = _closest_barycentric(point, data[index].triangle)
                distance_squared = _dot(_sub(point, closest), _sub(point, closest))
                if distance_squared < best_squared or (
                    distance_squared == best_squared and best is not None and index < best.triangle_index
                ):
                    best_squared = distance_squared
                    best = _Nearest(math.sqrt(distance_squared), _interpolate_triangle(data[index].triangle, barycentric), index)
            continue
        for child in (node.left, node.right):
            if child is not None:
                counter += 1
                distance = _bounds_distance_squared(point, child.bounds_min, child.bounds_max)
                if distance <= best_squared:
                    heapq.heappush(queue, (distance, counter, child))
    if best is None:
        raise RuntimeError("BVH contains no triangles")
    return best


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _clean(value: float) -> float:
    return 0.0 if abs(value) <= 1e-12 else value


def _pose_displacement(sample: _Sample, pose: Pose) -> Vector3:
    matrices = dict(pose)
    transformed = (0.0, 0.0, 0.0)
    for bone, weight in sample.influences:
        matrix = matrices.get(bone)
        if matrix is None:
            point = sample.point
        else:
            x, y, z = sample.point
            point = (
                matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
                matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
                matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
            )
        transformed = _add(transformed, _mul(point, weight))  # type: ignore[arg-type]
    return _sub(transformed, sample.point)  # type: ignore[return-value]


def _direction_metrics(
    samples: Sequence[_Sample],
    target_data: Sequence[_TriangleData],
    target_bvh: _BvhNode,
    poses: Sequence[Pose],
    diagonal: float,
) -> tuple[list[float], list[float], list[float], list[float]]:
    surfaces: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    skinning: list[float] = []
    for sample in samples:
        nearest = _nearest(sample.point, target_data, target_bvh)
        surfaces.append(nearest.distance / diagonal)
        normals.append(math.degrees(math.acos(max(-1.0, min(1.0, _dot(sample.normal, nearest.sample.normal))))))
        uvs.append(_length(_sub(sample.uv, nearest.sample.uv)))
        for pose in poses:
            source_displacement = _pose_displacement(sample, pose)
            target_displacement = _pose_displacement(nearest.sample, pose)
            skinning.append(_length(_sub(source_displacement, target_displacement)) / diagonal)
    return surfaces, normals, uvs, skinning


def _project_basis(view: Vector3) -> tuple[Vector3, Vector3]:
    forward = _normalized(view)
    reference = (0.0, 1.0, 0.0) if abs(forward[2]) > 0.9 else (0.0, 0.0, 1.0)
    right = _normalized(_cross(reference, forward))
    up = _normalized(_cross(forward, right))
    return right, up


def _project_region(
    region: SmdRegion,
    right: Vector3,
    up: Vector3,
    frame: tuple[float, float, float, float],
    resolution: int,
) -> Image.Image:
    minimum_x, maximum_x, minimum_y, maximum_y = frame
    scale_x = (resolution - 1) / max(maximum_x - minimum_x, 1e-9)
    scale_y = (resolution - 1) / max(maximum_y - minimum_y, 1e-9)
    image = Image.new("L", (resolution, resolution), 0)
    draw = ImageDraw.Draw(image)
    for triangle in region.triangles:
        projected = []
        for vertex in triangle.vertices:
            x = (_dot(vertex.position, right) - minimum_x) * scale_x
            y = (maximum_y - _dot(vertex.position, up)) * scale_y
            projected.append((x, y))
        draw.polygon(projected, fill=255)
    return image


def _boundary_points(image: Image.Image) -> tuple[tuple[int, int], ...]:
    bounds = image.getbbox()
    if bounds is None:
        return ()
    pixels = image.load()
    width, height = image.size
    left, top, right, bottom = bounds
    points = []
    for y in range(max(0, top - 1), min(height, bottom + 1)):
        for x in range(max(0, left - 1), min(width, right + 1)):
            if not pixels[x, y]:
                continue
            if x == 0 or y == 0 or x == width - 1 or y == height - 1 or any(
                not pixels[nx, ny]
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            ):
                points.append((x, y))
    return tuple(points)


@dataclass(frozen=True)
class _KdNode:
    point: tuple[int, int]
    axis: int
    left: "_KdNode | None"
    right: "_KdNode | None"


def _kd_tree(points: Sequence[tuple[int, int]], depth: int = 0) -> _KdNode | None:
    if not points:
        return None
    axis = depth % 2
    ordered = sorted(points, key=lambda point: (point[axis], point[1 - axis]))
    midpoint = len(ordered) // 2
    return _KdNode(
        ordered[midpoint],
        axis,
        _kd_tree(ordered[:midpoint], depth + 1),
        _kd_tree(ordered[midpoint + 1 :], depth + 1),
    )


def _kd_distance(point: tuple[int, int], node: _KdNode | None, best: float = math.inf) -> float:
    if node is None:
        return best
    distance = (point[0] - node.point[0]) ** 2 + (point[1] - node.point[1]) ** 2
    best = min(best, float(distance))
    delta = point[node.axis] - node.point[node.axis]
    near, far = (node.left, node.right) if delta < 0 else (node.right, node.left)
    best = _kd_distance(point, near, best)
    if delta * delta < best:
        best = _kd_distance(point, far, best)
    return best


def _prepare_silhouettes(original: SmdRegion, contract: MetricContract) -> tuple[_SilhouetteReference, ...]:
    references = []
    for view in contract.canonical_views:
        right, up = _project_basis(view)
        original_projection = tuple((_dot(vertex.position, right), _dot(vertex.position, up)) for triangle in original.triangles for vertex in triangle.vertices)
        minimum_x = min(value[0] for value in original_projection)
        maximum_x = max(value[0] for value in original_projection)
        minimum_y = min(value[1] for value in original_projection)
        maximum_y = max(value[1] for value in original_projection)
        padding = max(maximum_x - minimum_x, maximum_y - minimum_y, 1e-6) * 0.05
        frame = (minimum_x - padding, maximum_x + padding, minimum_y - padding, maximum_y + padding)
        original_mask = _project_region(original, right, up, frame, contract.silhouette_resolution)
        boundary = _boundary_points(original_mask)
        references.append(
            _SilhouetteReference(right, up, frame, original_mask, boundary, _kd_tree(boundary))
        )
    return tuple(references)


def _silhouette_metrics_prepared(
    reference: PreparedRegionReference,
    candidate: SmdRegion,
) -> tuple[float, float]:
    worst_iou_loss = 0.0
    boundary_distances: list[float] = []
    for item in reference.silhouettes:
        candidate_mask = _project_region(
            candidate,
            item.right,
            item.up,
            item.frame,
            reference.contract.silhouette_resolution,
        )
        intersection = ImageChops.multiply(item.mask, candidate_mask).histogram()[255]
        union = ImageChops.lighter(item.mask, candidate_mask).histogram()[255]
        iou_loss = 0.0 if union == 0 else 1.0 - intersection / union
        worst_iou_loss = max(worst_iou_loss, iou_loss)
        candidate_boundary = _boundary_points(candidate_mask)
        if not item.boundary and not candidate_boundary:
            continue
        if not item.boundary or not candidate_boundary:
            boundary_distances.append(float(reference.contract.silhouette_resolution))
            continue
        candidate_tree = _kd_tree(candidate_boundary)
        boundary_distances.extend(math.sqrt(_kd_distance(point, candidate_tree)) for point in item.boundary)
        boundary_distances.extend(math.sqrt(_kd_distance(point, item.boundary_tree)) for point in candidate_boundary)
    return worst_iou_loss, _percentile(boundary_distances, 0.95)


def _validate_structure(original: SmdRegion, candidate: SmdRegion) -> None:
    if original.material.casefold() != candidate.material.casefold():
        raise ValueError("candidate material differs from original")
    original_bones = set(original.bone_ids)
    candidate_bones = set()
    for triangle in candidate.triangles:
        if _triangle_area(triangle) <= 1e-15:
            raise ValueError("candidate contains a degenerate triangle")
        for vertex in triangle.vertices:
            positive = tuple(influence for influence in vertex.influences if influence.weight > 1e-8)
            if len(positive) > 3:
                raise ValueError("candidate exceeds Source bone influence limit")
            candidate_bones.update(influence.bone for influence in positive)
            if not all(math.isfinite(value) for value in (*vertex.position, *vertex.normal, *vertex.uv)):
                raise ValueError("candidate contains a non-finite attribute")
    if not candidate_bones.issubset(original_bones):
        raise ValueError("candidate introduces a new bone")


def prepare_region_reference(original: SmdRegion, contract: MetricContract) -> PreparedRegionReference:
    _validate_structure(original, original)
    bounds_min = original.bounds_min
    bounds_max = original.bounds_max
    diagonal = max(_length(_sub(bounds_max, bounds_min)), 1e-9)
    original_data = _triangle_data(original)
    original_bvh = _build_bvh(original_data, tuple(range(len(original_data))))
    return PreparedRegionReference(
        original,
        contract,
        diagonal,
        original_data,
        original_bvh,
        _samples(original, contract.sample_count, contract.seed + ":original"),
        _prepare_silhouettes(original, contract),
    )


def measure_region_prepared(reference: PreparedRegionReference, candidate: SmdRegion) -> RegionMetrics:
    original = reference.original
    contract = reference.contract
    _validate_structure(original, candidate)
    candidate_data = _triangle_data(candidate)
    candidate_bvh = _build_bvh(candidate_data, tuple(range(len(candidate_data))))
    candidate_samples = _samples(candidate, contract.sample_count, contract.seed + ":candidate")
    forward = _direction_metrics(reference.samples, candidate_data, candidate_bvh, contract.poses, reference.diagonal)
    reverse = _direction_metrics(candidate_samples, reference.triangle_data, reference.bvh, contract.poses, reference.diagonal)
    surfaces = forward[0] + reverse[0]
    normals = forward[1] + reverse[1]
    uvs = forward[2] + reverse[2]
    skinning = forward[3] + reverse[3]
    silhouette_iou, silhouette_boundary = _silhouette_metrics_prepared(reference, candidate)
    return RegionMetrics(
        surface_p95=_clean(_percentile(surfaces, 0.95)),
        surface_max=_clean(max(surfaces, default=0.0)),
        normal_p95_degrees=_clean(_percentile(normals, 0.95)),
        normal_max_degrees=_clean(max(normals, default=0.0)),
        silhouette_iou_loss=_clean(silhouette_iou),
        silhouette_boundary_p95_px=_clean(silhouette_boundary),
        uv_p95=_clean(_percentile(uvs, 0.95)),
        material_boundary_p95_px=_clean(silhouette_boundary),
        skinning_p95=_clean(_percentile(skinning, 0.95)),
        skinning_max=_clean(max(skinning, default=0.0)),
    )


def measure_region(original: SmdRegion, candidate: SmdRegion, contract: MetricContract) -> RegionMetrics:
    return measure_region_prepared(prepare_region_reference(original, contract), candidate)


def validate_region(metrics: RegionMetrics, budget: RegionBudget) -> ValidationDecision:
    gates = {
        "surface": metrics.surface_p95 <= budget.surface_p95 and metrics.surface_max <= budget.surface_max,
        "normal": metrics.normal_p95_degrees <= budget.normal_p95_degrees and metrics.normal_max_degrees <= budget.normal_max_degrees,
        "silhouette": metrics.silhouette_iou_loss <= budget.silhouette_iou_loss and metrics.silhouette_boundary_p95_px <= budget.silhouette_boundary_p95_px,
        "uv": metrics.uv_p95 <= budget.uv_p95,
        "material-boundary": metrics.material_boundary_p95_px <= budget.material_boundary_p95_px,
        "skinning": metrics.skinning_p95 <= budget.skinning_p95 and metrics.skinning_max <= budget.skinning_max,
    }
    ratios = (
        metrics.surface_p95 / budget.surface_p95,
        metrics.surface_max / budget.surface_max,
        metrics.normal_p95_degrees / budget.normal_p95_degrees,
        metrics.normal_max_degrees / budget.normal_max_degrees,
        metrics.silhouette_iou_loss / budget.silhouette_iou_loss,
        metrics.silhouette_boundary_p95_px / budget.silhouette_boundary_p95_px,
        metrics.uv_p95 / budget.uv_p95,
        metrics.material_boundary_p95_px / budget.material_boundary_p95_px,
        metrics.skinning_p95 / budget.skinning_p95,
        metrics.skinning_max / budget.skinning_max,
    )
    return ValidationDecision(
        all(gates.values()),
        tuple(name for name, passed in gates.items() if not passed),
        max(0.0, min(1.0, 1.0 - max(ratios))),
    )
