import json
import sys

import click
from flask import current_app
from flask.cli import AppGroup, with_appcontext

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


@click.command("chat")
@click.option("--lat", type=float, help="Share a location with the session.")
@click.option("--lon", type=float)
@click.option("--session", "session_id", help="Resume a session id.")
@click.option("--trace", is_flag=True, help="Print each turn's trace.")
@with_appcontext
def chat_cli(lat: float | None, lon: float | None, session_id: str | None, trace: bool) -> None:
    """Chat with the agent in the terminal (reads stdin; blank line or EOF quits)."""
    from app.chat_api import ChatUnavailable, chat_agent

    try:
        agent = chat_agent()
    except ChatUnavailable as exc:
        raise click.ClickException(str(exc)) from exc
    location = {"lat": lat, "lon": lon} if lat is not None and lon is not None else None
    interactive = sys.stdin.isatty()
    while True:
        try:
            line = input("you> " if interactive else "")
        except EOFError:
            break
        if not line.strip():
            break
        if not interactive:
            click.echo(f"you> {line}")
        result = agent.chat(line, session_id=session_id, location=location)
        session_id, location = result.session_id, None
        click.echo(f"bot> {result.reply}")
        if trace:
            t = result.trace
            click.echo(
                f"     [{t['latency_ms']} ms, {t['input_tokens']} in/{t['output_tokens']} out, "
                f"awaiting={t['awaiting']}, intent={t['intent']}, "
                f"slots={json.dumps(t['slots_after'])}]"
            )
            for call in t["tool_calls"]:
                status = "ok" if call["ok"] else f"error: {call['error']}"
                click.echo(f"     - {call['name']}({json.dumps(call['args'])}) {status}")
    click.echo(f"(session {session_id})")
