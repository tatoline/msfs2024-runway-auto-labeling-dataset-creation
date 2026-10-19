"""Shared aircraft-freeze, pose, time, and screen-grab functions."""
import math
import time
import mss
from PIL import Image
_MSS = getattr(mss, "MSS", None) or mss.mss

MANIFEST_FIELDS = [
    "scenario_id", "image_file", "icao", "airport", "runway",
    "lat", "lon", "alt_m", "alt_ft", "heading_true_deg", "glide_angle_deg",
    "ground_dist_m", "alt_diff_m", "runway_length_m", "runway_width_m",
    "weather", "hour", "minute", "target", "simulator", "aircraft",
    "camera_view", "zoom", "image_w", "image_h", "capture_time_utc", "notes",
]

def freeze_aircraft(ae, on=True):
    """Stop physics moving the plane, so it holds a teleported pose."""
    v = 1 if on else 0
    for ev in ("FREEZE_LATITUDE_LONGITUDE_SET", "FREEZE_ALTITUDE_SET",
               "FREEZE_ATTITUDE_SET"):
        try:
            ae.find(ev)(v)
        except Exception:
            pass

def set_time(ae, hour, minute):
    """MSFS ignores writes to LOCAL_TIME, so fire the ZULU set events instead."""
    try:
        ae.find("ZULU_HOURS_SET")(int(hour))
        ae.find("ZULU_MINUTES_SET")(int(minute))
    except Exception as e:
        print("   !! time-set failed:", e)

def set_pose(aq, s, pitch_deg=0.0):
    """Place the aircraft.

    UNITS: despite their names, PLANE_PITCH_DEGREES / PLANE_BANK_DEGREES /
    PLANE_HEADING_DEGREES_TRUE are read by SimConnect in RADIANS.

    heading comes from the data as yaw_true_deg, which already points at the aim
    point (it includes the lateral deviation), so no correction is applied here.
    pitch defaults to 0 - the ODME source states PitchDeg = 0, and the aircraft
    flies level down the glide path.
    """
    aq.set("PLANE_LATITUDE", s["lat"])
    aq.set("PLANE_LONGITUDE", s["lon"])
    aq.set("PLANE_ALTITUDE", s["alt_ft"])
    aq.set("PLANE_PITCH_DEGREES", math.radians(pitch_deg))
    aq.set("PLANE_BANK_DEGREES", math.radians(s.get("roll", 0.0)))
    aq.set("PLANE_HEADING_DEGREES_TRUE", math.radians(s["heading_true"] % 360))
    for v in ("VELOCITY_BODY_X", "VELOCITY_BODY_Y", "VELOCITY_BODY_Z",
              "AIRSPEED_TRUE", "VERTICAL_SPEED"):
        try:
            aq.set(v, 0)
        except Exception:
            pass

def hold_pose(aq, s, seconds, pitch_deg=0.0, hz=4):
    """Re-assert the pose while the scenery streams, so it cannot drift."""
    if seconds < 0 or hz <= 0:
        raise ValueError("seconds must be nonnegative and hz must be positive")
    deadline = time.monotonic() + seconds
    set_pose(aq, s, pitch_deg)
    while (remaining := deadline - time.monotonic()) > 0:
        time.sleep(min(1.0 / hz, remaining))
        set_pose(aq, s, pitch_deg)

def grab_screenshot(path, out_w=None, out_h=None, fmt="png", quality=92,
                    png_compress_level=1, timings=None, capture_meta=None):
    """Capture the primary monitor and downscale before saving."""
    started = time.perf_counter()
    with _MSS() as sct:
        m = sct.monitors[1]
        shot = sct.grab(m)
    im = Image.frombytes("RGB", shot.size, shot.rgb)
    source_size = im.size
    if out_w and out_h and im.size != (out_w, out_h):
        im = im.resize((out_w, out_h), Image.LANCZOS)
    if capture_meta is not None:
        capture_meta.update(
            source_monitor_index=1,
            source_size_px=list(source_size),
            output_size_px=[im.width, im.height],
            resize_scale_xy=[im.width / source_size[0], im.height / source_size[1]],
            full_primary_monitor=True,
        )
    grabbed = time.perf_counter()
    if fmt.lower() in ("jpg", "jpeg"):
        im.save(path, "JPEG", quality=quality, optimize=True)
    else:
        # PNG compression changes file size/CPU cost, never the image pixels.
        im.save(path, "PNG", optimize=False, compress_level=png_compress_level)
    if timings is not None:
        timings["grab_resize_s"] = grabbed - started
        timings["encode_write_s"] = time.perf_counter() - grabbed
    return im.width, im.height

def order_scenarios(batch, order="airport-distance"):
    """Keep legacy index ordering unless the run explicitly groups time of day."""
    if order == "airport-time-distance":
        return sorted(batch, key=lambda s: (s["icao"], s["hour"], s["minute"], s["gd"], s["sid"]))
    return sorted(batch, key=lambda s: (s["icao"], s["gd"]))
