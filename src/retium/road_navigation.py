"""Compare candidate routes with reported closures; never claim complete coverage."""
from __future__ import annotations

import math
from .road_disruptions import SOURCES, valid_point

CELL = .01
MAX_CELLS = 100_000
MAX_COMPARISONS = 2_000_000


def validate_routes(body):
    if not isinstance(body, dict) or set(body) - {"routes", "cached_only"} or type(body.get("cached_only", False)) is not bool:
        raise ValueError("Send candidate routes and an optional cached_only flag")
    routes = body.get("routes")
    if not isinstance(routes, list) or not 1 <= len(routes) <= 4:
        raise ValueError("Send one to four route candidates")
    for route in routes:
        if not isinstance(route, list) or not 2 <= len(route) <= 20000 or not all(valid_point(p) and len(p) == 2 for p in route):
            raise ValueError("Invalid candidate route geometry")
    return routes


def cells(a, b, margin=0):
    west, east = math.floor((min(a[0], b[0])-margin)/CELL), math.floor((max(a[0], b[0])+margin)/CELL)
    south, north = math.floor((min(a[1], b[1])-margin)/CELL), math.floor((max(a[1], b[1])+margin)/CELL)
    if (east-west+1)*(north-south+1) > MAX_CELLS:
        raise ValueError("Route is too large for closure comparison")
    for x in range(west, east+1):
        for y in range(south, north+1):
            yield x, y


def follows_segment(a, b, c, d):
    """Nearby aligned overlap, not just a crossing (possibly an overpass)."""
    scale = 111320 * max(.01, math.cos(math.radians((a[1]+b[1]+c[1]+d[1])/4)))
    xy = lambda p: ((p[0]-a[0])*scale, (p[1]-a[1])*111320)
    bx, by = xy(b); cx, cy = xy(c); dx, dy = xy(d)
    length = math.hypot(bx, by); road_length = math.hypot(dx-cx, dy-cy)
    if length < .1 or road_length < .1:
        return False
    cosine = abs((bx*(dx-cx)+by*(dy-cy))/(length*road_length))
    if cosine < .866:  # Within 30 degrees; either direction, provider geometry is not consistently oriented.
        return False
    ux, uy = bx/length, by/length
    start, end = sorted((cx*ux+cy*uy, dx*ux+dy*uy))
    if min(length, end)-max(0, start) < min(5, length/2, road_length/2):
        return False
    # Check separation in the overlapping interval, avoiding nearby extensions.
    along = (max(0, start)+min(length, end))/2
    projection_c, projection_d = cx*ux+cy*uy, dx*ux+dy*uy
    fraction = (along-projection_c)/(projection_d-projection_c)
    return abs((cx+fraction*(dx-cx))*uy-(cy+fraction*(dy-cy))*ux) <= 20


def route_closures(route, features):
    index, entries = {}, 0
    for a, b in zip(route, route[1:]):
        if abs(a[0]-b[0]) > 180 or abs(a[1]) > 80 or abs(b[1]) > 80:
            raise ValueError("Closure comparison is unavailable for this route")
        for cell in cells(a, b, .002):
            entries += 1
            if entries > MAX_CELLS:
                raise ValueError("Route is too large for closure comparison")
            index.setdefault(cell, []).append((a, b))
    found, comparisons = [], 0
    for feature in features:
        if feature["properties"].get("road_impact") != "closed":
            continue
        geometry = feature.get("geometry", {})
        lines = [geometry.get("coordinates", [])] if geometry.get("type") == "LineString" else geometry.get("coordinates", []) if geometry.get("type") == "MultiLineString" else []
        matched = False
        for line in lines:
            for c, d in zip(line, line[1:]):
                for cell in cells(c, d):
                    for a, b in index.get(cell, []):
                        comparisons += 1
                        if comparisons > MAX_COMPARISONS:
                            raise ValueError("Route is too large for closure comparison")
                        if follows_segment(a, b, c, d):
                            matched = True
                            break
                    if matched: break
                if matched: break
            if matched: break
        if matched:
            p = feature["properties"]
            found.append({"id":feature["id"], "title":p.get("title"), "reason":p.get("reason"), "source":p.get("source"), "stale":bool(p.get("stale"))})
    return found


def assess_routes(roads, body):
    routes = validate_routes(body)
    if not roads.store.is_enabled("roads"):
        return {"selected":0, "status":"disabled", "warning":"Road closure checks are off. Enable Road closures & disruptions in Map layers."}
    points = [point for route in routes for point in route]
    west, east = min(p[0] for p in points), max(p[0] for p in points)
    south, north = min(p[1] for p in points), max(p[1] for p in points)
    if east-west > 180 or south < -80 or north > 80:
        return {"selected":0, "status":"unavailable", "warning":"Road closure checks are unavailable for this route."}
    data = roads.load((max(-180,west-.002), max(-90,south-.002), min(180,east+.002), min(90,north+.002)), 10,
                      cached_only=body.get("cached_only", False))
    if data["status"] in {"error", "uncovered", "disabled"}:
        return {"selected":0, "status":data["status"], "warning":"Road closure data is unavailable for this route. Conditions have not been checked."}
    try:
        matches = [route_closures(route, data["features"]) for route in routes]
    except ValueError as exc:
        return {"selected":0, "status":"unavailable", "warning":str(exc)}
    # Preserve router ordering among candidates with the same number of matches.
    selected = min(range(len(routes)), key=lambda i: len(matches[i]))
    covered = all(any(source["bounds"][0] <= p[0] <= source["bounds"][2] and source["bounds"][1] <= p[1] <= source["bounds"][3] for source in SOURCES.values()) for p in routes[selected])
    count = len(matches[selected])
    warning = f"{count} reported closure{'s' if count != 1 else ''} on this route. No supplied alternative avoids them all." if count else ""
    notes = []
    if selected:
        notes.append("Alternative chosen to reduce reported closures." if count else "Alternative chosen around reported closure segments.")
    if data["status"] == "stale":
        notes.append("Closure data is stale; current conditions are unconfirmed.")
        warning = " ".join(filter(None, [warning, notes[-1]]))
    if not covered:
        notes.append("Parts of this route have no connected road feed.")
        warning = " ".join(filter(None, [warning, notes[-1]]))
    notes.append("Road reports are incomplete; lane, direction and access details still need checking.")
    if not roads.store.is_enabled("roads"):
        return {"selected":0, "status":"disabled", "warning":"Road closure checks were switched off."}
    return {"selected":selected, "status":"closures" if count else "checked", "warning":warning,
            "note":" ".join(notes), "closures":matches[selected][:10], "counts":[len(m) for m in matches]}
