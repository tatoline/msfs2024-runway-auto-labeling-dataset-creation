"""Client and validation helpers for the MSFS 2024 native Camera API bridge."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid


SCHEMA = "msfs2024-camera-capture/v3"
SUPPORTED_SCHEMAS = {
    "msfs2024-camera-capture/v1",
    "msfs2024-camera-capture/v2",
    SCHEMA,
}
PROTOCOL = 1
WORLD_REFERENTIAL = 2

LIGHT_INTENSITY_NAMES = {
    0: "NONE",
    1: "LOW",
    2: "MEDIUM",
    3: "HIGH",
}
APPROACH_LIGHT_SYSTEM_NAMES = {
    0: "NONE",
    1: "ODALS",
    2: "MALSF",
    3: "MALSR",
    4: "SSALF",
    5: "SSALR",
    6: "ALSF1",
    7: "ALSF2",
    8: "RAIL",
    9: "CALVERT",
    10: "CALVERT2",
    11: "MALS",
    12: "SALS",
    13: "SALSF",
    14: "SSALS",
}


class CameraApiError(RuntimeError):
    """Raised when a frame cannot be safely paired with Camera API telemetry."""


def normalize_facility_response(response):
    """Attach ordered primary/secondary threshold records to each runway.

    SimConnect returns both requested PAVEMENT child records immediately after
    their parent RUNWAY, in facility-definition order. The native bridge keeps
    the unique parent IDs so this relationship is explicit and auditable.
    """
    pavements_by_parent = {}
    for pavement in response.get("pavements", []):
        pavements_by_parent.setdefault(pavement.get("parent_unique_id"), []).append(pavement)
    approaches_by_parent = {}
    for approach in response.get("approach_lights", []):
        approaches_by_parent.setdefault(approach.get("parent_unique_id"), []).append(approach)
    normalized = []
    for runway in response.get("runways", []):
        children = sorted(pavements_by_parent.get(runway.get("unique_id"), []),
                          key=lambda value: value.get("unique_id", -1))
        if len(children) != 2:
            raise CameraApiError(
                "Facilities API did not return primary and secondary threshold records")
        value = dict(runway)
        value["edge_lights_name"] = LIGHT_INTENSITY_NAMES.get(
            value.get("edge_lights"), "UNKNOWN")
        value["center_lights_name"] = LIGHT_INTENSITY_NAMES.get(
            value.get("center_lights"), "UNKNOWN")
        value["primary_threshold"] = {
            key: children[0][key] for key in ("length_m", "width_m", "enabled")}
        value["secondary_threshold"] = {
            key: children[1][key] for key in ("length_m", "width_m", "enabled")}
        approach_children = sorted(
            approaches_by_parent.get(runway.get("unique_id"), []),
            key=lambda child: child.get("unique_id", -1))
        if len(approach_children) == 2:
            fields = ("system", "strobe_count", "has_end_lights", "has_reil_lights",
                      "has_touchdown_lights", "on_ground", "enabled", "offset_m",
                      "spacing_m", "slope_deg")
            value["primary_approach_lights"] = {
                key: approach_children[0][key] for key in fields}
            value["secondary_approach_lights"] = {
                key: approach_children[1][key] for key in fields}
            value["primary_approach_lights"]["system_name"] = (
                APPROACH_LIGHT_SYSTEM_NAMES.get(
                    value["primary_approach_lights"]["system"], "UNKNOWN"))
            value["secondary_approach_lights"]["system_name"] = (
                APPROACH_LIGHT_SYSTEM_NAMES.get(
                    value["secondary_approach_lights"]["system"], "UNKNOWN"))
        normalized.append(value)
    if not normalized:
        raise CameraApiError("Facilities API returned no runways")
    return normalized


def _reader(stream, destination):
    try:
        for line in iter(stream.readline, ""):
            destination.put(line.rstrip("\r\n"))
    finally:
        destination.put(None)


class NativeCameraClient:
    """Persistent line-protocol client for camera_bridge.exe."""

    def __init__(self, executable, timeout=15.0, client_id="ODM Runway Capture"):
        self.executable = str(Path(executable).resolve())
        self.timeout = float(timeout)
        if not Path(self.executable).is_file():
            raise CameraApiError(
                f"Camera helper not found: {self.executable}. Install the MSFS 2024 "
                "SDK and build camera_bridge first."
            )
        self.process = subprocess.Popen(
            [self.executable, "--client-id", client_id],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._stdout = queue.Queue()
        self._stderr = queue.Queue()
        threading.Thread(target=_reader, args=(self.process.stdout, self._stdout), daemon=True).start()
        threading.Thread(target=_reader, args=(self.process.stderr, self._stderr), daemon=True).start()
        ready = self._receive("ready")
        if ready.get("protocol") != PROTOCOL or not ready.get("camera_acquired"):
            self.close()
            raise CameraApiError(f"Camera helper did not acquire the add-on camera: {ready}")

    def _diagnostics(self):
        lines = []
        while True:
            try:
                line = self._stderr.get_nowait()
            except queue.Empty:
                break
            if line:
                lines.append(line)
        return " | ".join(lines[-5:])

    def _receive(self, expected_event, token=None):
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = self._diagnostics()
                raise CameraApiError(
                    f"Timed out waiting for camera helper {expected_event}"
                    + (f": {detail}" if detail else "")
                )
            try:
                line = self._stdout.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                detail = self._diagnostics()
                code = self.process.poll()
                raise CameraApiError(
                    f"Camera helper closed unexpectedly (exit {code})"
                    + (f": {detail}" if detail else "")
                )
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CameraApiError(f"Invalid camera helper output: {line!r}") from exc
            if message.get("event") == "error":
                raise CameraApiError(message.get("message", "unknown native Camera API error"))
            if message.get("event") == expected_event and (token is None or message.get("token") == token):
                return message

    def _send(self, line):
        if self.process.poll() is not None or not self.process.stdin:
            raise CameraApiError("Camera helper is not running")
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def set_absolute(self, pitch_deg, bank_deg, heading_deg, fov_deg,
                     position_x=0.0, position_y=0.0, position_z=0.0):
        """Attach at an eyepoint offset and set rotation in the world frame."""
        values = (pitch_deg, bank_deg, heading_deg % 360.0, math.radians(fov_deg),
                  position_x, position_y, position_z)
        if not all(math.isfinite(float(v)) for v in values) or not 1 < fov_deg < 160:
            raise CameraApiError("Invalid camera pose/FOV")
        token = uuid.uuid4().hex
        self._send("SET " + token + " " + " ".join(f"{float(v):.12g}" for v in values))
        answer = self._receive("set", token)
        if not answer.get("ok"):
            raise CameraApiError(answer.get("message", "CameraSet failed"))
        return answer

    def set_targeted(self, target_latitude_deg, target_longitude_deg, target_altitude_raw,
                     fov_deg, position_x=0.0, position_y=0.0, position_z=0.0):
        """Attach at an eyepoint offset and look at a world-coordinate target."""
        values = (math.radians(fov_deg), position_x, position_y, position_z,
                  target_latitude_deg, target_longitude_deg, target_altitude_raw)
        if not all(math.isfinite(float(v)) for v in values) or not 1 < fov_deg < 160:
            raise CameraApiError("Invalid targeted camera pose/FOV")
        token = uuid.uuid4().hex
        self._send("SET_TARGET " + token + " " + " ".join(f"{float(v):.12g}" for v in values))
        answer = self._receive("set", token)
        if not answer.get("ok"):
            raise CameraApiError(answer.get("message", "targeted CameraSet failed"))
        return answer

    def snapshot(self):
        token = uuid.uuid4().hex
        self._send(f"SNAPSHOT {token}")
        return self._receive("snapshot", token)

    def facility(self, icao):
        if not isinstance(icao, str) or not icao.isascii() or not icao.isalnum() or len(icao) > 8:
            raise CameraApiError(f"Invalid facility ICAO: {icao!r}")
        token = uuid.uuid4().hex
        self._send(f"FACILITY {token} {icao.upper()}")
        answer = self._receive("facility", token)
        if answer.get("icao") != icao.upper() or not isinstance(answer.get("runways"), list):
            raise CameraApiError("Invalid facility response")
        return answer

    def close(self):
        process = getattr(self, "process", None)
        if not process or process.poll() is not None:
            return
        try:
            if process.stdin:
                process.stdin.write("QUIT\n")
                process.stdin.flush()
            process.wait(timeout=3)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=2)
            except Exception:
                process.kill()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CameraApiError(f"Telemetry has invalid {name}")
    return float(value)


def normalize_snapshot(snapshot, expected_fov_deg, fov_tolerance_deg=0.05):
    """Validate one native response and convert its world altitude to metres.

    The beta documentation does not state the world-camera altitude unit. We
    choose feet or metres by comparing it with the independently read aircraft
    altitude and record that decision in every frame.
    """
    if snapshot.get("event") != "snapshot" or snapshot.get("protocol") != PROTOCOL:
        raise CameraApiError("Wrong camera telemetry protocol")
    status = snapshot.get("status", {})
    if status.get("acquired_state") != 1 or status.get("game_controlled") is not False:
        raise CameraApiError(
            "Add-on camera is not exclusively controlled by this capture process "
            f"(state={status.get('acquired_state')}, game_controlled={status.get('game_controlled')})"
        )
    camera, aircraft = snapshot.get("camera", {}), snapshot.get("aircraft", {})
    if camera.get("position_referential") != WORLD_REFERENTIAL:
        raise CameraApiError("Camera position was not returned in the world reference frame")
    if camera.get("rotation_referential") != WORLD_REFERENTIAL:
        raise CameraApiError("Camera rotation is not in the world reference frame")

    x = _finite_number(camera.get("x"), "camera latitude")
    y = _finite_number(camera.get("y"), "camera longitude")
    z = _finite_number(camera.get("z"), "camera altitude")
    raw_pitch = _finite_number(camera.get("pitch_deg"), "camera pitch")
    raw_bank = _finite_number(camera.get("bank_deg"), "camera bank")
    raw_heading = _finite_number(camera.get("heading_deg"), "camera heading") % 360.0
    fov_rad = _finite_number(camera.get("fov_rad"), "camera FOV")
    fov_deg = math.degrees(fov_rad)
    if abs(fov_deg - expected_fov_deg) > fov_tolerance_deg:
        raise CameraApiError(
            f"Camera FOV drifted: expected {expected_fov_deg:.4f} deg, got {fov_deg:.4f} deg"
        )
    if not (-90 <= x <= 90 and -180 <= y <= 180 and 1 < fov_deg < 160):
        raise CameraApiError("Camera world position/FOV is implausible")

    aircraft_alt_ft = _finite_number(aircraft.get("altitude_ft"), "aircraft altitude")
    aircraft_alt_m = aircraft_alt_ft * 0.3048
    # At normal capture altitudes this comparison is unambiguous. Save both the
    # raw value and the selected interpretation so the beta API remains auditable.
    if abs(z - aircraft_alt_m) <= abs(z * 0.3048 - aircraft_alt_m):
        altitude_unit, altitude_m = "metres", z
    else:
        altitude_unit, altitude_m = "feet", z * 0.3048
    if abs(altitude_m - aircraft_alt_m) > 100.0:
        raise CameraApiError(
            "Camera world altitude is more than 100 m from the aircraft; "
            "the beta API altitude convention must be checked"
        )

    target_x = camera.get("target_x")
    target_y = camera.get("target_y")
    target_z = camera.get("target_z")
    raw_target = ({"latitude_deg": target_x, "longitude_deg": target_y,
                   "altitude_raw": target_z}
                  if all(value is not None for value in (target_x, target_y, target_z)) else None)
    # The current beta build returns 0/0/0 here even after accepting a targeted
    # CameraSet. Never interpret that sentinel as a real target.
    targeted = (raw_target is not None and -90 <= float(target_x) <= 90
                and -180 <= float(target_y) <= 180
                and not (float(target_x) == 0 and float(target_y) == 0 and float(target_z) == 0))
    if targeted:
        target_lat = _finite_number(target_x, "camera target latitude")
        target_lon = _finite_number(target_y, "camera target longitude")
        target_alt_raw = _finite_number(target_z, "camera target altitude")
        target_alt_m = target_alt_raw if altitude_unit == "metres" else target_alt_raw * 0.3048
        north = (target_lat - x) * 111132.0
        east = (target_lon - y) * 111320.0 * math.cos(math.radians(x))
        up = target_alt_m - altitude_m
        horizontal = math.hypot(east, north)
        if horizontal < 10.0:
            raise CameraApiError("Camera world target is too close to define a stable view")
        heading = math.degrees(math.atan2(east, north)) % 360.0
        pitch = math.degrees(math.atan2(up, horizontal))
        bank = 0.0
        target = {
            "latitude_deg": target_lat, "longitude_deg": target_lon,
            "altitude_raw": target_alt_raw, "altitude_m": target_alt_m,
        }
        orientation_source = "world_target"
    else:
        heading, pitch, bank = raw_heading, raw_pitch, raw_bank
        target = None
        orientation_source = "raw_pbh"

    return {
        "native_utc": snapshot.get("utc"),
        "native_steady_ns": snapshot.get("steady_ns"),
        "status": {"acquired_state": 1, "game_controlled": False},
        "camera_world": {
            "latitude_deg": x,
            "longitude_deg": y,
            "altitude_raw": z,
            "altitude_raw_unit": altitude_unit,
            "altitude_m": altitude_m,
            "pitch_deg": pitch,
            "bank_deg": bank,
            "heading_true_deg": heading,
            "raw_pbh_deg": {"pitch": raw_pitch, "bank": raw_bank, "heading": raw_heading},
            "raw_target_returned_by_api": raw_target,
            "target_world": target,
            "orientation_source": orientation_source,
            "fov_deg": fov_deg,
            "fov_axis": "vertical",
            # Retained for v1 readers; the 20-image pilot disproved the name.
            "horizontal_fov_deg_assumed": fov_deg,
            "fov_rad_raw": fov_rad,
        },
        "aircraft": {
            "latitude_deg": _finite_number(aircraft.get("latitude_deg"), "aircraft latitude"),
            "longitude_deg": _finite_number(aircraft.get("longitude_deg"), "aircraft longitude"),
            "altitude_ft": aircraft_alt_ft,
            "altitude_m": aircraft_alt_m,
            "pitch_deg": _finite_number(aircraft.get("pitch_deg"), "aircraft pitch"),
            "bank_deg": _finite_number(aircraft.get("bank_deg"), "aircraft bank"),
            "heading_true_deg": _finite_number(aircraft.get("heading_true_deg"), "aircraft heading") % 360.0,
        },
    }


def _angle_error(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def apply_commanded_target(snapshot, target_world):
    """Attach the exact commanded target when the beta API returns 0/0/0."""
    camera = snapshot["camera_world"]
    target_lat, target_lon, target_alt_m = target_world
    north = (target_lat - camera["latitude_deg"]) * 111132.0
    east = ((target_lon - camera["longitude_deg"]) * 111320.0
            * math.cos(math.radians(camera["latitude_deg"])))
    up = target_alt_m - camera["altitude_m"]
    horizontal = math.hypot(east, north)
    if horizontal < 10.0:
        raise CameraApiError("Commanded camera target is too close")
    camera.update(
        pitch_deg=math.degrees(math.atan2(up, horizontal)),
        bank_deg=0.0,
        heading_true_deg=math.degrees(math.atan2(east, north)) % 360.0,
        target_world={"latitude_deg": target_lat, "longitude_deg": target_lon,
                      "altitude_m": target_alt_m},
        orientation_source="commanded_world_target",
    )


def make_frame_record(scenario_id, image_file, image_path, before, after,
                      expected_fov_deg, image_size, max_position_drift_m=0.25,
                      max_angle_drift_deg=0.05, commanded_target_world=None):
    """Create an auditable record only when the camera stayed fixed."""
    first = normalize_snapshot(before, expected_fov_deg)
    second = normalize_snapshot(after, expected_fov_deg)
    if commanded_target_world is not None:
        apply_commanded_target(first, commanded_target_world)
        apply_commanded_target(second, commanded_target_world)
    a, b = first["camera_world"], second["camera_world"]
    lat_m = (b["latitude_deg"] - a["latitude_deg"]) * 111132.0
    lon_m = ((b["longitude_deg"] - a["longitude_deg"]) * 111320.0
             * math.cos(math.radians((a["latitude_deg"] + b["latitude_deg"]) / 2)))
    alt_m = b["altitude_m"] - a["altitude_m"]
    drift_m = math.sqrt(lat_m * lat_m + lon_m * lon_m + alt_m * alt_m)
    angle_drift = max(
        abs(b["pitch_deg"] - a["pitch_deg"]),
        abs(b["bank_deg"] - a["bank_deg"]),
        _angle_error(b["heading_true_deg"], a["heading_true_deg"]),
        abs(b["raw_pbh_deg"]["pitch"] - a["raw_pbh_deg"]["pitch"]),
        abs(b["raw_pbh_deg"]["bank"] - a["raw_pbh_deg"]["bank"]),
        _angle_error(b["raw_pbh_deg"]["heading"], a["raw_pbh_deg"]["heading"]),
    )
    if drift_m > max_position_drift_m or angle_drift > max_angle_drift_deg:
        raise CameraApiError(
            f"Camera moved during screenshot (position {drift_m:.3f} m, angle {angle_drift:.3f} deg)"
        )
    payload = Path(image_path).read_bytes()
    return {
        "schema": SCHEMA,
        "scenario_id": scenario_id,
        "image_file": image_file,
        "image_size": list(image_size),
        "image_sha256": hashlib.sha256(payload).hexdigest(),
        "camera_fov_convention": "vertical_validated_by_16_9_pilot",
        "commanded_target_world": ({"latitude_deg": commanded_target_world[0],
                                     "longitude_deg": commanded_target_world[1],
                                     "altitude_m": commanded_target_world[2]}
                                    if commanded_target_world is not None else None),
        "before": first,
        "after": second,
        "camera_drift_during_capture": {
            "position_m": drift_m,
            "max_angle_deg": angle_drift,
            "limits": {"position_m": max_position_drift_m, "angle_deg": max_angle_drift_deg},
        },
    }


def write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)
