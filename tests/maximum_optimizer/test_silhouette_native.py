from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import unittest

from PIL import Image, ImageChops

from maximum_optimizer import metrics as metrics_module
from maximum_optimizer.silhouette_native import MaskBatch, RawMaskSilhouetteKernel


EXPERIMENT_DLL = Path(os.environ.get("MAXIMUM_SILHOUETTE_EXPERIMENT_DLL", ""))


def mask(width: int, height: int, points: set[tuple[int, int]]) -> Image.Image:
    image = Image.new("L", (width, height), 0)
    pixels = image.load()
    for x, y in points:
        pixels[x, y] = 255
    return image


def padded_batch(
    images: tuple[Image.Image, ...],
    *,
    row_padding: int = 0,
    view_padding: int = 0,
    offset: int = 0,
) -> MaskBatch:
    width, height = images[0].size
    row_stride = width + row_padding
    view_stride = row_stride * height + view_padding
    storage = bytearray(offset + view_stride * len(images))
    for view, image in enumerate(images):
        raw = image.tobytes()
        for y in range(height):
            start = offset + view * view_stride + y * row_stride
            storage[start : start + width] = raw[y * width : (y + 1) * width]
    return MaskBatch(storage, width, height, len(images), row_stride, view_stride, offset)


def oracle(
    originals: tuple[Image.Image, ...],
    candidates: tuple[Image.Image, ...],
    empty_distance: int,
) -> dict[str, object]:
    original_boundaries = tuple(metrics_module._boundary_points(image) for image in originals)
    candidate_boundaries = tuple(metrics_module._boundary_points(image) for image in candidates)
    intersections = []
    unions = []
    distances_by_view = []
    distances = []
    worst_iou = 0.0
    for original, candidate, original_boundary, candidate_boundary in zip(
        originals, candidates, original_boundaries, candidate_boundaries
    ):
        intersection = ImageChops.multiply(original, candidate).histogram()[255]
        union = ImageChops.lighter(original, candidate).histogram()[255]
        intersections.append(intersection)
        unions.append(union)
        worst_iou = max(worst_iou, 0.0 if union == 0 else 1.0 - intersection / union)
        view_distances: list[int] = []
        if original_boundary or candidate_boundary:
            if not original_boundary or not candidate_boundary:
                view_distances.append(empty_distance * empty_distance)
            else:
                candidate_tree = metrics_module._kd_tree(candidate_boundary)
                original_tree = metrics_module._kd_tree(original_boundary)
                view_distances.extend(
                    int(metrics_module._kd_distance(point, candidate_tree))
                    for point in original_boundary
                )
                view_distances.extend(
                    int(metrics_module._kd_distance(point, original_tree))
                    for point in candidate_boundary
                )
        distances_by_view.append(tuple(view_distances))
        distances.extend(view_distances)
    p95_squared = (
        sorted(distances)[min(len(distances) - 1, max(0, int(math.ceil(len(distances) * 0.95)) - 1))]
        if distances
        else 0
    )
    return {
        "original_boundaries": original_boundaries,
        "candidate_boundaries": candidate_boundaries,
        "distances_by_view": tuple(distances_by_view),
        "intersections": tuple(intersections),
        "unions": tuple(unions),
        "distance_count": len(distances),
        "p95_squared": p95_squared,
        "worst_iou": worst_iou,
        "boundary_p95": math.sqrt(p95_squared),
    }


@unittest.skipUnless(EXPERIMENT_DLL.is_file(), "experimental silhouette DLL not configured")
class RawMaskSilhouetteKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kernel = RawMaskSilhouetteKernel(EXPERIMENT_DLL)

    def assert_exact_case(
        self,
        originals: tuple[Image.Image, ...],
        candidates: tuple[Image.Image, ...],
        *,
        empty_distance: int | None = None,
        original_padding: tuple[int, int, int] = (0, 0, 0),
        candidate_padding: tuple[int, int, int] = (0, 0, 0),
    ) -> None:
        width, height = originals[0].size
        sentinel = width if empty_distance is None else empty_distance
        expected = oracle(originals, candidates, sentinel)
        original_batch = padded_batch(
            originals,
            row_padding=original_padding[0],
            view_padding=original_padding[1],
            offset=original_padding[2],
        )
        candidate_batch = padded_batch(
            candidates,
            row_padding=candidate_padding[0],
            view_padding=candidate_padding[1],
            offset=candidate_padding[2],
        )

        actual = self.kernel.measure(
            original_batch,
            candidate_batch,
            empty_distance=sentinel,
            debug=True,
        )

        self.assertEqual(actual.intersections, expected["intersections"])
        self.assertEqual(actual.unions, expected["unions"])
        self.assertEqual(actual.distance_count, expected["distance_count"])
        self.assertEqual(actual.p95_distance_squared, expected["p95_squared"])
        self.assertEqual(actual.debug.original_boundaries, expected["original_boundaries"])
        self.assertEqual(actual.debug.candidate_boundaries, expected["candidate_boundaries"])
        self.assertEqual(actual.debug.distances_by_view, expected["distances_by_view"])
        self.assertEqual(
            struct.pack("=d", actual.worst_iou_loss),
            struct.pack("=d", expected["worst_iou"]),
        )
        self.assertEqual(
            struct.pack("=d", actual.boundary_p95_px),
            struct.pack("=d", expected["boundary_p95"]),
        )

    def test_empty_full_single_pixel_and_border_contracts(self) -> None:
        width, height = 9, 7
        empty = mask(width, height, set())
        full = mask(width, height, {(x, y) for y in range(height) for x in range(width)})
        single = mask(width, height, {(4, 3)})
        border = mask(width, height, {(0, y) for y in range(height)} | {(x, 0) for x in range(width)})
        for name, original, candidate in (
            ("both-empty", empty, empty),
            ("original-empty", empty, single),
            ("candidate-empty", single, empty),
            ("full", full, full),
            ("single", single, single),
            ("border", border, single),
        ):
            with self.subTest(name=name):
                self.assert_exact_case((original,), (candidate,), empty_distance=13)

    def test_disconnected_tiny_ties_odd_dimensions_and_orientation(self) -> None:
        width, height = 11, 9
        disconnected = mask(width, height, {(1, 1), (1, 2), (8, 6), (9, 6)})
        tied = mask(width, height, {(3, 4), (7, 4)})
        center = mask(width, height, {(5, 4)})
        horizontal = mask(width, height, {(x, 2) for x in range(1, 10)})
        vertical = mask(width, height, {(2, y) for y in range(1, 8)})

        self.assert_exact_case(
            (disconnected, tied, horizontal),
            (center, center, vertical),
        )

    def test_different_strides_alignments_and_multiple_views(self) -> None:
        width, height = 13, 5
        originals = (
            mask(width, height, {(1, 1), (2, 1), (2, 2)}),
            mask(width, height, {(11, 3), (11, 4)}),
            mask(width, height, set()),
        )
        candidates = (
            mask(width, height, {(2, 1), (3, 1), (3, 2)}),
            mask(width, height, {(10, 2), (10, 3)}),
            mask(width, height, {(6, 2)}),
        )

        self.assert_exact_case(
            originals,
            candidates,
            original_padding=(3, 5, 1),
            candidate_padding=(7, 9, 3),
        )

    def test_maximum_corner_distance_uses_exact_uint32_squared_value(self) -> None:
        width, height = 257, 259
        original = mask(width, height, {(0, 0)})
        candidate = mask(width, height, {(width - 1, height - 1)})

        self.assert_exact_case((original,), (candidate,))
        result = self.kernel.measure(
            padded_batch((original,)),
            padded_batch((candidate,)),
            empty_distance=width,
        )
        self.assertEqual(result.p95_distance_squared, 256 * 256 + 258 * 258)

    def test_float64_is_used_end_to_end_without_float32_rounding(self) -> None:
        original = mask(8, 8, {(0, 0)})
        candidate = mask(8, 8, {(2, 1)})

        result = self.kernel.measure(
            padded_batch((original,)),
            padded_batch((candidate,)),
            empty_distance=8,
        )

        self.assertEqual(result.p95_distance_squared, 5)
        self.assertEqual(struct.pack("=d", result.boundary_p95_px), struct.pack("=d", math.sqrt(5)))
        self.assertNotEqual(
            struct.pack("=d", result.boundary_p95_px),
            struct.pack("=d", struct.unpack("=f", struct.pack("=f", math.sqrt(5)))[0]),
        )

    def test_rejects_invalid_layout_and_non_binary_masks(self) -> None:
        image = mask(5, 5, {(2, 2)})
        valid = padded_batch((image,))
        invalid_stride = MaskBatch(bytearray(25), 5, 5, 1, 4, 25, 0)
        non_binary = padded_batch((image,))
        non_binary.buffer[0] = 1

        with self.assertRaisesRegex(ValueError, "row stride"):
            self.kernel.measure(invalid_stride, valid, empty_distance=5)
        with self.assertRaisesRegex(ValueError, "binary"):
            self.kernel.measure(non_binary, valid, empty_distance=5)


if __name__ == "__main__":
    unittest.main()
