"""Generate one additive enhanced-light package for the five dataset runways.

Geometry and approach-light metadata are read from a SimConnect Facilities API
snapshot.  The package is an explicitly synthetic visibility augmentation: it
does not replace the stock runway, ILS, PAPI, or navigation facilities.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from html import escape
import json
import math
from pathlib import Path
import sys

DATASET_ROOT = Path(__file__).resolve().parent.parent / "tools"
if str(DATASET_ROOT) not in sys.path:
    sys.path.insert(0, str(DATASET_ROOT))
from facility_geometry import geodesic_destination, runway_end_name


TARGETS = {
    "EDDM": {"runway": "26R", "name": "Munich", "region": "Europe", "country": "DE"},
    "EDDS": {"runway": "25", "name": "Stuttgart", "region": "Europe", "country": "DE"},
    "LFQQ": {"runway": "26", "name": "Lille", "region": "Europe", "country": "FR"},
    "LIRF": {"runway": "16L", "name": "Rome Fiumicino", "region": "Europe", "country": "IT"},
    "LSZH": {"runway": "14", "name": "Zurich", "region": "Europe", "country": "CH"},
}

VERSION = "1.6.0"
PROFILE = "unboosted-daylight-v2p10"
BOOST_DAYLIGHT = False
PRESETS = {
    # Keep the v2.9 candela/radius values so v2.10 isolates one variable:
    # MSFS's automatic daylight brightness boost.
    "RUNWAY_WHITE": ((255, 252, 238), 1100, 0.001),
    "RUNWAY_YELLOW": ((255, 205, 45), 1100, 0.002),
    "RUNWAY_RED": ((255, 38, 28), 1500, 0.002),
    "THRESHOLD_GREEN": ((25, 255, 105), 2100, 0.002),
    "APPROACH_WHITE": ((245, 250, 255), 2500, 0.001),
    "APPROACH_RED": ((255, 35, 28), 2200, 0.002),
    "TDZ_WHITE": ((255, 252, 238), 1200, 0.001),
}

# Fixed airport factors fitted from the manually scored v2.8 pilot. The target
# is a useful 5-10 rating while retaining visibility in rain and fog.
# The LightRow XML format has no camera-distance intensity curve; MSFS applies
# its own physical distance attenuation to these fixed candela values.
AIRPORT_INTENSITY_SCALE = {
    "EDDM": 0.42,
    "EDDS": 0.95,
    "LFQQ": 0.42,
    "LIRF": 0.50,
    "LSZH": 0.42,
}


def range_inclusive(start: float, stop: float, step: float) -> list[float]:
    if stop < start:
        return []
    count = int(math.floor((stop - start) / step + 1e-9))
    values = [start + i * step for i in range(count + 1)]
    if not values or stop - values[-1] > 1e-6:
        values.append(stop)
    return values


def find_target_runway(airport: dict, runway_name: str) -> tuple[dict, str]:
    for runway in airport.get("runways", []):
        primary = runway_end_name(runway["primary_number"], runway["primary_designator"])
        secondary = runway_end_name(runway["secondary_number"], runway["secondary_designator"])
        if primary == runway_name:
            return runway, "primary"
        if secondary == runway_name:
            return runway, "secondary"
    raise ValueError(f"runway {runway_name} is absent from facility data")


def selected_end_geometry(runway: dict, end: str) -> dict:
    center_lat = float(runway["latitude_deg"])
    center_lon = float(runway["longitude_deg"])
    center_alt = float(runway["altitude_m"])
    axis = float(runway["heading_deg"])
    length = float(runway["length_m"])
    width = float(runway["width_m"])
    slope = float(runway.get("true_slope_deg", runway.get("slope_deg", 0.0)))
    half_rise = math.tan(math.radians(slope)) * length / 2.0
    if end == "primary":
        lat, lon = geodesic_destination(center_lat, center_lon, axis + 180.0, length / 2.0)
        threshold_alt = center_alt - half_rise
        landing_course = axis
        threshold = runway.get("primary_threshold", {})
        approach = runway.get("primary_approach_lights", {})
    else:
        lat, lon = geodesic_destination(center_lat, center_lon, axis, length / 2.0)
        threshold_alt = center_alt + half_rise
        landing_course = (axis + 180.0) % 360.0
        threshold = runway.get("secondary_threshold", {})
        approach = runway.get("secondary_approach_lights", {})
    displacement = float(threshold.get("length_m", 0.0)) if threshold.get("enabled") else 0.0
    if not 0 <= displacement < length:
        raise ValueError("invalid displaced threshold")
    if displacement:
        lat, lon = geodesic_destination(lat, lon, landing_course, displacement)
    return {
        "threshold_lat": lat,
        "threshold_lon": lon,
        "threshold_alt_m": threshold_alt,
        "course_deg": landing_course,
        "usable_length_m": length - displacement,
        "width_m": width,
        "center_lat": center_lat,
        "center_lon": center_lon,
        "center_alt_m": center_alt,
        "approach": approach,
    }


def point_at(geometry: dict, along_m: float, cross_m: float) -> tuple[float, float]:
    lat, lon = geodesic_destination(
        geometry["threshold_lat"], geometry["threshold_lon"],
        geometry["course_deg"], along_m)
    return geodesic_destination(lat, lon, geometry["course_deg"] + 90.0, cross_m)


def distance_and_bearing(start, end):
    """Short-range spherical inverse used to derive a fitted runway axis."""
    lat1, lon1 = map(math.radians, start)
    lat2, lon2 = map(math.radians, end)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = (math.sin(dlat / 2.0) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2)
    distance = 6_371_008.8 * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    y = math.sin(dlon) * math.cos(lat2)
    x = (math.cos(lat1) * math.sin(lat2)
         - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return distance, math.degrees(math.atan2(y, x)) % 360.0


def apply_fitted_geometry(geometry, fitted):
    corners = fitted["corners"]
    if len(corners) != 4:
        raise ValueError("fitted runway geometry does not contain four corners")
    near = ((corners[0][0] + corners[1][0]) / 2.0,
            (corners[0][1] + corners[1][1]) / 2.0)
    far = ((corners[2][0] + corners[3][0]) / 2.0,
           (corners[2][1] + corners[3][1]) / 2.0)
    length, course = distance_and_bearing(near, far)
    width_a, _ = distance_and_bearing(corners[0][:2], corners[1][:2])
    width_b, _ = distance_and_bearing(corners[2][:2], corners[3][:2])
    result = dict(geometry)
    result.update(
        threshold_lat=near[0], threshold_lon=near[1],
        threshold_alt_m=(corners[0][2] + corners[1][2]) / 2.0,
        course_deg=course, usable_length_m=length,
        width_m=float(fitted.get("width_m") or (width_a + width_b) / 2.0),
    )
    return result


def add(groups, geometry, preset, along_m, cross_m):
    groups[preset].append(point_at(geometry, along_m, cross_m))


def add_approach(groups, geometry, report):
    approach = geometry["approach"] or {}
    system = str(approach.get("system_name") or "ALSF2").upper()
    fallback = "system_name" not in approach
    spacing = float(approach.get("spacing_m") or 30.0)
    offset = float(approach.get("offset_m") or spacing)
    if spacing <= 0 or not math.isfinite(spacing):
        spacing = 30.0
    if offset < 0 or not math.isfinite(offset):
        offset = spacing

    if system == "NONE":
        station_count, style = 0, "none"
    elif system == "ODALS":
        station_count, style = 7, "odals"
    elif system in {"MALS", "MALSF", "MALSR"}:
        station_count, style = 15, "medium"
    elif system in {"SALS", "SALSF", "SSALS", "SSALF", "SSALR"}:
        station_count, style = 24, "simple"
    elif system in {"CALVERT", "CALVERT2"}:
        station_count, style = 30, "calvert"
    else:
        station_count, style = 24, "precision"

    stations = [offset + spacing * i for i in range(station_count)]
    five = (-2.0574, -1.0287, 0.0, 1.0287, 2.0574)
    station_500 = (min(range(station_count), key=lambda i: abs(stations[i] - 152.4))
                   if station_count else None)
    station_1000 = (min(range(station_count), key=lambda i: abs(stations[i] - 304.8))
                    if station_count else None)
    for index, distance in enumerate(stations):
        along = -distance
        if style == "odals":
            add(groups, geometry, "APPROACH_WHITE", along, 0.0)
            continue
        if style in {"precision", "simple"} and index == station_1000:
            for cross in range_inclusive(-15.24, 15.24, 1.524):
                add(groups, geometry, "APPROACH_WHITE", along, cross)
        else:
            for cross in five:
                add(groups, geometry, "APPROACH_WHITE", along, cross)
        if style in {"precision", "simple"} and index == station_500:
            for cross in (-7.8, -6.3, -4.8, -3.3, 3.3, 4.8, 6.3, 7.8):
                add(groups, geometry, "APPROACH_WHITE", along, cross)
        if style == "calvert" and index in {14, 19, 24}:
            extent = 18.0 + (index - 14) * 0.6
            for cross in range_inclusive(-extent, extent, 1.5):
                add(groups, geometry, "APPROACH_WHITE", along, cross)
        if system == "ALSF2" and index < 9:
            for cross in (-10.524, -9.0, -7.476, 7.476, 9.0, 10.524):
                add(groups, geometry, "APPROACH_RED", along, cross)
    report.update(
        approach_system=system,
        approach_style=style,
        approach_spacing_m=spacing,
        approach_offset_m=offset,
        approach_metadata_fallback=fallback,
    )


def build_light_points(geometry: dict, runway_name: str) -> tuple[dict, dict]:
    groups = defaultdict(list)
    length = geometry["usable_length_m"]
    half_width = geometry["width_m"] / 2.0
    for along in range_inclusive(0.0, length, 60.0):
        preset = "RUNWAY_YELLOW" if length - along <= 600.0 else "RUNWAY_WHITE"
        add(groups, geometry, preset, along, -half_width)
        add(groups, geometry, preset, along, +half_width)
    threshold_extent = half_width + 13.716
    for cross in range_inclusive(-threshold_extent, threshold_extent, 1.524):
        add(groups, geometry, "THRESHOLD_GREEN", 0.0, cross)
    for index, along in enumerate(range_inclusive(0.0, length, 15.0)):
        remaining = length - along
        if remaining <= 300.0:
            preset = "RUNWAY_RED"
        elif remaining <= 900.0:
            preset = "RUNWAY_RED" if index % 2 == 0 else "RUNWAY_WHITE"
        else:
            preset = "RUNWAY_WHITE"
        add(groups, geometry, preset, along, 0.0)
    # Append the far-end bar after the centreline. EdgeLights is a polyline;
    # putting the bar first created a long return segment from the runway end
    # to the first red centreline point and made the MSFS compiler run away.
    for cross in range_inclusive(-half_width, half_width, 3.0):
        add(groups, geometry, "RUNWAY_RED", length, cross)
    approach = geometry["approach"] or {}
    if approach.get("has_touchdown_lights", True):
        for distance in range_inclusive(30.0, min(900.0, length), 30.0):
            for centre in (-9.0, +9.0):
                for cross in (centre - 1.5, centre, centre + 1.5):
                    add(groups, geometry, "TDZ_WHITE", distance, cross)
    report = {"runway": runway_name, "usable_length_m": length, "width_m": geometry["width_m"]}
    add_approach(groups, geometry, report)
    report["light_counts"] = {name: len(points) for name, points in groups.items()}
    report["total_lights"] = sum(report["light_counts"].values())
    return dict(groups), report


def preset_xml(name, color, intensity, radius):
    rgb = " ".join(map(str, color))
    boost = "TRUE" if BOOST_DAYLIGHT else "FALSE"
    return (
        f'    <LightPreset name="{name}">\n'
        f'      <Light relativePosition="0 0.10 0" type="OMNIDIRECTIONAL" '
        f'radius="{radius:.3f}" color="{rgb}" intensity="{intensity}" '
        f'boostDayLight="{boost}" />\n'
        f'    </LightPreset>'
    )


def airport_presets(icao):
    scale = AIRPORT_INTENSITY_SCALE[icao]
    return {
        name: (color, max(1, round(intensity * scale)), radius)
        for name, (color, intensity, radius) in PRESETS.items()
    }


def row_xml(runway_name, preset, points):
    vertices = "\n".join(
        f'        <Vertex lat="{lat:.10f}" lon="{lon:.10f}" />'
        for lat, lon in points)
    title = f"{runway_name} enhanced {preset.lower().replace('_', ' ')}"
    return (
        f'      <EdgeLights displayName="{escape(title)}" preset="{preset}" '
        f'snapToVertices="TRUE">\n{vertices}\n      </EdgeLights>'
    )


def airport_xml(icao, metadata, geometry, groups):
    presets = "\n".join(
        preset_xml(name, color, intensity, radius)
        for name, (color, intensity, radius) in airport_presets(icao).items())
    rows = "\n".join(
        row_xml(metadata["runway"], name, points)
        for name, points in groups.items())
    return f'''  <Airport displayName="{icao} {metadata['runway']} enhanced lights {VERSION}"
           name="{escape(metadata['name'])}"
           region="{metadata['region']}"
           regionCode="{metadata['country']}"
           lat="{geometry['center_lat']:.10f}"
           lon="{geometry['center_lon']:.10f}"
           alt="{geometry['center_alt_m']:.3f}"
           altType="GEOID"
           ident="{icao}"
           airportTestRadius="6000.0"
           onlyAddIfReplace="TRUE"
           applyFlatten="FALSE">
{presets}
    <ApronEdgeLights>
{rows}
    </ApronEdgeLights>
  </Airport>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", required=True)
    parser.add_argument("--runway-geometry",
                        help="validated fitted corners used to align lights with rendered pavement")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.facilities).read_text(encoding="utf-8"))
    airports = payload.get("airports", {})
    fitted_payload = (json.loads(Path(args.runway_geometry).read_text(encoding="utf-8"))
                      if args.runway_geometry else None)
    xml_airports, report_airports = [], {}
    for icao, metadata in TARGETS.items():
        if icao not in airports:
            raise ValueError(f"{icao} is absent from facility data")
        runway, end = find_target_runway(airports[icao], metadata["runway"])
        geometry = selected_end_geometry(runway, end)
        if fitted_payload:
            fitted = fitted_payload.get("airports", {}).get(icao)
            if not fitted or fitted.get("runway") != metadata["runway"]:
                raise ValueError(
                    f"validated fitted geometry is missing for {icao} {metadata['runway']}")
            geometry = apply_fitted_geometry(geometry, fitted)
        groups, report = build_light_points(geometry, metadata["runway"])
        report.update(
            selected_end=end, landing_course_deg=geometry["course_deg"],
            runway_geometry_source=("validated_human_fitted_rendered_pavement"
                                    if fitted_payload else "facilities_api"),
            intensity_scale=AIRPORT_INTENSITY_SCALE[icao],
            intensities_cd={name: values[1]
                            for name, values in airport_presets(icao).items()},
        )
        report_airports[icao] = report
        xml_airports.append(airport_xml(icao, metadata, geometry, groups))
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<FSData version="9.0" source="ODM five-airport enhanced lights {VERSION}">\n'
        + "\n".join(xml_airports) + "\n</FSData>\n")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(xml, encoding="utf-8")
    report = {
        "schema": "odm-enhanced-runway-lights/v1",
        "package_version": VERSION,
        "profile": PROFILE,
        "boost_daylight": BOOST_DAYLIGHT,
        "source_facilities": str(Path(args.facilities).resolve()),
        "source_facilities_schema": payload.get("schema"),
        "airports": report_airports,
        "limitations": [
            "Additive omnidirectional lights; the stock airport is not replaced.",
            "Designed only for scenarios at least 2 km from the runway.",
            "Approach geometry follows the Facilities API system family but is not a photometric copy.",
            "Images made with this package must remain a separate enhanced/synthetic dataset variant.",
        ],
    }
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Generated {output}")
    for icao, value in report_airports.items():
        fallback = " (fallback)" if value["approach_metadata_fallback"] else ""
        print(f"  {icao} {value['runway']}: {value['total_lights']} lights, "
              f"{value['approach_system']}{fallback}")


if __name__ == "__main__":
    main()
