"""Build a portable walking + driving pack from a regional OSM PBF.

Run in a Python 3.12+ environment with pyvalhalla==3.8.3. Processing is local.
The input must cover the supplied bounds; include a generous surrounding buffer.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from retium.offline_routing import GRAPH_VERSION, OfflineRouting


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--bounds", type=float, nargs=4, required=True, metavar=("W", "S", "E", "N"))
    parser.add_argument("--source", required=True, help="OSM extract source and date")
    args = parser.parse_args()
    if importlib.metadata.version("pyvalhalla") != GRAPH_VERSION:
        parser.error("Install pyvalhalla==" + GRAPH_VERSION)
    if args.output.exists():
        parser.error("Output already exists; choose a new filename")
    from valhalla import get_config
    import valhalla
    binary = Path(valhalla.__file__).parent / "bin" / "valhalla_build_tiles"
    if not binary.exists():
        binary = binary.with_suffix(".exe")
    with tempfile.TemporaryDirectory(prefix="reticom-route-build-") as temporary:
        root = Path(temporary)
        tiles = root / "tiles"
        tiles.mkdir()
        config = get_config(tile_dir=tiles, tile_extract="")
        config["mjolnir"].update({"concurrency": 2, "include_pedestrian": True,
                                   "include_driveways": True, "include_driving": True,
                                   "use_admin_db": False, "use_timezone_db": False})
        path = root / "valhalla.json"
        path.write_text(json.dumps(config), "utf-8")
        subprocess.run([str(binary), "-c", str(path), str(args.pbf.resolve())], cwd=root, check=True)
        files = {}
        for file in sorted(tiles.rglob("*.gph")):
            with file.open("rb") as source:
                files["tiles/" + file.relative_to(tiles).as_posix()] = hashlib.file_digest(source, "sha256").hexdigest()
        manifest = {"format": "reticom-routing-v1", "valhalla_version": GRAPH_VERSION,
                    "id": args.id, "name": args.name, "bounds": args.bounds,
                    "profiles": ["walking", "driving"], "source": args.source,
                    "created_at": datetime.now(timezone.utc).isoformat(), "files": files}
        OfflineRouting._validate_manifest(manifest)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pending = args.output.with_suffix(".part")
        with zipfile.ZipFile(pending, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest))
            for name in files:
                bundle.write(root / name, name)
        pending.replace(args.output)
        print(f"Built {args.output}: {len(files)} routing tiles, {args.output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
