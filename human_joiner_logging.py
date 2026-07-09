from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(app_name: str, *, log_dir: Path | str = DEFAULT_LOG_DIR) -> Path:
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = path / f"{app_name}_{timestamp}.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(file_handler)

    logging.captureWarnings(True)
    logging.getLogger(__name__).info("Logging started: %s", log_path)
    return log_path
