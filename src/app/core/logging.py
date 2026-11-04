"""Centralized logging configuration for structured and standardized application logs."""

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings


def setup_logging(settings: "Settings") -> logging.Logger:
    """Configure system-wide logging based on active environment settings.

    Args:
        settings: Application settings instance containing log_level and environment.

    Returns:
        Configured root application logger.
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Standard format with ISO-style timestamps and execution coordinates
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # Silence noisy external library logs unless in full debug mode
    if not settings.debug:
        for noisy_logger in ("httpx", "httpcore", "urllib3", "asyncio", "celery.worker.strategy"):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    logger = logging.getLogger("app")
    logger.setLevel(log_level)
    logger.info(
        "Logging initialized for environment=%s at level=%s",
        settings.environment,
        settings.log_level,
    )
    return logger
