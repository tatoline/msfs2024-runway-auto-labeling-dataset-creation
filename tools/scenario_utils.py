"""Distance-stratified scenario selection used by the review tool."""
from collections import defaultdict

def select_balanced(pool, count):
    """Allocate across airports, then proportionally across hours; span distance."""
    by_airport = defaultdict(list)
    for row in pool:
        by_airport[row['icao']].append(row)
    if count > len(pool) or count <= 0:
        raise ValueError('Not enough unique scenarios for the requested batch size')
    quotas = {key: 0 for key in sorted(by_airport)}
    for _ in range(count):
        key = min((k for k in quotas if quotas[k] < len(by_airport[k])),
                  key=lambda k: (quotas[k], k))
        quotas[key] += 1
    selected = []
    for airport, target in quotas.items():
        groups = defaultdict(list)
        for row in by_airport[airport]:
            groups[(row['hour'], row['minute'])].append(row)
        exact = {key: target * len(rows) / len(by_airport[airport]) for key, rows in groups.items()}
        allocated = {key: int(value) for key, value in exact.items()}
        for key in sorted(groups, key=lambda k: (-(exact[k] - allocated[k]), k))[:target-sum(allocated.values())]:
            allocated[key] += 1
        for key, rows in sorted(groups.items()):
            rows.sort(key=lambda r: (r['gd'], r['sid']))
            n = allocated[key]
            if n == 1:
                selected.append(rows[len(rows)//2])
            elif n:
                selected.extend(rows[round(i*(len(rows)-1)/(n-1))] for i in range(n))
    return sorted(selected, key=lambda r: (r['icao'], r['gd'], r['sid']))


import math
M_LAT = 111132.0
def m_lon(lat): return 111320.0 * math.cos(math.radians(lat))

def runway_corners(g, width_m):
    """4 corners (lat, lon, alt) from the two thresholds + width.

    Order: threshold-right, threshold-left, far-left, far-right, looking along
    the landing direction.
    """
    t, o = g["landing_threshold"], g["opposite_threshold"]
    lat0 = (t["latitude"] + o["latitude"]) / 2.0
    dE = (o["longitude"] - t["longitude"]) * m_lon(lat0)
    dN = (o["latitude"] - t["latitude"]) * M_LAT
    L = math.hypot(dE, dN)
    aE, aN = dE / L, dN / L          # along the runway
    pE, pN = aN, -aE                 # across it
    hw = width_m / 2.0
    pts = []
    for base in (t, o):
        for s in (+1, -1):
            lat = base["latitude"] + (s * hw * pN) / M_LAT
            lon = base["longitude"] + (s * hw * pE) / m_lon(lat0)
            pts.append([round(lat, 8), round(lon, 8),
                        round(base["altitude_msl_m"], 3)])
    return [pts[0], pts[1], pts[3], pts[2]]
