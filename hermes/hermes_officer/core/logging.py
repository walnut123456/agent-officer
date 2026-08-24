from __future__ import annotations

import sys
from loguru import logger

from .config import AppSettings

def configure_logging(settings: AppSettings) -> None:
    """Configure bounded console and file sinks once per process."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
        enqueue=True,
    )
    if settings.log_file_enabled:
        log_path = settings.log_path.expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_path,
            level=settings.log_level,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
            rotation="100 MB",
            retention="14 days",
            compression="zip",
            enqueue=True,
        )
