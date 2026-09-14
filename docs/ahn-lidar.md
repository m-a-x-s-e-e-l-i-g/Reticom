# International LiDAR elevation layer

The optional **National LiDAR elevation** layer is available under **Map layers → Map & terrain**. Reticom selects a public high-resolution raster from the current map area:

- **Netherlands:** AHN, 0.5 m DTM or DSM from PDOK.
- **England:** Environment Agency, 1 m DTM or last-return DSM.
- **United States:** USGS 3DEP bare-earth DTM, 1 m where available. This provider has no DSM, so a DSM selection falls back to DTM.

The source is selected from the map center. When no supported national source covers the current area, Reticom reports that high-resolution LiDAR is unavailable there and leaves the existing global contour layer available as broad terrain context.

Use **Bare-earth terrain (DTM)** to see ground height with buildings, trees and other non-ground objects removed. Use **Surface / buildings (DSM)** to see the measured surface, including buildings and vegetation. The source raster resolution is 0.5 m, but the rendered map image and the view scale do not make every visible feature survey-grade.

These are airborne laser-altimetry products, not satellite LiDAR. Reticom requests tiles only when the layer is enabled and the map is zoomed to the provider's close-zoom threshold. Each map source is bounded to its provider's coverage and uses the public WMS endpoint directly; it is online-only and is not included in offline map downloads or team data.

This layer is visual context. It is not a live survey, a guaranteed vertical datum conversion, a safe route, or a substitute for local measurements. Coverage, capture dates and interpretation can vary. Keep the existing contour layer enabled separately when broad terrain context is more useful.

Sources and attribution: [PDOK / AHN](https://www.pdok.nl/introductie/-/article/actueel-hoogtebestand-nederland-ahn), [Environment Agency / Defra](https://environment.data.gov.uk/dataset/13787b9a-26a4-4775-8523-806d13af58fc), and [USGS 3DEP](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services).

## Verification

Run `node --test tests/ahn-layer.test.mjs tests/intel-packs.test.mjs` and `python -m pytest tests/test_intel_packs.py`.

For manual verification, open Map layers in Command or Field, enable **National LiDAR elevation**, select each mode, and zoom to a supported area. Confirm that the map attribution changes to the selected national provider. Panning to an unsupported country must show the unavailable-area status and must not cause national LiDAR requests.
