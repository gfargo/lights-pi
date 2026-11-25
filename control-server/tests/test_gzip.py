"""Gzip hook: the pure decision helpers plus one round trip through Flask."""
import gzip
import os

os.environ.setdefault("MOCK_DMX", "1")

import app as app_module  # noqa: E402
from app import _accepts_gzip, _should_gzip  # noqa: E402


def test_accepts_gzip_variants():
    assert _accepts_gzip("gzip, deflate, br")
    assert _accepts_gzip("br;q=1.0, gzip;q=0.8")
    assert _accepts_gzip("*")
    assert not _accepts_gzip("gzip;q=0")
    assert not _accepts_gzip("br, deflate")
    assert not _accepts_gzip(None)


def test_should_gzip_rules():
    assert _should_gzip(200, "text/html", None, False, 5000)
    assert _should_gzip(200, "application/json", None, False, 2048)
    assert not _should_gzip(200, "text/html", None, False, 100)          # too small
    assert not _should_gzip(200, "text/event-stream", None, True, None)  # SSE
    assert not _should_gzip(200, "font/woff2", None, False, 50000)       # binary
    assert not _should_gzip(200, "text/html", "gzip", False, 5000)       # already encoded
    assert not _should_gzip(304, "text/html", None, False, 5000)
    assert not _should_gzip(200, "text/html", None, True, 5000)          # streamed


def test_index_is_gzipped_when_asked():
    client = app_module.app.test_client()
    plain = client.get("/")
    zipped = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in plain.headers
    assert zipped.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in zipped.headers.get("Vary", "")
    assert gzip.decompress(zipped.data) == plain.data
    assert len(zipped.data) < len(plain.data) / 3
