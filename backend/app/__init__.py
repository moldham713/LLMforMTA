import logging

from flask import Flask

from .config import Settings
from .extensions import make_engine, make_redis


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=settings.log_level)

    app = Flask(__name__)
    # Both clients connect lazily, so building the app never touches the network.
    app.extensions["db_engine"] = make_engine(settings.database_url)
    app.extensions["redis"] = make_redis(settings.redis_url)

    from .health import bp as health_bp

    app.register_blueprint(health_bp)
    return app
