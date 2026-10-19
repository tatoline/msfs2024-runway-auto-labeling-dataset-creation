# Calibration and independent validation

Camera telemetry removes unknown cockpit-view assumptions, but it does not guarantee that published or Facilities geometry exactly follows the visible runway paint. This workflow keeps camera measurement, runway fitting, and pixel residual correction separate.

## Start with a useful pilot

Capture near, middle, and far examples for every target runway, including different lateral positions. For the full pixel-residual model, the fitter requires at least eight accepted reviewed images per airport. Use more than the minimum to cover meaningful variation, and reserve different scenarios for validation.

A five-image example can check imports, paths, and the review interface. It is not enough to calibrate five airports.

## 1. Create geometry-only draft labels and a review package

Assume `runs/calibration_01` is your completed calibration capture.

```powershell
python tools/label_camera_api_capture.py --dataset-dir runs/calibration_01 --output-dir work/calibration_initial --overlay
python tools/prepare_label_review.py --dataset-dir runs/calibration_01 --labels-dir work/calibration_initial --output-dir work/calibration_review --count 50
python tools/review_training_labels.py --package-dir work/calibration_review --output work/calibration_review/qa/review.json --annotator "Your name"
```

Use the number of candidate images available in your run instead of 50 if necessary. Correct all four pavement corners carefully, inspect the full frame, and finish every decision.

## 2. Fit rendered runway geometry

```powershell
python tools/fit_camera_api_runway_geometry.py --dataset-dir runs/calibration_01 --package-dir work/calibration_review --review work/calibration_review/qa/review.json --output-dir work/geometry_fit
```

The output `work/geometry_fit/fit_report.json` contains the world corners for each fitted runway. The constrained robust fit adjusts near/far threshold centres and a shared runway-width correction. It does not warp images.

Inspect the reported shifts, boundary hits, and error summaries. Training-set fit errors alone are not independent evidence.

## 3. Generate geometry-corrected labels

```powershell
python tools/label_camera_api_capture.py --dataset-dir runs/calibration_01 --output-dir work/calibration_geometry_labels --runway-geometry work/geometry_fit/fit_report.json --overlay
```

## 4. Optionally fit a small pixel residual

```powershell
python tools/fit_pixel_residual_calibration.py --labels-dir work/calibration_geometry_labels --package-dir work/calibration_review --review work/calibration_review/qa/review.json --output work/pixel_calibration.json
```

This fits a low-capacity ridge regression for each airport and corner. Its inputs are projected near-edge vertical position, polygon height/width, and horizontal position. It learns a small remaining screen-space offset.

The pixel model is paired with the SHA-256 of the geometry file. If you edit the geometry file, fit or correctly rebind the model; do not bypass the hash check.

## 5. Validate on separate images

Capture a new batch, `runs/validation_01`, whose scenario IDs are absent from every calibration review. Keep the same camera and scenery. Then project it:

```powershell
python tools/label_camera_api_capture.py --dataset-dir runs/validation_01 --output-dir work/validation_labels --runway-geometry work/geometry_fit/fit_report.json --pixel-calibration work/pixel_calibration.json --overlay
python tools/prepare_label_review.py --dataset-dir runs/validation_01 --labels-dir work/validation_labels --output-dir work/validation_review --count 20
python tools/review_training_labels.py --package-dir work/validation_review --output work/validation_review/qa/review.json --annotator "Your name"
```

Measure these validation corners independently and carefully. Simply accepting every shown polygon measures agreement with the displayed proposal, not accuracy against an independent reference. A second reviewer or blinded corner measurement provides stronger evidence.

Calculate pixel errors:

```powershell
python tools/evaluate_labels.py --labels-dir work/validation_labels --package-dir work/validation_review --review work/validation_review/qa/review.json --calibration-review work/calibration_review/qa/review.json --output work/independent_validation.json
```

Repeat `--calibration-review` for every review used by either fit. The evaluator rejects overlapping scenario IDs or image hashes. It compares four Euclidean corner errors per accepted image, verifies review/image hashes, and reports mean, median, 95th percentile, and maximum.

Default working limits: mean ≤1.25 px, p95 ≤3 px, max ≤5 px, with at least ten accepted validation images. These are project working limits, not a certification standard. A successful report does not validate untested airports, scenery, camera settings, or visibility conditions.

## 6. Reuse only after checking

Use your checked geometry and pixel model on later captures with the same setup. Review ambiguous visibility and out-of-range cases. Revalidate after changing camera, output aspect ratio, aircraft camera offset, scenery, or simulator version.

Prepare a review package covering all candidates that need individual review, then export the completed decisions. Preserve the distinction between individually reviewed labels and automatic projections in your dataset documentation.

## Precise reference-point application

```powershell
python tools/prepare_reference_points.py --labels-dir work/calibration_initial --output work/references.json
python tools/review_runway_corners.py --dataset-dir runs/calibration_01 --references work/references.json --output work/manual_reference_points.json
```

The reference app supports point clicking, zoom, pan, and autosave. Its output is a measurement aid, not the final-review schema consumed by the fitters. Use these measurements to help establish the final-review corners.
