"""Adapt Malta Planning Authority's LiDAR services to Web Mercator tiles."""

import io
import math
import threading
from functools import lru_cache
from urllib.parse import urlencode

from PIL import Image

from .intel_sources import ProviderError, fetch_bytes


MALTA_WMS_ROOT = "https://malta.coverage.wetransform.eu/wms/"
MALTA_WCS_URL = "https://malta.coverage.wetransform.eu/dtm_1m_2018/ows"
MALTA_LAYERS = {"dtm": "EL.GridCoverage.Malta", "dsm": "EL.GridCoverage.DSM2018"}
MALTA_BOUNDS = (13.92, 35.59, 14.83, 36.30)
TILE_SIZE = 256
MALTA_CONTOUR_MIN_ZOOM = 15
MALTA_CONTOUR_MAX_ZOOM = 17
MALTA_UTM_BOUNDS = (425918, 3959779, 461967, 3993828)
_terrain_downloads = threading.BoundedSemaphore(4)


def _web_tile_bounds(z, x, y):
    scale = 1 << z

    def latitude(row):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + row / TILE_SIZE) / scale))))

    return x / scale * 360 - 180, latitude(TILE_SIZE), (x + 1) / scale * 360 - 180, latitude(0)


def _utm33(longitude, latitude):
    # WGS84 transverse Mercator, zone 33N. Malta lies within one degree of its central meridian.
    lat = math.radians(latitude)
    delta = math.radians(longitude - 15)
    sine, cosine = math.sin(lat), math.cos(lat)
    t = math.tan(lat) ** 2
    eccentricity = 0.00669437999014
    c = eccentricity / (1 - eccentricity) * cosine ** 2
    n = 6378137 / math.sqrt(1 - eccentricity * sine ** 2)
    a = cosine * delta
    meridian = 6378137 * (0.998324298453 * lat - 0.002514607061 * math.sin(2 * lat)
                         + 0.000002639047 * math.sin(4 * lat) - 0.000000003418 * math.sin(6 * lat))
    east = 500000 + 0.9996 * n * (a + (1 - t + c) * a ** 3 / 6)
    north = 0.9996 * (meridian + n * math.tan(lat) * (a ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * a ** 4 / 24))
    return east, north


def terrarium_png(image):
    rgb = bytearray()
    for height in image.getdata():
        value = int((height + 32768) * 256)
        rgb.extend((value >> 16, (value >> 8) & 255, value & 255))
    output = io.BytesIO()
    Image.frombytes("RGB", image.size, bytes(rgb)).save(output, format="PNG")
    return output.getvalue()


@lru_cache(maxsize=256)
def malta_terrain_tile(z, x, y):
    if not MALTA_CONTOUR_MIN_ZOOM <= z <= MALTA_CONTOUR_MAX_ZOOM:
        return None
    west, south, east, north = _web_tile_bounds(z, x, y)
    left, bottom, right, top = MALTA_BOUNDS
    if east <= left or west >= right or north <= bottom or south >= top:
        return None
    corners = [_utm33(lon, lat) for lon, lat in [(west, north), (west, south), (east, south), (east, north)]]
    es, ns = zip(*corners)
    left, bottom, right, top = MALTA_UTM_BOUNDS
    e0, e1 = max(left, math.floor(min(es)) - 8), min(right, math.ceil(max(es)) + 8)
    n0, n1 = max(bottom, math.floor(min(ns)) - 8), min(top, math.ceil(max(ns)) + 8)
    if e1 <= e0 or n1 <= n0:
        return None
    query = urlencode({"service": "WCS", "request": "GetCoverage", "version": "2.0.1", "coverageId": "dtm_1m_2018",
                       "format": "image/tiff", "subset": [f"E({e0},{e1})", f"N({n0},{n1})"],
                       "scaleSize": "E(288),N(288)"}, doseq=True)
    with _terrain_downloads:
        raw = fetch_bytes(f"{MALTA_WCS_URL}?{query}", timeout=5, max_bytes=400_000)
    with Image.open(io.BytesIO(raw), formats=("TIFF",)) as tile:
        if tile.mode != "L" or tile.size != (288, 288):
            raise ProviderError("Unexpected Malta terrain format")
        scale, tie = tile.tag_v2.get(33550), tile.tag_v2.get(33922)
        if not scale or len(scale) != 3 or min(scale[:2]) <= 0 or not tie or len(tie) != 6:
            raise ProviderError("Malta terrain has no usable georeferencing")
        dx, dy, _ = scale
        i, j, _, origin_e, origin_n, _ = tie
        quad = tuple(value for e, n in corners for value in ((e - origin_e) / dx + i, (origin_n - n) / dy + j))
        ground = tile.convert("F").transform((TILE_SIZE, TILE_SIZE), Image.Transform.QUAD, quad, Image.Resampling.BILINEAR)
    return terrarium_png(ground)


def overzoom_terrain(data, levels, x, y):
    with Image.open(io.BytesIO(data), formats=("PNG",)) as tile:
        heights = Image.new("F", tile.size)
        heights.putdata([r * 256 + g + b / 256 - 32768 for r, g, b in tile.convert("RGB").getdata()])
    size = TILE_SIZE >> levels
    left, top = (x % (1 << levels)) * size, (y % (1 << levels)) * size
    return terrarium_png(heights.transform((TILE_SIZE, TILE_SIZE), Image.Transform.EXTENT,
                                            (left, top, left + size, top + size), Image.Resampling.BILINEAR))


def malta_tile(mode, z, x, y):
    if mode not in MALTA_LAYERS or not 12 <= z <= 18 or not (0 <= x < 1 << z and 0 <= y < 1 << z):
        raise ValueError("Invalid Malta LiDAR tile")
    scale = 1 << z

    def latitude(row):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + row / TILE_SIZE) / scale))))

    west, east = x / scale * 360 - 180, (x + 1) / scale * 360 - 180
    north, south = latitude(0), latitude(TILE_SIZE)
    left, bottom, right, top = MALTA_BOUNDS
    if east <= left or west >= right or north <= bottom or south >= top:
        raise ValueError("Tile is outside Malta LiDAR coverage")
    query = urlencode({"service": "WMS", "request": "GetMap", "version": "1.3.0", "layers": MALTA_LAYERS[mode],
                       "styles": "", "format": "image/png", "transparent": "true", "width": TILE_SIZE, "height": TILE_SIZE,
                       "crs": "CRS:84", "bbox": f"{west},{south},{east},{north}"})
    raw = fetch_bytes(f"{MALTA_WMS_ROOT}{mode}_1m_2018/ows?{query}", timeout=12, max_bytes=1_000_000)
    with Image.open(io.BytesIO(raw), formats=("PNG",)) as response:
        if response.size != (TILE_SIZE, TILE_SIZE):
            raise OSError("Unexpected Malta LiDAR tile dimensions")
        image = response.convert("RGBA")
    edges = [TILE_SIZE * (north - latitude(row)) / (north - south) for row in range(0, TILE_SIZE + 1, 16)]
    mesh = [((0, row, TILE_SIZE, row+16), (0, upper, 0, lower, TILE_SIZE, lower, TILE_SIZE, upper))
            for row, upper, lower in zip(range(0, TILE_SIZE, 16), edges, edges[1:])]
    # Each latitude strip maps to the corresponding Mercator tile rows.
    result = image.transform(image.size, Image.Transform.MESH, mesh, Image.Resampling.BILINEAR)
    output = io.BytesIO()
    result.save(output, format="PNG")
    return output.getvalue()
