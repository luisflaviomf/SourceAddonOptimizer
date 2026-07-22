from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageChops

from maximum_optimizer import metrics as metrics_module
from maximum_optimizer.silhouette_backend import (
    configure_silhouette_backend,
    reset_silhouette_backend,
)
from maximum_optimizer.silhouette_native import (
    EXPECTED_SILHOUETTE_BUILD_ID,
    MaskBatch,
    NativeSilhouettePackage,
    RawMaskSilhouetteKernel,
)


PROMOTED_DLL = (
    Path(__file__).resolve().parents[2]
    / "maximum_optimizer"
    / "native"
    / "bin"
    / "win-x64"
    / "meshopt_bridge.dll"
)


def promoted_package(path: Path = PROMOTED_DLL) -> NativeSilhouettePackage:
    import hashlib

    payload = path.read_bytes()
    return NativeSilhouettePackage(
        dll_path=path.resolve(),
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        api_version="1.0.0",
        build_id=EXPECTED_SILHOUETTE_BUILD_ID,
        architecture="x64",
    )


class NativeContractTests(unittest.TestCase):
    def test_requires_an_absolute_manifest_path(self) -> None:
        package = NativeSilhouettePackage(
            Path("meshopt_bridge.dll"), "0" * 64, 1, "1.0.0", EXPECTED_SILHOUETTE_BUILD_ID, "x64"
        )
        with self.assertRaisesRegex(ValueError, "absolute"):
            RawMaskSilhouetteKernel(package)

    @unittest.skipUnless(PROMOTED_DLL.is_file(), "promoted silhouette DLL not built")
    def test_reports_and_validates_the_complete_promoted_abi(self) -> None:
        kernel = RawMaskSilhouetteKernel(promoted_package())
        self.assertEqual(kernel.api_version, "1.0.0")
        self.assertEqual(kernel.build_id, EXPECTED_SILHOUETTE_BUILD_ID)
        self.assertEqual(kernel.architecture, "x64")

    @unittest.skipUnless(PROMOTED_DLL.is_file(), "promoted silhouette DLL not built")
    def test_rejects_wrong_hash_and_size_before_loading(self) -> None:
        package = promoted_package()
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            RawMaskSilhouetteKernel(
                NativeSilhouettePackage(
                    package.dll_path,
                    "0" * 64,
                    package.size,
                    package.api_version,
                    package.build_id,
                    package.architecture,
                )
            )
        with self.assertRaisesRegex(RuntimeError, "size"):
            RawMaskSilhouetteKernel(
                NativeSilhouettePackage(
                    package.dll_path,
                    package.sha256,
                    package.size + 1,
                    package.api_version,
                    package.build_id,
                    package.architecture,
                )
            )

    @unittest.skipUnless(PROMOTED_DLL.is_file(), "promoted silhouette DLL not built")
    def test_rejects_non_amd64_pe_before_loading(self) -> None:
        payload = bytearray(PROMOTED_DLL.read_bytes())
        pe_offset = struct.unpack_from("<I", payload, 0x3C)[0]
        struct.pack_into("<H", payload, pe_offset + 4, 0x014C)
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "meshopt_bridge.dll"
            fake.write_bytes(payload)
            with self.assertRaisesRegex(RuntimeError, "AMD64"):
                RawMaskSilhouetteKernel(promoted_package(fake))


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


@unittest.skipUnless(PROMOTED_DLL.is_file(), "promoted silhouette DLL not built")
class RawMaskSilhouetteKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kernel = RawMaskSilhouetteKernel(promoted_package())

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

    def test_opt_in_prepared_metrics_are_bitwise_exact_and_use_one_batch_call(self) -> None:
        from tests.maximum_optimizer.test_metrics import BUDGET, CONTRACT, make_disc

        original = make_disc(64)
        candidate = make_disc(6)
        reset_silhouette_backend()
        baseline_reference = metrics_module.prepare_region_reference(original, CONTRACT)
        baseline = metrics_module.measure_region_prepared(baseline_reference, candidate)
        baseline_decision = metrics_module.validate_region(baseline, BUDGET)

        backend = configure_silhouette_backend(promoted_package())
        self.assertTrue(backend.initialize())
        try:
            metrics_module._reset_silhouette_experiment_diagnostics()
            with mock.patch(
                "maximum_optimizer.metrics._boundary_points",
                side_effect=AssertionError("experimental path rebuilt Python boundaries"),
            ), mock.patch(
                "maximum_optimizer.metrics._kd_tree",
                side_effect=AssertionError("experimental path rebuilt Python KD trees"),
            ):
                native_reference = metrics_module.prepare_region_reference(original, CONTRACT)
                actual = metrics_module.measure_region_prepared(native_reference, candidate)
            diagnostics = metrics_module._get_silhouette_experiment_diagnostics()
        finally:
            reset_silhouette_backend()

        self.assertEqual(actual, baseline)
        self.assertEqual(metrics_module.validate_region(actual, BUDGET), baseline_decision)
        for field in baseline.__dataclass_fields__:
            self.assertEqual(
                struct.pack("=d", getattr(actual, field)),
                struct.pack("=d", getattr(baseline, field)),
                field,
            )
        self.assertEqual(diagnostics["calls"], 1)
        self.assertGreater(diagnostics["mask_preparation_ns"], 0)
        self.assertGreater(diagnostics["native_call_ns"], 0)


if __name__ == "__main__":
    unittest.main()
