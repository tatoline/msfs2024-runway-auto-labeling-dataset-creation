"""Create draft runway labels from per-frame MSFS 2024 Camera API telemetry.

These are pilot-validation candidates, not final ground truth. The Camera API's
FOV convention and alignment with rendered runway paint must first be measured
against manual reference clicks.
"""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from projection_utils import bbox_from, camera_basis, draw_overlay
from camera_api import SCHEMA, SUPPORTED_SCHEMAS
from facility_geometry import corners_from_facility


WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3


def geodetic_to_ecef(lat_deg, lon_deg, altitude_m):
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    radius = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    return np.array([
        (radius + altitude_m) * cos_lat * math.cos(lon),
        (radius + altitude_m) * cos_lat * math.sin(lon),
        (radius * (1.0 - WGS84_E2) + altitude_m) * sin_lat,
    ])


def ecef_delta_to_enu(delta, lat_deg, lon_deg):
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    return np.array([
        -sin_lon * delta[0] + cos_lon * delta[1],
        -sin_lat * cos_lon * delta[0] - sin_lat * sin_lon * delta[1] + cos_lat * delta[2],
        cos_lat * cos_lon * delta[0] + cos_lat * sin_lon * delta[1] + sin_lat * delta[2],
    ])


def circular_midpoint_degrees(a, b):
    delta = (b - a + 180.0) % 360.0 - 180.0
    return (a + delta / 2.0) % 360.0


def camera_from_record(record):
    a, b = record["before"]["camera_world"], record["after"]["camera_world"]
    a_fov = a["fov_deg"] if "fov_deg" in a else a["horizontal_fov_deg_assumed"]
    b_fov = b["fov_deg"] if "fov_deg" in b else b["horizontal_fov_deg_assumed"]
    return {
        "lat": (a["latitude_deg"] + b["latitude_deg"]) / 2.0,
        # Captures are local and do not cross the antimeridian.
        "lon": (a["longitude_deg"] + b["longitude_deg"]) / 2.0,
        "alt_m": (a["altitude_m"] + b["altitude_m"]) / 2.0,
        "pitch": (a["pitch_deg"] + b["pitch_deg"]) / 2.0,
        "bank": (a["bank_deg"] + b["bank_deg"]) / 2.0,
        "heading": circular_midpoint_degrees(a["heading_true_deg"], b["heading_true_deg"]),
        "fov": (a_fov + b_fov) / 2.0,
    }


def focal_length_px(fov_deg, width, height, fov_axis="vertical"):
    """Convert the Camera API FOV to a pinhole focal length in pixels.

    MSFS 2024's add-on Camera API behaves as a vertical FOV for the 16:9
    screenshots in the pilot. Keeping the axis explicit prevents the former
    1.78x focal-length error from returning silently.
    """
    if fov_axis not in {"vertical", "horizontal"}:
        raise ValueError(f"Unsupported FOV axis: {fov_axis}")
    sensor_pixels = height if fov_axis == "vertical" else width
    return sensor_pixels / 2.0 / math.tan(math.radians(fov_deg) / 2.0)


def project_runway(corners, camera, width, height, fov_axis="vertical"):
    """Project WGS84 runway points through the telemetry camera pinhole model."""
    camera_ecef = geodetic_to_ecef(camera["lat"], camera["lon"], camera["alt_m"])
    right, up, forward = camera_basis(camera["heading"], camera["pitch"], camera["bank"])
    focal = focal_length_px(camera["fov"], width, height, fov_axis)
    projected = []
    for lat, lon, altitude_m in corners:
        point_ecef = geodetic_to_ecef(lat, lon, altitude_m)
        enu = ecef_delta_to_enu(point_ecef - camera_ecef, camera["lat"], camera["lon"])
        depth = float(enu @ forward)
        if depth <= 1.0:
            projected.append((None, None, False))
            continue
        x = width / 2.0 + focal * float(enu @ right) / depth
        y = height / 2.0 - focal * float(enu @ up) / depth
        projected.append((x, y, 0 <= x < width and 0 <= y < height))
    return projected


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_runway_geometry(path):
    """Load four-corner world geometry from a prior reference fit."""
    source = Path(path).resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    airports = payload.get("airports")
    if not isinstance(airports, dict) or not airports:
        raise ValueError("runway geometry file has no airports section")
    result = {}
    for icao, geometry in airports.items():
        corners = geometry.get("corners")
        if (not isinstance(corners, list) or len(corners) != 4 or
                any(not isinstance(point, list) or len(point) != 3 or
                    not all(isinstance(value, (int, float)) and math.isfinite(value)
                            for value in point) for point in corners)):
            raise ValueError(f"invalid runway corners for {icao}")
        result[icao] = {"runway": geometry.get("runway"), "corners": corners}
    return source, payload, result


def load_pixel_calibration(path):
    source = Path(path).resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema") != "runway_pixel_residual_calibration_v1":
        raise ValueError("unsupported pixel calibration schema")
    models = payload.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("pixel calibration has no airport models")
    return source, payload, models


def apply_pixel_calibration(projection, model, width, height):
    points = np.asarray([[x, y] for x, y, _ in projection], dtype=float)
    features = np.asarray([points[0, 1], np.ptp(points[:, 1]),
                           np.ptp(points[:, 0]), np.mean(points[:, 0])])
    mean = np.asarray(model["feature_mean"], dtype=float)
    scale = np.asarray(model["feature_scale"], dtype=float)
    vector = np.r_[1.0, (features - mean) / scale]
    corrections = np.stack([
        vector @ np.asarray(corner["beta_xy"], dtype=float)
        for corner in model["corners"]
    ])
    adjusted = points + corrections
    return [(float(x), float(y), bool(visible and 0 <= x < width and 0 <= y < height))
            for (x, y), (_, _, visible) in zip(adjusted, projection)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--overlay", action="store_true")
    ap.add_argument("--min-box-px", type=float, default=25.0)
    ap.add_argument("--fov-axis", choices=("vertical", "horizontal"), default="vertical",
                    help="MSFS 2024 Camera API pilot validates vertical at 16:9")
    ap.add_argument("--runway-geometry",
                    help="fit report containing an airports section with corrected world corners")
    ap.add_argument("--facility-data",
                    help="MSFS Facilities API JSON; preferred source for runway geometry")
    ap.add_argument("--pixel-calibration",
                    help="validated human-review residual calibration applied after projection")
    args = ap.parse_args()
    if args.runway_geometry and args.facility_data:
        ap.error("choose either --facility-data or --runway-geometry")
    if args.pixel_calibration and not args.runway_geometry:
        ap.error("--pixel-calibration requires --runway-geometry")
    if args.min_box_px < 0:
        ap.error("minimum box size must be nonnegative")
    dataset, output = Path(args.dataset_dir).resolve(), Path(args.output_dir).resolve()
    if output.exists():
        ap.error("output directory already exists; choose a new path")
    run = json.loads((dataset / "capture_run.json").read_text(encoding="utf-8"))
    if not str(run.get("pipeline", "")).startswith("MSFS 2024 native Camera API") or \
            run.get("status") != "CAPTURE_COMPLETE_AWAITING_LABEL_VALIDATION":
        ap.error("dataset is not a completed Camera API capture")
    with (dataset / "manifest.csv").open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    if args.only:
        selected = set(args.only)
        rows = [row for row in rows if row["scenario_id"] in selected]
    if not rows:
        ap.error("no images selected")
    scenarios = {
        item["sid"]: item
        for item in json.loads((dataset / "captured_batch.json").read_text(encoding="utf-8"))
    }
    geometry_source = geometry_payload = geometry = None
    if args.runway_geometry:
        try:
            geometry_source, geometry_payload, geometry = load_runway_geometry(args.runway_geometry)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            ap.error(str(error))
    pixel_source = pixel_payload = pixel_models = None
    if args.pixel_calibration:
        try:
            pixel_source, pixel_payload, pixel_models = load_pixel_calibration(args.pixel_calibration)
            if pixel_payload.get("source_runway_geometry_sha256") != file_sha256(geometry_source):
                raise ValueError("pixel calibration belongs to different runway geometry")
            if pixel_payload.get("fov_convention") != args.fov_axis:
                raise ValueError("pixel calibration uses a different FOV convention")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            ap.error(str(error))
    facility_source = facility_payload = facilities = None
    facility_argument = args.facility_data
    if (not facility_argument and not args.runway_geometry and
            (dataset / "runway_facilities.json").is_file()):
        facility_argument = dataset / "runway_facilities.json"
    if facility_argument:
        try:
            facility_source = Path(facility_argument).resolve()
            facility_payload = json.loads(facility_source.read_text(encoding="utf-8"))
            if facility_payload.get("schema") not in {"msfs2024-runway-facilities/v1", "msfs2024-runway-facilities/v2"}:
                raise ValueError("unsupported Facilities API geometry schema")
            facilities = facility_payload.get("airports")
            if not isinstance(facilities, dict) or not facilities:
                raise ValueError("Facilities API file has no airports")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            ap.error(str(error))

    for folder in ("labels", "labels_seg", "labels_pose", "labels_px"):
        (output / folder).mkdir(parents=True, exist_ok=False)
    if args.overlay:
        (output / "label_preview").mkdir()
    report = {
        "status": "CAMERA_API_PROJECTED_CANDIDATES_NOT_GROUND_TRUTH",
        "dataset": str(dataset),
        "manifest_sha256": file_sha256(dataset / "manifest.csv"),
        "projection": "WGS84 ECEF to local ENU; per-frame Camera API pose and FOV; pinhole camera",
        "fov_convention": args.fov_axis,
        "corner_order": ["threshold_right", "threshold_left", "far_left", "far_right"],
        "images": [],
    }
    if geometry_source:
        report.update(
            runway_geometry_file=str(geometry_source),
            runway_geometry_sha256=file_sha256(geometry_source),
            runway_geometry_status=geometry_payload.get("status"),
            runway_geometry_limitations=geometry_payload.get("limitations", []),
        )
    if pixel_source:
        report.update(
            pixel_calibration_file=str(pixel_source),
            pixel_calibration_sha256=file_sha256(pixel_source),
            pixel_calibration_status=pixel_payload.get("status"),
            pixel_calibration_limitations=pixel_payload.get("limitations", []),
        )
    if facility_source:
        report.update(
            runway_geometry_file=str(facility_source),
            runway_geometry_sha256=file_sha256(facility_source),
            runway_geometry_status="live_msfs_facilities_api",
            runway_geometry_captured_utc=facility_payload.get("captured_utc"),
        )
    for row in rows:
        sid = row["scenario_id"]
        item = {"scenario_id": sid, "status": "excluded"}
        report["images"].append(item)
        image_path = (dataset / row["image_file"]).resolve()
        telemetry_path = (dataset / row["telemetry_file"]).resolve()
        if not image_path.is_relative_to(dataset) or not telemetry_path.is_relative_to(dataset):
            item["reason"] = "path outside dataset"
            continue
        if not image_path.is_file() or not telemetry_path.is_file() or sid not in scenarios:
            item["reason"] = "missing image, telemetry or scenario geometry"
            continue
        record = json.loads(telemetry_path.read_text(encoding="utf-8"))
        if record.get("schema") not in SUPPORTED_SCHEMAS or record.get("scenario_id") != sid:
            item["reason"] = "telemetry schema/scenario mismatch"
            continue
        if file_sha256(image_path) != record.get("image_sha256") or \
                record.get("image_sha256") != row.get("image_sha256"):
            item["reason"] = "image hash does not match telemetry and manifest"
            continue
        with Image.open(image_path) as image:
            width, height = image.size
        if [width, height] != record.get("image_size"):
            item["reason"] = "image size does not match telemetry"
            continue
        capture_geometry = record.get("capture_geometry")
        if capture_geometry:
            source_size = capture_geometry.get("source_size_px")
            output_size = capture_geometry.get("output_size_px")
            scales = capture_geometry.get("resize_scale_xy")
            if (not isinstance(source_size, list) or len(source_size) != 2 or
                    not isinstance(output_size, list) or output_size != [width, height] or
                    not isinstance(scales, list) or len(scales) != 2 or
                    not all(isinstance(value, (int, float)) and math.isfinite(value)
                            for value in source_size + scales) or
                    min(source_size) <= 0 or abs(scales[0] - scales[1]) > 1e-6):
                item["reason"] = "screen capture used an invalid or non-uniform image mapping"
                continue
        camera = camera_from_record(record)
        runway_corners = scenarios[sid]["corners"]
        geometry_kind = "scenario_raw"
        if facilities is not None:
            airport_facility = facilities.get(scenarios[sid]["icao"])
            if not airport_facility:
                item["reason"] = "airport missing from Facilities API geometry"
                continue
            try:
                runway_corners = corners_from_facility(
                    airport_facility, scenarios[sid]["runway"])
            except ValueError as error:
                item["reason"] = str(error)
                continue
            geometry_kind = "msfs_simconnect_facilities_api"
        elif geometry is not None:
            airport_geometry = geometry.get(scenarios[sid]["icao"])
            if not airport_geometry or airport_geometry["runway"] != scenarios[sid]["runway"]:
                item["reason"] = "corrected runway geometry missing or runway mismatch"
                continue
            runway_corners = airport_geometry["corners"]
            geometry_kind = "manual_reference_fit_world_corners"
        projection = project_runway(runway_corners, camera, width, height, args.fov_axis)
        if pixel_models is not None and all(x is not None and y is not None
                                            for x, y, _ in projection):
            if pixel_payload.get("image_size") != [width, height]:
                item["reason"] = "pixel calibration uses a different image size"
                continue
            if scenarios[sid]["icao"] not in pixel_models:
                item["reason"] = "airport missing from pixel calibration"
                continue
            projection = apply_pixel_calibration(
                projection, pixel_models[scenarios[sid]["icao"]], width, height)
            geometry_kind += "+human_review_pixel_residual_v1"
        bbox = bbox_from(projection, width, height)
        item.update(image_file=row["image_file"], telemetry_file=row["telemetry_file"],
                    image_size=[width, height], camera_world=camera,
                    corners_px=projection, bbox_px=bbox, bbox_xyxy_px=bbox,
                    fov_axis=args.fov_axis, geometry_source=geometry_kind,
                    capture_geometry=(capture_geometry or "not recorded by legacy Camera API pilot"),
                    within_reference_distance_range=True,
                    reference_distance_range_m=None)
        if args.overlay:
            draw_overlay(image_path, projection, bbox,
                         output / "label_preview" / f"{sid}.png",
                         notice="PILOT DRAFT - compare with manual runway corners")
        if not all(visible for _, _, visible in projection):
            item["reason"] = "runway is not fully inside the image"
            continue
        if bbox is None or min(bbox[2] - bbox[0], bbox[3] - bbox[1]) < args.min_box_px:
            item["reason"] = "runway box is below minimum pixel size"
            continue
        x0, y0, x1, y1 = bbox
        box = f"0 {(x0+x1)/2/width:.6f} {(y0+y1)/2/height:.6f} {(x1-x0)/width:.6f} {(y1-y0)/height:.6f}"
        (output / "labels" / f"{sid}.txt").write_text(box + "\n", encoding="utf-8")
        polygon = "0 " + " ".join(f"{x/width:.6f} {y/height:.6f}" for x, y, _ in projection)
        (output / "labels_seg" / f"{sid}.txt").write_text(polygon + "\n", encoding="utf-8")
        keypoints = " ".join(f"{x/width:.6f} {y/height:.6f} 2" for x, y, _ in projection)
        (output / "labels_pose" / f"{sid}.txt").write_text(box + " " + keypoints + "\n", encoding="utf-8")
        pixels = {
            "scenario_id": sid,
            "corner_order": report["corner_order"],
            "corners_px": [[x, y] for x, y, _ in projection],
            "bbox_xyxy_px": list(bbox),
            "source": ("MSFS 2024 Camera API pose + explicit FOV axis + "
                       f"{geometry_kind}; see label_report validation status"),
        }
        (output / "labels_px" / f"{sid}.json").write_text(
            json.dumps(pixels, indent=2), encoding="utf-8")
        item["status"] = "candidate"

    output.mkdir(exist_ok=True)
    (output / "label_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    candidates = sum(item["status"] == "candidate" for item in report["images"])
    print(f"PILOT DRAFT: {candidates} candidates, {len(rows)-candidates} excluded.")
    print(f"Review overlays and measure errors before any bulk capture: {output}")


if __name__ == "__main__":
    main()
