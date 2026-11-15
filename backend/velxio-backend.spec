# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the Velxio Windows desktop backend.

Layout produced:

    backend/dist/velxio-backend/
        velxio-backend.exe
        _internal/
            app/…                 backend Python modules
            sdk/velxio-chip.h    custom-chip header
            scripts/…            backend helper scripts
            frontend_dist/…      Vite build served by FastAPI StaticFiles
            <dependency data>    every wheel's non-Python payload

The Tauri shell ships that whole directory as resources/backend and launches
velxio-backend.exe with --port. No Python is required on the target machine.
"""

import re
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

ROOT = Path(SPECPATH).resolve()          # backend/
REPO = ROOT.parent

# ── requirement → import package(s) ─────────────────────────────────────────
MODULES = {
    "fastapi": ["fastapi"],
    "uvicorn": ["uvicorn"],
    "websockets": ["websockets"],
    "pydantic": ["pydantic"],
    "pydantic-settings": ["pydantic_settings"],
    "httpx": ["httpx"],
    "mcp": ["mcp"],
    "esptool": ["esptool"],
    "wasmtime": ["wasmtime"],
    "zstandard": ["zstandard"],
}

DIST_NAMES = list(MODULES)


def _requirement_names() -> list[str]:
    names: list[str] = []
    req = ROOT / "requirements.txt"
    if not req.is_file():
        return DIST_NAMES
    for raw in req.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;\[\s]", line, 1)[0].strip()
        if name:
            names.append(name)
    return names


datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []

# Every package in requirements.txt: non-Python data files, native libraries
# and importlib metadata (some libraries read their own distribution metadata
# at runtime). collect_* are best-effort: a wheel without payload is fine.
for dist in _requirement_names():
    for module in MODULES.get(dist, [dist.replace("-", "_")]):
        try:
            datas += collect_data_files(module)
        except Exception:
            pass
        try:
            binaries += collect_dynamic_libs(module)
        except Exception:
            pass
        try:
            hiddenimports += collect_submodules(module)
        except Exception:
            pass
    try:
        datas += copy_metadata(dist, recursive=True)
    except Exception:
        pass

# Application payload (not importable as packages on their own).
datas += [
    (str(ROOT / "sdk"), "sdk"),
    (str(ROOT / "scripts"), "scripts"),
]
_template = ROOT / "app" / "services" / "esp-idf-template"
if _template.is_dir():
    datas.append((str(_template), "app/services/esp-idf-template"))

# Frontend SPA served by FastAPI's StaticFiles mount.
_frontend = REPO / "frontend" / "dist"
if (_frontend / "index.html").is_file():
    datas.append((str(_frontend), "frontend_dist"))
else:
    print("WARNING: frontend/dist missing — run `npm run build:desktop` first")

hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "websockets.legacy",
    "websockets.legacy.server",
    "websockets.legacy.client",
    "mcp.server.fastmcp",
    "esptool",
    "wasmtime",
    "zstandard",
    "pydantic_settings",
]

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "pytest",
        "IPython",
        "notebook",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="velxio-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="velxio-backend",
)
