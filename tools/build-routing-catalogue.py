"""Build release-ready .retiroute files and their catalogue; never publishes.

Example: --regions noord-brabant zeeland limburg --date 2026-09-06
Run with Python 3.12 + pyvalhalla 3.8.3 on Linux/WSL.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

from retium.offline_routing import OfflineRouting
from retium.routing_catalogue import validate_catalogue


def coordinates(value):
    if len(value) >= 2 and isinstance(value[0], (float, int)):
        yield value
    else:
        for child in value:
            yield from coordinates(child)


def build(args):
    stamp = date.fromisoformat(args.date)
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen("https://download.geofabrik.de/index-v1.json", timeout=60) as response:
        features = {f["properties"]["id"]: f for f in json.load(response)["features"]}
    entries = []
    for region in args.regions:
        feature = features[region]
        props = feature["properties"]
        points = list(coordinates(feature["geometry"]["coordinates"]))
        bounds = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
        source = props["urls"]["pbf"].replace("-latest.osm.pbf", f"-{stamp:%y%m%d}.osm.pbf")
        if not source.startswith("https://download.geofabrik.de/"):
            raise ValueError("Unexpected OSM provider")
        pbf = args.cache / f"{region}-{stamp}.osm.pbf"
        if not pbf.exists():
            temporary = pbf.with_suffix(".part")
            print(f"Downloading {props['name']} ({stamp})", flush=True)
            with urllib.request.urlopen(source, timeout=60) as response, temporary.open("wb") as output:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 2 * 1024**3:
                        raise ValueError("Source exceeds the 2 GB build limit; choose a smaller region")
                    output.write(chunk)
            temporary.replace(pbf)
        pack_id = f"{props.get('parent', 'world')}-{region}"
        filename = f"{pack_id}-{stamp}.retiroute"
        output = args.output / filename
        if not output.exists():
            subprocess.run([sys.executable, str(Path(__file__).with_name("build-routing-pack.py")),
                            str(pbf), str(output), "--id", pack_id, "--name", props["name"],
                            "--bounds", *map(str, bounds), "--source", source], check=True)
        with zipfile.ZipFile(output) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            OfflineRouting._validate_manifest(manifest)
            if manifest["id"] != pack_id or manifest["source"] != source or manifest["bounds"] != bounds:
                raise ValueError(f"Cached pack does not match {region}'s source and coverage")
            if set(bundle.namelist()) != set(manifest["files"]) | {"manifest.json"}:
                raise ValueError("Pack has unexpected or missing files")
            for name, expected in manifest["files"].items():
                with bundle.open(name) as file:
                    if hashlib.file_digest(file, "sha256").hexdigest() != expected:
                        raise ValueError(f"Corrupt graph tile: {name}")
            expanded = sum(e.file_size for e in bundle.infolist())
        with output.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        if output.stat().st_size > 512 * 1024**2 or expanded > 2 * 1024**3:
            raise ValueError(f"{region} exceeds phone storage limits; split it")
        entries.append({"id": manifest["id"], "name": props["name"],
                        "country": "Netherlands" if props.get("parent") == "netherlands" else props["name"],
                        "bounds": bounds, "profiles": ["walking", "driving"],
                        "data_date": str(stamp), "created_at": manifest["created_at"],
                        "bytes": output.stat().st_size, "expanded_bytes": expanded,
                        "sha256": digest, "source": source,
                        "url": f"https://github.com/{args.repository}/releases/download/{args.tag}/{filename}"})
        print(f"Ready: {props['name']} · {output.stat().st_size:,} bytes", flush=True)
    catalogue = {"format": "reticom-routing-catalogue-v1", "valhalla_version": "3.8.3", "published": False,
                 "generated_at": datetime.now(timezone.utc).isoformat(), "regions": entries}
    validate_catalogue(catalogue)
    output = args.output / "catalogue.json"
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(catalogue, indent=2), "utf-8")
    temporary.replace(output)
    print(f"Catalogue: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", nargs="+", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", default="m-a-x-s-e-e-l-i-g/Reticom")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    build(parser.parse_args())
