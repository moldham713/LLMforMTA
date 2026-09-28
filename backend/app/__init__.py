import logging
import time

from flask import Flask

from .config import Settings
from .extensions import make_engine, make_redis


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=settings.log_level)

    app = Flask(__name__)
    app.extensions["settings"] = settings
    # Both clients connect lazily, so building the app never touches the network.
    app.extensions["db_engine"] = make_engine(settings.database_url)
    app.extensions["redis"] = make_redis(settings.redis_url)
    # Injectable so tests can freeze "now" for realtime data.
    app.extensions["clock"] = time.time

    from .cli import gtfs_cli, stations_cli
    from .health import bp as health_bp
    from .realtime_api import bp as realtime_bp
    from .stations_api import bp as stations_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(stations_bp)
    app.register_blueprint(realtime_bp)
    app.cli.add_command(gtfs_cli)
    app.cli.add_command(stations_cli)
    return app
