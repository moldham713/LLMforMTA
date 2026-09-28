"""Debug endpoints over the station lookup service (no LLM involved)."""

from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request

from app.stations.lookup import StationLookup

bp = Blueprint("stations", __name__, url_prefix="/api/stations")

MAX_LIMIT = 20


class BadRequest(ValueError):
    pass


def lookup() -> StationLookup:
    ext = current_app.extensions
    return StationLookup(ext["db_engine"], ext["settings"].search_relative_cutoff)


def number_arg(name: str, *, default=None, required=False, lo=None, hi=None, cast=float):
    raw = request.args.get(name)
    if raw is None or raw == "":
        if required:
            raise BadRequest(f"{name} is required")
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise BadRequest(f"{name} must be a number") from None
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise BadRequest(f"{name} must be between {lo} and {hi}")
    return value


def text_arg(name: str, *, required=False) -> str | None:
    value = request.args.get(name, "").strip()
    if required and not value:
        raise BadRequest(f"{name} is required")
    return value or None


@bp.errorhandler(ValueError)
def _bad_request(exc: ValueError):
    return jsonify(error=str(exc)), 400


@bp.get("/search")
def search():
    q = text_arg("q", required=True)
    route = text_arg("route")
    limit = number_arg("limit", default=5, lo=1, hi=MAX_LIMIT, cast=int)
    results = lookup().search_stations(q, route=route, limit=limit)
    return jsonify(query=q, route=route, results=[asdict(r) for r in results])


@bp.get("/nearest")
def nearest():
    lat = number_arg("lat", required=True, lo=-90, hi=90)
    lon = number_arg("lon", required=True, lo=-180, hi=180)
    limit = number_arg("limit", default=5, lo=1, hi=MAX_LIMIT, cast=int)
    max_meters = number_arg("max_meters", default=1000, lo=1, hi=10_000)
    results = lookup().nearest_stations(lat, lon, limit=limit, max_meters=max_meters)
    return jsonify(results=[asdict(r) for r in results])


@bp.get("/<int:complex_id>/routes")
def routes(complex_id: int):
    result = lookup().routes_at_station(complex_id)
    if result is None:
        return jsonify(error=f"unknown complex_id {complex_id}"), 404
    return jsonify(asdict(result))
