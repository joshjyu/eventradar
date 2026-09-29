"""`eventradar health` commands."""

import typer

from eventradar.cli.common import (
    DEFAULT_CONFIG,
    DEFAULT_ENV,
    ConfigDir,
    EnvName,
    load,
)
from eventradar.pipeline.runner import enable_source, source_health

app = typer.Typer(no_args_is_help=True)


@app.command("status")
def status(
    config_dir: ConfigDir = DEFAULT_CONFIG, env: EnvName = DEFAULT_ENV
) -> None:
    """
    Show each source's health.

    Parameters:
      config_dir: Config directory.
      env: Environment name.
    """
    bundle = load(config_dir)
    for state in source_health(bundle, env):
        detail = f" ({state.reason})" if state.reason else ""
        typer.echo(
            f"{state.source_id}\t{state.status}\t"
            f"unhealthy_runs={state.unhealthy_runs}{detail}"
        )


@app.command("enable")
def enable(
    source_id: str,
    config_dir: ConfigDir = DEFAULT_CONFIG,
    env: EnvName = DEFAULT_ENV,
) -> None:
    """
    Re-enable a source disabled by health checks.

    Parameters:
      source_id: Source to re-enable.
      config_dir: Config directory.
      env: Environment name.
    """
    bundle = load(config_dir)
    if source_id not in bundle.sources:
        typer.echo(f"unknown source: {source_id}", err=True)
        raise typer.Exit(2)
    state = enable_source(bundle, env, source_id)
    typer.echo(f"{state.source_id}\t{state.status}")
