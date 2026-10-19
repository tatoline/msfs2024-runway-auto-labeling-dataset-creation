"""Add synthetic rain and retain camera metadata with explicit source/output provenance."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from augment_synthetic_rain import PRESETS, augment, write_augmented_manifest
from validate_dataset import validate


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-dir',required=True,help='complete Camera API capture')
    ap.add_argument('--output-dir',required=True)
    ap.add_argument('--preset',choices=sorted(PRESETS),default='heavy')
    ap.add_argument('--seed',type=int,default=20260903)
    ap.add_argument('--angle',type=float,default=10)
    args=ap.parse_args();source=Path(args.source_dir).resolve();out=Path(args.output_dir).resolve()
    if out.exists(): ap.error('output exists; use a fresh directory')
    if out.is_relative_to(source): ap.error('output must be outside the source capture')
    result=validate(source)
    if result['kind']!='capture': ap.error('use a complete capture, before label review/export')
    with (source/'manifest.csv').open(newline='',encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
    (out/'images').mkdir(parents=True);(out/'telemetry').mkdir()
    records=[]
    for index,row in enumerate(rows,1):
        src=source/row['image_file'];destination=out/'images'/src.name
        record=augment(src,destination,preset_name=args.preset,master_seed=args.seed,angle_deg=args.angle)
        records.append(record)
        telemetry=json.loads((source/row['telemetry_file']).read_text(encoding='utf-8'))
        # Keep camera observations exactly as recorded. Bind the derivative image
        # separately and retain the original image/telemetry as the source evidence.
        telemetry['source_image_sha256']=record['source_sha256']
        telemetry['image_sha256']=record['output_sha256']
        telemetry['synthetic_appearance']={'effect':'synthetic_heavy_rain_v1','seed':record['image_seed'],
            'preset':args.preset,'geometry_changed':False,'native_precipitation':False,
            'note':'Camera observations belong to the unchanged source geometry; pixels have synthetic rain.'}
        target=out/row['telemetry_file'];target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(telemetry,indent=2)+'\n',encoding='utf-8')
        print(f'[{index}/{len(rows)}] {row["scenario_id"]}')
    for name in ['captured_batch.json','runway_facilities.json']:
        if (source/name).is_file(): shutil.copy2(source/name,out/name)
    run=json.loads((source/'capture_run.json').read_text(encoding='utf-8'))
    run.update(appearance='synthetic_heavy_rain_v1',source_capture=str(source),
               derivative_created_utc=datetime.now(timezone.utc).isoformat())
    (out/'capture_run.json').write_text(json.dumps(run,indent=2)+'\n',encoding='utf-8')
    write_augmented_manifest(source,out,records)
    audit={'schema':'synthetic_weather_augmentation/v1','effect':'synthetic_heavy_rain_v1',
           'source_directory':str(source),'preset':args.preset,'master_seed':args.seed,
           'angle_from_vertical_deg':args.angle,'parameters':PRESETS[args.preset],
           'geometry_changed':False,'native_msfs_precipitation_particles':False,'images':records}
    (out/'augmentation_audit.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(validate(out),indent=2))
    print('Generate fresh labels and review this derived capture. Source files are preserved.')


if __name__=='__main__': main()
