"""Debug endpoints over the departures service (no LLM involved)."""

from flask import Blueprint, current_app, jsonify

from app.extensions import make_store
from app.realtime.departures import DeparturesService, UnknownStation
from app.stations_api import lookup, number_arg, text_arg

bp = Blueprint("realtime", __name__, url_prefix="/api")


def departures_service() -> DeparturesService:
    ext = current_app.extensions
    settings = ext["settings"]
    return DeparturesService(
        lookup(),
        make_store(ext["redis"], settings),
        clock=ext["clock"],
        stale_after=settings.rt_stale_seconds,
        alerts_stale_after=settings.rt_alerts_stale_seconds,
    )


# BadRequest is a ValueError, as are unknown routes and directions from the service.
@bp.errorhandler(ValueError)
def _bad_request(exc: ValueError):
    return jsonify(error=str(exc)), 400


@bp.errorhandler(UnknownStation)
def _not_found(exc: UnknownStation):
    return jsonify(error=str(exc)), 404


@bp.get("/departures")
def departures():
    result = departures_service().get_departures(
        number_arg("complex_id", required=True, cast=int),
        text_arg("route", required=True),
        text_arg("direction", required=True),
        n=number_arg("n", default=3, lo=1, hi=10, cast=int),
    )
    return jsonify(result)


@bp.get("/alerts")
def alerts():
    result = departures_service().get_alerts(
        route=text_arg("route"), complex_id=number_arg("complex_id", cast=int)
    )
    return jsonify(result)
