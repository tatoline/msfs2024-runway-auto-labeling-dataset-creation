# Dataset downloads and data card

The large research releases are hosted outside GitHub. The repository contains the code and a five-image example, so cloning it does not download thousands of screenshots.

## Downloads

- **[Main dataset — 6,480 images (Google Drive)](https://drive.google.com/drive/folders/1d4ydNrJpQxvQFkwajBlacNzDbCVHPCru):** `MSFS_Runway_Dataset_6480_v1`, covering Broken Clouds, Rain, and Heavy Rain with the simulator's default runway lights. It contains 6,201 training-ready images and 279 excluded images kept for audit.
- **[Extra enhanced-light dataset — 2,474 images (Google Drive)](https://drive.google.com/drive/folders/19fP06hFjSw1DKWCZRlh-cjm9oT4TtDeh):** `MSFS_Runway_Dataset_EnhancedLights_2474_v2`, covering **Rain and Heavy Rain only**, captured with custom enhanced runway lights. It contains 2,247 training-ready images and 227 excluded images kept for audit.

Start with the main dataset. The enhanced-light dataset is an optional supplement for studying runway visibility in Rain and Heavy Rain; it is **not an upgrade to or replacement for the main dataset**. Its `v2` suffix identifies a version of the supplement only. Keep the two datasets separate so their lighting conditions remain clear.

The five-image example is enough to try the tools; downloading the full datasets is optional.

## Release inventory

| Dataset | Weather | Images | Training-ready | Excluded |
|---|---|---:|---:|---:|
| Main — 6480 v1 | Broken Clouds | 2,700 | 2,578 | 122 |
| Main — 6480 v1 | Rain | 1,080 | 1,036 | 44 |
| Main — 6480 v1 | Heavy Rain | 2,700 | 2,587 | 113 |
| **Main total** | | **6,480** | **6,201** | **279** |
| Extra — EnhancedLights 2474 v2 | Rain | 710 | 556 | 154 |
| Extra — EnhancedLights 2474 v2 | Heavy Rain | 1,764 | 1,691 | 73 |
| **Extra enhanced-light total** | | **2,474** | **2,247** | **227** |

Together, the main dataset and supplement store 8,954 screenshots, with 8,448 training-ready entries and 506 exclusions. Their images occupy approximately 18.69 GB in the project inventory. This is the stored total across two rendering variants, not 8,954 unique scenario definitions.

## Coverage and camera

- Airports/runways: EDDM 26R, EDDS 25, LFQQ 26, LIRF 16L, LSZH 14.
- Output: 1920×1080 PNG.
- Broken Clouds and Heavy Rain in the original release: 06:00, 12:00, 17:00, 19:00 UTC.
- Original Rain: the planned noon subset.
- Production camera: measured add-on camera, 28.555° vertical FOV, nominal zero world pitch/bank, offset `(0,0,16)` m.
- Enhanced-light capture: scenarios with ground distance to the project aim point of at least 2 km.

## Annotation meaning

The polygon describes usable runway pavement, in near-right, near-left, far-left, far-right order along the landing direction. Exclude shoulders, blast pads, taxiways, and pavement before a displaced landing threshold. Light glow and outer light centres do not define polygon vertices.

Typical formats:

- `labels/`: YOLO detection boxes, class 0.
- `labels_seg/`: normalized four-corner segmentation polygons.
- `labels_pose/`: detection box plus four ordered keypoints.
- `labels_px/`: exact image-pixel corners and provenance.
- JSON Lines and COCO-style exports.
- Manifests, image hashes, camera telemetry, and QA/review records.

The large releases are organised by weather, with separate usable/excluded folders and training/all-image manifests. The simple public exporter produces a flat reviewed release; the folder layout is not required to match the historical master packages.

## Provenance and quality

The original 6,201 usable labels include 760 human-reviewed projections, 106 human-reviewed corrections, 443 validated earlier projections, and 4,892 validated automatic projections. An independent 19-image test measured mean corner error 0.879 px, p95 2.347 px, and maximum 3.982 px. A later Heavy Rain audit accepted 301 of 309 reviewed labels without modification.

These are historical measurements for that camera/scenery/calibration. They are not a promise for a different installation. Most labels were not individually reviewed; the manifests preserve that distinction.

For enhanced lights, a selected difficult-case visibility review covered 304 images: 160 visible, 104 invisible, 40 uncertain. Another 83 images failed the minimum pixel-size rule. The 227 exclusions are therefore **104 + 40 + 83**, and excluded files/records remain available for audit. An excluded runway image is not automatically a negative example.

## Appearance disclosures

Heavy Rain uses deterministic image-space rain over MSFS backgrounds. Enhanced-light images use a custom additive scenery package. These variants should be identified in training and evaluation reports. They have not been validated as physical rainfall or certified aerodrome photometry.

## Training splits

No official train/validation/test split is assigned here. Group related viewpoints, times, source images and derivatives, and stock/enhanced versions of the same scenario together. An airport-held-out test measures geographic generalisation; a grouped within-airport split answers a different question. Avoid near-duplicate scenery leaking between partitions.

## Access and reuse terms

The repository's MIT licence covers its software and documentation. Dataset, simulator imagery, and contributed scenario terms remain separate. See the corresponding release documentation and the repository's `NOTICE.md`.
