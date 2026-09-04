from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw


SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def generate_icon(path: Path) -> None:
    scale = 4
    size = 256 * scale
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (24 * scale, 24 * scale, 232 * scale, 232 * scale),
        radius=44 * scale,
        fill=(2, 7, 6, 255),
        outline=(72, 224, 159, 255),
        width=8 * scale,
    )
    center = 128 * scale
    for radius, alpha, width in ((72, 72, 5), (48, 112, 6)):
        draw.ellipse(
            (center - radius * scale, center - radius * scale, center + radius * scale, center + radius * scale),
            outline=(72, 224, 159, alpha),
            width=width * scale,
        )
    draw.line((center, 52 * scale, center, 204 * scale), fill=(72, 224, 159, 190), width=5 * scale)
    draw.line((52 * scale, center, 204 * scale, center), fill=(72, 224, 159, 190), width=5 * scale)
    draw.ellipse((111 * scale, 111 * scale, 145 * scale, 145 * scale), fill=(255, 107, 82, 255))
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def generate_version_info(path: Path, version: tuple[int, int, int]) -> None:
    major, minor, patch = version
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'Reticom'),
         StringStruct('FileDescription', 'Reticom Command Center'),
         StringStruct('FileVersion', '{major}.{minor}.{patch}'),
         StringStruct('InternalName', 'Reticom-Command'),
         StringStruct('LegalCopyright', 'Copyright 2026 m-a-x-s-e-e-l-i-g'),
         StringStruct('OriginalFilename', 'Reticom-Command.exe'),
         StringStruct('ProductName', 'Reticom Command'),
         StringStruct('ProductVersion', '{major}.{minor}.{patch}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path.write_text(content, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2 or not (match := SEMVER.fullmatch(sys.argv[1])):
        raise SystemExit("usage: generate_windows_assets.py MAJOR.MINOR.PATCH")
    version = tuple(int(value) for value in match.groups())
    output = Path(__file__).resolve().parents[1] / "build" / "windows"
    output.mkdir(parents=True, exist_ok=True)
    generate_icon(output / "reticom.ico")
    generate_version_info(output / "version-info.txt", version)


if __name__ == "__main__":
    main()
