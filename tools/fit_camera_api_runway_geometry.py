"""Fit reusable simulator runway corners to reviewed Camera API pilot labels.

The Camera API fixes the per-frame camera pose. This calibration therefore
adjusts only each airport's simulator runway geometry: near/far centre in
north/east/up and one common runway width. The result still requires validation
on new images that were not used by the fit.
"""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from facility_geometry import corners_from_facility, find_runway, geodesic_destination
from label_camera_api_capture import camera_from_record, project_runway


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def midpoint(a, b):
    return [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0]


def translate(point, north_m, east_m, up_m):
    distance = math.hypot(north_m, east_m)
    if distance:
        bearing = math.degrees(math.atan2(east_m, north_m))
        lat, lon = geodesic_destination(point[0], point[1], bearing, distance)
    else:
        lat, lon = point[0], point[1]
    return [lat, lon, point[2] + up_m]


def bearing_between(a, b):
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360.0


def fitted_corners(seed_near, seed_far, seed_width, values):
    near = translate(seed_near, *values[:3])
    far = translate(seed_far, *values[3:6])
    width = seed_width + values[6]
    course = bearing_between(near, far)
    near_right = geodesic_destination(near[0], near[1], course + 90.0, width / 2.0)
    near_left = geodesic_destination(near[0], near[1], course - 90.0, width / 2.0)
    far_left = geodesic_destination(far[0], far[1], course - 90.0, width / 2.0)
    far_right = geodesic_destination(far[0], far[1], course + 90.0, width / 2.0)
    return [
        [near_right[0], near_right[1], near[2]],
        [near_left[0], near_left[1], near[2]],
        [far_left[0], far_left[1], far[2]],
        [far_right[0], far_right[1], far[2]],
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--package-dir", required=True)
    ap.add_argument("--review", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()
    dataset = Path(args.dataset_dir).resolve()
    package = Path(args.package_dir).resolve()
    review_path = Path(args.review).resolve()
    output = Path(args.output_dir).resolve()
    if output.exists():
        ap.error("output directory already exists; choose a new path")

    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("status") != "COMPLETE_READY_TO_EXPORT":
        ap.error("review is incomplete")
    manifest_path = package / "manifest.csv"
    report_path = package / "qa" / "label_report.json"
    if review.get("manifest_sha256") != digest(manifest_path):
        ap.error("review does not match the review-package manifest")
    if review.get("draft_report_sha256") != digest(report_path):
        ap.error("review does not match the draft label report")

    with manifest_path.open(newline="", encoding="utf-8") as file:
        manifest = {row["scenario_id"]: row for row in csv.DictReader(file)}
    batch = {item["sid"]: item for item in json.loads(
        (dataset / "captured_batch.json").read_text(encoding="utf-8"))}
    facilities_path = dataset / "runway_facilities.json"
    facilities_payload = json.loads(facilities_path.read_text(encoding="utf-8"))
    facilities = facilities_payload["airports"]

    records = []
    excluded = []
    for sid, decision in review["images"].items():
        if decision.get("decision") == "excluded":
            excluded.append(sid)
            continue
        if decision.get("decision") not in {"accepted_projection", "accepted_corrected"}:
            ap.error(f"unresolved review decision for {sid}")
        image_path = package / decision["image_file"]
        if digest(image_path) != decision.get("image_sha256"):
            ap.error(f"review image changed: {sid}")
        telemetry_path = dataset / manifest[sid]["telemetry_file"]
        telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
        if telemetry.get("image_sha256") != decision["image_sha256"]:
            ap.error(f"telemetry/image mismatch: {sid}")
        records.append((sid, decision, telemetry))

    airports = sorted({decision["icao"] for _, decision, _ in records})
    report = {
        "status": "CAMERA_API_SCENERY_CALIBRATION_REQUIRES_NEW_IMAGE_VALIDATION",
        "dataset": str(dataset),
        "review_package": str(package),
        "review_file": str(review_path),
        "review_sha256": digest(review_path),
        "facility_file": str(facilities_path),
        "facility_sha256": digest(facilities_path),
        "fov_convention": "vertical",
        "corner_order": ["threshold_right", "threshold_left", "far_left", "far_right"],
        "fit_method": "robust least squares; per-airport near/far centre NEU shifts and common width",
        "regularization": {
            "pixel_sigma": 2.0,
            "horizontal_shift_sigma_m": 50.0,
            "vertical_shift_sigma_m": 8.0,
            "width_shift_sigma_m": 3.0,
        },
        "excluded_review_images": excluded,
        "limitations": [
            "The same reviewed images were used to fit and score this calibration.",
            "Validate on a new, disjoint Camera API capture before bulk labeling.",
            "The fitted corners describe the rendered simulator runway for this annotation policy.",
            "Human visibility review remains required; geometry cannot make a hidden runway visible.",
        ],
        "images": [],
        "airports": {},
    }

    for icao in airports:
        airport_records = [record for record in records if record[1]["icao"] == icao]
        scenario = batch[airport_records[0][0]]
        facility_airport = facilities[icao]
        seed = corners_from_facility(facility_airport, scenario["runway"])
        seed_near, seed_far = midpoint(seed[0], seed[1]), midpoint(seed[2], seed[3])
        runway, _ = find_runway(facility_airport, scenario["runway"])
        seed_width = float(runway["width_m"])

        def corners(values):
            return fitted_corners(seed_near, seed_far, seed_width, values)

        def predicted(values, decision, telemetry):
            camera = camera_from_record(telemetry)
            width, height = decision["image_size"]
            points = project_runway(corners(values), camera, width, height, "vertical")
            if any(x is None or y is None for x, y, _ in points):
                return np.full((4, 2), 1e6)
            return np.asarray([[x, y] for x, y, _ in points], dtype=float)

        scales = np.asarray([50.0, 50.0, 8.0, 50.0, 50.0, 8.0, 3.0])

        def residual(values):
            pixels = []
            for _, decision, telemetry in airport_records:
                measured = np.asarray(decision["corners_px"], dtype=float)
                pixels.extend(((predicted(values, decision, telemetry) - measured) / 2.0).ravel())
            return np.r_[pixels, values / scales]

        lower = np.asarray([-200.0, -200.0, -30.0, -300.0, -200.0, -30.0, -15.0])
        upper = np.asarray([200.0, 200.0, 30.0, 300.0, 200.0, 30.0, 15.0])
        fit = least_squares(residual, np.zeros(7), bounds=(lower, upper),
                            loss="soft_l1", f_scale=1.0, max_nfev=2000)
        if not fit.success:
            raise RuntimeError(f"{icao} fit failed: {fit.message}")
        fitted = corners(fit.x)
        before_errors, after_errors = [], []
        for sid, decision, telemetry in airport_records:
            measured = np.asarray(decision["corners_px"], dtype=float)
            before = predicted(np.zeros(7), decision, telemetry)
            after = predicted(fit.x, decision, telemetry)
            before_distance = np.linalg.norm(before - measured, axis=1)
            after_distance = np.linalg.norm(after - measured, axis=1)
            before_errors.extend(before_distance)
            after_errors.extend(after_distance)
            report["images"].append({
                "scenario_id": sid,
                "icao": icao,
                "ground_distance_m": float(manifest[sid]["ground_dist_m"]),
                "decision": decision["decision"],
                "before_corner_errors_px": before_distance.tolist(),
                "after_corner_errors_px": after_distance.tolist(),
                "before_rmse_px": float(np.sqrt(np.mean(before_distance**2))),
                "after_rmse_px": float(np.sqrt(np.mean(after_distance**2))),
            })
        report["airports"][icao] = {
            "runway": scenario["runway"],
            "corners": fitted,
            "near_shift_neu_m": fit.x[:3].tolist(),
            "far_shift_neu_m": fit.x[3:6].tolist(),
            "width_m": seed_width + float(fit.x[6]),
            "fit_scenarios": [sid for sid, _, _ in airport_records],
            "before_mean_error_px": float(np.mean(before_errors)),
            "after_mean_error_px": float(np.mean(after_errors)),
            "after_max_error_px": float(np.max(after_errors)),
            "optimizer_cost": float(fit.cost),
            "bound_hit": bool(np.any(np.isclose(fit.x, lower, atol=1e-3) |
                                     np.isclose(fit.x, upper, atol=1e-3))),
        }

    all_before = [value for image in report["images"] for value in image["before_corner_errors_px"]]
    all_after = [value for image in report["images"] for value in image["after_corner_errors_px"]]
    report["summary"] = {
        "accepted_images_used": len(records),
        "excluded_images_not_used": len(excluded),
        "before_mean_corner_error_px": float(np.mean(all_before)),
        "before_max_corner_error_px": float(np.max(all_before)),
        "after_mean_corner_error_px": float(np.mean(all_after)),
        "after_max_corner_error_px": float(np.max(all_after)),
    }
    output.mkdir(parents=True)
    (output / "fit_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "review_used.json").write_bytes(review_path.read_bytes())
    print(json.dumps(report["summary"], indent=2))
    for icao, item in report["airports"].items():
        print(f"{icao}: {item['before_mean_error_px']:.2f}px -> "
              f"{item['after_mean_error_px']:.2f}px mean; max {item['after_max_error_px']:.2f}px")


if __name__ == "__main__":
    main()
