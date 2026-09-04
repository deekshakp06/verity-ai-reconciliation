"""
logger.py
---------
One place to configure logging for the whole project. Replaces scattered
print() statements with proper leveled logging (INFO / WARNING / ERROR),
written both to console and to a log file so a run can be inspected after
the fact — the same principle as the audit trail, applied to the system's
own operational behavior, not just its business decisions.
"""

import logging
import os
from config import LOG_DIR, APP_LOG_PATH

os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:  # avoid duplicate handlers if called multiple times
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(APP_LOG_PATH)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
