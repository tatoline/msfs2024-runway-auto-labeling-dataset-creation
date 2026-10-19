"""Export human-reviewed runway ground truth in pixel, YOLO and COCO formats."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil

from PIL import Image

from review_training_labels import FINAL, SCHEMA, SUPPORTED_POLICIES, sha256, valid_polygon


def polygon_area(points):
    return abs(sum(points[i][0]*points[(i+1)%4][1]-points[(i+1)%4][0]*points[i][1]
                   for i in range(4)))/2


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--package-dir',required=True)
    ap.add_argument('--review',required=True)
    ap.add_argument('--output-dir',required=True)
    ap.add_argument('--copy-mode',choices=['copy','hardlink'],default='copy',help='copy is portable; hardlink saves local disk space')
    args=ap.parse_args()
    package,review_path,output=Path(args.package_dir).resolve(),Path(args.review),Path(args.output_dir)
    if output.exists():ap.error('Use a fresh output directory')
    manifest_path,report_path=package/'manifest.csv',package/'qa/label_report.json'
    review=json.loads(review_path.read_text())
    review_policy=review.get('annotation_policy')
    review_policy_hash=hashlib.sha256(review_policy.encode()).hexdigest() if isinstance(review_policy,str) else None
    if review_policy not in SUPPORTED_POLICIES or review.get('annotation_policy_sha256')!=review_policy_hash:
        ap.error('Review uses an unknown or altered annotation policy')
    expected=(SCHEMA,sha256(manifest_path),sha256(report_path),review_policy_hash)
    actual=(review.get('schema'),review.get('manifest_sha256'),review.get('draft_report_sha256'),review.get('annotation_policy_sha256'))
    if actual!=expected:ap.error('Review is not bound to this exact package, draft report and policy')
    if review.get('status')!='COMPLETE_READY_TO_EXPORT':ap.error('Review is incomplete; every image needs accept/correct/exclude')
    if not review.get('annotator','').strip():ap.error('Review has no named annotator')
    with manifest_path.open(newline='',encoding='utf-8') as f:manifest=list(csv.DictReader(f))
    by_id={r['scenario_id']:r for r in manifest};decisions=review.get('images',{})
    if set(decisions)!=set(by_id) or any(r.get('decision') not in FINAL for r in decisions.values()):
        ap.error('Review decisions do not exactly match the package manifest')
    accepted=[];excluded=[]
    for sid,row in by_id.items():
        rec=decisions[sid];path=(package/rec['image_file']).resolve()
        if not path.is_relative_to(package) or not path.is_file() or sha256(path)!=rec['image_sha256']:
            ap.error(f'Image missing, unsafe or changed: {sid}')
        with Image.open(path) as im:size=im.size
        if list(size)!=rec['image_size']:ap.error(f'Image dimensions changed: {sid}')
        if rec['decision']=='excluded':
            if not rec.get('exclusion_reason','').strip():ap.error(f'Excluded image needs a reason: {sid}')
            excluded.append(rec);continue
        if rec.get('full_frame_checked') is not True:ap.error(f'Accepted image lacks required full-frame review: {sid}')
        if not valid_polygon(rec['corners_px'],*size):ap.error(f'Invalid accepted polygon: {sid}')
        accepted.append((row,rec,path))
    if not accepted:ap.error('Review excludes every image')
    for name in ['images','labels','labels_seg','labels_pose','labels_px','qa']: (output/name).mkdir(parents=True,exist_ok=True)
    coco={'info':{'description':'Human-reviewed MSFS runway pixel ground truth','version':'1.0','created_utc':datetime.now(timezone.utc).isoformat()},
          'licenses':[],'categories':[{'id':0,'name':'runway','supercategory':'runway','keypoints':['near_right','near_left','far_left','far_right'],'skeleton':[]}],
          'images':[],'annotations':[]}
    jsonl=[];release_manifest=[]
    for image_id,(row,rec,src) in enumerate(accepted):
        sid=row['scenario_id'];dst=output/'images'/src.name
        if args.copy_mode=='copy':shutil.copy2(src,dst)
        else:os.link(src,dst)
        width,height=rec['image_size'];points=[[float(x),float(y)] for x,y in rec['corners_px']]
        xs=[p[0] for p in points];ys=[p[1] for p in points];x0,y0,x1,y1=min(xs),min(ys),max(xs),max(ys);bw,bh=x1-x0,y1-y0
        payload={'scenario_id':sid,'image_file':f'images/{src.name}','image_sha256':rec['image_sha256'],'image_width':width,'image_height':height,
                 'class_id':0,'class_name':'runway','corner_order':review['corner_order'],'polygon_px':points,
                 'bbox_xyxy_px':[x0,y0,x1,y1],'bbox_xywh_px':[x0,y0,bw,bh],
                 'annotation_decision':rec['decision'],'annotator':review['annotator'],'reviewed_utc':rec['reviewed_utc'],'notes':rec.get('notes','')}
        payload['full_frame_checked']=True;payload['contrast_used']=bool(rec.get('contrast_used',False))
        (output/'labels_px'/f'{sid}.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
        jsonl.append(json.dumps(payload,separators=(',',':')))
        box=f'0 {(x0+x1)/2/width:.9f} {(y0+y1)/2/height:.9f} {bw/width:.9f} {bh/height:.9f}'
        (output/'labels'/f'{sid}.txt').write_text(box+'\n',encoding='utf-8')
        polygon='0 '+' '.join(f'{x/width:.9f} {y/height:.9f}' for x,y in points)
        (output/'labels_seg'/f'{sid}.txt').write_text(polygon+'\n',encoding='utf-8')
        pose=box+' '+' '.join(f'{x/width:.9f} {y/height:.9f} 2' for x,y in points)
        (output/'labels_pose'/f'{sid}.txt').write_text(pose+'\n',encoding='utf-8')
        area=polygon_area(points)
        coco['images'].append({'id':image_id,'file_name':f'images/{src.name}','width':width,'height':height})
        coco['annotations'].append({'id':image_id,'image_id':image_id,'category_id':0,'segmentation':[sum(points,[])],
              'area':area,'bbox':[x0,y0,bw,bh],'iscrowd':0,'keypoints':sum(([x,y,2] for x,y in points),[]),'num_keypoints':4})
        release_row=dict(row,image_file=f'images/{src.name}',annotation_decision=rec['decision'],label_file=f'labels_px/{sid}.json',released_image_sha256=rec['image_sha256'])
        if row.get('telemetry_file'):
            source_telemetry=(package/row['telemetry_file']).resolve()
            if not source_telemetry.is_relative_to(package) or not source_telemetry.is_file():
                ap.error(f'Missing or unsafe telemetry: {sid}')
            telemetry=json.loads(source_telemetry.read_text(encoding='utf-8'))
            if telemetry.get('image_sha256')!=rec['image_sha256']:
                ap.error(f'Telemetry/image hash mismatch: {sid}')
            destination=output/'telemetry'/source_telemetry.name
            destination.parent.mkdir(exist_ok=True)
            shutil.copy2(source_telemetry,destination)
            release_row['telemetry_file']=f'telemetry/{destination.name}'
        release_manifest.append(release_row)
    (output/'annotations.jsonl').write_text('\n'.join(jsonl)+'\n',encoding='utf-8')
    (output/'annotations_coco.json').write_text(json.dumps(coco,indent=2),encoding='utf-8')
    fields=list(release_manifest[0])
    with (output/'manifest.csv').open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(release_manifest)
    with (output/'excluded.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['scenario_id','image_file','exclusion_reason','notes','reviewed_utc']);w.writeheader();w.writerows({k:r.get(k,'') for k in w.fieldnames} for r in excluded)
    # Keep reviewed exclusions as evidence, separate from training images.
    for rec in excluded:
        source_image=(package/rec['image_file']).resolve()
        destination=output/'excluded/images'/source_image.name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source_image,destination)
        row=by_id[rec['scenario_id']]
        if row.get('telemetry_file'):
            source_telemetry=(package/row['telemetry_file']).resolve()
            if not source_telemetry.is_relative_to(package) or not source_telemetry.is_file():
                ap.error(f'Missing or unsafe excluded telemetry: {rec["scenario_id"]}')
            destination=output/'excluded/telemetry'/source_telemetry.name
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source_telemetry,destination)
    shutil.copy2(review_path,output/'qa/review_decisions.json');shutil.copy2(report_path,output/'qa/source_draft_report.json')
    (output/'classes.txt').write_text('runway\n',encoding='utf-8')
    receipt={'status':'HUMAN_REVIEWED_TRAINING_GROUND_TRUTH','created_utc':datetime.now(timezone.utc).isoformat(),
             'annotator':review['annotator'],'annotation_policy':review_policy,'corner_order':review['corner_order'],
             'source_manifest_sha256':review['manifest_sha256'],'source_draft_report_sha256':review['draft_report_sha256'],
             'review_sha256':sha256(review_path),'accepted_images':len(accepted),'excluded_images':len(excluded),
             'accepted_projection':sum(r['decision']=='accepted_projection' for _,r,_ in accepted),
             'accepted_corrected':sum(r['decision']=='accepted_corrected' for _,r,_ in accepted),
             'contrast_used_images':sum(bool(r.get('contrast_used')) for _,r,_ in accepted),
             'copy_mode':args.copy_mode,'limitations':['Ground truth means human judgment under the recorded annotation policy; it is not metaphysical or survey-grade truth.','No train/validation/test split is assigned. Group related scenarios when splitting.']}
    (output/'release_receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    (output/'README.md').write_text(f'''# Human-reviewed runway training ground truth\n\nAccepted images: {len(accepted)}. Excluded: {len(excluded)}. Annotator: {review['annotator']}.\n\nExact pixel polygons and boxes are in `labels_px/*.json` and `annotations.jsonl`.\n`annotations_coco.json` contains COCO segmentation, bounding boxes and keypoints.\nYOLO detection, segmentation and pose formats are in their respective folders.\nAll coordinates refer to the unchanged accepted images in `images/`; dimensions are recorded in each pixel JSON. Camera telemetry is included where supplied by the capture.\n\nAnnotation policy: {review_policy}\n\nNo train/validation/test split is assigned. Read `release_receipt.json` and keep\nscenario groups separated when creating the split. Excluded images are listed\nin `excluded.csv`, with screenshots retained in `excluded/images/`. These are not automatically valid empty-negative training examples.\n''',encoding='utf-8')
    print(f'HUMAN_REVIEWED_TRAINING_GROUND_TRUTH: {len(accepted)} accepted, {len(excluded)} excluded -> {output}')


if __name__=='__main__':main()
