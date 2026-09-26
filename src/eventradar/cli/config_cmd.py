"""`eventradar config` commands."""

import typer

from eventradar.cli.common import DEFAULT_CONFIG, ConfigDir, load
from eventradar.sources.registry import build_source

app = typer.Typer(no_args_is_help=True)


@app.command("validate")
def validate(config_dir: ConfigDir = DEFAULT_CONFIG) -> None:
    """
    Validate every config file, reference, and adapter parameter.

    Parameters:
      config_dir: Config directory.
    """
    bundle = load(config_dir)
    problems = []
    for source in bundle.sources.values():
        try:
            build_source(source)
        except ValueError as exc:
            problems.append(f"source '{source.id}': {exc}")
    for problem in problems:
        typer.echo(f"config error: {problem}", err=True)
    if problems:
        raise typer.Exit(2)
    typer.echo(
        f"ok: {len(bundle.sources)} sources, {len(bundle.profiles)} profiles"
    )
