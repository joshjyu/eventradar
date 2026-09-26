"""Options and helpers shared by CLI commands."""

import logging
import os
from pathlib import Path
from typing import Annotated

import typer

from eventradar.config import ConfigBundle, ConfigError, load_config

ConfigDir = Annotated[
    Path,
    typer.Option(
        "--config-dir",
        envvar="EVENTRADAR_CONFIG",
        help="Path to the config directory.",
    ),
]
EnvName = Annotated[
    str,
    typer.Option(
        "--env", envvar="EVENTRADAR_ENV", help="Environment from settings."
    ),
]
DEFAULT_CONFIG = Path("config")
DEFAULT_ENV = "local"


def setup_logging() -> None:
    """Configure logging; HTTP libraries stay quiet so URLs never leak."""
    logging.basicConfig(
        level=os.environ.get("EVENTRADAR_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Request URLs can carry API keys in query strings.
    for name in ("httpx", "httpcore", "botocore", "boto3", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def load(config_dir: Path) -> ConfigBundle:
    """
    Load config or exit with every problem listed.

    Parameters:
      config_dir: Config directory.
    Returns:
      The loaded bundle.
    """
    try:
        return load_config(config_dir)
    except ConfigError as exc:
        for problem in exc.problems:
            typer.echo(f"config error: {problem}", err=True)
        raise typer.Exit(2) from exc
