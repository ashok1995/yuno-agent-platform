import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import settings

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent


def resolve_log_file() -> Path:
    if settings.LOG_FILE_PATH:
        return Path(settings.LOG_FILE_PATH).expanduser().resolve()
    return PROJECT_ROOT / "logs" / "backend.log"


def setup_logging() -> None:
    """Configure yuno.* loggers to write INFO+ to logs/backend.log and stderr."""
    log_file = resolve_log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    level_name = (settings.LOG_LEVEL or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)

    for logger_name in ("yuno", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.handlers.clear()
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        logger.propagate = False

    logging.getLogger("yuno.runtime").info(
        "Logging initialized — file=%s level=%s",
        log_file,
        level_name,
    )
