"""Top-level Typer application and pipeline commands."""

import asyncio
import json
from typing import Annotated

import typer

from eventradar.cli import config_cmd, source_cmd
from eventradar.cli.common import (
    DEFAULT_CONFIG,
    DEFAULT_ENV,
    ConfigDir,
    EnvName,
    load,
    setup_logging,
)
from eventradar.pipeline.runner import RunOptions, replay, run

app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(config_cmd.app, name="config", help="Inspect configuration.")
app.add_typer(source_cmd.app, name="source", help="Work with sources.")


@app.callback()
def _main() -> None:
    """Config-driven discovery and publishing of regional events."""
    setup_logging()


@app.command("run")
def run_cmd(
    config_dir: ConfigDir = DEFAULT_CONFIG,
    env: EnvName = DEFAULT_ENV,
    profile: Annotated[
        list[str] | None,
        typer.Option("--profile", help="Limit to profile id (repeatable)."),
    ] = None,
    publish: Annotated[
        bool, typer.Option("--publish/--no-publish", help="Upload outputs.")
    ] = True,
) -> None:
    """
    Fetch, normalize, and publish once.

    Parameters:
      config_dir: Config directory.
      env: Environment name.
      profile: Profiles to publish; all when omitted.
      publish: Whether to upload outputs.
    """
    bundle = load(config_dir)
    options = RunOptions(
        env=env,
        publish=publish,
        profile_ids=tuple(profile) if profile else None,
    )
    summary = asyncio.run(run(bundle, options))
    report = {
        "run_id": summary.run_id,
        "status": summary.status,
        "profiles": summary.profiles,
        "merged": summary.merged,
        "enrich": summary.enrich.__dict__ if summary.enrich else None,
        "sources": {
            r.source_id: {
                "status": r.status,
                "fetched": r.fetched,
                "changed": r.changed,
                "parsed": r.parsed,
                "parse_errors": r.parse_errors,
            }
            for r in summary.sources
        },
    }
    typer.echo(json.dumps(report, indent=2))
    if summary.status == "failed":
        raise typer.Exit(1)


@app.command("replay")
def replay_cmd(
    config_dir: ConfigDir = DEFAULT_CONFIG,
    env: EnvName = DEFAULT_ENV,
    source: Annotated[
        list[str] | None,
        typer.Option("--source", help="Limit to source id (repeatable)."),
    ] = None,
) -> None:
    """
    Reparse stored raw records without network access.

    Parameters:
      config_dir: Config directory.
      env: Environment name.
      source: Sources to replay; all when omitted.
    """
    bundle = load(config_dir)
    unknown = [s for s in source or [] if s not in bundle.sources]
    if unknown:
        typer.echo(f"unknown source(s): {', '.join(unknown)}", err=True)
        raise typer.Exit(2)
    written = replay(bundle, env, tuple(source) if source else None)
    typer.echo(json.dumps(written, indent=2))
