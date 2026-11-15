"""Velxio desktop backend entry point.

Replaces docker/entrypoint.sh for the Windows build:

    secret key  -> %APPDATA%\\Velxio\\.secret_key (secrets.token_urlsafe(48))
    data        -> %APPDATA%\\Velxio
    logs        -> %LOCALAPPDATA%\\Velxio\\logs\\backend.log
    server      -> uvicorn on 127.0.0.1:<--port>
    lifecycle   -> exits when stdin reaches EOF (the Tauri shell closes the
                   pipe) or when the Windows Job Object is closed.

No nginx, no process supervision shell. The Tauri shell owns the process.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import secrets
import sys
import threading
from pathlib import Path

from app.core import paths


def _bootstrap_paths() -> tuple[Path, Path]:
    data = paths.data_dir()
    logs = paths.log_dir()
    data.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("VELXIO_DATA_DIR", str(data))
    os.environ.setdefault("VELXIO_LOG_DIR", str(logs))
    os.environ.setdefault("VELXIO_BUILD_ROOT", str(paths.build_dir()))
    os.environ.setdefault("VELXIO_ARTIFACT_CACHE", str(paths.artifact_cache_dir()))
    os.environ.setdefault("VELXIO_LIBCACHE_DIR", str(paths.libcache_dir()))
    # The OSS backend is stateless today. Point a future/pro overlay's DB at
    # the same per-user directory so all state lands in APPDATA\Velxio.
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(data / 'velxio.db').as_posix()}")
    # No product-news fetch: the desktop build must work with zero network.
    os.environ.setdefault("VELXIO_NEWS", "off")
    # The bundle has no arduino-cli; never discover a system-installed one
    # (its startup core sync would reach downloads.arduino.cc).
    os.environ.setdefault("VELXIO_ARDUINO_CLI_DISABLED", "1")
    return data, logs


def _bootstrap_secret(data: Path) -> None:
    if os.environ.get("SECRET_KEY"):
        return
    secret_file = data / ".secret_key"
    key = ""
    if secret_file.is_file():
        try:
            key = secret_file.read_text(encoding="utf-8").strip()
        except OSError:
            key = ""
    if not key:
        key = secrets.token_urlsafe(48)
        secret_file.write_text(key, encoding="utf-8")
        try:
            os.chmod(secret_file, 0o600)
        except OSError:
            pass
    os.environ["SECRET_KEY"] = key


def _bootstrap_logging(logs: Path) -> None:
    log_file = logs / "backend.log"
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if sys.stderr is not None and sys.stderr.isatty():
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        root.addHandler(stream)


def _watch_stdin() -> bool:
    """Return True when stdin reached EOF (parent closed the pipe).

    A windowed (PyInstaller --noconsole) process may not have a valid fd 0
    when launched without piped stdio; in that case return False and rely on
    the shell's explicit kill / Job Object instead of exiting immediately.
    """
    try:
        os.fstat(0)
    except OSError:
        return False
    try:
        while True:
            chunk = os.read(0, 1)
            if not chunk:
                break
    except OSError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Velxio backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("VELXIO_PORT", "8001")))
    args = parser.parse_args()

    data, logs = _bootstrap_paths()
    _bootstrap_secret(data)
    _bootstrap_logging(logs)
    paths.ensure_runtime_dirs()

    logger = logging.getLogger("velxio.launcher")
    logger.info("Velxio backend starting on %s:%s (data=%s, logs=%s)",
                args.host, args.port, data, logs)

    import uvicorn

    from app.main import app

    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
    )
    server = uvicorn.Server(config)

    def _watch() -> None:
        if _watch_stdin():
            logger.info("stdin closed — shutting down")
            server.should_exit = True

    threading.Thread(target=_watch, name="stdin-watch", daemon=True).start()

    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("interrupted")
    finally:
        logger.info("Velxio backend stopped")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        import multiprocessing

        multiprocessing.freeze_support()
    raise SystemExit(main())
