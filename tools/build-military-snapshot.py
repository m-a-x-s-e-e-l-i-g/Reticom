"""Build the optional local Reticom snapshot from a published Overture release."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time

from retium.intel_sources import _feature, _osm_kind


def build(release, output):
    import duckdb

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="military-snapshot-", dir=output.parent) as work:
        temporary = Path(work) / output.name
        with duckdb.connect() as source, closing(sqlite3.connect(temporary)) as target, target:
            source.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2'")
            target.executescript("""
                PRAGMA user_version = 1;
                CREATE TABLE snapshot (release TEXT, built_at INTEGER);
                CREATE TABLE features (id TEXT PRIMARY KEY, west REAL, south REAL, east REAL, north REAL,
                                       size REAL, feature TEXT);
                CREATE INDEX feature_west ON features(west);
            """)
            for theme, layer, category_column, predicate in (
                ("base", "land_use", "class", "subtype = 'military'"),
                ("base", "infrastructure", "class", "subtype = 'airport' AND (class LIKE '%airport' OR class IN ('airstrip', 'runway'))"),
                ("places", "place", "basic_category", "basic_category = 'police_station'"),
            ):
                url = f"s3://overturemaps-us-west-2/release/{release}/theme={theme}/type={layer}/*.parquet"
                rows = source.execute(f"""
                    SELECT id, {category_column}, names.primary, bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax,
                           COALESCE(TRY(ST_AsGeoJSON(ST_SimplifyPreserveTopology(geometry, 0.00002))), TRY(ST_AsGeoJSON(geometry)))
                    FROM read_parquet(?)
                    WHERE {predicate} AND geometry IS NOT NULL AND bbox.xmin IS NOT NULL
                """, [url])
                count = 0
                while batch := rows.fetchmany(1000):
                    features = []
                    for identifier, category, name, west, south, east, north, geometry in batch:
                        if None in (west, south, east, north, geometry) or not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
                            continue
                        if layer == "place":
                            tags = {"amenity": "police"}
                        elif layer == "land_use":
                            tags = {"landuse": "military", "military": category}
                        elif category == "military_airport":
                            tags = {"military": "airfield"}
                        else:
                            tags = {"aeroway": category if category in {"airstrip", "runway"} else "aerodrome"}
                        kind, label = _osm_kind(tags, "military")
                        feature = _feature("military", identifier, json.loads(geometry), name or label, label, kind,
                                           source="Overture Maps Places" if layer == "place" else "Overture Maps / OpenStreetMap",
                                           source_url="https://docs.overturemaps.org/attribution/",
                                           attribution="Overture Maps Places" if layer == "place" else "Overture Maps · © OpenStreetMap contributors · ODbL")
                        features.append((identifier, west, south, east, north, (east-west)*(north-south),
                                         json.dumps(feature, separators=(",", ":"))))
                    target.executemany("INSERT OR IGNORE INTO features VALUES (?, ?, ?, ?, ?, ?, ?)", features)
                    count += len(features)
                if not count:
                    raise ValueError(f"No {layer} features found in Overture {release}")
                print(f"{layer}: {count:,} features", flush=True)
            target.execute("INSERT INTO snapshot VALUES (?, ?)", (release, int(time.time())))
        temporary.replace(output)
        print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", help="Overture release, e.g. 2026-11-18.0")
    parser.add_argument("output", type=Path, help="Path to intel-packs/military-world.sqlite3")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", args.release):
        parser.error("use an Overture release such as 2026-11-18.0")
    build(args.release, args.output)
