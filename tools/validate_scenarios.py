"""Check converted scenario files or a flat capture batch without starting MSFS."""
import argparse
import json
import math
from pathlib import Path
import re


def number(value, name, lower=-math.inf, upper=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if not lower <= value <= upper:
        raise ValueError(f'{name} must be between {lower} and {upper}')
    return value


def validate_flat(row):
    if not isinstance(row, dict):
        raise ValueError('each scenario must be an object')
    sid = row['sid']
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', sid):
        raise ValueError('sid must be a safe, nonempty filename stem')
    if not re.fullmatch(r'[A-Z0-9]{4}', row['icao']):
        raise ValueError('icao must contain four uppercase letters/digits')
    for key in ('airport', 'runway', 'weather'):
        if not isinstance(row[key], str) or not row[key].strip():
            raise ValueError(f'{key} must be nonempty text')
    for key, lo, hi in [('lat',-90,90),('lon',-180,180),('alt_m',-1000,30000),
                         ('alt_ft',-3281,100000),('heading_true',0,360),
                         ('landing_course_true_deg',0,360),('roll',-180,180),
                         ('gd',0,1e7),('ad',-30000,30000),('glide_angle_deg',0,90),
                         ('runway_width_m',1,300),('runway_length_m',1,10000)]:
        number(row[key], key, lo, hi)
    for key, hi in [('hour',23),('minute',59)]:
        number(row[key],key,0,hi)
        if int(row[key]) != row[key]:
            raise ValueError(f'{key} must be an integer')
    if abs(row['alt_ft'] - row['alt_m'] * 3.28084) > 1:
        raise ValueError('alt_m and alt_ft differ by more than one foot')
    corners = row['corners']
    if not isinstance(corners,list) or len(corners)!=4:
        raise ValueError('corners must contain four [latitude, longitude, altitude_m] points')
    for point in corners:
        if not isinstance(point,list) or len(point)!=3:
            raise ValueError('each world corner must contain three numbers')
        for val, name, lo, hi in zip(point,['latitude','longitude','altitude'],[-90,-180,-1000],[90,180,30000]):
            number(val,name,lo,hi)
    if len({tuple(p) for p in corners}) != 4:
        raise ValueError('world corners must be distinct')
    return row


def converted_to_flat(data, sid):
    from scenario_utils import runway_corners
    rw,g=data['runway_data'],data['geometry']
    p,o=data['position_of_aircraft'],data['aircraft_orientation']
    distance=data['distance_aircraft_to_aim_point']
    for key in ['landing_threshold','opposite_threshold']:
        for field in ['latitude','longitude','altitude_msl_m']:
            number(g[key][field],f'geometry.{key}.{field}')
    if g['landing_threshold']==g['opposite_threshold']:
        raise ValueError('landing and opposite thresholds must differ')
    row=dict(sid=sid,icao=rw['icao_code'],airport=rw['name'],runway=rw['runway_name'],
             weather=data['weather'],hour=data['daytime']['hours'],minute=data['daytime']['minutes'],
             gd=distance['ground_distance_m'],ad=distance['altitude_difference_m'],
             lat=p['latitude'],lon=p['longitude'],alt_m=p['altitude_msl_m'],
             alt_ft=p['altitude_msl_m']*3.28084,heading_true=o['yaw_true_deg'],
             glide_angle_deg=abs(o['pitch_deg']),roll=o['roll_deg'],
             runway_length_m=rw['runway_length_m'],runway_width_m=rw['runway_width_m'],
             landing_course_true_deg=g['landing_course_true_deg'],
             corners=runway_corners(g,rw['runway_width_m']))
    return validate_flat(row)


def read_scenarios(path):
    path=Path(path)
    files=sorted(path.glob('*.json')) if path.is_dir() else [path]
    if not files:
        raise ValueError('no JSON scenarios found')
    rows=[]
    for file in files:
        value=json.loads(file.read_text(encoding='utf-8-sig'))
        if isinstance(value,list):
            rows.extend(validate_flat(row) for row in value)
        elif 'runway_data' in value:
            rows.append(converted_to_flat(value,file.stem))
        else:
            rows.append(validate_flat(value))
    ids=[r['sid'] for r in rows]
    if len(ids)!=len(set(ids)):
        raise ValueError('duplicate scenario IDs')
    return rows


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input',help='converted scenario directory, a converted JSON, or flat batch JSON')
    args=ap.parse_args()
    try:
        rows=read_scenarios(args.input)
    except (OSError,ValueError,KeyError,TypeError,ZeroDivisionError) as error:
        ap.error(str(error))
    print(f'PASS: {len(rows)} unique scenarios; airports: {", ".join(sorted({r["icao"] for r in rows}))}')
    print('Weather groups: '+', '.join(sorted({r['weather'] for r in rows})))
    print('This validates input structure and units, not rendered runway alignment.')


if __name__=='__main__':
    main()
