"""The MTA subway realtime feeds and which routes each one carries."""

import os

BASE_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/"

TRIP_FEEDS = {
    "1234567": BASE_URL + "nyct%2Fgtfs",
    "ace": BASE_URL + "nyct%2Fgtfs-ace",
    "bdfm": BASE_URL + "nyct%2Fgtfs-bdfm",
    "g": BASE_URL + "nyct%2Fgtfs-g",
    "jz": BASE_URL + "nyct%2Fgtfs-jz",
    "nqrw": BASE_URL + "nyct%2Fgtfs-nqrw",
    "l": BASE_URL + "nyct%2Fgtfs-l",
    "si": BASE_URL + "nyct%2Fgtfs-si",
}
ALERTS_FEED = "alerts"
# The JSON variant carries Mercury's alert_type inline, so no Mercury proto is needed.
ALERTS_URL = BASE_URL + "camsys%2Fsubway-alerts.json"

# Observed in the live feeds; used to judge freshness for a route, never to route data.
FEED_ROUTES = {
    "1234567": {"1", "2", "3", "4", "5", "5X", "6", "6X", "7", "7X", "GS"},
    "ace": {"A", "C", "E", "H"},
    "bdfm": {"B", "D", "F", "FX", "M", "FS"},
    "g": {"G"},
    "jz": {"J", "Z"},
    "nqrw": {"N", "Q", "R", "W"},
    "l": {"L"},
    "si": {"SI"},
}


def feeds_for_routes(route_ids: set[str]) -> set[str]:
    return {feed for feed, routes in FEED_ROUTES.items() if routes & route_ids}


def feed_urls(env=os.environ) -> dict[str, str]:
    """All feed URLs; RT_FEED_URL_<FEED> (e.g. RT_FEED_URL_ACE, RT_FEED_URL_ALERTS) overrides."""
    urls = {**TRIP_FEEDS, ALERTS_FEED: ALERTS_URL}
    return {name: env.get(f"RT_FEED_URL_{name.upper()}") or url for name, url in urls.items()}
