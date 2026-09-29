"""
The feed server: plans published as calendar subscriptions.

A calendar app (Google Calendar "From URL", Apple Calendar "New Calendar
Subscription", Notion Calendar through the Google or Apple account it shows, Outlook
"Subscribe from web") reads a URL every few hours and shows what it finds. That is
all a feed needs: one address per plan, `/feed/<token>.ics`, answered from the
store (`cps.store`, one SQLite file) `service` writes. No account and no sign-in, which is why this
is step one before any Google integration: nothing here needs Google's approval.

When a feed is read and its plan is older than `service.FEED_REFRESH`, the answer is
the plan as it stands, and a refresh starts in the background: the timetable is
read again from its link and the plan continues from now (`service.continue_plan`).
A refresh takes tens of seconds, far longer than a calendar app waits, so it is never
done while a request waits; the next read gets the new plan.

The server is a plain WSGI application, so `cps serve` can run it with the standard
library for one person on one machine, and a deployment can put the same `app`
behind any WSGI server. Access logs leave out the path: a feed address is a secret.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from . import service

DEFAULT_STORE = service.FEED_STORE
_FEED = re.compile(r"/feed/([A-Za-z0-9_-]{22,64})\.ics")

StartResponse = Callable[[str, list[tuple[str, str]]], Any]


class FeedApp:
    """The WSGI application. `clock`, `fetch` and `background` exist for tests."""

    def __init__(
        self,
        store: str | os.PathLike = DEFAULT_STORE,
        *,
        age: timedelta = service.FEED_REFRESH,
        background: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        fetch: Callable[[str], bytes] = service.fetch_calendar,
    ) -> None:
        self.store = Path(store)
        self.age = age
        self.clock = clock
        self.refresher = service.Refresher(store, background=background, clock=clock, fetch=fetch)

    def __call__(self, environ: dict, start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "")
        if method not in ("GET", "HEAD"):
            return _answer(start_response, "405 Method Not Allowed", b"read only\n", [("Allow", "GET, HEAD")])
        if path == "/health":
            return _answer(start_response, "200 OK", b"ok\n")
        match = _FEED.fullmatch(path)
        subscription = service.load_subscription(self.store, match.group(1)) if match else None
        if subscription is None or subscription.plan is None:
            return _answer(start_response, "404 Not Found", b"no such feed\n")
        if service.is_stale(subscription, self.clock(), self.age):
            self.refresh(subscription.token)
        body = service.feed_ics(subscription)
        headers = [
            ("Content-Type", "text/calendar; charset=utf-8"),
            ("Content-Disposition", 'inline; filename="study-plan.ics"'),
            ("Cache-Control", "private, max-age=900"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Robots-Tag", "noindex"),
        ]
        return _answer(start_response, "200 OK", b"" if method == "HEAD" else body, headers)

    def refresh(self, token: str) -> None:
        """Refresh one feed (`service.Refresher`)."""
        self.refresher.refresh(token)


def _answer(
    start_response: StartResponse, status: str, body: bytes, headers: list[tuple[str, str]] | None = None
) -> list[bytes]:
    plain = [("Content-Type", "text/plain; charset=utf-8")]
    start_response(status, [*(headers or plain), ("Content-Length", str(len(body)))])
    return [body]


class _Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    """Log the status and the time, not the path, which holds the feed's secret."""

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {self.command} {code}\n")


def serve(store: str | os.PathLike = DEFAULT_STORE, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the feed server until interrupted."""
    app = FeedApp(store)
    with make_server(host, port, app, server_class=_Server, handler_class=_QuietHandler) as server:
        print(f"serving feeds from {Path(store)} on http://{host}:{port}/feed/<token>.ics", flush=True)
        server.serve_forever()
