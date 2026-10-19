"""Fit a small pixel correction from reviewed Camera API runway corners.

World geometry explains the physical runway. This second calibration learns
repeatable screen-space residuals caused by renderer alignment, rasterization,
and the human boundary convention. It is deliberately low capacity: one ridge
model per airport and corner, using only projected runway position and size.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np


SCHEMA = "runway_pixel_residual_calibration_v1"
FEATURES = ["near_right_y_px", "bbox_height_px", "bbox_width_px", "center_x_px"]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def feature_vector(points):
    points = np.asarray(points, dtype=float)
    return np.asarray([
        points[0, 1],
        np.ptp(points[:, 1]),
        np.ptp(points[:, 0]),
        np.mean(points[:, 0]),
    ])


def quantile(values, p):
    return float(np.quantile(np.asarray(values, dtype=float), p))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels-dir", required=True,
                    help="current geometry-only labels to calibrate")
    ap.add_argument("--package-dir", required=True,
                    help="review package containing the exact reviewed images")
    ap.add_argument("--review", required=True)
    ap.add_argument("--additional-review", action="append", nargs=2,
                    metavar=("PACKAGE_DIR", "REVIEW"), default=[],
                    help="additional complete or partial review whose finalized decisions are disjoint")
    ap.add_argument("--output", required=True)
    ap.add_argument("--ridge", type=float, default=2.0,
                    help="regularization applied to feature slopes")
    args = ap.parse_args()
    labels = Path(args.labels_dir).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        ap.error("output already exists; choose a new path")
    if args.ridge <= 0:
        ap.error("ridge must be positive")

    report_path = labels / "label_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    predictions = {
        item["scenario_id"]: np.asarray([point[:2] for point in item["corners_px"]], dtype=float)
        for item in report.get("images", []) if item.get("status") == "candidate"
    }
    records, review_sources, seen, image_sizes, policies = [], [], set(), set(), {}
    review_specs = [(args.package_dir, args.review)] + args.additional_review
    for package_arg, review_arg in review_specs:
        package, review_path = Path(package_arg).resolve(), Path(review_arg).resolve()
        review = json.loads(review_path.read_text(encoding="utf-8"))
        if review.get("status") not in {"COMPLETE_READY_TO_EXPORT", "IN_PROGRESS_NOT_GROUND_TRUTH"}:
            ap.error(f"unsupported review status: {review_path}")
        package_report = package / "qa" / "label_report.json"
        if review.get("draft_report_sha256") != sha256(package_report):
            ap.error(f"review does not match its review package: {review_path}")
        accepted = 0
        for sid, decision in review.get("images", {}).items():
            if decision.get("decision") not in {"accepted_projection", "accepted_corrected"}:
                continue
            if sid in seen:
                ap.error(f"duplicate finalized review image: {sid}")
            if sid not in predictions:
                ap.error(f"reviewed image is absent from current labels: {sid}")
            measured = np.asarray(decision.get("corners_px"), dtype=float)
            if measured.shape != (4, 2) or not np.all(np.isfinite(measured)):
                ap.error(f"invalid reviewed corners: {sid}")
            size = decision.get("image_size")
            if not isinstance(size, list) or len(size) != 2:
                ap.error(f"invalid reviewed image size: {sid}")
            image_sizes.add(tuple(size)); seen.add(sid); accepted += 1
            records.append((sid, decision["icao"], predictions[sid], measured))
        policy_hash = review.get("annotation_policy_sha256")
        policies[policy_hash] = review.get("annotation_policy")
        review_sources.append({
            "package": str(package),
            "review": str(review_path),
            "review_sha256": sha256(review_path),
            "status": review.get("status"),
            "accepted_images_used": accepted,
            "annotation_policy": review.get("annotation_policy"),
            "annotation_policy_sha256": policy_hash,
        })
    if not records:
        ap.error("review contains no accepted images")
    if len(image_sizes) != 1:
        ap.error(f"reviewed images use multiple sizes: {sorted(image_sizes)}")

    models = {}
    before, after = [], []
    for icao in sorted({record[1] for record in records}):
        airport = [record for record in records if record[1] == icao]
        if len(airport) < 8:
            ap.error(f"{icao} has only {len(airport)} samples; at least 8 are required")
        x = np.stack([feature_vector(predicted) for _, _, predicted, _ in airport])
        mean, scale = np.mean(x, axis=0), np.std(x, axis=0)
        scale[scale < 1e-6] = 1.0
        z = np.c_[np.ones(len(x)), (x - mean) / scale]
        penalty = np.diag([0.0] + [args.ridge] * len(FEATURES))
        corner_models = []
        for corner in range(4):
            target = np.stack([measured[corner] - predicted[corner]
                               for _, _, predicted, measured in airport])
            beta = np.linalg.solve(z.T @ z + penalty, z.T @ target)
            corner_models.append({"beta_xy": beta.tolist()})
        models[icao] = {
            "samples": len(airport),
            "feature_mean": mean.tolist(),
            "feature_scale": scale.tolist(),
            "corners": corner_models,
        }
        for _, _, predicted, measured in airport:
            vector = np.r_[1.0, (feature_vector(predicted) - mean) / scale]
            correction = np.stack([vector @ np.asarray(item["beta_xy"])
                                   for item in corner_models])
            before.extend(np.linalg.norm(predicted - measured, axis=1).tolist())
            after.extend(np.linalg.norm(predicted + correction - measured, axis=1).tolist())

    payload = {
        "schema": SCHEMA,
        "status": "PIXEL_RESIDUAL_CALIBRATION_REQUIRES_DISJOINT_VALIDATION",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "method": "per-airport per-corner ridge regression",
        "feature_names": FEATURES,
        "ridge": args.ridge,
        "image_size": list(next(iter(image_sizes))),
        "fov_convention": report.get("fov_convention"),
        "source_label_report": str(report_path),
        "source_label_report_sha256": sha256(report_path),
        "source_runway_geometry_sha256": report.get("runway_geometry_sha256"),
        "training_review": review_sources[0]["review"],
        "training_review_sha256": review_sources[0]["review_sha256"],
        "training_reviews": review_sources,
        "annotation_policies": [
            {"sha256": digest, "policy": policy} for digest, policy in policies.items()
        ],
        "training_images": len(records),
        "training_metrics": {
            "before_mean_corner_error_px": float(np.mean(before)),
            "before_p95_corner_error_px": quantile(before, .95),
            "before_max_corner_error_px": float(np.max(before)),
            "after_mean_corner_error_px": float(np.mean(after)),
            "after_p95_corner_error_px": quantile(after, .95),
            "after_max_corner_error_px": float(np.max(after)),
        },
        "models": models,
        "limitations": [
            "The same reviewed images were used to fit and score these training metrics.",
            "Validate on disjoint reviewed images before applying to the remaining dataset.",
            "This correction is valid only with the bound runway geometry, image size and FOV convention.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["training_metrics"], indent=2))
    print(f"Saved -> {output}")


if __name__ == "__main__":
    main()
