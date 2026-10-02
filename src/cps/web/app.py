"""
The hosted product: one FastAPI application for the pages, the calendar feeds and
the one-tap reports (docs/ROADMAP.md, W2 to W6).

A student has no account and no password (D5). Starting creates a subscription
with a secret token, and two secret addresses carry it: the page
(`/p/<token>`) and the feed (`/feed/<token>.ics`). That is the security model of
a calendar subscription link, stated on the page: whoever has the address can see
and change the plan. What follows from it:

* **No token in a log or a referrer.** The access log names the route, not the
  path (`/p/{token}`), and every answer says `Referrer-Policy: no-referrer`, so a
  link followed from a page does not carry the address away.
* **A GET never changes anything.** A calendar app, a mail client or a chat
  preview may open a link in an event by itself. The link in each event opens a
  page that asks what happened; only the button on that page (a POST) records it.
* **No cookies, no third parties.** The pages are HTML forms, one stylesheet and
  the week calendar's script, all served from here, under a Content-Security-Policy
  that allows nothing else (no inline script or style). Every page works without
  the script, which only turns the week into a calendar to drag on. Nothing is
  there to consent to, and nothing to track with.
* **Rate limits** on what costs the server something (reading a timetable link for
  a stranger, replanning), per address and per token (`limits.Limiter`).
* **Cross-site forms refused.** A POST that a browser says comes from another site
  (`Sec-Fetch-Site`) is refused.

Every decision about plans is `cps.service`'s; this module reads forms, calls it,
and fills templates (invariant 11).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from cps import service

from . import forms
from .limits import Limiter

HERE = Path(__file__).parent
LOG = logging.getLogger("cps.web")
MAX_UPLOAD = 5 * 1024 * 1024  # the same bound as a timetable read from a link
SWEEP_EVERY = timedelta(hours=6)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; "
        "img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

OUTCOME_WORDS = {
    "done": "Done",
    "skipped": "Skipped",
    "struggled": "Hard",
}


FAMILIARITY = (
    (1, "New to me"),
    (2, "Seen it, shaky"),
    (3, "Know it partly"),
    (4, "Know it well"),
    (5, "Know it very well"),
)


def _nice_date(value: str) -> str:
    """ "2027-01-25 13:45" as a person reads it: "Mon 25 Jan 2027, 13:45"."""
    try:
        moment = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return str(value)
    return f"{moment:%a} {moment.day} {moment:%b %Y}, {moment:%H:%M}"


# What a task change says once it is done, by the `saved` query parameter.
TASK_NOTICES = {
    "added": "Added. The plan has made room for it.",
    "finished": "Done. Its remaining sessions are free time again.",
    "reopened": "Reopened. What is left is planned again.",
    "deleted": "Deleted.",
}


@dataclass
class Config:
    """Everything the app needs from outside. `from_env` reads a deployment's."""

    store: Path = field(default_factory=lambda: Path(service.FEED_STORE))
    base_url: str | None = None  # the public address, e.g. https://example.onrender.com
    contact: str | None = None  # who to write to, shown on the privacy page
    sweep_every: timedelta | None = SWEEP_EVERY
    backup_dir: Path | None = None  # a dated copy of the store every `backup_every`, a week kept
    backup_every: timedelta = timedelta(days=1)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    fetch: Callable[[str], bytes] = service.fetch_calendar
    background: bool = True  # refresh feeds in a thread (tests turn it off)
    start_limit: tuple[int, float] = (10, 3600.0)  # new plans per address per hour
    change_limit: tuple[int, float] = (120, 3600.0)  # reports and edits per token per hour

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            store=Path(os.environ.get("CPS_DB") or service.FEED_STORE),
            # Render sets RENDER_EXTERNAL_URL to the service's public address.
            base_url=os.environ.get("CPS_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or None,
            contact=os.environ.get("CPS_CONTACT") or None,
            backup_dir=Path(os.environ["CPS_BACKUP_DIR"]) if os.environ.get("CPS_BACKUP_DIR") else None,
        )


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    store = config.store
    feeds = service.Refresher(store, background=config.background, clock=config.clock, fetch=config.fetch)
    starts = Limiter(*config.start_limit)
    changes = Limiter(*config.change_limit)
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["outcome_words"] = OUTCOME_WORDS
    templates.env.globals["familiarity"] = FAMILIARITY
    templates.env.filters["nice_date"] = _nice_date

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = threading.Event()
        if config.sweep_every is not None:
            period = config.sweep_every.total_seconds()

            def sweep() -> None:
                while not stop.is_set():
                    try:
                        gone = service.sweep_subscriptions(store, config.clock())
                        if gone:
                            LOG.info("deleted %d expired subscriptions", gone)
                    except Exception:  # a failed sweep must not stop the next one
                        LOG.exception("sweep failed")
                    stop.wait(period)

            threading.Thread(target=sweep, name="cps-sweep", daemon=True).start()
        if config.backup_dir is not None:
            directory, every = config.backup_dir, config.backup_every.total_seconds()

            def backup() -> None:
                # In the server's own process: a host's scheduled job may not see
                # the server's disk. One copy at start, then one per period.
                while not stop.is_set():
                    try:
                        service.backup_subscriptions(store, directory, config.clock())
                    except Exception:
                        LOG.exception("backup failed")
                    stop.wait(every)

            threading.Thread(target=backup, name="cps-backup", daemon=True).start()
        yield
        stop.set()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable) -> Response:
        began = time.perf_counter()
        if request.method == "POST" and not _same_origin(request):
            response: Response = HTMLResponse("Refused: this form was sent from another site.", 403)
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        route = request.scope.get("route")
        LOG.info(
            "%s %s %d %.0fms",
            request.method,
            "/static" if request.url.path.startswith("/static/") else getattr(route, "path", "(no route)"),
            response.status_code,
            (time.perf_counter() - began) * 1000,
        )
        return response

    # ---------------------------------------------------------------- helpers

    def page(request: Request, name: str, status: int = 200, **context: Any) -> HTMLResponse:
        token = context.get("token")
        if token and "courses" not in context:
            # Every page of a plan carries the new-task sheet, which suggests courses.
            subscription = service.load_subscription(store, token)
            context["courses"] = service.course_names(subscription) if subscription else []
        return templates.TemplateResponse(request, name, context, status_code=status)

    def base(request: Request) -> str:
        return (config.base_url or str(request.base_url)).rstrip("/")

    def load(token: str) -> service.Subscription | None:
        return service.load_subscription(store, token)

    def missing(request: Request) -> HTMLResponse:
        return page(
            request,
            "message.html",
            404,
            title="Not found",
            message="This address is not, or no longer, a study plan. Plans are deleted 30 days "
            "after their last exam or deadline, or when their owner deletes them.",
        )

    def too_many(request: Request) -> HTMLResponse:
        return page(request, "message.html", 429, title="Slow down", message="Too many requests; try later.")

    def client(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    # ---------------------------------------------------------------- pages

    @app.get("/health")
    def health() -> Response:
        return Response("ok\n", media_type="text/plain")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return page(request, "home.html", tz="Europe/Paris")

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request) -> HTMLResponse:
        return page(request, "privacy.html", contact=config.contact, retention=service.RETENTION.days)

    @app.post("/start")
    async def start(request: Request) -> Response:
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD + 64 * 1024:
            return page(request, "home.html", 413, error="That file is too large for a timetable.")
        if not starts.allow(client(request)):
            return too_many(request)
        form = await request.form(max_files=1, max_fields=10)
        link = str(form.get("link") or "").strip() or None
        tz = str(form.get("tz") or "Europe/Paris").strip()
        upload = form.get("file")
        ics = None
        if isinstance(upload, UploadFile) and upload.filename:
            ics = await upload.read(MAX_UPLOAD + 1)
            if len(ics) > MAX_UPLOAD:
                return page(request, "home.html", 413, error="That file is too large for a timetable.")
        try:
            if link is None and ics is None:
                raise service.InvalidInput("paste your timetable's link, or choose its file")
            subscription = service.start_subscription(
                tz=tz, source_url=link, ics=None if link else ics, now=config.clock(), fetch=config.fetch
            )
        except service.ServiceError as error:
            return page(request, "home.html", 400, error=str(error), link=link or "", tz=tz)
        service.save_subscription(store, subscription)
        service.log_event(store, subscription.token, "start", "link" if link else "file")
        return RedirectResponse(f"/p/{subscription.token}/settings?new=1", 303)

    def plan_page(
        request: Request,
        subscription: service.Subscription,
        tab: str,
        status: int = 200,
        **context: Any,
    ) -> HTMLResponse:
        """The main screen: the panel (today, reports, deadlines, exams) beside the
        calendar. On a phone the tab decides which of the two shows."""
        token = subscription.token
        if subscription.source_url and service.is_stale(subscription, config.clock()):
            feeds.refresh(token)
        today = config.clock().astimezone(ZoneInfo(subscription.options["tz"])).date()
        return page(
            request,
            "plan.html",
            status,
            token=token,
            view=service.today_view(subscription, config.clock()),
            first=today.isoformat(),
            tab=tab,
            **context,
        )

    @app.get("/p/{token}", response_class=HTMLResponse)
    def today(request: Request, token: str, reported: str | None = None, saved: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return plan_page(
            request, subscription, "today", reported=OUTCOME_WORDS.get(reported or ""), saved=_notice(saved)
        )

    @app.get("/p/{token}/week", response_class=HTMLResponse)
    def week(request: Request, token: str, new: int = 0, saved: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return plan_page(request, subscription, "calendar", new=bool(new), saved=_notice(saved))

    @app.get("/p/{token}/panel", response_class=HTMLResponse)
    def panel(request: Request, token: str) -> HTMLResponse:
        """The panel alone, for the page to redraw after a change."""
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return page(
            request, "_panel.html", token=token, view=service.today_view(subscription, config.clock())
        )

    @app.get("/p/{token}/agenda", response_class=HTMLResponse)
    def agenda(request: Request, token: str, w: int = 0) -> HTMLResponse:
        """The week as a list of days: the calendar for a browser without scripts."""
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if subscription.plan is None:
            return RedirectResponse(f"/p/{token}", 303)  # type: ignore[return-value]
        w = max(-8, min(w, 60))
        return page(
            request,
            "agenda.html",
            token=token,
            agenda=service.agenda(subscription, w, config.clock()),
            tab="calendar",
        )

    def days_shown(start: str | None, days: int) -> tuple[date | None, int]:
        try:
            first = date.fromisoformat(start) if start else None
        except ValueError:
            raise service.InvalidInput(f"{start!r} is not a date like 2026-10-05") from None
        return first, max(1, min(days, 14))

    @app.get("/p/{token}/calendar.json")
    def calendar(request: Request, token: str, start: str | None = None, days: int = 7) -> Response:
        subscription = load(token)
        if subscription is None:
            return JSONResponse({"message": "no such plan"}, 404)
        try:
            first, count = days_shown(start, days)
            view = service.calendar_view(subscription, first, count, config.clock())
        except service.ServiceError as error:
            return JSONResponse(error.to_dict(), 400)
        return JSONResponse(view)

    @app.post("/p/{token}/activities")
    async def activities(request: Request, token: str, start: str | None = None, days: int = 7) -> Response:
        """The calendar's busy times, all of them, as the page holds them after an
        edit; the plan is made again at once and the new calendar returned."""
        if load(token) is None:
            return JSONResponse({"message": "no such plan"}, 404)
        if not changes.allow(token):
            return JSONResponse({"message": "Too many changes; try again in a while."}, 429)
        if int(request.headers.get("content-length") or 0) > 64 * 1024:
            return JSONResponse({"message": "too many busy times"}, 413)
        try:
            rows = await request.json()
            if not isinstance(rows, list) or len(rows) > 200 or not all(isinstance(r, dict) for r in rows):
                raise service.InvalidInput("busy times come as a list of at most 200 rows")
            first, count = days_shown(start, days)
            fresh = service.update_subscription(
                store,
                token,
                lambda current: service.revise_subscription(
                    current, busy_rows=rows, now=config.clock(), strict=False
                ),
            )
            if fresh is None:
                return JSONResponse({"message": "no such plan"}, 404)
            view = service.calendar_view(fresh, first, count, config.clock())
        except ValueError:  # the body is not JSON
            return JSONResponse({"message": "not a list of busy times"}, 400)
        except service.ServiceError as error:
            return JSONResponse(error.to_dict(), 400)
        service.log_event(store, token, "activities", str(len(rows)))
        return JSONResponse(view)

    async def session_change(
        request: Request,
        token: str,
        sid: str,
        start: str | None,
        days: int,
        change: Callable[[service.Subscription, dict], service.Subscription],
        kind: str,
    ) -> Response:
        """A change to one session from the calendar (move, unpin, report): applied
        without losing a concurrent write, answered with the new calendar."""
        if load(token) is None:
            return JSONResponse({"message": "no such plan"}, 404)
        if not changes.allow(token):
            return JSONResponse({"message": "Too many changes; try again in a while."}, 429)
        if int(request.headers.get("content-length") or 0) > 4096:
            return JSONResponse({"message": "too long"}, 413)
        try:
            body = await request.json() if int(request.headers.get("content-length") or 0) else {}
            if not isinstance(body, dict):
                raise service.InvalidInput("not a change to a session")
            first, count = days_shown(start, days)
            fresh = service.update_subscription(store, token, lambda current: change(current, body))
            if fresh is None:
                return JSONResponse({"message": "no such plan"}, 404)
            view = service.calendar_view(fresh, first, count, config.clock())
        except ValueError:
            return JSONResponse({"message": "not a change to a session"}, 400)
        except service.ServiceError as error:
            return JSONResponse(error.to_dict(), 400)
        service.log_event(store, token, kind, str(body.get("outcome", "")))
        return JSONResponse(view)

    @app.post("/p/{token}/sessions/{sid}/move")
    async def move(
        request: Request, token: str, sid: str, start: str | None = None, days: int = 7
    ) -> Response:
        def change(current: service.Subscription, body: dict) -> service.Subscription:
            return service.move_session(current, sid, str(body.get("to") or ""), now=config.clock())

        return await session_change(request, token, sid, start, days, change, "move")

    @app.post("/p/{token}/sessions/{sid}/unpin")
    async def unpin(
        request: Request, token: str, sid: str, start: str | None = None, days: int = 7
    ) -> Response:
        def change(current: service.Subscription, body: dict) -> service.Subscription:
            return service.unpin_session(current, sid, now=config.clock())

        return await session_change(request, token, sid, start, days, change, "unpin")

    @app.post("/p/{token}/sessions/{sid}/report")
    async def report_json(
        request: Request, token: str, sid: str, start: str | None = None, days: int = 7
    ) -> Response:
        def change(current: service.Subscription, body: dict) -> service.Subscription:
            return service.report_session(current, sid, str(body.get("outcome") or ""), now=config.clock())

        return await session_change(request, token, sid, start, days, change, "report")

    @app.get("/p/{token}/settings", response_class=HTMLResponse)
    def settings(request: Request, token: str, new: int = 0) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return page(
            request,
            "settings.html",
            token=token,
            values=service.setup_view(subscription),
            new=bool(new),
            empty_rows=forms.EMPTY_ROWS,
            tab="settings",
        )

    @app.post("/p/{token}/settings", response_class=HTMLResponse)
    async def save_settings(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=400)
        raw = {k: str(v) for k, v in form.items()}
        values = forms.settings_values(raw, [str(d) for d in form.getlist("rest")])
        new = raw.get("new") == "1"
        try:
            change = forms.revision(values)
            service.update_subscription(
                store,
                token,
                lambda current: service.revise_subscription(
                    current, now=config.clock(), fetch=config.fetch, **change
                ),
            )
        except service.ServiceError as error:
            shown = {**service.setup_view(subscription), **values}
            return page(
                request,
                "settings.html",
                400,
                token=token,
                values=shown,
                new=new,
                empty_rows=forms.EMPTY_ROWS,
                error=str(error),
                tab="settings",
            )
        service.log_event(store, token, "settings")
        return RedirectResponse(f"/p/{token}/week?new=1" if new else f"/p/{token}?saved=1", 303)

    def back_to(token: str, where: str, **query: Any) -> str:
        """Where a task form returns to: the page it was sent from, among the
        plan's own pages only (never an address taken from the form)."""
        path = {"tasks": f"/p/{token}/tasks", "calendar": f"/p/{token}/week"}.get(where, f"/p/{token}")
        extra = "&".join(f"{k}={v}" for k, v in query.items())
        return f"{path}?{extra}" if extra else path

    def tasks_page(
        request: Request, subscription: service.Subscription, status: int = 200, **context: Any
    ) -> HTMLResponse:
        return page(
            request,
            "tasks.html",
            status,
            token=subscription.token,
            view=service.tasks_view(subscription, config.clock()),
            tab="tasks",
            **context,
        )

    @app.get("/p/{token}/tasks", response_class=HTMLResponse)
    def tasks(request: Request, token: str, saved: str | None = None) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return tasks_page(request, subscription, saved=TASK_NOTICES.get(saved or ""))

    @app.post("/p/{token}/tasks", response_class=HTMLResponse)
    async def add_task(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=12)
        name = str(form.get("name") or "").strip()
        where = str(form.get("back") or "")
        try:
            other = str(form.get("hours_other") or "").strip()
            hours = forms._number(other or str(form.get("hours") or "2"), name or "the task")
            task = service.TaskSpec(
                name,
                str(form.get("due") or "").replace("T", " ").strip(),
                hours,
                str(form.get("course") or "").strip(),
            )
            service.update_subscription(
                store, token, lambda current: service.add_task(current, task, now=config.clock())
            )
        except service.ServiceError as error:
            if where == "tasks":
                return tasks_page(request, subscription, 400, task_error=str(error))
            return plan_page(request, subscription, "today", 400, task_error=str(error))
        service.log_event(store, token, "task")
        return RedirectResponse(back_to(token, where, saved="added"), 303)

    @app.post("/p/{token}/tasks/done", response_class=HTMLResponse)
    async def task_done(request: Request, token: str) -> Response:
        return await task_change(request, token, "done")

    @app.post("/p/{token}/tasks/delete", response_class=HTMLResponse)
    async def task_delete(request: Request, token: str) -> Response:
        return await task_change(request, token, "delete")

    async def task_change(request: Request, token: str, kind: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=6)
        name = str(form.get("name") or "")
        where = str(form.get("back") or "")
        finished = str(form.get("done") or "1") == "1"
        try:
            if kind == "delete":
                change = lambda current: service.delete_task(current, name, now=config.clock())  # noqa: E731
            else:
                change = lambda current: service.set_task_done(  # noqa: E731
                    current, name, finished, now=config.clock()
                )
            service.update_subscription(store, token, change)
        except service.ServiceError as error:
            return tasks_page(request, subscription, 400, task_error=str(error))
        notice = "deleted" if kind == "delete" else ("finished" if finished else "reopened")
        service.log_event(store, token, notice)
        return RedirectResponse(back_to(token, where, saved=notice), 303)

    @app.get("/p/{token}/feed", response_class=HTMLResponse)
    def feed_page(request: Request, token: str, new: int = 0) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        url = service.feed_url(base(request), token)
        return page(
            request,
            "feed.html",
            token=token,
            feed=url,
            webcal="webcal://" + url.split("://", 1)[1],
            page_url=f"{base(request)}/p/{token}",
            new=bool(new),
            planned=subscription.plan is not None,
            tab="connect",
        )

    @app.get("/feed/{name}")
    def feed(request: Request, name: str) -> Response:
        token = name.removesuffix(".ics")
        subscription = load(token) if name.endswith(".ics") else None
        if subscription is None or subscription.plan is None:
            return Response("no such feed\n", 404, media_type="text/plain")
        if service.is_stale(subscription, config.clock()):
            feeds.refresh(token)
        return Response(
            service.feed_ics(subscription, base(request)),
            media_type="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": 'inline; filename="study-plan.ics"',
                "Cache-Control": "private, max-age=900",
            },
        )

    @app.get("/s/{token}/{sid}", response_class=HTMLResponse)
    def session(request: Request, token: str, sid: str) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        card = service.session_card(subscription, sid, config.clock())
        if card is None:
            return page(
                request,
                "message.html",
                404,
                title="Session not found",
                message="This session is no longer in your plan: the plan has changed since. "
                "Your calendar shows the new one after its next refresh.",
                token=token,
            )
        return page(request, "session.html", token=token, card=card)

    @app.post("/s/{token}/{sid}", response_class=HTMLResponse)
    async def report(request: Request, token: str, sid: str) -> Response:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=5)
        outcome = str(form.get("outcome") or "")
        try:
            done = service.update_subscription(
                store,
                token,
                lambda current: service.report_session(current, sid, outcome, now=config.clock()),
            )
        except service.ServiceError as error:
            return page(request, "message.html", 400, title="Not recorded", message=str(error), token=token)
        if done is None:
            return missing(request)
        service.log_event(store, token, "report", outcome)
        return RedirectResponse(f"/p/{token}?reported={outcome}", 303)

    @app.get("/p/{token}/delete", response_class=HTMLResponse)
    def confirm_delete(request: Request, token: str) -> HTMLResponse:
        if load(token) is None:
            return missing(request)
        return page(request, "delete.html", token=token, tab="settings")

    @app.post("/p/{token}/delete", response_class=HTMLResponse)
    def delete(request: Request, token: str) -> HTMLResponse:
        if not service.delete_subscription(store, token):
            return missing(request)
        return page(
            request,
            "message.html",
            title="Deleted",
            message="Your plan, your timetable, your deadlines and everything you reported are "
            "deleted. Remove the calendar subscription from your calendar app too: it will "
            "stop updating now.",
        )

    return app


def _notice(saved: str) -> str | None:
    if saved == "1":
        return "Saved. The plan has changed to match."
    return TASK_NOTICES.get(saved)


def _same_origin(request: Request) -> bool:
    """Whether a POST comes from this site's own pages.

    Browsers say where a request comes from in `Sec-Fetch-Site`; "cross-site" and
    "same-site" (another app on a shared host domain) are refused. Origin cannot be
    used alone: under `Referrer-Policy: no-referrer` a browser sends `Origin: null`
    with the site's own forms (found in the browser run, AUDIT.md item 37). A client
    that sends neither header (curl, a test) is not a browser a page can abuse."""
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is None or origin == "null":
        return True
    return urlsplit(origin).netloc == request.headers.get("host", "")
