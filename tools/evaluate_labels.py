"""Compare projected corners to hash-bound human review on independent images."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
from review_training_labels import SUPPORTED_POLICIES, valid_polygon


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--labels-dir',required=True)
    ap.add_argument('--package-dir',required=True)
    ap.add_argument('--review',required=True)
    ap.add_argument('--calibration-review',action='append',required=True,
                    help='training review JSON; repeat for every review used by either fit')
    ap.add_argument('--output',required=True)
    ap.add_argument('--mean-limit',type=float,default=1.25)
    ap.add_argument('--p95-limit',type=float,default=3)
    ap.add_argument('--max-limit',type=float,default=5)
    ap.add_argument('--min-images',type=int,default=10)
    args=ap.parse_args()
    output=Path(args.output);package=Path(args.package_dir)
    if output.exists(): ap.error('output exists; choose a fresh validation filename')
    report_path=Path(args.labels_dir)/'label_report.json'
    report=json.loads(report_path.read_text(encoding='utf-8'))
    review=json.loads(Path(args.review).read_text(encoding='utf-8'))
    if review.get('status')!='COMPLETE_READY_TO_EXPORT': ap.error('validation review is incomplete')
    if review.get('draft_report_sha256')!=sha(package/'qa/label_report.json'): ap.error('review/report hash mismatch')
    if review.get('manifest_sha256')!=sha(package/'manifest.csv'): ap.error('review/manifest hash mismatch')
    policy=review.get('annotation_policy','')
    if policy not in SUPPORTED_POLICIES or review.get('annotation_policy_sha256')!=hashlib.sha256(policy.encode()).hexdigest():
        ap.error('unknown or altered annotation policy')
    train_ids=set();train_hashes=set()
    for path in args.calibration_review:
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        for sid,row in data['images'].items():
            if row.get('decision') in {'accepted_projection','accepted_corrected'}:
                train_ids.add(sid);train_hashes.add(row.get('image_sha256'))
    predicted={r['scenario_id']:r for r in report['images'] if r['status']=='candidate'}
    with (package/'manifest.csv').open(newline='',encoding='utf-8') as f:
        manifest={r['scenario_id']:r for r in csv.DictReader(f)}
    source=Path(report['dataset'])
    errors=[];images=[];excluded=0
    for sid,row in review['images'].items():
        if sid in train_ids or row.get('image_sha256') in train_hashes:
            ap.error(f'calibration/validation overlap: {sid}')
        if row.get('decision')=='excluded': excluded+=1;continue
        if row.get('decision') not in {'accepted_projection','accepted_corrected'}: ap.error(f'unresolved decision: {sid}')
        if row.get('full_frame_checked') is not True: ap.error(f'missing full-frame check: {sid}')
        if sid not in predicted or sid not in manifest: ap.error(f'missing validation prediction: {sid}')
        rec=predicted[sid]
        if sha(package/row['image_file'])!=row['image_sha256'] or sha(source/rec['image_file'])!=row['image_sha256']:
            ap.error(f'validation image hash mismatch: {sid}')
        if not valid_polygon(row['corners_px'],*row['image_size']): ap.error(f'invalid reference polygon: {sid}')
        measured=np.asarray(row['corners_px'],dtype=float)
        projected=np.asarray([p[:2] for p in rec['corners_px']],dtype=float)
        distance=np.linalg.norm(projected-measured,axis=1)
        errors.extend(distance.tolist());images.append({'scenario_id':sid,'corner_errors_px':distance.tolist()})
    if len(images)<args.min_images: ap.error(f'only {len(images)} accepted images; need at least {args.min_images}')
    metrics={'mean_px':float(np.mean(errors)),'median_px':float(np.median(errors)),
             'p95_px':float(np.quantile(errors,.95)),'max_px':float(np.max(errors))}
    passed=metrics['mean_px']<=args.mean_limit and metrics['p95_px']<=args.p95_limit and metrics['max_px']<=args.max_limit
    payload={'status':'PASS' if passed else 'FAIL','accepted_images':len(images),'excluded_images':excluded,
             'corner_observations':len(errors),'metrics':metrics,'disjoint_ids_and_image_hashes':True,
             'label_report_sha256':sha(report_path),'review_sha256':sha(args.review),
             'runway_geometry_sha256':report.get('runway_geometry_sha256'),
             'pixel_calibration_sha256':report.get('pixel_calibration_sha256'),
             'limits':{'mean_px':args.mean_limit,'p95_px':args.p95_limit,'max_px':args.max_limit},'images':images}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(payload,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(payload['metrics'],indent=2));print(payload['status'])
    if not passed: raise SystemExit(1)


if __name__=='__main__': main()
