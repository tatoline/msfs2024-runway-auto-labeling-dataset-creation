"""Camera basis, bounding boxes, and saved-image overlay helpers."""
import math
import numpy as np

def camera_basis(heading_deg, pitch_deg, roll_deg=0.0):
    """Right / Up / Forward unit vectors of the camera, in local ENU."""
    h, p, r = (math.radians(heading_deg), math.radians(pitch_deg),
               math.radians(roll_deg))
    fwd_flat = np.array([math.sin(h), math.cos(h), 0.0])
    right = np.array([math.cos(h), -math.sin(h), 0.0])
    up_flat = np.array([0.0, 0.0, 1.0])
    fwd = math.cos(p) * fwd_flat + math.sin(p) * up_flat
    up = -math.sin(p) * fwd_flat + math.cos(p) * up_flat
    if r:
        right, up = (math.cos(r) * right + math.sin(r) * up,
                     -math.sin(r) * right + math.cos(r) * up)
    return right, up, fwd

def bbox_from(proj, W, H):
    pts = [(u, v) for u, v, _ in proj if u is not None]
    if len(pts) < 2:
        return None
    xs = [max(0.0, min(W - 1.0, p[0])) for p in pts]
    ys = [max(0.0, min(H - 1.0, p[1])) for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, y0, x1, y1

def draw_overlay(img_path, proj, bbox, out_path, notice=None):
    from PIL import Image, ImageDraw
    im = Image.open(img_path).convert("RGB")
    d = ImageDraw.Draw(im)
    pts = [(u, v) for u, v, _ in proj if u is not None]
    if len(pts) == 4:
        d.polygon(pts, outline=(0, 255, 0))
        for i, (u, v) in enumerate(pts):
            d.ellipse([u - 4, v - 4, u + 4, v + 4], outline=(255, 0, 0), width=2)
            d.text((u + 6, v - 6), str(i), fill=(255, 255, 0))
    if bbox:
        d.rectangle(bbox, outline=(0, 128, 255), width=2)
    if notice:
        d.rectangle((6, 6, min(im.width-1, 8+len(notice)*7), 28), fill=(0, 0, 0))
        d.text((10, 10), notice, fill=(255, 220, 0))
    im.save(out_path, optimize=False, compress_level=1)
