import logging

from agentic_workflow_for_literature_synthesis.core.logging import (
    configure_logging,
    get_logger,
)


def test_configure_logging_sets_requested_level() -> None:
    configure_logging("DEBUG")

    logger = logging.getLogger("literature_synthesis")

    assert logger.level == logging.DEBUG


def test_get_logger_uses_application_namespace() -> None:
    logger = get_logger("agents.search")

    assert logger.name == "literature_synthesis.agents.search"
