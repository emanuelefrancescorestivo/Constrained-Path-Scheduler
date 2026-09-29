"""Shared fixtures: a local web server that plays a university timetable's link."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class _Timetable(BaseHTTPRequestHandler):
    """`/<file>.ics` from examples/, plus the ways a link goes wrong."""

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/web-page":
            self._send(200, b"<html><body>Log in to see your timetable</body></html>", "text/html")
        elif path == "/missing":
            self._send(404, b"not found", "text/plain")
        elif path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/sample-timetable.ics")
            self.end_headers()
        elif path == "/moved-inside":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.7/sample-timetable.ics")
            self.end_headers()
        elif (EXAMPLES / path.lstrip("/")).is_file() and path.endswith(".ics"):
            self._send(200, (EXAMPLES / path.lstrip("/")).read_bytes(), "text/calendar")
        else:
            self._send(404, b"not found", "text/plain")

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="session")
def timetable_server() -> Iterator[str]:
    """Base URL of a local server with the example calendars and some bad links."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Timetable)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
