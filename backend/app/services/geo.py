import math

import pygeohash as pgh

# Minimum geohash cell dimension (km) per precision. Used to pick a query
# precision coarse enough that the searcher's cell plus its 8 neighbors
# fully cover their distance preference, whichever edge of the cell they
# sit on.
_CELL_MIN_KM = {1: 5000.0, 2: 625.0, 3: 156.0, 4: 19.5, 5: 4.9, 6: 0.61}

EARTH_RADIUS_KM = 6371.0


def encode(lat: float, lng: float, precision: int = 7) -> str:
    return pgh.encode(lat, lng, precision=precision)


def precision_for_radius(radius_km: float) -> int:
    """Finest precision whose cell (plus neighbors) still covers radius_km."""
    for precision in sorted(_CELL_MIN_KM, reverse=True):
        if _CELL_MIN_KM[precision] >= radius_km:
            return precision
    return 1


def cover_prefixes(geohash: str, radius_km: float) -> list[str]:
    """Geohash prefixes (center cell + 8 neighbors) covering a search radius.

    Cells at the poles/antimeridian can lack a neighbor; those are skipped.
    """
    center = geohash[: precision_for_radius(radius_km)]
    cells = {center}
    for vertical in (None, "top", "bottom"):
        try:
            row = pgh.get_adjacent(center, vertical) if vertical else center
            cells.add(row)
            cells.add(pgh.get_adjacent(row, "left"))
            cells.add(pgh.get_adjacent(row, "right"))
        except Exception:
            continue
    return sorted(cells)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))
