"""Validate a capture or reviewed release, including image hashes and label formats."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image
from review_training_labels import valid_polygon


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def checked_path(root,relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root): raise ValueError(f'path leaves dataset: {relative}')
    if not path.is_file(): raise ValueError(f'missing file: {relative}')
    return path


def validate(root):
    root=Path(root).resolve()
    with (root/'manifest.csv').open(newline='',encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
    if not rows: raise ValueError('empty manifest')
    ids=[r['scenario_id'] for r in rows]
    if len(ids)!=len(set(ids)): raise ValueError('duplicate scenario IDs')
    release=(root/'release_receipt.json').is_file()
    seen=set()
    for row in rows:
        sid=row['scenario_id'];image_path=checked_path(root,row['image_file']);hash_value=digest(image_path)
        if hash_value != row['image_sha256']: raise ValueError(f'image hash mismatch: {sid}')
        if hash_value in seen: raise ValueError(f'duplicate image content: {sid}')
        seen.add(hash_value)
        with Image.open(image_path) as image:
            image.load();width,height=image.size
        if (width,height)!=(int(row['image_w']),int(row['image_h'])): raise ValueError(f'dimension mismatch: {sid}')
        if not release:
            telemetry=json.loads(checked_path(root,row['telemetry_file']).read_text(encoding='utf-8'))
            if telemetry.get('scenario_id')!=sid or telemetry.get('image_sha256')!=hash_value:
                raise ValueError(f'telemetry identity/hash mismatch: {sid}')
            if telemetry.get('image_size')!=[width,height]: raise ValueError(f'telemetry dimensions mismatch: {sid}')
        else:
            if row.get('telemetry_file'):
                telemetry=json.loads(checked_path(root,row['telemetry_file']).read_text(encoding='utf-8'))
                if telemetry.get('scenario_id')!=sid or telemetry.get('image_sha256')!=hash_value:
                    raise ValueError(f'release telemetry identity/hash mismatch: {sid}')
            payload=json.loads(checked_path(root,row['label_file']).read_text(encoding='utf-8'))
            points=payload.get('polygon_px',payload.get('corners_px'))
            if not valid_polygon(points,width,height): raise ValueError(f'invalid polygon: {sid}')
            if payload.get('image_sha256')!=hash_value: raise ValueError(f'label image hash mismatch: {sid}')
            points=np.asarray(points,dtype=float)
            lo=points.min(axis=0);hi=points.max(axis=0)
            expected_box=[0,*((lo+hi)/2/[width,height]),*((hi-lo)/[width,height])]
            expected_seg=[0,*((points/[width,height]).reshape(-1))]
            expected_pose=[*expected_box,*[v for p in points/[width,height] for v in [*p,2]]]
            for folder,expected in [('labels',expected_box),('labels_seg',expected_seg),('labels_pose',expected_pose)]:
                values=[float(v) for v in checked_path(root,f'{folder}/{sid}.txt').read_text().split()]
                if len(values)!=len(expected) or not np.allclose(values,expected,atol=2e-6,rtol=0):
                    raise ValueError(f'{folder} differs from pixel corners: {sid}')
    if release:
        receipt=json.loads((root/'release_receipt.json').read_text(encoding='utf-8'))
        if receipt.get('accepted_images',receipt.get('images'))!=len(rows): raise ValueError('release receipt count mismatch')
        jsonl=[json.loads(line) for line in (root/'annotations.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
        if {r['scenario_id'] for r in jsonl}!=set(ids) or len(jsonl)!=len(rows): raise ValueError('JSON Lines coverage mismatch')
        coco=json.loads((root/'annotations_coco.json').read_text(encoding='utf-8'))
        if len(coco['images'])!=len(rows) or len(coco['annotations'])!=len(rows): raise ValueError('COCO count mismatch')
        if {r['file_name'] for r in coco['images']}!={r['image_file'] for r in rows}: raise ValueError('COCO image coverage mismatch')
    else:
        batch=json.loads((root/'captured_batch.json').read_text(encoding='utf-8'))
        if {r['sid'] for r in batch}!=set(ids) or len(batch)!=len(rows): raise ValueError('batch/manifest coverage mismatch')
    return {'status':'PASS','kind':'reviewed_release' if release else 'capture','images':len(rows),
            'checks':'manifest identity, image hashes, uniqueness, decoding, dimensions, telemetry or label/export consistency',
            'note':'File integrity does not certify visual alignment or runway visibility.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dataset-dir',required=True)
    ap.add_argument('--output',help='optional new JSON QA report')
    args=ap.parse_args()
    if args.output and Path(args.output).exists(): ap.error('output report exists')
    try: result=validate(args.dataset_dir)
    except (OSError,ValueError,KeyError,TypeError) as error: ap.error(str(error))
    if args.output:
        target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
