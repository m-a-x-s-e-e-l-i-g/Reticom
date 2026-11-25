# International LiDAR elevation layer

The optional **National LiDAR elevation** layer is available under **Map layers → Map & terrain**. Reticom selects a public high-resolution raster from the current map area:

- **Netherlands:** AHN, 0.5 m DTM or DSM from PDOK.
- **England:** Environment Agency, 1 m DTM or last-return DSM.
- **France:** IGN LiDAR HD terrain and surface hillshades from 0.5 m source grids where published.
- **Malta:** Planning Authority, 1 m DTM and DSM from the 2018 Maltese Islands survey.
- **United States:** USGS 3DEP bare-earth DTM, 1 m where available. This provider has no DSM, so a DSM selection falls back to DTM.

The source is selected from the map center. When no supported national source covers the current area, Reticom reports that high-resolution LiDAR is unavailable there and leaves the existing global contour layer available as broad terrain context.

Use **Bare-earth terrain (DTM)** to see the ground with buildings and vegetation removed. Use **Surface / buildings (DSM)** to see the measured surface, including buildings and vegetation. Source resolution varies by provider.

Reticom requests these online tiles when the layer is enabled and the map reaches the provider's minimum zoom. Reticom serves Malta's geographic tiles in Web Mercator through its local backend. The other providers supply Web Mercator directly.

This layer is visual context. It is not a live survey, a guaranteed vertical datum conversion, a safe route, or a substitute for local measurements. Coverage, capture dates and interpretation can vary. Keep the existing contour layer enabled separately when broad terrain context is more useful.

Sources and attribution. [PDOK / AHN](https://www.pdok.nl/introductie/-/article/actueel-hoogtebestand-nederland-ahn) · [Environment Agency / Defra](https://environment.data.gov.uk/dataset/13787b9a-26a4-4775-8523-806d13af58fc) · [IGN LiDAR HD](https://geoservices.ign.fr/lidarhd) · [Malta terrain](https://portal.data.gov.mt/dataset/digital-terrain-model-2018) · [Malta surface](https://portal.data.gov.mt/dataset/digital-surface-model-2018) · [USGS 3DEP](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services)

## Verification

Run `node --test tests/ahn-layer.test.mjs tests/intel-packs.test.mjs` and `python -m pytest tests/test_intel_packs.py`.

For manual verification, open Map layers in Command or Field, enable **National LiDAR elevation**, select each mode, and zoom to a supported area. Confirm that the map attribution changes to the selected national provider. Panning to an unsupported country must show the unavailable-area status and must not cause national LiDAR requests.
