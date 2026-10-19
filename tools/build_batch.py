"""Select a single-weather capture batch from already-converted scenarios."""
import argparse
import json
from pathlib import Path
from scenario_utils import select_balanced
from validate_scenarios import read_scenarios


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--src',required=True,help='converted scenario directory or flat JSON list')
    ap.add_argument('--output',required=True,help='new batch JSON path')
    ap.add_argument('--weather',required=True)
    ap.add_argument('--icao',nargs='+')
    ap.add_argument('--hour',nargs='+',type=int)
    ap.add_argument('--min-dist',type=float,default=300)
    ap.add_argument('--max-dist',type=float,default=1e9)
    ap.add_argument('--count',type=int,help='omit to include all matching scenarios')
    args=ap.parse_args()
    output=Path(args.output)
    if output.exists(): ap.error('output exists; choose a new batch filename')
    if args.min_dist<0 or args.max_dist<args.min_dist: ap.error('invalid distance limits')
    try:
        pool=[r for r in read_scenarios(args.src) if r['weather']==args.weather
              and (not args.icao or r['icao'] in args.icao)
              and (not args.hour or r['hour'] in args.hour)
              and args.min_dist<=r['gd']<=args.max_dist]
        if not pool: raise ValueError('no scenarios match the filters')
        rows=select_balanced(pool,args.count) if args.count is not None else pool
    except (OSError,ValueError,KeyError,TypeError) as error: ap.error(str(error))
    rows.sort(key=lambda r:(r['icao'],r['hour'],r['minute'],r['gd'],r['sid']))
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
    print(f'Prepared {len(rows)} {args.weather} scenarios -> {output}')


if __name__=='__main__': main()
