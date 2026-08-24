"""Compatibility entry point for the unified Python backend."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _use_project_virtualenv() -> None:
    """Make the convenient `py server.py` command use locked dependencies."""
    if os.getenv("HERMES_VENV_REEXEC") == "1":
        return
    executable_name = "python.exe" if os.name == "nt" else "python"
    executable_dir = "Scripts" if os.name == "nt" else "bin"
    project_python = PROJECT_ROOT / ".venv" / executable_dir / executable_name
    if project_python.is_file() and Path(sys.executable).resolve() != project_python.resolve():
        child_environment = os.environ.copy()
        child_environment["HERMES_VENV_REEXEC"] = "1"
        return_code = subprocess.call(
            [str(project_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            env=child_environment,
        )
        raise SystemExit(return_code)


_use_project_virtualenv()

import uvicorn  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")
for _path_key, _default_path in (("FILE_SAVE_PATH", "file_db_dir"),):
    _configured_path = Path(os.getenv(_path_key, _default_path)).expanduser()
    if not _configured_path.is_absolute():
        os.environ[_path_key] = str((PROJECT_ROOT / _configured_path).resolve())

from hermes_officer.app import create_app  # noqa: E402
from hermes_officer.core.config import AppSettings  # noqa: E402

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)

settings = AppSettings.from_env()
app = create_app(settings)
if settings.ui_enabled:
    from hermes_officer.web import mount_ui

    mount_ui(app, settings)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hermes 智维企业智能工作台")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--workers", type=int, default=settings.workers)
    return parser.parse_args()


if __name__ == "__main__":
    args = _arguments()
    workers = max(1, args.workers)
    if settings.ui_enabled and workers != 1:
        raise SystemExit("NiceGUI uses per-client server state; APP_WORKERS must be 1")
    # Embedded Qdrant owns an exclusive file lock. Uvicorn reload imports the app
    # in both the watcher and worker processes, so local vector persistence must
    # run as a single process. Remote Qdrant keeps the usual development reload.
    reload_enabled = settings.environment == "local" and workers == 1 and settings.qdrant_path is None
    target = "server:app" if reload_enabled or workers > 1 else app
    uvicorn.run(
        app=target,
        host=args.host,
        port=args.port,
        workers=workers,
        reload=reload_enabled,
        timeout_keep_alive=75,
    )
