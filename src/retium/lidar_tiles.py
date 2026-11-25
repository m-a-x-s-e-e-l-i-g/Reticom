"""Adapt Malta Planning Authority's CRS:84 WMS to Web Mercator tiles."""

import io
import math
from urllib.parse import urlencode

from PIL import Image

from .intel_sources import fetch_bytes


MALTA_WMS_ROOT = "https://malta.coverage.wetransform.eu/wms/"
MALTA_LAYERS = {"dtm": "EL.GridCoverage.Malta", "dsm": "EL.GridCoverage.DSM2018"}
MALTA_BOUNDS = (13.92, 35.59, 14.83, 36.30)
TILE_SIZE = 256


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
