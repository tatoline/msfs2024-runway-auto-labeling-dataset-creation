# Examples

## Scenario inputs

`scenarios/` contains five converted noon Broken Clouds scenarios, one per airport. They are adapted from the project scenario collection provided by **Siddhartha Gupta**.

Read the [scenario-format guide](../docs/scenario-format.md) for field descriptions and units. Validate the examples with:

```powershell
python tools/validate_scenarios.py examples/scenarios
```

`scenarios.json` is a flat five-record capture batch corresponding to `sample_capture/`. It can be passed directly to the capture script. The converted files demonstrate the upstream input format and may use different scenario IDs.

## Saved capture and candidate labels

`sample_capture/` contains five 1920×1080 stock-light Broken Clouds images with matching camera telemetry and a capture manifest. There is one example per airport.

`sample_labels/` contains automatic four-corner label candidates for these images. Follow the review and export steps in the main README to create a reviewed example dataset.

## Calibration profiles

- `profiles/five_airport_geometry.json`: rendered runway-corner geometry for the five example runways.
- `profiles/five_airport_pixel_calibration.json`: paired per-airport pixel-residual coefficients for 1920×1080 images.
- `profiles/facilities_snapshot.json`: a Facilities API snapshot for trying light generation without running MSFS.

The pixel profile is bound to its geometry file by SHA-256. Keep them paired. Validate the profiles against your own camera, scenery, and simulator version before using them for new captures.

See [NOTICE.md](../NOTICE.md) for attribution and licence scope.
