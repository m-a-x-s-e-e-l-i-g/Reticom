"""Geometry-only helpers for bounded, overlapping OSM area caches."""
import copy
import json
import math


def intersects(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def covers(bounds, rectangles):
    """Exact rectangle-union coverage, not the union's bounding box.

    Sweep vertical strips and merge their covered latitude intervals. A hole
    between two saved areas must never be advertised as a downloaded area.
    """
    w, s, e, n = bounds
    rectangles = [r for r in rectangles if intersects(r, bounds)]
    xs = sorted({w, e, *(max(w, min(e, x)) for r in rectangles for x in (r[0], r[2]))})
    for left, right in zip(xs, xs[1:]):
        middle = (left + right) / 2
        until = s
        for bottom, top in sorted((max(s, r[1]), min(n, r[3])) for r in rectangles if r[0] <= middle <= r[2]):
            if bottom > until + 1e-10:
                return False
            until = max(until, top)
        if until < n - 1e-10:
            return False
    return True


def buffered_bounds(bounds, max_area, max_span=2):
    """Add a 15% pan margin per side, retaining provider/world area limits."""
    w, s, e, n = bounds
    for margin in (.15, .075, .025, 0):
        dx, dy = (e-w)*margin, (n-s)*margin
        candidate = (max(-180, w-dx), max(-90, s-dy), min(180, e+dx), min(90, n+dy))
        cw, cs, ce, cn = candidate
        area = (ce-cw)*(cn-cs)*111.32**2*max(.01, math.cos(math.radians((cs+cn)/2)))
        if area <= max_area and ce-cw <= max_span and cn-cs <= max_span:
            return candidate
    return tuple(bounds)


def merge_entries(entries, bbox, credential_hash, feature_intersects, max_features):
    """Combine saved areas without dropping clipped hiking-route fragments.

    Caller supplies newest entries first, of one freshness class. A newer
    containing area supersedes older entries, including deleted objects.
    """
    selected, rectangles = [], []
    for entry in entries:
        if covers(entry["bounds"], rectangles):
            continue
        selected.append(entry)
        if not entry["data"].get("truncated"):
            rectangles.append(entry["bounds"])
        if covers(bbox, rectangles):
            break
    complete = covers(bbox, rectangles)
    merged, newer_areas = {}, []
    for entry in selected:
        for feature in entry["data"].get("features", []):
            if not feature_intersects(feature, bbox):
                continue
            # An empty newer result is authoritative inside its own area. Do
            # not resurrect an older marker there just because the older cache
            # also covers another, still-needed part of the viewport.
            extent = geometry_extent(feature.get("geometry", {}).get("coordinates", []))
            if extent and any(r[0] <= extent[0] and r[1] <= extent[1] and r[2] >= extent[2] and r[3] >= extent[3] for r in newer_areas):
                continue
            identity = feature.get("id", feature.get("properties", {}).get("id"))
            key = str(identity) if identity is not None else json.dumps(feature, sort_keys=True)
            previous = merged.get(key)
            if previous is None:
                merged[key] = copy.deepcopy(feature)
                continue
            # OSM ways/relations in hiking responses are clipped to each query.
            # Keep disconnected pieces; never connect gaps with invented lines.
            old, new = previous.get("geometry", {}), feature.get("geometry", {})
            if old.get("type") in {"LineString", "MultiLineString"} and new.get("type") in {"LineString", "MultiLineString"}:
                parts = old["coordinates"] if old["type"] == "MultiLineString" else [old["coordinates"]]
                additions = new["coordinates"] if new["type"] == "MultiLineString" else [new["coordinates"]]
                seen = {json.dumps(part) for part in parts}
                for part in additions:
                    encoded = json.dumps(part)
                    if encoded not in seen:
                        parts = [*parts, part]
                        seen.add(encoded)
                if len(parts) > 1:
                    previous["geometry"] = {"type": "MultiLineString", "coordinates": parts}
        if not entry["data"].get("truncated"):
            newer_areas.append(entry["bounds"])
    data = {**selected[0]["data"], "features": list(merged.values())[:max_features],
            "coverage_complete": complete, "cache_areas": len(selected),
            "truncated": len(merged) > max_features or any(e["data"].get("truncated") for e in selected)}
    if not complete:
        data["note"] = (data.get("note", "") + " Only part of this view is saved, or a saved result was limited. Missing areas are not known to be empty.").strip()
    return {"bounds": list(bbox), "credential_hash": credential_hash,
            "fetched_at": min(e["fetched_at"] for e in selected),
            "pinned": bool(selected) and all(entry.get("pinned") for entry in selected), "data": data}


def geometry_extent(coordinates):
    if not isinstance(coordinates, list) or not coordinates:
        return None
    if len(coordinates) >= 2 and all(isinstance(n, (int, float)) for n in coordinates[:2]):
        return (*coordinates[:2], *coordinates[:2])
    parts = [extent for part in coordinates if (extent := geometry_extent(part)) is not None]
    if not parts:
        return None
    return min(p[0] for p in parts), min(p[1] for p in parts), max(p[2] for p in parts), max(p[3] for p in parts)
