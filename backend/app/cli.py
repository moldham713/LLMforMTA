import json

import click
from flask import current_app
from flask.cli import AppGroup

from app.gtfs.loader import LoadError, load_gtfs
from app.stations.loader import load_stations

gtfs_cli = AppGroup("gtfs", help="Static GTFS feed.")
stations_cli = AppGroup("stations", help="MTA Subway Stations dataset.")


@gtfs_cli.command("load")
@click.option("--source", help="URL or local zip path. Defaults to GTFS_URL.")
@click.option("--force", is_flag=True, help="Reload even if unchanged; skip shrink check.")
def gtfs_load(source: str | None, force: bool) -> None:
    """Download and load the subway GTFS feed."""
    settings = current_app.extensions["settings"]
    try:
        result = load_gtfs(
            current_app.extensions["db_engine"], source or settings.gtfs_url, force=force
        )
    except LoadError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"status: {result.status}")
    click.echo(f"sha256: {result.file_sha256}")
    if result.feed_version:
        click.echo(f"feed_version: {result.feed_version}")
    if result.row_counts:
        click.echo(f"rows: {json.dumps(result.row_counts)}")
    click.echo(f"download: {result.download_seconds}s, total: {result.duration_seconds}s")


@stations_cli.command("load")
@click.option("--source", help="URL or local CSV path. Defaults to STATIONS_URL.")
def stations_load(source: str | None) -> None:
    """Load stations, rebuild complexes, and reseed aliases."""
    settings = current_app.extensions["settings"]
    result = load_stations(current_app.extensions["db_engine"], source or settings.stations_url)
    click.echo(
        f"stations: {result.stations}, complexes: {result.complexes}, aliases: {result.aliases}"
    )
    if result.skipped_aliases:
        click.echo(f"skipped aliases (unknown complex): {', '.join(result.skipped_aliases)}")
