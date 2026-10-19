"""Prepare a portable draft review batch and stratified overlay sheets.

Only candidates inside the reference distance ranges are selected. This is
not a release approval or a train/validation/test split.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil

from PIL import Image
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from scenario_utils import select_balanced
from projection_utils import draw_overlay


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dataset-dir', required=True)
    ap.add_argument('--labels-dir', required=True)
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--count', type=int, default=1000)
    ap.add_argument('--priority-ids',
                    help='optional JSON list, or object with scenario_ids, that must be included')
    ap.add_argument('--exclude-ids',
                    help='optional JSON list, or object with scenario_ids, to omit (for example, already-reviewed images)')
    ap.add_argument('--copy-mode', choices=['copy','hardlink'], default='copy',
                    help='copy is portable; hardlink saves local disk space')
    args = ap.parse_args()
    dataset, labels, output = Path(args.dataset_dir).resolve(), Path(args.labels_dir), Path(args.output_dir)
    if output.exists():
        ap.error('Use a fresh output directory')
    report = json.loads((labels/'label_report.json').read_text())
    if report.get('manifest_sha256') != hashlib.sha256((dataset/'manifest.csv').read_bytes()).hexdigest():
        ap.error('Labels do not match this capture manifest')
    batch = {s['sid']:s for s in json.loads((dataset/'captured_batch.json').read_text())}
    camera_api_report = report.get('status') == 'CAMERA_API_PROJECTED_CANDIDATES_NOT_GROUND_TRUTH'
    records = {s['scenario_id']:s for s in report['images']
               if s['status']=='candidate' and
               (s.get('within_reference_distance_range') is True or camera_api_report)}
    if not records:
        ap.error('No reviewable candidate labels were found')
    excluded_ids=[]
    if args.exclude_ids:
        payload=json.loads(Path(args.exclude_ids).read_text(encoding='utf-8'))
        excluded_ids=payload.get('scenario_ids',[]) if isinstance(payload,dict) else payload
        if not isinstance(excluded_ids,list) or len(excluded_ids)!=len(set(excluded_ids)):
            ap.error('excluded IDs must be a unique JSON list')
        unknown=[sid for sid in excluded_ids if sid not in records]
        if unknown:
            ap.error(f'excluded IDs are not reviewable candidates: {unknown[:5]}')
        for sid in excluded_ids:
            del records[sid]
    if args.count > len(records):
        ap.error(f'Requested {args.count} images; only {len(records)} remain after exclusions')
    priority=[]
    if args.priority_ids:
        payload=json.loads(Path(args.priority_ids).read_text(encoding='utf-8'))
        priority_ids=payload.get('scenario_ids',[]) if isinstance(payload,dict) else payload
        if not isinstance(priority_ids,list) or len(priority_ids)!=len(set(priority_ids)):
            ap.error('priority IDs must be a unique JSON list')
        missing=[sid for sid in priority_ids if sid not in records]
        if missing:
            ap.error(f'priority IDs are not reviewable candidates: {missing[:5]}')
        if len(priority_ids)>args.count:
            ap.error('priority ID count exceeds requested review count')
        priority=[batch[sid] for sid in priority_ids]
    priority_set={s['sid'] for s in priority}
    remaining=[batch[sid] for sid in records if sid not in priority_set]
    selected = priority + (select_balanced(remaining, args.count-len(priority))
                           if args.count > len(priority) else [])
    with (dataset/'manifest.csv').open(newline='',encoding='utf-8') as f:
        manifest = {s['scenario_id']:s for s in csv.DictReader(f)}
    output.mkdir(parents=True)
    folders = ['images','labels','labels_seg','labels_pose','qa']
    if camera_api_report:
        folders.append('telemetry')
    for name in folders:
        (output/name).mkdir()
    def transfer(src,dst):
        os.link(src,dst) if args.copy_mode=='hardlink' else shutil.copy2(src,dst)
    for s in selected:
        sid=s['sid']; src=(dataset/records[sid]['image_file']).resolve()
        if not src.is_relative_to(dataset):
            raise ValueError('Image path outside dataset')
        transfer(src,output/'images'/src.name)
        for name in ['labels','labels_seg','labels_pose']:
            transfer(labels/name/f'{sid}.txt',output/name/f'{sid}.txt')
        if camera_api_report:
            telemetry=(dataset/records[sid]['telemetry_file']).resolve()
            if not telemetry.is_relative_to(dataset):
                raise ValueError('Telemetry path outside dataset')
            transfer(telemetry,output/'telemetry'/telemetry.name)
    selected_ids={s['sid'] for s in selected}
    subset_records=[r for r in report['images'] if r['scenario_id'] in selected_ids]
    for name in ['calibration_used.json','projection_fit_used.json']:
        if (labels/name).is_file():
            shutil.copy2(labels/name,output/'qa'/name)
    (output/'qa/label_report.json').write_text(json.dumps(dict(report,images=subset_records),indent=2),encoding='utf-8')
    with (output/'manifest.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(next(iter(manifest.values()))))
        writer.writeheader();writer.writerows(manifest[s['sid']] for s in selected)
    (output/'classes.txt').write_text('runway\n',encoding='utf-8')
    counts=Counter((s['icao'],s['hour']) for s in selected)
    limitations = (['Camera API projected candidates require human pilot acceptance before release.',
                    'Facilities geometry and displaced-threshold handling must be checked against rendered runway paint.',
                    'No capture or label files outside this new folder were modified.']
                   if camera_api_report else
                   ['Range membership does not certify every image or viewing angle.',
                    'Manual references used noon lighting only; other hours require visibility review.',
                    'No capture or label files outside this new folder were modified.'])
    summary={'status':'DRAFT_REVIEW_ONLY_NOT_TRAINING_RELEASE','count':len(selected),
        'source_manifest_sha256':report['manifest_sha256'], 'source_label_report_sha256':hashlib.sha256((labels/'label_report.json').read_bytes()).hexdigest(),
        'selection':('required priority cases plus balanced airports, proportional hours, and recorded distances'
                     if priority else
                     'balanced airports, proportional hours, spanning recorded distances inside each airport reference range'),
        'priority_scenario_ids':[s['sid'] for s in priority],
        'excluded_scenario_ids':excluded_ids,
        'copy_mode':args.copy_mode,
        'airport_hour_counts':[dict(icao=a,hour=h,count=n) for (a,h),n in sorted(counts.items())],
        'reference_ranges_m':{a:[min(s['gd'] for s in selected if s['icao']==a),
                                 max(s['gd'] for s in selected if s['icao']==a)]
                              for a in sorted({s['icao'] for s in selected})},
        'scenario_ids':[s['sid'] for s in selected], 'train_val_test_split':None,
        'limitations':limitations}
    (output/'review_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')

    # Two different-distance samples per airport/hour. Do not silently approve
    # the sheets: they are evidence for an actual visual review.
    samples=[]
    airports=sorted({s['icao'] for s in selected}); hours=sorted({s['hour'] for s in selected})
    for a in airports:
        for h in hours:
            pool=[s for s in selected if s['icao']==a and s['hour']==h]
            if not pool:
                continue
            for fraction in [.15,.8]:
                pool.sort(key=lambda s:s['gd'])
                samples.append(pool[round(fraction*(len(pool)-1))])
    fig,axes=plt.subplots(len(airports),len(hours)*2,figsize=(24,12))
    axes_flat=list(np.asarray(axes).reshape(-1))
    for ax,s in zip(axes_flat,samples):
        rec=records[s['sid']]; image=output/rec['image_file']
        draw_overlay(image,rec['corners_px'],rec['bbox_px'],output/'qa'/f'{s["sid"]}.png',notice='DRAFT REVIEW - not approved for training')
        ax.imshow(Image.open(image)); pts=np.array(rec['corners_px'])[:,:2];closed=np.vstack([pts,pts[0]])
        ax.plot(*closed.T,color='#00ff00',lw=.9)
        x0,y0,x1,y1=rec['bbox_px'];ax.set_xlim(x0-30,x1+30);ax.set_ylim(y1+25,y0-25)
        ax.set_title(f'{s["icao"]} {s["hour"]:02d} UTC {s["gd"]:.0f}m\n{s["sid"]}',fontsize=8)
        ax.tick_params(labelsize=5)
    for ax in axes_flat[len(samples):]:
        ax.set_visible(False)
    fig.suptitle('DRAFT: airport/time/distance checks; green = corrected projection',fontsize=15)
    fig.tight_layout(rect=(0,0,1,.965));fig.savefig(output/'qa/stratified_overlays.png',dpi=150);plt.close(fig)
    fig,axes=plt.subplots(len(airports),len(hours),figsize=(20,14))
    axes_flat=list(np.asarray(axes).reshape(-1))
    for ax,s in zip(axes_flat,samples[::2]):
        ax.imshow(Image.open(output/records[s['sid']]['image_file']));ax.axis('off')
        ax.set_title(f'{s["icao"]} {s["hour"]:02d} UTC - {s["sid"]}',fontsize=10)
    for ax in axes_flat[len(samples[::2]):]:
        ax.set_visible(False)
    fig.tight_layout();fig.savefig(output/'qa/full_frame_samples.png',dpi=110);plt.close(fig)
    (output/'qa/sample_ids.json').write_text(json.dumps([s['sid'] for s in samples],indent=2),encoding='utf-8')
    print(f'Prepared {len(selected)} DRAFT image/label pairs and {len(samples)} review overlays in {output}')


if __name__ == '__main__':
    main()
