"""Create deterministic heavy-rain training images without changing geometry.

The source screenshots are never modified.  Each output is accompanied by an
audit record that binds source/output hashes to the exact augmentation seed and
parameters.  Labels may be copied unchanged because dimensions and pixels are
not warped or cropped.
"""

import argparse
import csv
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


PRESETS = {
    "heavy": {
        "brightness": 0.88,
        "color": 0.82,
        "contrast": 0.94,
        "layers": [
            # count per megapixel, length px, width px, alpha, blur px
            (1350, (8, 18), (1, 1), (28, 58), 0.35),
            (620, (18, 34), (1, 2), (42, 82), 0.50),
            (150, (36, 72), (2, 3), (52, 105), 0.70),
        ],
    },
    "severe": {
        "brightness": 0.84,
        "color": 0.76,
        "contrast": 0.91,
        "layers": [
            (1900, (9, 20), (1, 1), (32, 68), 0.35),
            (900, (20, 40), (1, 2), (48, 94), 0.55),
            (240, (42, 84), (2, 4), (62, 122), 0.80),
        ],
    },
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_seed(master_seed, relative_name):
    value = hashlib.sha256(f"{master_seed}:{relative_name}".encode("utf-8")).digest()
    return int.from_bytes(value[:8], "big")


def rain_layer(size, rng, count, length_range, width_range, alpha_range,
               angle_deg, angle_jitter_deg, blur_radius):
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    margin = int(length_range[1] * 2)
    for _ in range(count):
        length = rng.uniform(*length_range)
        angle = math.radians(angle_deg + rng.uniform(-angle_jitter_deg, angle_jitter_deg))
        # Angle zero is vertical; positive values lean with screen-space wind.
        dx = math.sin(angle) * length
        dy = math.cos(angle) * length
        x = rng.uniform(-margin, width + margin)
        y = rng.uniform(-margin, height + margin)
        tone = rng.randint(202, 242)
        alpha = rng.randint(*alpha_range)
        draw.line((x, y, x + dx, y + dy),
                  fill=(tone, tone + rng.randint(0, 8), min(255, tone + 12), alpha),
                  width=rng.randint(*width_range))
    return layer.filter(ImageFilter.GaussianBlur(blur_radius))


def augment(source, destination, *, preset_name, master_seed, angle_deg):
    params = PRESETS[preset_name]
    with Image.open(source) as opened:
        base = opened.convert("RGB")
    base = ImageEnhance.Color(base).enhance(params["color"])
    base = ImageEnhance.Contrast(base).enhance(params["contrast"])
    base = ImageEnhance.Brightness(base).enhance(params["brightness"])
    composite = base.convert("RGBA")
    megapixels = base.width * base.height / 1_000_000.0
    seed = image_seed(master_seed, source.name)
    rng = random.Random(seed)
    for density, lengths, widths, alphas, blur in params["layers"]:
        count = max(1, round(density * megapixels))
        layer = rain_layer(base.size, rng, count, lengths, widths, alphas,
                           angle_deg, 3.5, blur)
        composite = Image.alpha_composite(composite, layer)
    destination.parent.mkdir(parents=True, exist_ok=True)
    composite.convert("RGB").save(destination, format="PNG", compress_level=1)
    return {
        "source_file": source.name,
        "output_file": destination.name,
        "source_sha256": sha256(source),
        "output_sha256": sha256(destination),
        "image_seed": seed,
        "width": base.width,
        "height": base.height,
        "geometry_changed": False,
        "labels_may_be_copied_unchanged": True,
    }


def resolve_images(path):
    images = path / "images" if (path / "images").is_dir() else path
    if not images.is_dir():
        raise FileNotFoundError(f"image directory not found: {images}")
    files = sorted(images.glob("*.png"))
    if not files:
        raise FileNotFoundError(f"no PNG images found: {images}")
    return images, files


def write_augmented_manifest(source_root, output, records):
    source_manifest = source_root / "manifest.csv"
    if not source_manifest.is_file():
        return
    by_name = {record["source_file"]: record for record in records}
    with source_manifest.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    extra = ["source_image_sha256", "synthetic_effect", "synthetic_image_seed"]
    fields.extend(field for field in extra if field not in fields)
    with (output / "manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            name = Path(row.get("image_file", "")).name
            record = by_name.get(name)
            if not record:
                continue
            row["image_file"] = f"images/{record['output_file']}"
            if "image_sha256" in row:
                row["image_sha256"] = record["output_sha256"]
            row.update(
                source_image_sha256=record["source_sha256"],
                synthetic_effect="synthetic_heavy_rain_v1",
                synthetic_image_seed=record["image_seed"],
            )
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--preset", choices=sorted(PRESETS), default="heavy")
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument("--angle", type=float, default=10.0,
                        help="screen-space rain lean in degrees from vertical")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    source_root = Path(args.source_dir).resolve()
    output = Path(args.output_dir).resolve()
    if output.exists():
        parser.error("output already exists; choose a new --output-dir")
    _, files = resolve_images(source_root)
    if args.limit is not None:
        if args.limit <= 0:
            parser.error("limit must be positive")
        files = files[:args.limit]
    (output / "images").mkdir(parents=True)
    records = []
    for index, source in enumerate(files, 1):
        print(f"[{index}/{len(files)}] {source.name}", flush=True)
        records.append(augment(
            source, output / "images" / source.name,
            preset_name=args.preset, master_seed=args.seed, angle_deg=args.angle))
    audit = {
        "schema": "synthetic_weather_augmentation/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(source_root),
        "effect": "synthetic_heavy_rain_v1",
        "preset": args.preset,
        "master_seed": args.seed,
        "angle_from_vertical_deg": args.angle,
        "parameters": PRESETS[args.preset],
        "provenance_notice": (
            "Simulator background with deterministic synthetic camera-space rain; "
            "not native MSFS precipitation particles."
        ),
        "geometry_changed": False,
        "labels_may_be_copied_unchanged": True,
        "images": records,
    }
    (output / "augmentation_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8")
    write_augmented_manifest(source_root, output, records)
    (output / "README.txt").write_text(
        "Synthetic Heavy Rain v1\n\n"
        "The simulator backgrounds are unchanged geometrically. Dense rain streaks "
        "were added deterministically in camera space without windshield droplets "
        "or additional fog. Runway labels may be copied unchanged. See "
        "augmentation_audit.json for full provenance.\n",
        encoding="utf-8")
    print(f"Saved {len(records)} augmented images -> {output}")


if __name__ == "__main__":
    main()
