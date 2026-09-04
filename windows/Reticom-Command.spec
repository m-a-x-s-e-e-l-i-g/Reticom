import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


project_root = Path(SPECPATH).parent
static_dir = project_root / "src" / "retium" / "static"
generated_dir = project_root / "build" / "windows"

datas = [(str(static_dir), "retium/static")]
datas += collect_data_files("webview")
datas += copy_metadata("pywebview")
# RNS discovers interface names by globbing its package directory at runtime.
# Preserve those small source files so the same discovery works when frozen.
datas += collect_data_files(
    "RNS",
    include_py_files=True,
    includes=["Interfaces/*.py", "Interfaces/util/*.py"],
)

hiddenimports = collect_submodules("RNS.Interfaces")
hiddenimports += [
    "RNS.Interfaces.AutoInterface",
    "RNS.Interfaces.AX25KISSInterface",
    "RNS.Interfaces.BackboneInterface",
    "RNS.Interfaces.I2PInterface",
    "RNS.Interfaces.Interface",
    "RNS.Interfaces.KISSInterface",
    "RNS.Interfaces.LocalInterface",
    "RNS.Interfaces.PipeInterface",
    "RNS.Interfaces.RNodeInterface",
    "RNS.Interfaces.RNodeMultiInterface",
    "RNS.Interfaces.SerialInterface",
    "RNS.Interfaces.TCPInterface",
    "RNS.Interfaces.UDPInterface",
    "RNS.Interfaces.WeaveInterface",
    "PIL.Image",
    "qrcode.image.svg",
    "uvicorn.lifespan.on",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "zxingcpp",
]

a = Analysis(
    [str(project_root / "src" / "retium" / "desktop.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "cefpython3",
        "faster_whisper",
        "onnxruntime",
        "torch",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Reticom-Command",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(generated_dir / "reticom.ico"),
    version=str(generated_dir / "version-info.txt"),
)
