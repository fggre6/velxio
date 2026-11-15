"""Windows desktop path resolution for the bundled Velxio backend.

The Docker image used fixed Linux paths (/app/data, /var/lib/velxio-build, …).
The desktop build keeps every mutable piece of state in the per-user
profile instead:

    %APPDATA%\\Velxio            data   (secret key, databases, caches)
    %LOCALAPPDATA%\\Velxio\\logs  logs   (backend + desktop debug logs)

Environment overrides win so a portable/test install can point elsewhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


def _roaming_base() -> Path:
    raw = os.environ.get("APPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Roaming"


def _local_base() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def data_dir() -> Path:
    """%APPDATA%\\Velxio — secret key, databases, persistent caches."""
    return _env_path("VELXIO_DATA_DIR") or (_roaming_base() / "Velxio")


def log_dir() -> Path:
    """%LOCALAPPDATA%\\Velxio\\logs — never roams, safe to delete."""
    return _env_path("VELXIO_LOG_DIR") or (_local_base() / "Velxio" / "logs")


def cache_dir() -> Path:
    """%LOCALAPPDATA%\\Velxio\\cache — disposable compiler/ccache data."""
    return _env_path("VELXIO_CACHE_DIR") or (_local_base() / "Velxio" / "cache")


def build_dir() -> Path:
    """Persistent ESP-IDF / compile build root."""
    return _env_path("VELXIO_BUILD_ROOT") or (data_dir() / "build")


def artifact_cache_dir() -> Path:
    return _env_path("VELXIO_ARTIFACT_CACHE") or (build_dir() / "artifacts")


def libcache_dir() -> Path:
    return _env_path("VELXIO_LIBCACHE_DIR") or (data_dir() / "libcache")


def frontend_dist_dir() -> Path | None:
    """Locate the bundled frontend `dist` directory.

    PyInstaller puts data files under sys._MEIPASS (onedir: `<app>/_internal`).
    During development the Vite output lives at `<repo>/frontend/dist`.
    """
    explicit = _env_path("VELXIO_FRONTEND_DIST")
    if explicit and (explicit / "index.html").is_file():
        return explicit

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "frontend_dist"
        if (bundled / "index.html").is_file():
            return bundled

    here = Path(__file__).resolve()
    # backend/app/core/paths.py -> repo root
    repo_dist = here.parents[3] / "frontend" / "dist"
    if (repo_dist / "index.html").is_file():
        return repo_dist
    return None


def sdk_include_dir() -> Path | None:
    """Directory containing velxio-chip.h (backend/sdk in dev, sdk/ in bundle)."""
    explicit = _env_path("VELXIO_SDK_DIR")
    if explicit and (explicit / "velxio-chip.h").is_file():
        return explicit

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "sdk"
        if (bundled / "velxio-chip.h").is_file():
            return bundled

    here = Path(__file__).resolve()
    # backend/app/core/paths.py -> backend/sdk
    backend_sdk = here.parents[2] / "sdk"
    if (backend_sdk / "velxio-chip.h").is_file():
        return backend_sdk
    return None


def ensure_runtime_dirs() -> None:
    """Create the directories the backend writes to at startup."""
    for path in (data_dir(), log_dir(), build_dir(), artifact_cache_dir()):
        path.mkdir(parents=True, exist_ok=True)
