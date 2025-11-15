"""Configuration loading utilities for worker management."""

import logging
from typing import Any, Optional

from clx.infrastructure.config import WorkersManagementConfig, get_config

logger = logging.getLogger(__name__)


def load_worker_config(
    cli_overrides: Optional[dict[str, Any]] = None
) -> WorkersManagementConfig:
    """Load worker configuration from all sources with CLI overrides.

    Configuration is loaded in priority order (highest to lowest):
    1. CLI options (passed in cli_overrides)
    2. Environment variables
    3. Project config file
    4. User config file
    5. System config file
    6. Defaults

    Args:
        cli_overrides: Dictionary of CLI option overrides. Expected keys:
            - workers: Execution mode ('direct' or 'docker')
            - worker_count: Default worker count
            - notebook_workers: Notebook worker count
            - plantuml_workers: PlantUML worker count
            - drawio_workers: Draw.io worker count
            - no_auto_start: Disable auto-start
            - no_auto_stop: Disable auto-stop
            - fresh_workers: Don't reuse existing workers

    Returns:
        WorkersManagementConfig with CLI overrides applied
    """
    cli_overrides = cli_overrides or {}

    # Load base config from files + env (handled by ClxConfig)
    config = get_config().worker_management

    # Apply CLI overrides to global settings
    if cli_overrides.get("workers"):
        config.default_execution_mode = cli_overrides["workers"]
        logger.info(
            f"CLI override: default_execution_mode = {config.default_execution_mode}"
        )

    if cli_overrides.get("worker_count") is not None:
        config.default_worker_count = cli_overrides["worker_count"]
        logger.info(f"CLI override: default_worker_count = {config.default_worker_count}")

    if cli_overrides.get("no_auto_start"):
        config.auto_start = False
        logger.info("CLI override: auto_start = False")

    if cli_overrides.get("no_auto_stop"):
        config.auto_stop = False
        logger.info("CLI override: auto_stop = False")

    if cli_overrides.get("fresh_workers"):
        config.reuse_workers = False
        logger.info("CLI override: reuse_workers = False")

    # Apply per-type overrides
    for worker_type in ["notebook", "plantuml", "drawio"]:
        cli_key = f"{worker_type}_workers"
        if cli_overrides.get(cli_key) is not None:
            type_config = getattr(config, worker_type)
            type_config.count = cli_overrides[cli_key]
            logger.info(f"CLI override: {worker_type}.count = {type_config.count}")

    return config
