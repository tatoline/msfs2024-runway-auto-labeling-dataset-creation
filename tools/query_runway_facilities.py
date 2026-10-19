"""Read current runway records directly from the MSFS Facilities API."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from camera_api import NativeCameraClient, normalize_facility_response, write_json_atomic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--airports", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--helper", default=str(
        Path(__file__).resolve().parent.parent / "camera_bridge" / "bin" / "Release" / "camera_bridge.exe"))
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists():
        parser.error("output already exists; choose a fresh file")
    airports = [value.upper() for value in args.airports]
    if len(set(airports)) != len(airports):
        parser.error("airport ICAOs must be unique")
    with NativeCameraClient(args.helper, timeout=args.timeout) as client:
        records = {}
        for icao in airports:
            response = client.facility(icao)
            records[icao] = {"runways": normalize_facility_response(response)}
            print(f"{icao}: {len(response['runways'])} physical runway records")
    write_json_atomic(output, {
        "schema": "msfs2024-runway-facilities/v2",
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "source": "MSFS 2024 SimConnect Facilities API",
        "airports": records,
    })
    print(f"Saved -> {output}")


if __name__ == "__main__":
    main()
