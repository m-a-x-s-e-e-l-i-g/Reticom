# Satellite thermal layers

The optional **Satellite land-surface temperature** layer is available under **Map layers → Map & terrain**. It uses NASA's public Global Imagery Browse Services (GIBS) Web Mercator tiles and works worldwide where the satellite product has valid observations.

Choose a satellite pass from the layer control:

- **MODIS Terra / Aqua** daytime and nighttime land-surface temperature.
- **VIIRS NOAA-20** daytime and nighttime land-surface temperature.

These are satellite observations of the land surface, not air temperature, body temperature or a live thermal-camera feed. Cloud cover, acquisition timing, gaps between overpasses and the source's native resolution can leave areas blank or make nearby pixels differ. Reticom does not interpolate missing observations or present them as exact ground measurements.

The layer is online-only and is not included in offline map downloads or signed team data. It requests only the visible map tiles from NASA GIBS; the selected pass is saved locally. The raster is capped at the provider's global tile matrix and is intentionally shown as broad thermal context rather than high-resolution inspection imagery.

NASA FIRMS remains a separate **Satellite heat detections** pack. FIRMS points are thermal anomalies and should not be confused with the continuous land-surface temperature raster or treated as confirmed fires.

Sources and attribution: [NASA GIBS access documentation](https://nasa-gibs.github.io/gibs-api-docs/access-basics/), [NASA MODIS land-surface temperature](https://modis.gsfc.nasa.gov/data/dataprod/mod11.php), [NASA GIBS layer catalogue](https://gibs.earthdata.nasa.gov/layer-metadata/v1.0/).

## Verification

Run `node --test tests/thermal-layer.test.mjs tests/intel-packs.test.mjs` and `python -m pytest tests/test_intel_packs.py`.

For manual verification, open **Map layers**, enable **Satellite land-surface temperature**, switch between the six pass options and confirm the attribution changes only to NASA Earthdata / MODIS / VIIRS. Toggle the layer off and confirm that its raster source is removed without moving the map camera.
