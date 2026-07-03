import pygeohash as pgh

# 5-character geohash cells are ~4.9km x 4.9km. Good enough for a "nearby"
# feed at MVP scale. Cell-edge candidates just outside the queried prefix
# are missed; a proper radius search (e.g. geofirestore-style neighbor
# expansion) can replace this later without changing the stored data.
FEED_PRECISION = 5


def encode(lat: float, lng: float, precision: int = 7) -> str:
    return pgh.encode(lat, lng, precision=precision)


def feed_prefix(geohash: str) -> str:
    return geohash[:FEED_PRECISION]
