"""`eventradar source` commands."""

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer

from eventradar.cli.common import DEFAULT_CONFIG, ConfigDir, load
from eventradar.config.schema import ConfigBundle, SourceConfig
from eventradar.http import HttpClient
from eventradar.http.client import ResponseHook
from eventradar.http.redact import redact_url
from eventradar.sources.base import SourceContext
from eventradar.sources.registry import build_source

app = typer.Typer(no_args_is_help=True)


def _get(bundle: ConfigBundle, source_id: str) -> SourceConfig:
    """
    Look up a source or exit.

    Parameters:
      bundle: Loaded config.
      source_id: Source id.
    Returns:
      The source config.
    """
    if source_id not in bundle.sources:
        typer.echo(f"unknown source: {source_id}", err=True)
        raise typer.Exit(2)
    return bundle.sources[source_id]


@app.command("list")
def list_sources(config_dir: ConfigDir = DEFAULT_CONFIG) -> None:
    """
    List configured sources.

    Parameters:
      config_dir: Config directory.
    """
    bundle = load(config_dir)
    for sid, src in sorted(bundle.sources.items()):
        state = "enabled" if src.enabled else "disabled"
        typer.echo(f"{sid}\t{src.adapter}\t{state}")


async def _test(
    bundle: ConfigBundle,
    config: SourceConfig,
    hook: ResponseHook | None = None,
) -> dict[str, object]:
    """
    Fetch and parse one source without touching state.

    Parameters:
      bundle: Loaded config.
      config: Source to test.
      hook: Optional response callback for recording.
    Returns:
      Summary with counts and a sample of parsed titles.
    """
    source = build_source(config)
    now = datetime.now(UTC)
    async with HttpClient(bundle.settings.http, on_response=hook) as http:
        records = await source.fetch(SourceContext(http=http, now=now))
    errors: Counter[str] = Counter()
    titles = []
    for raw in records:
        try:
            titles.extend(d.title for d in source.parse(raw))
        except Exception as exc:
            errors[type(exc).__name__] += 1
    return {
        "source_id": config.id,
        "records": len(records),
        "parsed": len(titles),
        "parse_errors": dict(errors),
        "sample": titles[:5],
    }


@app.command("test")
def test_source(source_id: str, config_dir: ConfigDir = DEFAULT_CONFIG) -> None:
    """
    Fetch and parse one source live; prints a summary, stores nothing.

    Parameters:
      source_id: Source id.
      config_dir: Config directory.
    """
    bundle = load(config_dir)
    summary = asyncio.run(_test(bundle, _get(bundle, source_id)))
    typer.echo(json.dumps(summary, indent=2))
    if not summary["parsed"]:
        raise typer.Exit(1)


@app.command("record")
def record_source(
    source_id: str,
    config_dir: ConfigDir = DEFAULT_CONFIG,
    out: Annotated[
        Path | None,
        typer.Option(help="Output dir; default tests/fixtures/<source_id>."),
    ] = None,
) -> None:
    """
    Fetch one source live and save its HTTP responses as fixtures.

    Parameters:
      source_id: Source id.
      config_dir: Config directory.
      out: Output directory.
    """
    bundle = load(config_dir)
    target = out or Path("tests/fixtures") / source_id
    target.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, object]] = []

    def _save(response: httpx.Response) -> None:
        """
        Write one response body and index its metadata.

        Parameters:
          response: Final response for a request.
        """
        name = f"response-{len(index):03d}.body"
        (target / name).write_bytes(response.content)
        index.append(
            {
                "url": redact_url(str(response.request.url)),
                "status": response.status_code,
                "content_type": response.headers.get("content-type"),
                "body": name,
            }
        )

    summary = asyncio.run(_test(bundle, _get(bundle, source_id), _save))
    (target / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    typer.echo(json.dumps({**summary, "fixtures": str(target)}, indent=2))
