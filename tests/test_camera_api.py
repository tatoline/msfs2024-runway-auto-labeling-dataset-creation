"""Offline safety and geometry checks for the native Camera API pipeline."""

import json
import math
from pathlib import Path
import tempfile
import unittest

from camera_api import (CameraApiError, make_frame_record, normalize_facility_response,
                        normalize_snapshot)
from capture_scenarios_camera_api import (parser, target_from_view,
                                          verify_weather_render_state,
                                          advance_under_rate_streak,
                                          verify_precipitation,
                                          is_camera_motion_error)
from label_camera_api_capture import focal_length_px, project_runway
from facility_geometry import corners_from_facility, geodesic_destination


def snapshot(*, latitude=48.0, longitude=11.0, camera_altitude=1640.42,
             aircraft_altitude_ft=1640.42, pitch=0.0, bank=0.0, heading=0.0,
             fov_deg=48.684, acquired=1, game_controlled=False):
    return {
        "event": "snapshot", "protocol": 1, "utc": "2026-09-02T12:00:00.000Z",
        "steady_ns": 1,
        "status": {"acquired_state": acquired, "game_controlled": game_controlled},
        "camera": {
            "x": latitude, "y": longitude, "z": camera_altitude,
            "position_referential": 2, "pitch_deg": pitch, "bank_deg": bank,
            "heading_deg": heading, "rotation_referential": 2,
            "fov_rad": math.radians(fov_deg),
        },
        "aircraft": {
            "latitude_deg": latitude, "longitude_deg": longitude,
            "altitude_ft": aircraft_altitude_ft, "pitch_deg": 0.0,
            "bank_deg": 0.0, "heading_true_deg": heading,
        },
    }


class CameraApiTests(unittest.TestCase):
    def test_precipitation_state_can_be_flagged_for_unattended_capture(self):
        class Requests:
            def __init__(self, value):
                self.value = value

            def get(self, _name):
                return self.value

        state, issues = verify_precipitation(Requests(0), "Heavy Rain", action="flag")
        self.assertEqual(state, 0)
        self.assertEqual(len(issues), 1)
        state, issues = verify_precipitation(Requests(4), "Heavy Rain", action="flag")
        self.assertEqual(state, 4)
        self.assertEqual(issues, [])

    def test_world_altitude_unit_is_detected_and_audited(self):
        feet = normalize_snapshot(snapshot(), 48.684)
        self.assertEqual(feet["camera_world"]["altitude_raw_unit"], "feet")
        self.assertAlmostEqual(feet["camera_world"]["altitude_m"], 500.0, places=2)
        metres = normalize_snapshot(snapshot(camera_altitude=500.0), 48.684)
        self.assertEqual(metres["camera_world"]["altitude_raw_unit"], "metres")

    def test_capture_rejects_lost_control_and_fov_drift(self):
        with self.assertRaisesRegex(CameraApiError, "exclusively controlled"):
            normalize_snapshot(snapshot(game_controlled=True), 48.684)
        with self.assertRaisesRegex(CameraApiError, "FOV drifted"):
            normalize_snapshot(snapshot(fov_deg=50.0), 48.684)

    def test_world_target_defines_orientation_despite_ambiguous_raw_pbh(self):
        value = snapshot(pitch=0.03, bank=-100.56, heading=359.97)
        value["camera"].update(target_x=48.09, target_y=11.0, target_z=1640.42)
        camera = normalize_snapshot(value, 48.684)["camera_world"]
        self.assertEqual(camera["orientation_source"], "world_target")
        self.assertAlmostEqual(camera["heading_true_deg"], 0.0, places=4)
        self.assertAlmostEqual(camera["pitch_deg"], 0.0, places=4)
        target = target_from_view(48.0, 11.0, 500.0, 90.0, 0.0)
        self.assertGreater(target[1], 11.0)
        self.assertAlmostEqual(target[0], 48.0, places=5)

    def test_frame_record_binds_hash_and_rejects_motion(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "frame.png"
            image.write_bytes(b"test image bytes")
            record = make_frame_record("one", "images/one.png", image,
                                       snapshot(), snapshot(), 48.684, (1920, 1080))
            self.assertEqual(record["scenario_id"], "one")
            self.assertEqual(len(record["image_sha256"]), 64)
            moved = snapshot(latitude=48.00001)
            with self.assertRaisesRegex(CameraApiError, "moved during screenshot"):
                make_frame_record("one", "images/one.png", image,
                                  snapshot(), moved, 48.684, (1920, 1080))

    def test_wgs84_projection_has_expected_image_direction(self):
        camera = {"lat": 48.0, "lon": 11.0, "alt_m": 500.0,
                  "pitch": 0.0, "bank": 0.0, "heading": 0.0, "fov": 60.0}
        # A runway north of a level camera and below it must appear below centre;
        # its east edge must appear on the right.
        corners = [
            [48.0090, 11.0002, 400.0], [48.0090, 10.9998, 400.0],
            [48.0180, 10.9998, 400.0], [48.0180, 11.0002, 400.0],
        ]
        projected = project_runway(corners, camera, 1920, 1080)
        self.assertTrue(all(value[0] is not None for value in projected))
        self.assertGreater(projected[0][0], projected[1][0])
        self.assertTrue(all(value[1] > 540 for value in projected))

    def test_camera_api_fov_is_vertical_by_default(self):
        vertical = focal_length_px(48.684, 1920, 1080)
        horizontal = focal_length_px(48.684, 1920, 1080, "horizontal")
        self.assertAlmostEqual(horizontal / vertical, 1920 / 1080)
        camera = {"lat": 48.0, "lon": 11.0, "alt_m": 500.0,
                  "pitch": 0.0, "bank": 0.0, "heading": 0.0, "fov": 48.684}
        corners = [
            [48.0090, 11.0002, 400.0], [48.0090, 10.9998, 400.0],
            [48.0180, 10.9998, 400.0], [48.0180, 11.0002, 400.0],
        ]
        default_projection = project_runway(corners, camera, 1920, 1080)
        explicit_vertical = project_runway(corners, camera, 1920, 1080, "vertical")
        self.assertEqual(default_projection, explicit_vertical)

    def test_capture_default_matches_legacy_zoom82_at_16_by_9(self):
        args = parser().parse_args(["--batch", "pilot.json", "--output-dir", "out"])
        expected_vertical = math.degrees(2.0 * math.atan(
            (1080.0 / 1920.0) * math.tan(math.radians(48.684) / 2.0)))
        self.assertAlmostEqual(args.fov, expected_vertical, places=3)
        self.assertEqual(args.camera_motion_retries, 3)
        self.assertEqual(args.camera_transition_settle, 2.5)

    def test_only_camera_motion_validation_error_is_retryable(self):
        self.assertTrue(is_camera_motion_error(CameraApiError(
            "Camera moved during screenshot (position 0.000 m, angle 1.003 deg)")))
        self.assertFalse(is_camera_motion_error(CameraApiError(
            "Add-on camera is not exclusively controlled")))
        self.assertFalse(is_camera_motion_error(RuntimeError("Camera moved during screenshot")))

    def test_weather_render_guard_rejects_opaque_rate_and_cloud(self):
        rate, in_cloud, issues = verify_weather_render_state(
            4.4, 0, maximum=12.0, reject_in_cloud=True)
        self.assertEqual(rate, 4.4)
        self.assertFalse(in_cloud)
        self.assertEqual(issues, [])
        with self.assertRaisesRegex(RuntimeError, "exceeds visibility-safe"):
            verify_weather_render_state(30.0, 0, maximum=12.0)
        with self.assertRaisesRegex(RuntimeError, "inside cloud"):
            verify_weather_render_state(4.4, 1, reject_in_cloud=True)
        rate, in_cloud, issues = verify_weather_render_state(
            30.0, 1, maximum=12.0, reject_in_cloud=True, action="flag")
        self.assertEqual(rate, 30.0)
        self.assertTrue(in_cloud)
        self.assertEqual(len(issues), 2)

    def test_sustained_rate_guard_allows_isolated_under_rate_frame(self):
        streak = advance_under_rate_streak(4.0, 7.5, 0, 5)
        self.assertEqual(streak, 1)
        streak = advance_under_rate_streak(8.0, 7.5, streak, 5)
        self.assertEqual(streak, 0)
        for expected in range(1, 5):
            streak = advance_under_rate_streak(4.0, 7.5, streak, 5)
            self.assertEqual(streak, expected)
        with self.assertRaisesRegex(RuntimeError, "sustained precipitation-rate failure"):
            advance_under_rate_streak(4.0, 7.5, streak, 5)

    def test_facility_runway_is_oriented_for_selected_end(self):
        airport = {"runways": [{
            "latitude_deg": 48.0, "longitude_deg": 11.0, "altitude_m": 500.0,
            "heading_deg": 90.0, "length_m": 2000.0, "width_m": 40.0,
            "true_slope_deg": 0.0, "primary_number": 9, "primary_designator": 0,
            "secondary_number": 27, "secondary_designator": 0,
        }]}
        primary = corners_from_facility(airport, "9")
        secondary = corners_from_facility(airport, "27")
        # Runway 09 starts west of the centre and 27 starts east of it.
        self.assertLess(sum(p[1] for p in primary[:2]), 22.0)
        self.assertGreater(sum(p[1] for p in secondary[:2]), 22.0)
        # Reversing the selected end reverses corner order but not the footprint.
        self.assertAlmostEqual(primary[0][0], secondary[2][0], places=5)
        self.assertAlmostEqual(primary[0][1], secondary[2][1], places=5)

    def test_vincenty_destination_distance_scale(self):
        lat, lon = geodesic_destination(48.0, 11.0, 0.0, 1000.0)
        self.assertAlmostEqual((lat - 48.0) * 111190.0, 1000.0, delta=3.0)
        self.assertAlmostEqual(lon, 11.0, places=8)

    def test_facility_threshold_children_are_attached_in_definition_order(self):
        response = {
            "runways": [{"unique_id": 10, "primary_number": 9}],
            "pavements": [
                {"unique_id": 12, "parent_unique_id": 10, "length_m": 0,
                 "width_m": 40, "enabled": False},
                {"unique_id": 11, "parent_unique_id": 10, "length_m": 300,
                 "width_m": 40, "enabled": True},
            ],
        }
        runway = normalize_facility_response(response)[0]
        self.assertEqual(runway["primary_threshold"]["length_m"], 300)
        self.assertFalse(runway["secondary_threshold"]["enabled"])

    def test_displaced_landing_threshold_moves_near_end_inward(self):
        runway = {
            "latitude_deg": 48.0, "longitude_deg": 11.0, "altitude_m": 500.0,
            "heading_deg": 90.0, "length_m": 2000.0, "width_m": 40.0,
            "true_slope_deg": 0.0, "primary_number": 9, "primary_designator": 0,
            "secondary_number": 27, "secondary_designator": 0,
            "primary_threshold": {"length_m": 300.0, "width_m": 40.0, "enabled": True},
            "secondary_threshold": {"length_m": 0.0, "width_m": 0.0, "enabled": False},
        }
        without = dict(runway, primary_threshold={"length_m": 0.0, "enabled": False})
        base = corners_from_facility({"runways": [without]}, "9")
        shifted = corners_from_facility({"runways": [runway]}, "9")
        self.assertGreater(shifted[0][1], base[0][1])
        self.assertAlmostEqual(shifted[2][1], base[2][1], places=8)


if __name__ == "__main__":
    unittest.main()
