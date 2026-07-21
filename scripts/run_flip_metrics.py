import argparse
import json
import tempfile

import flip_evaluator
import numpy as np
from PIL import Image


def evaluate_pair(reference: str, test: str) -> tuple[float, float, float]:
    error_map, mean_error, _ = flip_evaluator.evaluate(
        reference,
        test,
        "LDR",
        inputsRGB=True,
        applyMagma=False,
        computeMeanError=True,
    )
    values = np.asarray(error_map, dtype=np.float64).reshape(-1)
    return float(mean_error), float(np.percentile(values, 95)), float(np.max(values))


def composite_on(image: Image.Image, value: int) -> Image.Image:
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (value, value, value, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--composite-alpha", action="store_true")
    args = parser.parse_args()

    if args.composite_alpha:
        with Image.open(args.reference) as reference, Image.open(args.test) as test:
            if reference.size != test.size:
                raise ValueError("Reference and test images must have identical dimensions.")
            measurements = []
            with tempfile.TemporaryDirectory(prefix="vtf_flip_") as temporary:
                for value, name in ((0, "black"), (255, "white")):
                    reference_path = f"{temporary}/reference-{name}.png"
                    test_path = f"{temporary}/test-{name}.png"
                    composite_on(reference, value).save(reference_path)
                    composite_on(test, value).save(test_path)
                    measurements.append(evaluate_pair(reference_path, test_path))
            mean_error = max(value[0] for value in measurements)
            p95_error = max(value[1] for value in measurements)
            maximum_error = max(value[2] for value in measurements)
    else:
        mean_error, p95_error, maximum_error = evaluate_pair(args.reference, args.test)

    print(
        json.dumps(
            {
                "mean": float(mean_error),
                "p95": float(p95_error),
                "maximum": float(maximum_error),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
