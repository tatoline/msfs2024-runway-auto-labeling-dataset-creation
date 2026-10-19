"""Capture scenarios with the MSFS 2024 native add-on Camera API.

Every saved image is bound to CameraGet telemetry
before and after the screen grab. A camera ownership, FOV, reference-frame or
drift failure aborts the run and the affected image is deleted.
"""

import argparse
import csv
import hashlib
import json
import math
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
import time

from SimConnect import SimConnect, AircraftRequests, AircraftEvents
from SimConnect.RequestList import Request

import capture_support as legacy
from camera_api import (CameraApiError, NativeCameraClient, make_frame_record,
                        normalize_facility_response, normalize_snapshot, write_json_atomic)


HERE = Path(__file__).resolve().parent
DEFAULT_HELPER = HERE.parent / "camera_bridge" / "bin" / "Release" / "camera_bridge.exe"
FIELDS = legacy.MANIFEST_FIELDS + [
    "telemetry_file", "image_sha256", "camera_fov_deg", "label_status",
    "weather_quality_status"
]


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--helper", default=str(DEFAULT_HELPER))
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--airport-settle", type=float, default=60.0)
    ap.add_argument("--time-settle", type=float, default=5.0)
    ap.add_argument("--camera-settle", type=float, default=0.25)
    ap.add_argument("--camera-transition-settle", type=float, default=2.5,
                    help="seconds to wait for the first Camera API view after an airport change")
    ap.add_argument("--order", choices=["airport-distance", "airport-time-distance"],
                    default="airport-time-distance")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--format", choices=["png", "jpg"], default="png")
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--png-compress-level", type=int, choices=range(10), default=1)
    ap.add_argument("--start-delay", type=float, default=10.0)
    ap.add_argument("--pitch", type=float, default=0.0, help="aircraft pitch")
    ap.add_argument("--camera-pitch", type=float, default=0.0,
                    help="absolute add-on camera pitch in world coordinates")
    ap.add_argument("--camera-bank", type=float, default=0.0)
    ap.add_argument("--camera-yaw-offset", type=float, default=0.0,
                    help="added to each scenario's true heading")
    ap.add_argument("--fov", type=float, default=28.555,
                    help="Camera API vertical FOV in degrees (28.555 matches legacy zoom 82 at 16:9)")
    ap.add_argument("--camera-x", type=float, default=0.0,
                    help="eyepoint-relative Camera API x offset in metres")
    ap.add_argument("--camera-y", type=float, default=0.0,
                    help="eyepoint-relative Camera API y offset in metres")
    ap.add_argument("--camera-z", type=float, default=16.0,
                    help="eyepoint-relative Camera API z offset in metres")
    ap.add_argument("--camera-timeout", type=float, default=15.0)
    ap.add_argument("--camera-motion-retries", type=int, default=3,
                    help="number of automatic re-aim/re-capture attempts after camera drift")
    ap.add_argument("--camera-retry-settle", type=float, default=1.0,
                    help="seconds to hold the aircraft before retrying a moved camera frame")
    ap.add_argument("--precip-rate-min", type=float, default=None,
                    help="abort when measured local precipitation is below this mm/h")
    ap.add_argument("--precip-rate-max", type=float, default=None,
                    help="abort when measured local precipitation is above this mm/h")
    ap.add_argument("--reject-in-cloud", action="store_true",
                    help="abort before capture when MSFS reports the aircraft inside cloud")
    ap.add_argument("--weather-quality-action", choices=("abort", "flag"), default="abort",
                    help="abort on rate/cloud quality failures, or save and flag the frame")
    ap.add_argument("--max-consecutive-under-rate-frames", type=int, default=0,
                    help="abort after this many consecutive frames below precip-rate-min; "
                         "0 disables the sustained under-rate guard")
    ap.add_argument("--aircraft", default="Cessna Citation CJ4")
    ap.add_argument("--notes", default="")
    return ap


def validate_args(ap, args):
    waits = (args.settle, args.airport_settle, args.time_settle,
             args.camera_settle, args.camera_transition_settle, args.camera_retry_settle,
             args.start_delay, args.camera_timeout)
    if min(waits) < 0 or args.camera_timeout == 0:
        ap.error("wait times must be nonnegative and camera-timeout must be positive")
    if args.start < 0 or (args.end is not None and args.end <= args.start):
        ap.error("require 0 <= start < end")
    if args.width <= 0 or args.height <= 0 or not 1 < args.fov < 160:
        ap.error("invalid dimensions or FOV")
    bounds = (args.precip_rate_min, args.precip_rate_max)
    if any(value is not None and value < 0 for value in bounds):
        ap.error("precipitation-rate bounds must be nonnegative")
    if all(value is not None for value in bounds) and bounds[0] > bounds[1]:
        ap.error("precip-rate-min must not exceed precip-rate-max")
    if args.max_consecutive_under_rate_frames < 0:
        ap.error("max-consecutive-under-rate-frames must be nonnegative")
    if args.camera_motion_retries < 0:
        ap.error("camera-motion-retries must be nonnegative")
    if args.max_consecutive_under_rate_frames and args.precip_rate_min is None:
        ap.error("max-consecutive-under-rate-frames requires precip-rate-min")
    if not Path(args.helper).is_file():
        ap.error("native Camera API helper is missing; install the MSFS 2024 SDK and run "
                 "camera_bridge\\build.ps1")


def target_from_view(latitude_deg, longitude_deg, altitude_m, heading_deg,
                     pitch_deg, distance_m=10000.0):
    heading = math.radians(heading_deg)
    target_lat = latitude_deg + math.cos(heading) * distance_m / 111132.0
    target_lon = longitude_deg + math.sin(heading) * distance_m / (
        111320.0 * math.cos(math.radians(latitude_deg)))
    target_alt_m = altitude_m + math.tan(math.radians(pitch_deg)) * distance_m
    return target_lat, target_lon, target_alt_m


def verify_precipitation(requests, expected_weather, action="abort"):
    """Read MSFS precipitation state and either reject or flag a mismatch."""
    if action not in {"abort", "flag"}:
        raise ValueError(f"unknown weather-quality action: {action}")
    raw_state = requests.get("AMBIENT_PRECIP_STATE")
    if raw_state is None:
        issue = "MSFS did not return AMBIENT PRECIP STATE"
        if action == "abort":
            raise RuntimeError(issue)
        return None, [issue]
    state = int(round(float(raw_state)))
    raining = bool(state & 4)
    snowing = bool(state & 8)
    expects_rain = expected_weather in {"Rain", "Heavy Rain"}
    valid = (raining and not snowing) if expects_rain else not (raining or snowing)
    issues = []
    if not valid:
        expected = "Rain (state mask includes 4)" if expects_rain else "no precipitation"
        issue = (
            f"weather verification failed for {expected_weather}: "
            f"MSFS precipitation state={state}; expected {expected}"
        )
        if action == "abort":
            raise RuntimeError(issue)
        issues.append(issue)
    return state, issues


def verify_weather_render_state(precip_rate, ambient_in_cloud, *,
                                minimum=None, maximum=None, reject_in_cloud=False,
                                action="abort"):
    """Validate weather quality and either reject or flag unsuitable states."""
    if action not in {"abort", "flag"}:
        raise ValueError(f"unknown weather-quality action: {action}")
    issues = []
    if precip_rate is None:
        if minimum is not None or maximum is not None:
            issues.append("MSFS did not return AMBIENT PRECIP RATE")
        rate = None
    else:
        rate = float(precip_rate)
        if minimum is not None and rate < minimum:
            issues.append(
                f"local precipitation rate {rate:.2f} mm/h is below required {minimum:.2f}"
            )
        if maximum is not None and rate > maximum:
            issues.append(
                f"local precipitation rate {rate:.2f} mm/h exceeds visibility-safe {maximum:.2f}"
            )
    in_cloud = None if ambient_in_cloud is None else bool(round(float(ambient_in_cloud)))
    if reject_in_cloud and in_cloud is None:
        issues.append("MSFS did not return AMBIENT IN CLOUD")
    if reject_in_cloud and in_cloud:
        issues.append("aircraft is inside cloud; runway visibility requires review")
    if issues and action == "abort":
        raise RuntimeError("; ".join(issues))
    return rate, in_cloud, issues


def advance_under_rate_streak(rate, minimum, current, maximum):
    """Allow isolated rate failures but stop a persistently mislabeled run."""
    if minimum is None or maximum <= 0:
        return 0
    streak = current + 1 if rate is None or rate < minimum else 0
    if streak >= maximum:
        shown = "unavailable" if rate is None else f"{rate:.2f} mm/h"
        raise RuntimeError(
            f"sustained precipitation-rate failure: {streak} consecutive frames below "
            f"{minimum:.2f} mm/h (latest {shown}). Reapply the rain preset "
            "before starting a fresh output run."
        )
    return streak


def is_camera_motion_error(error):
    """Only motion validation failures are safe to re-aim and recapture."""
    return (isinstance(error, CameraApiError)
            and str(error).startswith("Camera moved during screenshot"))


def main():
    ap = parser()
    args = ap.parse_args()
    validate_args(ap, args)
    output = Path(args.output_dir).resolve()
    images = output / "images"
    telemetry_dir = output / "telemetry"
    manifest_path = output / "manifest.csv"
    if any((output / name).exists() for name in
           ("manifest.csv", "capture_run.json", "captured_batch.json")):
        ap.error("output already contains a capture run; choose a new --output-dir")

    batch = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    batch = legacy.order_scenarios(batch, args.order)[args.start:args.end]
    if not batch:
        ap.error("no scenarios selected")
    weather = {s["weather"] for s in batch}
    if len(weather) != 1:
        ap.error("capture one weather preset per run; weather is selected manually")

    images.mkdir(parents=True, exist_ok=False)
    telemetry_dir.mkdir()
    run_path = output / "capture_run.json"
    run = {
        "pipeline": "MSFS 2024 native Camera API",
        "arguments": vars(args),
        "scenario_ids": [s["sid"] for s in batch],
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "status": "CAPTURING_INCOMPLETE",
        "label_status": "per-frame telemetry captured; pilot validation required",
    }
    write_json_atomic(run_path, run)
    write_json_atomic(output / "captured_batch.json", batch)

    print(f"Camera API pilot: {len(batch)} images, {args.width}x{args.height}, "
          f"FOV {args.fov:.3f} deg.")
    print(f"Select weather '{next(iter(weather))}'. Keep MSFS full-screen and close all UI panels.")
    print(f"Switch to MSFS now. Starting in {args.start_delay:g}s.", flush=True)
    time.sleep(args.start_delay)

    completed = 0
    try:
        with ExitStack() as cleanup:
            sim = SimConnect()
            cleanup.callback(sim.exit)
            requests = AircraftRequests(sim, _time=200)
            precip_rate_request = Request(
                (b"AMBIENT PRECIP RATE", b"Millimeters of water"), sim, _time=0)
            events = AircraftEvents(sim)
            cleanup.callback(legacy.freeze_aircraft, events, False)
            legacy.freeze_aircraft(events, True)
            camera = cleanup.enter_context(NativeCameraClient(
                args.helper, timeout=args.camera_timeout))

            # Read the same runway records the running simulator uses.  Saving
            # this beside the images makes future labeling independent of an
            # external or stale runway database.
            facility_payload = {
                "schema": "msfs2024-runway-facilities/v1",
                "captured_utc": datetime.now(timezone.utc).isoformat(),
                "source": "MSFS 2024 SimConnect Facilities API",
                "airports": {},
            }
            for icao in sorted({scenario["icao"] for scenario in batch}):
                response = camera.facility(icao)
                facility_payload["airports"][icao] = {
                    "runways": normalize_facility_response(response),
                }
                print(f"Facilities API: {icao} returned {len(response['runways'])} runway records")
            facility_path = output / "runway_facilities.json"
            write_json_atomic(facility_path, facility_payload)
            run["runway_facilities_file"] = facility_path.name
            run["runway_facilities_sha256"] = hashlib.sha256(facility_path.read_bytes()).hexdigest()
            write_json_atomic(run_path, run)

            manifest = cleanup.enter_context(manifest_path.open(
                "x", newline="", encoding="utf-8"))
            writer = csv.DictWriter(manifest, fieldnames=FIELDS)
            writer.writeheader()
            previous_airport = None
            previous_time = None
            consecutive_under_rate_frames = 0
            for index, scenario in enumerate(batch, 1):
                sid = scenario["sid"]
                if Path(sid).name != sid or "/" in sid or "\\" in sid:
                    raise ValueError(f"Unsafe scenario ID: {sid}")
                image_name = f"{sid}.{args.format}"
                image_path = images / image_name
                print(f"[{index:>3}/{len(batch)}] {sid} {scenario['icao']} "
                      f"RWY {scenario['runway']} {scenario['gd']:.0f} m", flush=True)

                legacy.freeze_aircraft(events, True)
                legacy.set_time(events, scenario["hour"], scenario["minute"])
                current_time = (scenario["hour"], scenario["minute"])
                airport_changed = scenario["icao"] != previous_airport
                time_changed = current_time != previous_time
                heading = (scenario["heading_true"] + args.camera_yaw_offset) % 360.0
                rough_target = target_from_view(
                    scenario["lat"], scenario["lon"], scenario["alt_m"],
                    heading, args.camera_pitch)
                if airport_changed:
                    legacy.set_pose(requests, scenario, args.pitch)
                    # Give the teleported aircraft pose a moment to propagate, then
                    # point the add-on camera toward the runway before the long
                    # scenery wait. Otherwise the camera keeps targeting the prior
                    # airport and MSFS spends the wait rendering behind the aircraft.
                    legacy.hold_pose(requests, scenario, args.camera_settle, args.pitch)
                    camera.set_targeted(*rough_target, args.fov,
                                        args.camera_x, args.camera_y, args.camera_z)
                    time.sleep(args.camera_transition_settle)
                    print(f"      forward-facing scenery wait {args.airport_settle:g}s",
                          flush=True)
                    time.sleep(args.airport_settle)
                    previous_airport = scenario["icao"]
                    extra_wait = 0.0
                else:
                    extra_wait = args.time_settle if current_time != previous_time else 0.0
                legacy.hold_pose(requests, scenario, args.settle + extra_wait, args.pitch)
                previous_time = current_time

                precip_state, precip_state_issues = verify_precipitation(
                    requests, scenario["weather"], args.weather_quality_action)
                precip_rate = precip_rate_request.get()
                ambient_visibility_m = requests.get("AMBIENT_VISIBILITY")
                ambient_wind_mps = requests.get("AMBIENT_WIND_VELOCITY")
                ambient_in_cloud = requests.get("AMBIENT_IN_CLOUD")
                precip_rate, in_cloud, weather_quality_issues = verify_weather_render_state(
                    precip_rate, ambient_in_cloud,
                    minimum=args.precip_rate_min, maximum=args.precip_rate_max,
                    reject_in_cloud=args.reject_in_cloud,
                    action=args.weather_quality_action)
                weather_quality_issues = precip_state_issues + weather_quality_issues
                consecutive_under_rate_frames = advance_under_rate_streak(
                    precip_rate, args.precip_rate_min,
                    consecutive_under_rate_frames,
                    args.max_consecutive_under_rate_frames)
                if weather_quality_issues:
                    print(f"      WEATHER QUALITY FLAG: {'; '.join(weather_quality_issues)}", flush=True)
                if airport_changed or time_changed:
                    print(
                        f"      weather verified: state {precip_state}, "
                        f"rate {precip_rate}, visibility {ambient_visibility_m} m, "
                        f"wind {ambient_wind_mps} m/s, in-cloud {ambient_in_cloud}"
                    )

                # Targeted mode avoids the beta API's ambiguous PBH conversion.
                # First use the aircraft pose to establish the attached camera,
                # then read its exact world position and calculate a precise
                # 10 km look-at target for the requested heading/pitch.
                camera.set_targeted(*rough_target, args.fov,
                                    args.camera_x, args.camera_y, args.camera_z)
                time.sleep(args.camera_settle)
                rough = normalize_snapshot(camera.snapshot(), args.fov)
                world = rough["camera_world"]
                precise_target_m = target_from_view(
                    world["latitude_deg"], world["longitude_deg"], world["altitude_m"],
                    heading, args.camera_pitch)
                target_alt_raw = (precise_target_m[2] if world["altitude_raw_unit"] == "metres"
                                  else precise_target_m[2] / 0.3048)
                for capture_attempt in range(1, args.camera_motion_retries + 2):
                    if capture_attempt > 1:
                        legacy.hold_pose(
                            requests, scenario, args.camera_retry_settle, args.pitch)
                    camera.set_targeted(
                        precise_target_m[0], precise_target_m[1], target_alt_raw,
                        args.fov, args.camera_x, args.camera_y, args.camera_z)
                    time.sleep(args.camera_transition_settle if
                               airport_changed and capture_attempt == 1
                               else args.camera_settle)
                    before = camera.snapshot()
                    try:
                        capture_geometry = {}
                        width, height = legacy.grab_screenshot(
                            image_path, args.width, args.height, args.format,
                            args.quality, args.png_compress_level,
                            capture_meta=capture_geometry)
                        after = camera.snapshot()
                        telemetry_file = f"telemetry/{sid}.json"
                        record = make_frame_record(
                            sid, f"images/{image_name}", image_path, before, after,
                            args.fov, (width, height),
                            commanded_target_world=precise_target_m)
                    except BaseException as error:
                        image_path.unlink(missing_ok=True)
                        if (is_camera_motion_error(error)
                                and capture_attempt <= args.camera_motion_retries):
                            print(
                                f"      CAMERA MOTION RETRY {capture_attempt}/"
                                f"{args.camera_motion_retries}: {error}",
                                flush=True)
                            continue
                        raise
                    break
                record["capture_geometry"] = capture_geometry
                record["camera_capture_attempts"] = capture_attempt
                record["ambient_precip_state"] = precip_state
                record["ambient_precip_verified"] = not precip_state_issues
                record["ambient_precip_rate_mm_water"] = precip_rate
                record["ambient_visibility_m"] = ambient_visibility_m
                record["ambient_wind_velocity_mps"] = ambient_wind_mps
                record["ambient_in_cloud"] = ambient_in_cloud
                record["ambient_in_cloud_bool"] = in_cloud
                record["weather_quality_status"] = (
                    "FLAGGED_FOR_VISIBILITY_REVIEW" if weather_quality_issues else "PASS"
                )
                record["weather_quality_issues"] = weather_quality_issues
                write_json_atomic(output / telemetry_file, record)

                writer.writerow({
                    "scenario_id": sid, "image_file": f"images/{image_name}",
                    "icao": scenario["icao"], "airport": scenario["airport"],
                    "runway": scenario["runway"], "lat": scenario["lat"],
                    "lon": scenario["lon"], "alt_m": scenario["alt_m"],
                    "alt_ft": scenario["alt_ft"],
                    "heading_true_deg": scenario["heading_true"],
                    "glide_angle_deg": scenario["glide_angle_deg"],
                    "ground_dist_m": scenario["gd"], "alt_diff_m": scenario["ad"],
                    "runway_length_m": scenario["runway_length_m"],
                    "runway_width_m": scenario["runway_width_m"],
                    "weather": scenario["weather"], "hour": scenario["hour"],
                    "minute": scenario["minute"], "target": "runway",
                    "simulator": "MSFS 2024", "aircraft": args.aircraft,
                    "camera_view": "native-addon-camera", "zoom": "",
                    "image_w": width, "image_h": height,
                    "capture_time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "notes": args.notes, "telemetry_file": telemetry_file,
                    "image_sha256": record["image_sha256"],
                    "camera_fov_deg": args.fov,
                    "label_status": "telemetry-bound; pilot validation required",
                    "weather_quality_status": (
                        "FLAGGED_FOR_VISIBILITY_REVIEW" if weather_quality_issues else "PASS"
                    ),
                })
                manifest.flush()
                completed += 1
    except BaseException as error:
        run.update(status="ABORTED_INCOMPLETE", completed_images=completed,
                   error=f"{type(error).__name__}: {error}",
                   finished_utc=datetime.now(timezone.utc).isoformat())
        write_json_atomic(run_path, run)
        raise
    else:
        run.update(status="CAPTURE_COMPLETE_AWAITING_LABEL_VALIDATION",
                   completed_images=completed,
                   finished_utc=datetime.now(timezone.utc).isoformat())
        write_json_atomic(run_path, run)
        print(f"Done: {completed} telemetry-bound images in {output}")
        print("Next create projected labels, then compare a pilot with independently measured runway corners.")


if __name__ == "__main__":
    try:
        main()
    except CameraApiError as error:
        raise SystemExit(f"Camera API capture stopped safely: {error}")
