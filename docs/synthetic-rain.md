# Synthetic Heavy Rain

Native heavy precipitation sometimes hid the runway behind extreme haze or cloud. Other settings preserved visibility but showed little visible rain. The project therefore used accepted MSFS backgrounds with controlled image-space rain augmentation.

The implementation uses Python's `random`, SHA-256-derived seeds, and Pillow's drawing, compositing, Gaussian blur, and image-enhancement functions. It does not use a learned weather model or an external rain dataset.

## Capture-aware workflow

```powershell
python tools/augment_capture.py --source-dir runs/pilot_01 --output-dir runs/pilot_01_synthetic_rain --preset heavy --seed 20260903 --angle 10
```

The wrapper validates the source capture, augments its images, carries camera observations forward, and records both source and output hashes. Telemetry explicitly identifies the pixels as a synthetic derivative. Original camera observations are retained; no new native camera capture is claimed.

Generate fresh labels for the derivative folder before review/export:

```powershell
python tools/label_camera_api_capture.py --dataset-dir runs/pilot_01_synthetic_rain --output-dir work/synthetic_labels --runway-geometry examples/profiles/five_airport_geometry.json --pixel-calibration examples/profiles/five_airport_pixel_calibration.json --overlay
```

Replace the example profiles with your own checked profiles when appropriate. The fresh label report is bound to the new image and manifest hashes.

## Image-only workflow

```powershell
python tools/augment_synthetic_rain.py --source-dir path/to/png_images --output-dir work/rain_images --preset heavy --seed 20260903 --angle 10
```

This lower-level tool writes images and an augmentation audit, plus a transformed manifest if present. It does not by itself carry the complete Camera API capture metadata. Use the wrapper for a capture that you will label with this repository.

## Heavy preset

| Layer | Streaks per megapixel | Length | Width | Alpha (0–255) | Blur |
|---|---:|---:|---:|---:|---:|
| Fine | 1,350 | 8–18 px | 1 px | 28–58 | 0.35 px |
| Medium | 620 | 18–34 px | 1–2 px | 42–82 | 0.50 px |
| Long | 150 | 36–72 px | 2–3 px | 52–105 | 0.70 px |

The mean angle is 10° from vertical with ±3.5° jitter. Position, length, width, opacity, and grey/blue tone vary. The background factors are brightness 0.88, colour 0.82, and contrast 0.94. `severe` is another available preset; the project Heavy Rain examples use `heavy`.

The same seed, filename, preset, and image produce the same output within the same software environment. The source is not overwritten. Width, height, and runway geometry remain unchanged, so existing geometric corner coordinates remain valid; their provenance must be rebound to the derivative image hash.

## Interpretation

This is multi-scale appearance augmentation, with depth-like variation in streak size. It has no scene-depth model, droplet simulation, exposure-derived trajectory calculation, calibrated rainfall rate, windshield droplets, refraction, or validated light scattering. It can provide controlled rain-like appearance, but it should not be described as physically validated heavy rainfall.
