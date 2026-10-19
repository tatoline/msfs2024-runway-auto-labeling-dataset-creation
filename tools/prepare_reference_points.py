"""Create inputs for the saved-image Runway Reference Corners application."""
import argparse
import json
from pathlib import Path


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--labels-dir',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--scenario-ids',nargs='+')
    args=ap.parse_args();output=Path(args.output)
    if output.exists(): ap.error('output exists; choose a new references filename')
    report=json.loads((Path(args.labels_dir)/'label_report.json').read_text(encoding='utf-8'))
    rows=[r for r in report['images'] if r.get('status')=='candidate'
          and (not args.scenario_ids or r['scenario_id'] in args.scenario_ids)]
    if not rows: ap.error('no candidate references selected')
    output.parent.mkdir(parents=True,exist_ok=True)
    capture=Path(report['dataset'])
    if not capture.is_absolute(): capture=Path.cwd()/capture
    batch={r['sid']:r for r in json.loads((capture/'captured_batch.json').read_text(encoding='utf-8'))}
    references=[]
    for row in rows:
        scenario=batch[row['scenario_id']]
        distance=scenario['gd']
        group='near' if distance<1500 else 'middle' if distance<3500 else 'far'
        references.append({'scenario_id':row['scenario_id'],'image_file':row['image_file'],
                           'icao':scenario['icao'],'distance_group':group,'ground_distance_m':distance})
    output.write_text(json.dumps({'references':references},indent=2)+'\n',encoding='utf-8')
    print(f'Prepared {len(rows)} references -> {output}')


if __name__=='__main__': main()
