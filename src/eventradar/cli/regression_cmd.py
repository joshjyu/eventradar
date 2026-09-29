"""`eventradar regression` commands."""

from pathlib import Path
from typing import Annotated

import typer

from eventradar.regression import capture_case

app = typer.Typer(no_args_is_help=True)


@app.command("capture")
def capture(
    capture_file: Annotated[
        Path, typer.Argument(help="Health capture JSON from a run artifact.")
    ],
    slug: Annotated[str, typer.Option(help="Kebab-case case name.")],
    out: Annotated[
        Path, typer.Option(help="Regression cases directory.")
    ] = Path("tests/regression"),
) -> None:
    """
    Scaffold a regression case from a health capture.

    Parameters:
      capture_file: Capture JSON.
      slug: Case name.
      out: Cases directory.
    """
    case = capture_case(capture_file, slug, out)
    typer.echo(f"wrote {case}; edit expected.json, then fix the adapter")
