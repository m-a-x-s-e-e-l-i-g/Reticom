"""Real engine verification against the upstream Liechtenstein OSM test extract.

Run this in a network-isolated process to verify calculation, not a service call.
The pack must be built with build-routing-pack.py from Valhalla 3.8.3 test/data/
liechtenstein-latest.osm.pbf. This is historical test data, not a navigation pack.
"""
import json
from pathlib import Path
import sys
import tempfile

from retium.offline_routing import OfflineRouting, RoutingError

with tempfile.TemporaryDirectory(prefix="reticom-offline-check-") as temporary:
    store = OfflineRouting(Path(temporary))
    store.install(Path(sys.argv[1]))
    distances = []
    for profile in ("walking", "driving"):
        for origin in ([9.5215, 47.1396], [9.523, 47.141]):
            result = store.route(origin, [9.5095, 47.1661], profile)
            route = result["routes"][0]
            assert result["source"] == "offline"
            assert route["distance"] > 1000 and len(route["geometry"]["coordinates"]) > 10
            assert route["legs"][0]["steps"]
            distances.append(route["distance"])
            print(json.dumps({"profile": profile, "distance_m": route["distance"],
                              "duration_s": route["duration"], "points": len(route["geometry"]["coordinates"])}))
    assert distances[0] != distances[2], "Walking and driving should choose different paths"
    try:
        store.route([9.5215, 47.1396], [4, 51], "walking")
        raise AssertionError("Must reject destination outside pack")
    except RoutingError as error:
        assert error.code == "outside_coverage"
    finally:
        store.engine.close()
