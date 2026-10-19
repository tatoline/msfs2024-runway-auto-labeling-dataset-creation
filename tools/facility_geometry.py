"""Convert MSFS Facilities API runway records into WGS84 corner points."""

from __future__ import annotations

import math


WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_B = (1.0 - WGS84_F) * WGS84_A

DESIGNATORS = {0: "", 1: "L", 2: "R", 3: "C", 4: "W", 5: "A", 6: "B"}


def runway_end_name(number, designator):
    return f"{int(number)}{DESIGNATORS.get(int(designator), '?')}"


def geodesic_destination(lat_deg, lon_deg, bearing_deg, distance_m):
    """Vincenty's WGS84 direct solution for the short runway distances here."""
    if distance_m == 0:
        return float(lat_deg), float(lon_deg)
    alpha1 = math.radians(bearing_deg)
    phi1 = math.radians(lat_deg)
    tan_u1 = (1.0 - WGS84_F) * math.tan(phi1)
    cos_u1 = 1.0 / math.sqrt(1.0 + tan_u1 * tan_u1)
    sin_u1 = tan_u1 * cos_u1
    sigma1 = math.atan2(tan_u1, math.cos(alpha1))
    sin_alpha = cos_u1 * math.sin(alpha1)
    cos_sq_alpha = 1.0 - sin_alpha * sin_alpha
    u_sq = cos_sq_alpha * (WGS84_A * WGS84_A - WGS84_B * WGS84_B) / (WGS84_B * WGS84_B)
    coefficient_a = 1.0 + u_sq / 16384.0 * (
        4096.0 + u_sq * (-768.0 + u_sq * (320.0 - 175.0 * u_sq)))
    coefficient_b = u_sq / 1024.0 * (
        256.0 + u_sq * (-128.0 + u_sq * (74.0 - 47.0 * u_sq)))
    sigma = distance_m / (WGS84_B * coefficient_a)
    for _ in range(20):
        two_sigma_m = 2.0 * sigma1 + sigma
        sin_sigma, cos_sigma = math.sin(sigma), math.cos(sigma)
        cos_two_sigma_m = math.cos(two_sigma_m)
        delta_sigma = coefficient_b * sin_sigma * (
            cos_two_sigma_m + coefficient_b / 4.0 * (
                cos_sigma * (-1.0 + 2.0 * cos_two_sigma_m**2) -
                coefficient_b / 6.0 * cos_two_sigma_m *
                (-3.0 + 4.0 * sin_sigma**2) *
                (-3.0 + 4.0 * cos_two_sigma_m**2)))
        updated = distance_m / (WGS84_B * coefficient_a) + delta_sigma
        if abs(updated - sigma) < 1e-13:
            sigma = updated
            break
        sigma = updated
    sin_sigma, cos_sigma = math.sin(sigma), math.cos(sigma)
    numerator = sin_u1 * cos_sigma + cos_u1 * sin_sigma * math.cos(alpha1)
    denominator = (1.0 - WGS84_F) * math.sqrt(
        sin_alpha**2 + (sin_u1 * sin_sigma - cos_u1 * cos_sigma * math.cos(alpha1))**2)
    phi2 = math.atan2(numerator, denominator)
    lam = math.atan2(
        sin_sigma * math.sin(alpha1),
        cos_u1 * cos_sigma - sin_u1 * sin_sigma * math.cos(alpha1),
    )
    c = WGS84_F / 16.0 * cos_sq_alpha * (4.0 + WGS84_F * (4.0 - 3.0 * cos_sq_alpha))
    two_sigma_m = 2.0 * sigma1 + sigma
    longitude_correction = (1.0 - c) * WGS84_F * sin_alpha * (
        sigma + c * math.sin(sigma) * (
            math.cos(two_sigma_m) + c * math.cos(sigma) *
            (-1.0 + 2.0 * math.cos(two_sigma_m)**2)))
    lon2 = math.radians(lon_deg) + lam - longitude_correction
    return math.degrees(phi2), (math.degrees(lon2) + 540.0) % 360.0 - 180.0


def find_runway(facility_airport, runway_name):
    for runway in facility_airport.get("runways", []):
        primary = runway_end_name(runway["primary_number"], runway["primary_designator"])
        secondary = runway_end_name(runway["secondary_number"], runway["secondary_designator"])
        if primary == runway_name:
            return runway, "primary"
        if secondary == runway_name:
            return runway, "secondary"
    raise ValueError(f"runway {runway_name} is absent from Facilities API data")


def corners_from_facility(facility_airport, runway_name):
    """Return threshold-right, threshold-left, far-left, far-right corners."""
    runway, end = find_runway(facility_airport, runway_name)
    values = [runway[key] for key in (
        "latitude_deg", "longitude_deg", "altitude_m", "heading_deg",
        "length_m", "width_m", "true_slope_deg")]
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        raise ValueError("Facilities API runway contains invalid numeric data")
    lat, lon, altitude, heading, length, width, slope = map(float, values)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and
            100 <= length <= 10000 and 5 <= width <= 200 and abs(slope) < 10):
        raise ValueError("Facilities API runway dimensions or position are implausible")

    # The facility heading runs from the primary threshold to the secondary.
    primary_lat, primary_lon = geodesic_destination(lat, lon, heading + 180.0, length / 2.0)
    secondary_lat, secondary_lon = geodesic_destination(lat, lon, heading, length / 2.0)
    half_rise = math.tan(math.radians(slope)) * length / 2.0
    primary_alt, secondary_alt = altitude - half_rise, altitude + half_rise
    if end == "primary":
        near = (primary_lat, primary_lon, primary_alt)
        far = (secondary_lat, secondary_lon, secondary_alt)
        course = heading
        threshold = runway.get("primary_threshold", {})
    else:
        near = (secondary_lat, secondary_lon, secondary_alt)
        far = (primary_lat, primary_lon, primary_alt)
        course = (heading + 180.0) % 360.0
        threshold = runway.get("secondary_threshold", {})

    # Runway LENGTH includes displaced-threshold pavement. The annotation
    # policy starts at the landing threshold bar, so move only the selected
    # landing end inward. The far end remains the opposite physical runway end.
    threshold_offset = float(threshold.get("length_m", 0.0)) if threshold.get("enabled") else 0.0
    if not math.isfinite(threshold_offset) or not 0 <= threshold_offset < length:
        raise ValueError("Facilities API threshold offset is invalid")
    if threshold_offset:
        shifted_lat, shifted_lon = geodesic_destination(
            near[0], near[1], course, threshold_offset)
        direction = 1.0 if end == "primary" else -1.0
        shifted_alt = near[2] + direction * math.tan(math.radians(slope)) * threshold_offset
        near = (shifted_lat, shifted_lon, shifted_alt)

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
