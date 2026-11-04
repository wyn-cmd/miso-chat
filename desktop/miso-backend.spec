# PyInstaller spec for the Miso Chat desktop backend.
# Build it with desktop/build_backend.py on the OS you are shipping: PyInstaller cannot cross-compile.
#
# The bundle holds the server code, the web pages and VERSION. Characters are not included unless
# MISO_BUNDLE_CHARACTERS=1 is set before the build, which is for a personal build only.

import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)

datas = [
    (os.path.join(ROOT, "web", "static"), os.path.join("web", "static")),
    (os.path.join(ROOT, "VERSION"), "."),
]

characters = os.path.join(ROOT, "characters")
if os.environ.get("MISO_BUNDLE_CHARACTERS") == "1" and os.path.isdir(characters):
    datas.append((characters, "characters"))

hiddenimports = collect_submodules("uvicorn") + collect_submodules("google.genai") + [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

a = Analysis(
    [os.path.join(ROOT, "desktop", "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="miso-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="miso-backend",
)
