import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler


def _log_namer(default_name: str) -> str:
    """
    Custom namer so the log file is: ProductSyncLog YYYY-MM-DD.log
    TimedRotatingFileHandler appends a date suffix like .2026-02-24 to the base name.
    We rearrange it into the desired pattern.
    """
    dirname = os.path.dirname(default_name)
    # default_name looks like: logs/ProductSyncLog.log.2026-02-24
    parts = default_name.rsplit(".", 1)
    if len(parts) == 2:
        date_suffix = parts[1]  # e.g. "2026-02-24"
        return os.path.join(dirname, f"ProductSyncLog {date_suffix}.log")
    return default_name


def _log_rotator(source: str, dest: str) -> None:
    """Custom rotator that renames the source file to the dest path."""
    if os.path.exists(source):
        if os.path.exists(dest):
            os.remove(dest)
        os.rename(source, dest)


def setup_logging(log_dir: str = "logs") -> None:
    """
    Configure root logger with:
    - TimedRotatingFileHandler (day-wise rotation at midnight)
    - StreamHandler for stdout (visible via `docker logs`)

    File naming: ProductSyncLog YYYY-MM-DD.log
    """
    os.makedirs(log_dir, exist_ok=True)

    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Clear existing handlers to avoid duplicates on reload
    root_logger.handlers.clear()

    # File handler - day-wise rotation
    base_log_path = os.path.join(log_dir, "ProductSyncLog.log")
    file_handler = TimedRotatingFileHandler(
        filename=base_log_path,
        when="midnight",
        interval=1,
        backupCount=90,  # Keep 90 days of logs
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.namer = _log_namer
    file_handler.rotator = _log_rotator
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root_logger.addHandler(file_handler)

    # Rename current active log file to include today's date
    # so it matches the pattern even before first rotation
    today_log = os.path.join(
        log_dir,
        f"ProductSyncLog {datetime.now().strftime('%Y-%m-%d')}.log",
    )
    if not os.path.exists(today_log) and os.path.exists(base_log_path):
        # Link the base file - it will be the active writing target
        pass  # The base file IS today's log; rotation creates dated copies

    # Stdout handler - for docker logs
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root_logger.addHandler(stream_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger for a module."""
    return logging.getLogger(name)
