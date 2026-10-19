# Scenario format

Use one converted JSON file per scenario, as in `examples/scenarios/`, or one flat JSON list, as in `examples/scenarios.json`.

The converter and scenario generator are not required. You may write these fields from another source. The validator checks structure, finite numbers, ranges, unique IDs, and altitude-unit consistency. It cannot establish that the geometry matches the rendered airport.

## Converted record

| Object | Required fields |
|---|---|
| `runway_data` | `icao_code`, `name`, `runway_name`, `runway_length_m`, `runway_width_m` |
| `geometry` | `landing_threshold`, `opposite_threshold`, `landing_course_true_deg` |
| Each threshold | `latitude`, `longitude`, `altitude_msl_m` |
| `position_of_aircraft` | `latitude`, `longitude`, `altitude_msl_m` |
| `aircraft_orientation` | `yaw_true_deg`, `pitch_deg`, `roll_deg` |
| `distance_aircraft_to_aim_point` | `ground_distance_m`, `altitude_difference_m` |
| `daytime` | integer `hours` and `minutes` |
| `weather` | nonempty string; supplied groups are `Broken Clouds`, `Rain`, `Heavy Rain` |

The filename stem becomes the scenario ID, for example `AutoScenario_1202`. Keep it unique and use only letters, digits, underscores, and hyphens.

Additional source metadata is allowed. The batch builder only extracts the fields needed by capture and projection.

## Flat capture batch

The JSON root is a list. Each row has:

| Field | Meaning / units |
|---|---|
| `sid` | Unique scenario ID and output filename stem |
| `icao`, `airport`, `runway` | Airport and landing-runway identifiers |
| `weather` | Intended manually selected weather group |
| `hour`, `minute` | UTC time; capture uses Zulu events |
| `lat`, `lon` | Aircraft WGS-84 latitude and longitude in degrees |
| `alt_m`, `alt_ft` | Aircraft altitude in MSL metres and feet |
| `heading_true` | Aircraft true heading in degrees, pointing along the intended view |
| `roll` | Aircraft bank in degrees |
| `glide_angle_deg` | Scenario approach-angle metadata, absolute degrees |
| `gd`, `ad` | Ground distance to aim point and altitude difference in metres |
| `runway_length_m`, `runway_width_m` | Runway dimensions in metres |
| `landing_course_true_deg` | Landing runway true course in degrees |
| `corners` | Four world points, each `[latitude_deg, longitude_deg, altitude_m]` |

The corner order, looking along the landing direction, is:

1. Landing-threshold right.
2. Landing-threshold left.
3. Opposite-end left.
4. Opposite-end right.

The batch builder derives these corners from the two threshold centres and runway width. The production label tool prefers the captured Facilities data, or a supplied fitted geometry file, over the nominal scenario corners.

## Conventions that matter

- Latitude/longitude and headings are in degrees in JSON. The SimConnect pose helper converts the attitude values to the radians required by the wrapper calls.
- Camera FOV is **vertical**, in degrees. The provided 28.555° setting is about 48.684° horizontally at 16:9.
- The aircraft approach slope is not automatically the camera pitch. Capture uses explicit `--pitch` and `--camera-pitch` settings, both defaulting to zero.
- Altitude values must use a consistent convention. The historical project used simulator-aligned MSL values; camera altitude interpretation is recorded separately in telemetry.
- Thresholds must describe the usable landing surface, including a displaced landing threshold where applicable.
- The weather field documents the intended condition. It does not change the simulator preset.
- Use the five examples to understand the format; check runway geometry against your own simulator scenery.
