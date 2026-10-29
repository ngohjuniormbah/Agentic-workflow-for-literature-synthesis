import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure application-wide logging."""

    application_logger = logging.getLogger("literature_synthesis")
    application_logger.setLevel(level.upper())
    application_logger.propagate = False

    if not application_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
        )
        application_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger within the application namespace."""

    return logging.getLogger(f"literature_synthesis.{name}")
