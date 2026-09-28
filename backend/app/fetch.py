"""Resolve a data source (URL or local path) to a local file."""

import logging
import shutil
import tempfile
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 120


@contextmanager
def fetched(source: str, filename: str) -> Iterator[Path]:
    """Yield a local path; URLs are downloaded to a temp file removed on exit.

    Local paths let tests and manual loads use checked-in fixtures without network.
    """
    if not source.startswith(("http://", "https://")):
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"source not found: {source}")
        yield path
        return

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / filename
        log.info("downloading %s", source)
        req = urllib.request.Request(source, headers={"User-Agent": "nyc-transit-assistant"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp, path.open("wb") as out:
            shutil.copyfileobj(resp, out)
        yield path
