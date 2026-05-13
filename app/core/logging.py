import logging

import structlog
from structlog.types import Processor

from app.core.config import settings


def configure_logging() -> None:
    """Configure structlog once at application startup.

    Processor chain (in order):
      add_log_level -> add_logger_name -> TimeStamper(iso) -> StackInfoRenderer
      -> merge_contextvars (injects request_id et al.) -> EventRenamer
      -> JSONRenderer (prod) | ConsoleRenderer (dev)
    """
    if settings.app_env != "development":
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    processors: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.contextvars.merge_contextvars,
        structlog.processors.EventRenamer("message"),
        renderer,
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# Convenience re-export so callers can do:  from app.core.logging import get_logger
get_logger = structlog.get_logger
