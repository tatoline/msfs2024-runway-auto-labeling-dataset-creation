# Enhanced runway-light experiment

The current profile is `unboosted-daylight-v2p10`, package version 1.6.0. It was tuned for five selected runway approaches at ground distances of at least 2 km.

Weak stock lights in daytime fog/rain made some geometrically valid images difficult to learn from. Early custom versions caused merged bloom. The accepted profile uses small glow radii, fixed per-airport intensity factors, and `boostDayLight=false`.

This is an additive visibility experiment. It does not replace the stock runway, ILS, PAPI, taxiways, or navigation data. It is not a certified photometric copy of real airport fixtures.

## 1. Read current runway facilities

For an **offline source-generation example**, the small historical snapshot is included:

```powershell
python enhanced_lights/generate_five_airport_lights.py --facilities examples/profiles/facilities_snapshot.json --runway-geometry examples/profiles/five_airport_geometry.json --output work/lights_example/scene.xml --report work/lights_example/report.json
```

This produces the historical 4,052-point layout without opening MSFS. For a new live setup, read current facility records as follows.

Build the camera bridge and load a free flight in MSFS. Then run:

```powershell
python tools/query_runway_facilities.py --airports EDDM EDDS LFQQ LIRF LSZH --output work/lights/facilities.json --timeout 30
```

## 2. Generate the combined light scene

```powershell
python enhanced_lights/generate_five_airport_lights.py --facilities work/lights/facilities.json --runway-geometry examples/profiles/five_airport_geometry.json --output work/lights/scene.xml --report work/lights/generation_report.json
```

Use your own checked fitted geometry when available. Omit `--runway-geometry` to generate from Facilities data only, then inspect alignment. The existing target map contains:

| Airport | Landing runway | Approach family | Historical added lights | Intensity factor |
|---|---|---|---:|---:|
| EDDM | 26R | ALSF-2 | 856 | 0.42 |
| EDDS | 25 | ALSF-2 | 751 | 0.95 |
| LFQQ | 26 | CALVERT-2 | 739 | 0.42 |
| LIRF | 16L | CALVERT-2 | 891 | 0.50 |
| LSZH | 14 | CALVERT-2 | 815 | 0.42 |

The historical total was 4,052 sources. Fresh Facilities data or different geometry may produce different counts. New airports need changes to the target configuration plus new tuning and validation.

## 3. Prepare separate short-path projects

```powershell
python enhanced_lights/prepare_packages.py --scene work/lights/scene.xml --output-dir C:/msfs_light_build_v1
```

The output directory must be new. Separate airport packages and short paths avoid the large combined-build and long-path problems observed during development. Each package uses its own short preset names.

## 4. Fully close MSFS, then build

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File enhanced_lights/build_packages.ps1 -ProjectRoot C:/msfs_light_build_v1 -SdkRoot "C:\MSFS 2024 SDK"
```

The script refuses to attach to a running MSFS process. It builds each prepared airport project and checks for a layout and BGL. Build logs are saved beside the projects. It has timeout and output-size guards; if a build fails, inspect the logs and any SDK-started compiler process before retrying.

## 5. Install and restart

Each airport's `Packages` directory contains a folder such as `taylan-eddm-enhanced-lights`. With MSFS closed, copy those **package folders**, not the whole build directory, into your MSFS 2024 Community folder.

Use your actual Community location. It differs between installations, so this repository does not guess it or copy files there automatically. Remove or disable earlier conflicting versions of the same custom-light experiment. Restart MSFS and load a flight.

## 6. Capture and rate a pilot

Prepare scenarios that span all target airports and the intended distances. Use a fresh capture folder and record the variant in notes:

```powershell
python tools/capture_scenarios_camera_api.py --batch work/enhanced_pilot_batch.json --output-dir runs/enhanced_pilot_01 --order airport-time-distance --settle 3 --airport-settle 60 --time-settle 5 --start-delay 10 --fov 28.555 --camera-z 16 --notes "render_variant=enhanced_lighting;profile=unboosted-daylight-v2p10"
python tools/rate_light_pilot.py --pilot-dir runs/enhanced_pilot_01 --output work/light_ratings.json --annotator "Your name"
```

Use 1 for extreme overexposure, 5 for acceptable appearance, 10 for the preferred balance, or the separate not-visible decision. The app saves continuously and has an optional notes field.

Re-capture a pilot after changing the generator. XML changes only take effect after rebuilding, installing, and reloading the package. Do not label all images as visible simply because the lights are installed.

## Light parameters

| Preset | Base candela | Glow radius |
|---|---:|---:|
| Runway white | 1,100 | 0.001 m |
| Runway yellow | 1,100 | 0.002 m |
| Runway red | 1,500 | 0.002 m |
| Threshold green | 2,100 | 0.002 m |
| Approach white | 2,500 | 0.001 m |
| Approach red | 2,200 | 0.002 m |
| Touchdown-zone white | 1,200 | 0.001 m |

Multiply the base candela by the airport factor. The radius is an MSFS glow parameter, not lamp diameter. Values are fixed; the generator does not implement a camera-distance brightness curve.

The layout uses edge lights around 60 m apart, centreline lights at 15 m, touchdown-zone rows at 30 m through the first 900 m, and threshold/end/approach patterns. Approach metadata comes from Facilities records.

## Separate capture and release

Keep enhanced-light runs and releases separate from stock lighting. Record the package/profile, geometry, and optional rain augmentation. Use the normal projection/review/export/QA steps.

Fully hidden or uncertain runways should be excluded under the ordinary positive-label policy. The historical extension retained 2,247 of 2,474 images; 83 were below minimum pixel size, 104 were invisible, and 40 had uncertain visibility.
