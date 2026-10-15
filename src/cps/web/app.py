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

import json
import logging
import os
import re
import secrets
import threading
import time
import traceback
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.datastructures import UploadFile

from cps import service

from . import charts, forms
from .limits import Limiter

HERE = Path(__file__).parent
# A plan's pages and its sessions' pages: the language is the plan's.
PLAN_PATH = re.compile(r"^/(?:p|s)/([A-Za-z0-9_-]{22,64})(?:/|$)")
LOG = logging.getLogger("cps.web")
MAX_UPLOAD = 5 * 1024 * 1024  # the same bound as a timetable read from a link
MAX_POST_UPLOAD = 4 * 3 * 1024 * 1024 + 64 * 1024  # four photos of 3 MB, and the fields
MAX_NOTES_UPLOAD = 8 * 3 * 1024 * 1024 + 64 * 1024  # notes have up to eight pages (D26)
SWEEP_EVERY = timedelta(hours=6)
REMIND_EVERY = timedelta(minutes=1)  # how often the reminders due are sent (D38)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; "
        "img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "manifest-src 'self'; worker-src 'self'"
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
# A self-test asks how much was recalled instead (DECISIONS.md D25).
RECALL_WORDS = {
    "forgot": "Nothing",
    "some": "Some",
    "most": "Most",
    "all": "All",
    "skipped": "Skipped",
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
    return f"{service.format_day(moment.date(), 'short')} {moment.year}, {moment:%H:%M}"


# What a task change says once it is done, by the `saved` query parameter.
NETWORK_NOTICES = {
    "joined": "Welcome. Here is what students are sharing; follow the ones you like.",
    "profile": "Profile saved.",
    "posted": "Posted.",
    "left": "You have left the network. Your diary is still here, visible only to you.",
    "reported": "Thank you. The report is with the owner of this service.",
    "blocked": "Blocked. Neither of you sees the other any more; undo it in People.",
    "group": "Group created. Invite your friends by their handle.",
    "invited": "Invitation sent. They choose whether to join.",
    "joined_group": "You joined the group.",
    "left_group": "You left the group.",
    "group_saved": "Saved.",
    "demo": "Welcome. This student is yours: the timetable and the classmates are made up; try anything.",
}
CARD_NOTICES = {
    "card_saved": "Saved.",
    "card_deleted": "Deleted.",
}
# The four answers to a card, as its buttons say them (D29).
CARD_WORDS = {"again": "Again", "hard": "Hard", "good": "Good", "easy": "Easy"}
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
    remind_every: timedelta | None = REMIND_EVERY  # None: no reminders sent (tests send by hand)
    push_send: Callable[..., int] | None = None  # replaces the push sender (tests)
    backup_dir: Path | None = None  # a dated copy of the store every `backup_every`, a week kept
    backup_every: timedelta = timedelta(days=1)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    fetch: Callable[[str], bytes] = service.fetch_calendar
    background: bool = True  # refresh feeds in a thread (tests turn it off)
    start_limit: tuple[int, float] = (10, 3600.0)  # new plans per address per hour
    change_limit: tuple[int, float] = (120, 3600.0)  # reports and edits per token per hour
    signin_limit: tuple[int, float] = (30, 3600.0)  # passkey and recovery-code tries per address per hour
    admin_token: str | None = None  # the owner's review page, /admin/<token> (D20); none, no page
    # The public demo (D31): makes a fresh made-up student and returns their plan's
    # token; set, the home page offers it and every page says it is a demo.
    demo: Callable[[], str] | None = None

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            store=Path(os.environ.get("CPS_DB") or service.FEED_STORE),
            # Render sets RENDER_EXTERNAL_URL to the service's public address.
            base_url=os.environ.get("CPS_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or None,
            contact=os.environ.get("CPS_CONTACT") or None,
            backup_dir=Path(os.environ["CPS_BACKUP_DIR"]) if os.environ.get("CPS_BACKUP_DIR") else None,
            admin_token=os.environ.get("CPS_ADMIN_TOKEN") or None,
        )


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    store = config.store
    feeds = service.Refresher(store, background=config.background, clock=config.clock, fetch=config.fetch)
    starts = Limiter(*config.start_limit)
    changes = Limiter(*config.change_limit)
    errors_in = Limiter(10, 60.0)  # browser errors per address and minute (D43)
    signins = Limiter(*config.signin_limit)
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["outcome_words"] = OUTCOME_WORDS
    templates.env.globals["recall_words"] = RECALL_WORDS
    templates.env.globals["charts"] = charts
    templates.env.globals["familiarity"] = FAMILIARITY
    templates.env.filters["nice_date"] = _nice_date
    templates.env.filters["hours"] = service.format_number
    templates.env.globals["_"] = service.translate
    templates.env.globals["_n"] = service.translate_plural
    templates.env.globals["day_word"] = service.day_word
    templates.env.globals["year_words"] = service.year_words
    templates.env.globals["_h"] = _html
    templates.env.globals["lang"] = service.current_language
    templates.env.globals["languages"] = service.LANGUAGES
    templates.env.globals["demo"] = config.demo is not None

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
        if config.remind_every is not None:
            remind = config.remind_every.total_seconds()

            def reminders() -> None:
                # The first round one period after start, not during it: the app and
                # whatever opens the store at start (a test, a backup) do not race.
                while not stop.wait(remind):
                    try:
                        service.send_due_reminders(store, config.clock(), **push_options())
                    except Exception:  # a failed round must not stop the next one
                        LOG.exception("reminders failed")

            threading.Thread(target=reminders, name="cps-reminders", daemon=True).start()
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
        # The language (D10): a plan's own, else a `lang` asked for in the address,
        # else the browser's. No cookie.
        lang = None
        if not request.url.path.startswith("/static/"):
            found = PLAN_PATH.match(request.url.path)
            if found:
                lang = service.plan_language(store, found.group(1))
        chosen = service.activate_language(
            service.pick_language(
                lang, request.query_params.get("lang"), request.headers.get("accept-language")
            )
        )
        try:
            if request.method == "POST" and not _same_origin(request):
                response: Response = HTMLResponse("Refused: this form was sent from another site.", 403)
            else:
                response = await call_next(request)
        except Exception as error:  # a defect: counted in the owner's log (D43), a page for the student
            response = server_error(request, error)
        finally:
            service.deactivate_language(chosen)
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
        if token and ("courses" not in context or "theme" not in context or "motion" not in context):
            # Every page of a plan carries the new-task sheet, which suggests courses,
            # and the plan's appearance (D21) and motion (D30).
            subscription = service.load_subscription(store, token)
            context.setdefault("courses", service.course_names(subscription) if subscription else [])
            context.setdefault("theme", service.theme_of(subscription))
            context.setdefault("motion", service.motion_of(subscription))
        return templates.TemplateResponse(request, name, context, status_code=status)

    def base(request: Request) -> str:
        return (config.base_url or str(request.base_url)).rstrip("/")

    def server_error(request: Request, error: Exception) -> Response:
        """An error nothing expected: kept in the owner's error log (D43) by where it
        happened (the route, never the address), its kind, its message and the last
        lines of code it went through, then a page that says so."""
        route = getattr(request.scope.get("route"), "path", "") or service.page_of(request.url.path)
        frames = traceback.extract_tb(error.__traceback__)[-6:]
        detail = "\n".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in frames)
        try:
            service.record_error(
                store,
                source="server",
                route=route,
                kind=type(error).__name__,
                message=str(error),
                detail=detail,
                now=config.clock(),
            )
        except Exception:  # the log itself failing must not hide the page
            LOG.exception("could not record an error")
        LOG.error("%s %s: %s", request.method, route, type(error).__name__)
        found = PLAN_PATH.match(request.url.path)
        token = found.group(1) if found and service.load_subscription(store, found.group(1)) else None
        return templates.TemplateResponse(
            request,
            "message.html",
            {
                "title": service.translate("Something went wrong"),
                "message": service.translate(
                    "This is a mistake in the app, not yours. It has been noted for the person who "
                    "makes it; please try again in a moment."
                ),
                "token": token,
                "theme": "auto",
                "motion": "full",
                "courses": [],
            },
            status_code=500,
        )

    def load(token: str) -> service.Subscription | None:
        return service.load_subscription(store, token)

    def push_options() -> dict[str, Any]:
        """Who the pushes say they are from, and how they are sent (D38)."""
        options: dict[str, Any] = {"contact": config.contact, "base_url": config.base_url}
        if config.push_send is not None:
            options["send"] = config.push_send
        return options

    def missing(request: Request) -> HTMLResponse:
        return page(
            request,
            "message.html",
            404,
            title=service.translate("Not found"),
            message=service.translate(
                "This demo starts again from scratch when it restarts, and this student was in the "
                "one before. Start a new one: it takes a few seconds."
            )
            if config.demo
            else service.translate(
                "This address is not, or no longer, a study plan. Plans are deleted 12 months "
                "after they were last opened, or when their owner deletes them. If you lost your "
                "plan's address, sign in from the home page with your passkey or recovery code."
            ),
        )

    def too_many(request: Request) -> HTMLResponse:
        return page(
            request,
            "message.html",
            429,
            title=service.translate("Slow down"),
            message=service.translate("Too many requests; try later."),
        )

    def client(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    # ---------------------------------------------------------------- pages

    @app.get("/health")
    def health() -> Response:
        return Response("ok\n", media_type="text/plain")

    @app.post("/demo")
    def demo_start(request: Request) -> Response:
        """The public demo (D31): a fresh made-up student, with the semester so far,
        for whoever presses Start; their own address, like any plan's."""
        if config.demo is None:
            return missing(request)
        if not starts.allow(client(request)):
            return too_many(request)
        token = config.demo()
        service.log_event(store, token, "created", "demo")
        return RedirectResponse(f"/p/{token}?saved=demo", 303)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return page(request, "home.html", tz="Europe/Paris")

    # -- the app on a home screen (D33) ----------------------------------------

    def manifest(start: str) -> Response:
        """The web app manifest: what a phone needs to install the site as an app.
        A plan's own (`start` its address) opens that plan from the home screen."""
        body = {
            "id": start,
            "name": service.translate("Study plan"),
            "short_name": service.translate("Study plan"),
            "description": service.translate("Your timetable, your deadlines, your week"),
            "lang": service.current_language(),
            "start_url": start,
            "scope": "/",
            "display": "standalone",
            "background_color": "#f2f2f7",
            "theme_color": "#f2f2f7",
            "icons": [
                {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {
                    "src": "/static/icons/maskable-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
                {"src": "/static/icons/icon.svg", "sizes": "any", "type": "image/svg+xml"},
            ],
        }
        return Response(json.dumps(body, ensure_ascii=False), media_type="application/manifest+json")

    @app.get("/manifest.webmanifest")
    def site_manifest() -> Response:
        return manifest("/")

    @app.get("/p/{token}/manifest.webmanifest")
    def plan_manifest(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        return manifest(f"/p/{token}")

    @app.get("/sw.js")
    def service_worker() -> Response:
        # From the site's root, so that it may keep copies of every page (a worker
        # answers only below its own address); fetched again at each visit.
        return FileResponse(
            HERE / "static" / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/offline", response_class=HTMLResponse)
    def offline(request: Request) -> HTMLResponse:
        """What the service worker shows for a page it has no copy of."""
        return page(request, "offline.html")

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request) -> HTMLResponse:
        return page(
            request,
            "privacy.html",
            contact=config.contact,
            retention=round(service.RETENTION.days * 12 / 365),
        )

    @app.get("/guidelines", response_class=HTMLResponse)
    def guidelines(request: Request) -> HTMLResponse:
        return page(request, "guidelines.html", contact=config.contact)

    @app.post("/start")
    async def start(request: Request) -> Response:
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD + 64 * 1024:
            return page(
                request, "home.html", 413, error=service.translate("That file is too large for a timetable.")
            )
        if not starts.allow(client(request)):
            return too_many(request)
        form = await request.form(max_files=1, max_fields=11)
        link = str(form.get("link") or "").strip() or None
        tz = str(form.get("tz") or "Europe/Paris").strip()
        draw = form.get("draw") == "1"  # no file: the week is drawn by hand (D32)
        upload = form.get("file")
        ics = None
        if isinstance(upload, UploadFile) and upload.filename:
            ics = await upload.read(MAX_UPLOAD + 1)
            if len(ics) > MAX_UPLOAD:
                return page(
                    request,
                    "home.html",
                    413,
                    error=service.translate("That file is too large for a timetable."),
                )
        try:
            if link is None and ics is None and not draw:
                raise service.InvalidInput(
                    service.translate("paste your timetable's link, or choose its file")
                )
            subscription = service.start_subscription(
                tz=tz,
                source_url=link,
                ics=None if link else ics,
                now=config.clock(),
                fetch=config.fetch,
                lang=str(form.get("lang") or "") or None,
                draw=draw,
            )
        except service.ServiceError as error:
            return page(request, "home.html", 400, error=str(error), link=link or "", tz=tz)
        service.save_subscription(store, subscription)
        drawn = link is None and ics is None
        service.log_event(store, subscription.token, "start", "link" if link else "draw" if drawn else "file")
        if drawn:
            return RedirectResponse(f"/p/{subscription.token}/timetable", 303)
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
        service.log_visit(store, token, config.clock())
        today = config.clock().astimezone(ZoneInfo(subscription.options["tz"])).date()
        return page(
            request,
            "plan.html",
            status,
            token=token,
            first=today.isoformat(),
            tab=tab,
            **panel_context(subscription),
            **context,
        )

    def panel_context(subscription: service.Subscription) -> dict[str, Any]:
        now = config.clock()
        logged = service.logged_sessions(store, subscription)
        return {
            "view": service.today_view(subscription, now),
            "progress": service.progress_view(subscription, now, logged=logged),
            "review": service.review_with_ai(
                store, subscription, service.review_for_today(subscription, now, logged=logged), now=now
            ),
            "focus": subscription.options.get("focus"),
            "cards_due": service.cards_due(store, subscription, now),
            "account": service.account_view(store, subscription, now),
            "semester_over": service.semester_over(subscription, now),
            "budget": service.budget_offer(subscription, now),
            "exam_questions": service.exam_questions(subscription, now),
            "exam_results": list(service.EXAM_RESULTS.items()),
            "exam_helped": list(service.EXAM_HELPED.items()),
        }

    @app.get("/p/{token}", response_class=HTMLResponse)
    def today(request: Request, token: str, reported: str | None = None, saved: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return plan_page(
            request,
            subscription,
            "today",
            reported=service.translate({**OUTCOME_WORDS, **RECALL_WORDS}[reported])
            if reported in {**OUTCOME_WORDS, **RECALL_WORDS}
            else None,
            saved=_notice(saved),
        )

    @app.get("/p/{token}/week", response_class=HTMLResponse)
    def week(request: Request, token: str, new: int = 0, saved: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return plan_page(request, subscription, "calendar", new=bool(new), saved=_notice(saved))

    @app.get("/p/{token}/timetable", response_class=HTMLResponse)
    def timetable(request: Request, token: str) -> HTMLResponse:
        """Drawing a week by hand, for a student with no timetable file (D32): the
        calendar alone, classes first, then on to the exams."""
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return plan_page(request, subscription, "calendar", setup=True)

    @app.get("/p/{token}/panel", response_class=HTMLResponse)
    def panel(request: Request, token: str) -> HTMLResponse:
        """The panel alone, for the page to redraw after a change."""
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return page(request, "_panel.html", token=token, **panel_context(subscription))

    @app.get("/p/{token}/progress", response_class=HTMLResponse)
    def progress(request: Request, token: str, saved: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        service.log_visit(store, token, config.clock())
        now = config.clock()
        logged = service.logged_sessions(store, subscription)
        return page(
            request,
            "progress.html",
            token=token,
            view=service.progress_view(subscription, now, logged=logged),
            review=service.review_with_ai(
                store, subscription, service.weekly_review(subscription, now, logged=logged), now=now
            ),
            diary=service.diary_view(store, subscription, now),
            trend=service.trends_view(store, subscription, now, "12") if subscription.plan else None,
            groups=service.groups_view(store, subscription, now)["groups"],
            badges=service.badges_view(store, subscription, now, logged=logged),
            saved=_notice(saved),
            tab="progress",
        )

    @app.get("/p/{token}/trends", response_class=HTMLResponse)
    def trends(request: Request, token: str, span: str = "12") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        service.log_visit(store, token, config.clock())
        return page(
            request,
            "trends.html",
            token=token,
            view=service.trends_view(store, subscription, config.clock(), span),
            tab="progress",
        )

    # ------------------------------------------------------------ focus sessions

    @app.get("/p/{token}/focus", response_class=HTMLResponse)
    def focus(request: Request, token: str) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        service.log_visit(store, token, config.clock())
        return page(
            request,
            "focus.html",
            token=token,
            focus=service.focus_view(subscription, config.clock()),
            cards_due=service.cards_due(store, subscription, config.clock()),
            tab="focus",
        )

    async def focus_change(
        request: Request,
        token: str,
        change: Callable[[service.Subscription, Any], service.Subscription],
        kind: str,
    ) -> Response | service.Subscription:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=6)
        try:
            fresh = service.update_subscription(store, token, lambda current: change(current, form))
        except service.ServiceError as error:
            return page(
                request,
                "message.html",
                400,
                title=service.translate("Not saved"),
                message=str(error),
                token=token,
            )
        if fresh is None:
            return missing(request)
        service.log_event(store, token, kind)
        return fresh

    @app.post("/p/{token}/focus/start")
    async def focus_start(request: Request, token: str) -> Response:
        def change(current: service.Subscription, form: Any) -> service.Subscription:
            sid = str(form.get("sid") or "") or None
            return service.start_focus(current, str(form.get("course") or ""), sid=sid, now=config.clock())

        done = await focus_change(request, token, change, "focus")
        return done if isinstance(done, Response) else RedirectResponse(f"/p/{token}/focus", 303)

    @app.post("/p/{token}/focus/beat")
    def focus_beat(request: Request, token: str) -> Response:
        """The focus page's heartbeat (focus.js): it is open and visible."""
        if load(token) is None:
            return JSONResponse({"message": "no such plan"}, 404)
        try:
            fresh = service.update_subscription(
                store, token, lambda current: service.focus_beat(current, now=config.clock())
            )
        except service.ServiceError as error:
            return JSONResponse(error.to_dict(), 409)
        view = service.focus_view(fresh, config.clock()) if fresh else {}
        return JSONResponse({k: view.get(k) for k in ("elapsed", "away_minutes", "interruptions")})

    @app.post("/p/{token}/focus/finish")
    async def focus_finish(request: Request, token: str) -> Response:
        done = await focus_change(
            request,
            token,
            lambda current, form: service.finish_focus(current, now=config.clock()),
            "focus done",
        )
        return done if isinstance(done, Response) else RedirectResponse(f"/p/{token}/log", 303)

    @app.post("/p/{token}/focus/cancel")
    async def focus_cancel(request: Request, token: str) -> Response:
        done = await focus_change(
            request, token, lambda current, form: service.cancel_focus(current), "focus off"
        )
        return done if isinstance(done, Response) else RedirectResponse(f"/p/{token}/focus", 303)

    @app.get("/p/{token}/log", response_class=HTMLResponse)
    def log_form(request: Request, token: str) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return page(
            request,
            "log.html",
            token=token,
            draft=service.log_draft(subscription, config.clock()),
            profile=service.profile_of(store, token),
            tab="focus",
        )

    @app.post("/p/{token}/log", response_class=HTMLResponse)
    async def log_save(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        if int(request.headers.get("content-length") or 0) > MAX_POST_UPLOAD:
            return page(
                request,
                "log.html",
                413,
                token=token,
                draft=service.log_draft(subscription, config.clock()),
                profile=service.profile_of(store, token),
                error=service.translate("These photos are too large together; send fewer or smaller ones."),
                tab="focus",
            )
        form = await request.form(max_files=service.social.MAX_PHOTOS, max_fields=20)
        fields = {k: str(v) for k, v in form.items() if not isinstance(v, UploadFile)}
        photos = [await f.read() for f in form.getlist("photos") if isinstance(f, UploadFile) and f.filename]
        try:
            holder: dict[str, Any] = {}

            def change(current: service.Subscription) -> service.Subscription:
                fresh, post = service.log_session(store, current, fields, photos, now=config.clock())
                holder["post"] = post
                return fresh

            service.update_subscription(store, token, change)
        except service.ServiceError as error:
            draft = {**service.log_draft(subscription, config.clock()), **fields}
            return page(
                request,
                "log.html",
                400,
                token=token,
                draft=draft,
                profile=service.profile_of(store, token),
                error=str(error),
                tab="focus",
            )
        service.log_event(store, token, "logged", fields.get("visibility", ""))
        return RedirectResponse(f"/p/{token}/progress?saved=logged#diary", 303)

    @app.post("/p/{token}/posts/{post_id}/delete")
    def post_delete(request: Request, token: str, post_id: str) -> Response:
        if load(token) is None:
            return missing(request)
        service.delete_post(store, token, post_id)
        return RedirectResponse(f"/p/{token}/progress?saved=deleted#diary", 303)

    @app.post("/p/{token}/posts/{post_id}/visibility")
    async def post_visibility(request: Request, token: str, post_id: str) -> Response:
        if load(token) is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=3)
        try:
            service.set_post_visibility(store, token, post_id, str(form.get("visibility") or ""))
        except service.ServiceError as error:
            return page(
                request,
                "message.html",
                400,
                title=service.translate("Not saved"),
                message=str(error),
                token=token,
            )
        return RedirectResponse(f"/p/{token}/progress?saved=1#diary", 303)

    def photo_response(viewer: str | None, name: str) -> Response:
        found = service.photo_for(store, viewer, name)
        if found is None:
            return Response("not found\n", 404, media_type="text/plain")
        data, kind = found
        return Response(data, media_type=kind, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/p/{token}/m/{name}")
    def own_photo(token: str, name: str) -> Response:
        return photo_response(token if load(token) is not None else None, name)

    @app.get("/m/{name}")
    def public_photo(name: str) -> Response:
        return photo_response(None, name)

    # ---------------------------------------------------------- the study network

    def refused(request: Request, token: str, error: Exception, status: int = 400) -> HTMLResponse:
        return page(
            request,
            "message.html",
            status,
            title=service.translate("Not saved"),
            message=str(error),
            token=token,
        )

    def back(form: Any, token: str, fallback: str) -> str:
        """Where a form asks to return to, if it is one of this plan's own pages."""
        target = str(form.get("next") or "")
        own = f"/p/{token}"
        if (
            target == own or target.startswith(own + "/") or target.startswith(own + "?")
        ) and "//" not in target:
            return target
        return fallback

    @app.get("/p/{token}/community", response_class=HTMLResponse)
    def community(
        request: Request,
        token: str,
        tab: str = "following",
        uni: str = "",
        prog: str = "",
        course: str = "",
        kind: str = "",
        saved: str = "",
    ) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        service.log_visit(store, token, config.clock())
        view = service.community_view(
            store,
            subscription,
            tab=tab,
            filters={"uni": uni, "prog": prog, "course": course, "kind": kind},
            now=config.clock(),
        )
        return page(request, "community.html", token=token, view=view, saved=_notice(saved), tab="community")

    @app.get("/p/{token}/post/{post_id}", response_class=HTMLResponse)
    def post_page(request: Request, token: str, post_id: str, saved: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        view = service.post_view(store, subscription, post_id, config.clock())
        if view is None:
            return page(
                request,
                "message.html",
                404,
                title=service.translate("Not found"),
                message=service.translate("this post is not, or no longer, visible to you"),
                token=token,
            )
        return page(request, "post.html", token=token, **view, saved=_notice(saved), tab="community")

    @app.post("/p/{token}/post/{post_id}/kudos")
    async def kudos(request: Request, token: str, post_id: str) -> Response:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=3)
        try:
            service.toggle_kudos(store, token, post_id, now=config.clock())
        except service.ServiceError as error:
            return refused(request, token, error)
        return RedirectResponse(back(form, token, f"/p/{token}/post/{post_id}"), 303)

    @app.post("/p/{token}/post/{post_id}/comments")
    async def comment(request: Request, token: str, post_id: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=3)
        body = str(form.get("body") or "")
        try:
            service.add_comment(store, token, post_id, body)
        except service.ServiceError as error:
            view = service.post_view(store, subscription, post_id, config.clock())
            if view is None:
                return refused(request, token, error)
            return page(
                request, "post.html", 400, token=token, **view, error=str(error), draft=body, tab="community"
            )
        service.log_event(store, token, "comment")
        return RedirectResponse(f"/p/{token}/post/{post_id}#comments", 303)

    @app.post("/p/{token}/comments/{comment_id}/delete")
    async def comment_delete(request: Request, token: str, comment_id: str) -> Response:
        if load(token) is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=3)
        service.delete_comment(store, token, comment_id)
        return RedirectResponse(back(form, token, f"/p/{token}/community"), 303)

    def safe_next(token: str, value: str) -> str:
        """Where the profile form goes back to: an invitation of this plan (D40), or
        nowhere; never an address taken from a form as it is."""
        return value if re.fullmatch(rf"/p/{re.escape(token)}/join/[A-Za-z0-9_-]{{12}}", value) else ""

    @app.get("/p/{token}/profile", response_class=HTMLResponse)
    def profile(request: Request, token: str, next: str = "") -> HTMLResponse:
        if load(token) is None:
            return missing(request)
        current = service.profile_of(store, token)
        return page(
            request,
            "profile.html",
            token=token,
            profile=current,
            values=current or {},
            years=service.YEAR_WORDS,
            next=safe_next(token, next),
            tab="community",
        )

    @app.get("/p/{token}/universities")
    def universities(token: str, q: str = "") -> Response:
        """Universities as the student types (D39), the plan's country first."""
        subscription = load(token)
        if subscription is None:
            return JSONResponse([], 404)
        return JSONResponse(service.university_suggestions(q[:80], subscription.options["tz"]))

    @app.post("/p/{token}/profile", response_class=HTMLResponse)
    async def profile_save(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        subscription = load(token)
        form = await request.form(max_files=0, max_fields=10)
        fields = {k: str(v) for k, v in form.items()}
        before = service.profile_of(store, token)
        then = safe_next(token, fields.get("next", ""))
        try:
            saved = service.save_profile(
                store, token, fields, tz=subscription.options["tz"] if subscription else None
            )
        except service.ServiceError as error:
            return page(
                request,
                "profile.html",
                400,
                token=token,
                profile=before,
                values=fields,
                years=service.YEAR_WORDS,
                next=then,
                error=str(error),
                tab="community",
            )
        service.log_event(store, token, "profile", "new" if before is None else "changed")
        if then:
            return RedirectResponse(then, 303)
        if before is None:
            return RedirectResponse(f"/p/{token}/community?tab=explore&saved=joined", 303)
        return RedirectResponse(f"/p/{token}/u/{saved['handle']}?saved=profile", 303)

    @app.post("/p/{token}/profile/leave")
    def profile_leave(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        service.leave_network(store, token)
        service.log_event(store, token, "profile", "left")
        return RedirectResponse(f"/p/{token}/progress?saved=left#diary", 303)

    @app.get("/p/{token}/people", response_class=HTMLResponse)
    def people(request: Request, token: str, q: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return page(
            request,
            "people.html",
            token=token,
            profile=service.profile_of(store, token),
            view=service.people_view(store, subscription, q[:80]),
            tab="community",
        )

    @app.get("/p/{token}/u/{handle}", response_class=HTMLResponse)
    def person(request: Request, token: str, handle: str, saved: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        view = service.person_view(store, subscription, handle, config.clock())
        if view is None:
            return page(
                request,
                "message.html",
                404,
                title=service.translate("Not found"),
                message=service.translate("there is nobody called @{handle}", handle=handle[:40]),
                token=token,
            )
        return page(
            request,
            "person.html",
            token=token,
            **view,
            profile=service.profile_of(store, token),
            saved=_notice(saved),
            tab="progress" if view["me"] else "community",
        )

    async def circle_change(
        request: Request, token: str, change: Callable[[], Any], fallback: str
    ) -> Response:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=3)
        try:
            change()
        except service.ServiceError as error:
            return refused(request, token, error)
        return RedirectResponse(back(form, token, fallback), 303)

    @app.post("/p/{token}/u/{handle}/follow")
    async def follow(request: Request, token: str, handle: str) -> Response:
        return await circle_change(
            request, token, lambda: service.follow(store, token, handle), f"/p/{token}/u/{handle}"
        )

    @app.post("/p/{token}/u/{handle}/unfollow")
    async def unfollow(request: Request, token: str, handle: str) -> Response:
        return await circle_change(
            request, token, lambda: service.unfollow(store, token, handle), f"/p/{token}/u/{handle}"
        )

    @app.post("/p/{token}/requests/{handle}")
    async def answer_request(request: Request, token: str, handle: str) -> Response:
        form = await request.form(max_files=0, max_fields=3)
        accept = form.get("answer") == "accept"
        return await circle_change(
            request, token, lambda: service.answer_follow(store, token, handle, accept), f"/p/{token}/people"
        )

    @app.post("/p/{token}/followers/{handle}/remove")
    async def remove_follower(request: Request, token: str, handle: str) -> Response:
        return await circle_change(
            request, token, lambda: service.remove_follower(store, token, handle), f"/p/{token}/people"
        )

    @app.get("/p/{token}/explain", response_class=HTMLResponse)
    def explain_form(request: Request, token: str) -> HTMLResponse:
        if load(token) is None:
            return missing(request)
        return page(
            request,
            "explain.html",
            token=token,
            draft={},
            profile=service.profile_of(store, token),
            tab="community",
        )

    @app.post("/p/{token}/explain", response_class=HTMLResponse)
    async def explain_save(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        profile = service.profile_of(store, token)
        if int(request.headers.get("content-length") or 0) > MAX_POST_UPLOAD:
            error = service.translate("These photos are too large together; send fewer or smaller ones.")
            return page(
                request,
                "explain.html",
                413,
                token=token,
                draft={},
                profile=profile,
                error=error,
                tab="community",
            )
        form = await request.form(max_files=service.social.MAX_PHOTOS, max_fields=10)
        fields = {k: str(v) for k, v in form.items() if not isinstance(v, UploadFile)}
        photos = [await f.read() for f in form.getlist("photos") if isinstance(f, UploadFile) and f.filename]
        try:
            post = service.post_explanation(store, subscription, fields, photos, now=config.clock())
        except service.ServiceError as error:
            return page(
                request,
                "explain.html",
                400,
                token=token,
                draft=fields,
                profile=profile,
                error=str(error),
                tab="community",
            )
        service.log_event(store, token, "explained", post.visibility)
        return RedirectResponse(f"/p/{token}/post/{post.id}?saved=posted", 303)

    @app.get("/p/{token}/notes", response_class=HTMLResponse)
    def notes_library(
        request: Request,
        token: str,
        course: str = "",
        uni: str = "",
        prog: str = "",
        sort: str = "helpful",
        saved: str = "",
    ) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        service.log_visit(store, token, config.clock())
        view = service.library_view(
            store,
            subscription,
            filters={"course": course, "uni": uni, "prog": prog},
            sort=sort,
            now=config.clock(),
        )
        return page(request, "notes.html", token=token, view=view, saved=_notice(saved), tab="community")

    @app.get("/p/{token}/notes/new", response_class=HTMLResponse)
    def notes_form(request: Request, token: str) -> HTMLResponse:
        if load(token) is None:
            return missing(request)
        return page(
            request,
            "notes_new.html",
            token=token,
            draft={},
            profile=service.profile_of(store, token),
            tab="community",
        )

    @app.post("/p/{token}/notes/new", response_class=HTMLResponse)
    async def notes_save(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        profile = service.profile_of(store, token)

        def again(status: int, error: str, draft: dict) -> HTMLResponse:
            return page(
                request,
                "notes_new.html",
                status,
                token=token,
                draft=draft,
                profile=profile,
                error=error,
                tab="community",
            )

        if int(request.headers.get("content-length") or 0) > MAX_NOTES_UPLOAD:
            return again(
                413,
                service.translate("These photos are too large together; send fewer or smaller ones."),
                {},
            )
        form = await request.form(max_files=service.social.MAX_NOTE_PHOTOS, max_fields=10)
        fields = {k: str(v) for k, v in form.items() if not isinstance(v, UploadFile)}
        photos = [await f.read() for f in form.getlist("photos") if isinstance(f, UploadFile) and f.filename]
        try:
            post = service.post_notes(store, subscription, fields, photos, now=config.clock())
        except service.ServiceError as error:
            return again(400, str(error), fields)
        service.log_event(store, token, "notes", post.visibility)
        return RedirectResponse(f"/p/{token}/post/{post.id}?saved=posted", 303)

    # ------------------------------------------------------------ study groups (D28)

    @app.get("/p/{token}/groups", response_class=HTMLResponse)
    def groups(request: Request, token: str, saved: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        service.log_visit(store, token, config.clock())
        view = service.groups_view(store, subscription, config.clock())
        return page(request, "groups.html", token=token, view=view, saved=_notice(saved), tab="community")

    @app.post("/p/{token}/groups", response_class=HTMLResponse)
    async def group_create(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=4)
        fields = {k: str(v) for k, v in form.items()}
        try:
            group = service.create_group(store, token, fields, now=config.clock())
        except service.ServiceError as error:
            view = service.groups_view(store, subscription, config.clock())
            return page(
                request,
                "groups.html",
                400,
                token=token,
                view=view,
                error=str(error),
                draft=fields,
                tab="community",
            )
        service.log_event(store, token, "group", "created")
        return RedirectResponse(f"/p/{token}/groups/{group.id}?saved=group", 303)

    def group_page(
        request: Request, token: str, group_id: str, status: int = 200, **extra: Any
    ) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        view = service.group_view(store, subscription, group_id, config.clock())
        if view is None:
            return page(
                request,
                "message.html",
                404,
                title=service.translate("Not found"),
                message=service.translate("this group is not, or no longer, yours"),
                token=token,
            )
        if view.get("invite"):
            view["link"] = invite_link(request, view["invite"])
        return page(request, "group.html", status, token=token, view=view, tab="community", **extra)

    # -- invitation links (D40) ---------------------------------------------------

    def invite_link(request: Request, code: str) -> str:
        return f"{base(request)}/g/{code}"

    @app.get("/g/{code}", response_class=HTMLResponse)
    def invitation(request: Request, code: str) -> HTMLResponse:
        """An invitation link, as anyone who opens it sees it: the group, and how to
        join with one's own plan (this device's, remembered by pwa.js, or another)."""
        view = service.invitation_view(store, code)
        if view is None:
            return page(
                request,
                "message.html",
                404,
                title=service.translate("Not found"),
                message=service.translate("this invitation link does not work any more"),
            )
        return page(request, "invitation.html", invite=view)

    @app.get("/p/{token}/join/{code}", response_class=HTMLResponse)
    def join_page(request: Request, token: str, code: str) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        view = service.invitation_view(store, code)
        if view is None:
            return page(
                request,
                "message.html",
                404,
                title=service.translate("Not found"),
                message=service.translate("this invitation link does not work any more"),
                token=token,
            )
        return page(
            request,
            "join.html",
            token=token,
            invite=view,
            profile=service.profile_of(store, token),
            tab="community",
        )

    @app.post("/p/{token}/join/{code}", response_class=HTMLResponse)
    def join_group(request: Request, token: str, code: str) -> Response:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        try:
            group_id = service.join_group_by_code(store, token, code, now=config.clock())
        except service.ServiceError as error:
            view = service.invitation_view(store, code)
            return page(
                request,
                "join.html" if view else "message.html",
                400,
                token=token,
                invite=view,
                profile=service.profile_of(store, token),
                error=str(error),
                title=service.translate("Not found"),
                message=str(error),
                tab="community",
            )
        service.log_event(store, token, "group", "joined by link")
        return RedirectResponse(f"/p/{token}/groups/{group_id}?saved=joined_group", 303)

    @app.post("/p/{token}/join")
    async def join_pasted(request: Request, token: str) -> Response:
        """An invitation pasted on the Groups page: the link or its code."""
        if load(token) is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=2)
        code = service.invite_code_from(str(form.get("code") or ""))
        if not code:
            return RedirectResponse(f"/p/{token}/groups?saved=bad_invite", 303)
        return RedirectResponse(f"/p/{token}/join/{code}", 303)

    @app.post("/p/{token}/groups/{group_id}/link")
    async def group_link(request: Request, token: str, group_id: str) -> Response:
        """Make the group's invitation link, or (the owner) replace it."""
        if load(token) is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=2)
        try:
            service.group_invite_code(store, token, group_id, renew=form.get("renew") == "1")
        except service.ServiceError as error:
            return group_page(request, token, group_id, 400, error=str(error))
        return RedirectResponse(f"/p/{token}/groups/{group_id}#invite", 303)

    @app.get("/p/{token}/groups/{group_id}", response_class=HTMLResponse)
    def group(request: Request, token: str, group_id: str, saved: str = "") -> HTMLResponse:
        if load(token) is None:
            return missing(request)
        service.log_visit(store, token, config.clock())
        return group_page(request, token, group_id, saved=_notice(saved))

    async def group_change(
        request: Request, token: str, group_id: str, change: Callable[[dict], Any], done: str
    ) -> Response:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=4)
        fields = {k: str(v) for k, v in form.items()}
        try:
            change(fields)
        except service.ServiceError as error:
            return group_page(request, token, group_id, 400, error=str(error), draft=fields)
        return RedirectResponse(done, 303)

    @app.post("/p/{token}/groups/{group_id}/invite", response_class=HTMLResponse)
    async def group_invite(request: Request, token: str, group_id: str) -> Response:
        return await group_change(
            request,
            token,
            group_id,
            lambda f: service.invite_to_group(
                store, token, group_id, f.get("handle", ""), now=config.clock()
            ),
            f"/p/{token}/groups/{group_id}?saved=invited",
        )

    @app.post("/p/{token}/groups/{group_id}/settings", response_class=HTMLResponse)
    async def group_settings(request: Request, token: str, group_id: str) -> Response:
        return await group_change(
            request,
            token,
            group_id,
            lambda f: service.change_group(store, token, group_id, f),
            f"/p/{token}/groups/{group_id}?saved=group_saved",
        )

    @app.post("/p/{token}/groups/{group_id}/remove/{handle}", response_class=HTMLResponse)
    async def group_remove(request: Request, token: str, group_id: str, handle: str) -> Response:
        return await group_change(
            request,
            token,
            group_id,
            lambda f: service.remove_from_group(store, token, group_id, handle),
            f"/p/{token}/groups/{group_id}",
        )

    @app.post("/p/{token}/groups/{group_id}/answer", response_class=HTMLResponse)
    async def group_answer(request: Request, token: str, group_id: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=3)
        accept = form.get("answer") == "accept"
        try:
            joined = service.answer_group(store, token, group_id, accept, now=config.clock())
        except service.ServiceError as error:
            return refused(request, token, error)
        if joined:
            service.log_event(store, token, "group", "joined")
            return RedirectResponse(f"/p/{token}/groups/{group_id}?saved=joined_group", 303)
        return RedirectResponse(f"/p/{token}/groups", 303)

    @app.post("/p/{token}/groups/{group_id}/leave")
    def group_leave(request: Request, token: str, group_id: str) -> Response:
        if load(token) is None:
            return missing(request)
        service.leave_group(store, token, group_id)
        service.log_event(store, token, "group", "left")
        return RedirectResponse(f"/p/{token}/groups?saved=left_group", 303)

    # ------------------------------------------------------------ flashcards (D29)

    def cards_page(
        request: Request, subscription: service.Subscription, course: str, status: int = 200, **extra: Any
    ) -> HTMLResponse:
        view = service.cards_view(store, subscription, config.clock(), course=course[:80])
        return page(request, "cards.html", status, token=subscription.token, view=view, tab="focus", **extra)

    @app.get("/p/{token}/cards", response_class=HTMLResponse)
    def cards_list(
        request: Request, token: str, course: str = "", saved: str = "", added: int = 0
    ) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        service.log_visit(store, token, config.clock())
        notice = service.translate_plural("{n} card added.", "{n} cards added.", added) if added else None
        if saved in CARD_NOTICES:
            notice = service.translate(CARD_NOTICES[saved])
        return cards_page(request, subscription, course, saved=notice)

    @app.post("/p/{token}/cards", response_class=HTMLResponse)
    async def cards_add(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=6)
        fields = {k: str(v) for k, v in form.items()}
        try:
            added = service.add_cards(store, subscription, fields, now=config.clock())
        except service.ServiceError as error:
            return cards_page(
                request, subscription, fields.get("course", ""), 400, error=str(error), draft=fields
            )
        service.log_event(store, token, "cards", str(added))
        course = quote(fields.get("course", "").strip())
        return RedirectResponse(f"/p/{token}/cards?course={course}&added={added}", 303)

    @app.get("/p/{token}/cards/review", response_class=HTMLResponse)
    def cards_review(request: Request, token: str, course: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        view = service.card_review_view(store, subscription, config.clock(), course=course[:80])
        return page(request, "cards_review.html", token=token, view=view, words=CARD_WORDS, tab="focus")

    @app.post("/p/{token}/cards/{card_id}/answer")
    async def cards_answer(
        request: Request, token: str, card_id: str, background: BackgroundTasks
    ) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=3)
        course = str(form.get("course") or "")[:80]
        try:
            service.answer_card(
                store, subscription, card_id, str(form.get("grade") or ""), now=config.clock()
            )
        except service.ServiceError as error:
            return refused(request, token, error)
        # The student's own parameters, when a fit is due (D53): after the answer.
        background.add_task(service.refit_cards, store, subscription, config.clock())
        return RedirectResponse(f"/p/{token}/cards/review?course={quote(course)}", 303)

    @app.get("/p/{token}/cards/{card_id}", response_class=HTMLResponse)
    def card_page(request: Request, token: str, card_id: str) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        card = service.card_view(store, subscription, card_id)
        if card is None:
            return page(
                request,
                "message.html",
                404,
                title=service.translate("Not found"),
                message=service.translate("this card is not, or no longer, there"),
                token=token,
            )
        return page(request, "card.html", token=token, card=card, draft=card, tab="focus")

    @app.post("/p/{token}/cards/{card_id}", response_class=HTMLResponse)
    async def card_save(request: Request, token: str, card_id: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=5)
        fields = {k: str(v) for k, v in form.items()}
        card = service.card_view(store, subscription, card_id)
        try:
            service.edit_card(store, subscription, card_id, fields)
        except service.ServiceError as error:
            if card is None:
                return refused(request, token, error)
            return page(
                request, "card.html", 400, token=token, card=card, draft=fields, error=str(error), tab="focus"
            )
        return RedirectResponse(
            f"/p/{token}/cards?course={quote(fields.get('course', ''))}&saved=card_saved", 303
        )

    @app.post("/p/{token}/cards/{card_id}/delete")
    def card_delete(request: Request, token: str, card_id: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        card = service.card_view(store, subscription, card_id)
        service.delete_card(store, subscription, card_id)
        course = quote(card["course"]) if card else ""
        return RedirectResponse(f"/p/{token}/cards?course={course}&saved=card_deleted", 303)

    @app.get("/p/{token}/report/{kind}/{target}", response_class=HTMLResponse)
    def report_form(request: Request, token: str, kind: str, target: str) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        item = service.report_view(store, subscription, kind, target)
        if item is None or item["mine"]:
            return page(
                request,
                "message.html",
                404,
                title=service.translate("Not found"),
                message=service.translate("this post is not, or no longer, visible to you"),
                token=token,
            )
        return page(
            request, "report.html", token=token, item=item, reasons=service.REPORT_REASONS, tab="community"
        )

    @app.post("/p/{token}/report/{kind}/{target}", response_class=HTMLResponse)
    async def report_send(request: Request, token: str, kind: str, target: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=4)
        item = service.report_view(store, subscription, kind, target)
        try:
            if item is None:
                raise service.InvalidInput(
                    service.translate("this post is not, or no longer, visible to you")
                )
            service.report(store, token, kind, target, str(form.get("reason") or ""))
            if form.get("block") == "1" and item["author"]:
                service.block(store, token, item["author"]["handle"])
        except service.ServiceError as error:
            if item is None:
                return refused(request, token, error)
            return page(
                request,
                "report.html",
                400,
                token=token,
                item=item,
                reasons=service.REPORT_REASONS,
                error=str(error),
                tab="community",
            )
        service.log_event(store, token, "flagged", kind)
        blocked = form.get("block") == "1"
        return RedirectResponse(f"/p/{token}/community?saved={'blocked' if blocked else 'reported'}", 303)

    @app.post("/p/{token}/u/{handle}/block")
    async def block(request: Request, token: str, handle: str) -> Response:
        done = await circle_change(
            request, token, lambda: service.block(store, token, handle), f"/p/{token}/community?saved=blocked"
        )
        return done

    @app.post("/p/{token}/u/{handle}/unblock")
    async def unblock(request: Request, token: str, handle: str) -> Response:
        return await circle_change(
            request, token, lambda: service.unblock(store, token, handle), f"/p/{token}/people"
        )

    # ------------------------------------------------------------ the owner's review

    def admin_key(key: str) -> bool:
        """Whether `key` is the owner's review key (CPS_ADMIN_TOKEN). Without one
        set, or with one too short to be secret, there is no review page."""
        expected = config.admin_token or ""
        return len(expected) >= 24 and secrets.compare_digest(key.encode(), expected.encode())

    @app.get("/admin/{key}", response_class=HTMLResponse)
    def admin(request: Request, key: str, saved: str = "") -> HTMLResponse:
        if not admin_key(key):
            return missing(request)
        notice = service.translate("Done.") if saved else None
        return page(request, "admin.html", key=key, items=service.moderation_view(store), saved=notice)

    @app.post("/admin/{key}/review")
    async def admin_review(request: Request, key: str) -> Response:
        if not admin_key(key):
            return missing(request)
        form = await request.form(max_files=0, max_fields=4)
        keep = form.get("decision") == "keep"
        service.moderate(store, str(form.get("kind") or ""), str(form.get("target") or ""), keep)
        return RedirectResponse(f"/admin/{key}?saved=1", 303)

    @app.get("/admin/{key}/m/{name}")
    def admin_photo(key: str, name: str) -> Response:
        found = service.admin_photo(store, name) if admin_key(key) else None
        if found is None:
            return Response("not found\n", 404, media_type="text/plain")
        return Response(found[0], media_type=found[1], headers={"Cache-Control": "no-store"})

    @app.post("/p/{token}/review/seen")
    async def review_seen(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=3)
        week = str(form.get("week") or "")
        try:
            service.update_subscription(store, token, lambda current: service.hide_review(current, week))
        except service.ServiceError as error:
            return page(
                request,
                "message.html",
                400,
                title=service.translate("Not saved"),
                message=str(error),
                token=token,
            )
        return RedirectResponse(f"/p/{token}", 303)

    @app.post("/p/{token}/language")
    async def language(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=3)
        chosen = str(form.get("lang") or "")
        try:
            service.update_subscription(
                store, token, lambda current: service.set_language(current, chosen, now=config.clock())
            )
        except service.ServiceError as error:
            return page(
                request,
                "message.html",
                400,
                title=service.translate("Not saved"),
                message=str(error),
                token=token,
            )
        service.log_event(store, token, "language", chosen)
        return RedirectResponse(f"/p/{token}/settings?saved=language", 303)

    @app.post("/p/{token}/appearance")
    async def appearance(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=3)
        chosen = str(form.get("theme") or "")
        try:
            service.update_subscription(store, token, lambda current: service.set_theme(current, chosen))
        except service.ServiceError as error:
            return refused(request, token, error)
        service.log_event(store, token, "theme", chosen)
        return RedirectResponse(f"/p/{token}/settings?saved=theme#appearance", 303)

    @app.post("/p/{token}/motion")
    async def motion(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=3)
        chosen = str(form.get("motion") or "")
        try:
            service.update_subscription(store, token, lambda current: service.set_motion(current, chosen))
        except service.ServiceError as error:
            return refused(request, token, error)
        service.log_event(store, token, "motion", chosen)
        return RedirectResponse(f"/p/{token}/settings?saved=theme#appearance", 303)

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
        service.log_event(store, token, kind, str(body.get("outcome", body.get("action", ""))))
        return JSONResponse(view)

    # A class of the timetable skipped, caught up on alone, or kept (D60).
    @app.post("/p/{token}/lecture.json")
    async def lecture_json(request: Request, token: str, start: str | None = None, days: int = 7) -> Response:
        """From the calendar: answered with the calendar as it is after the change."""

        def change(current: service.Subscription, body: dict) -> service.Subscription:
            return service.change_lecture(
                current,
                str(body.get("at") or ""),
                str(body.get("ref") or ""),
                str(body.get("action") or ""),
                every_week=bool(body.get("every_week")),
                now=config.clock(),
            )

        return await session_change(request, token, "", start, days, change, "lecture")

    @app.post("/p/{token}/lecture")
    async def lecture_form(request: Request, token: str) -> Response:
        """From the week's list, the calendar for a browser without scripts."""
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=6)
        fields = {k: str(v) for k, v in form.items()}
        # One form, one button per choice: "skip-weekly" is a skip every week.
        action = fields.get("action", "")
        weekly = action.endswith("-weekly")
        try:
            service.update_subscription(
                store,
                token,
                lambda current: service.change_lecture(
                    current,
                    fields.get("at", ""),
                    fields.get("ref", ""),
                    action.removesuffix("-weekly"),
                    every_week=weekly,
                    now=config.clock(),
                ),
            )
        except service.ServiceError as error:
            return refused(request, token, error)
        service.log_event(store, token, "lecture", fields.get("action", ""))
        week = fields.get("w", "0")
        return RedirectResponse(f"/p/{token}/agenda?w={int(week) if week.lstrip('-').isdigit() else 0}", 303)

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
    def settings(request: Request, token: str, new: int = 0, saved: str = "") -> HTMLResponse:
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
            saved=_notice(saved),
            ai={"available": service.ai_available(), "on": service.ai_on(subscription)},
            account=service.account_view(store, subscription, config.clock()),
            reminders=service.reminders_view(store, subscription),
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
            # The offered exam rows came back as typed rows: not offered twice. Each
            # keeps what its exam practice is when not chosen (D46).
            stored = service.setup_view(subscription)
            defaults = {s["name"]: s["practice_default"] for s in stored["subjects"]}
            shown = {
                **stored,
                **values,
                "subjects": [
                    {**s, "practice_default": defaults.get(s["name"], service.PRACTICE_UNSIZED_HOURS)}
                    for s in values["subjects"]
                ],
                "suggested": [],
            }
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
                ai={"available": service.ai_available(), "on": service.ai_on(subscription)},
                account=service.account_view(store, subscription, config.clock()),
                reminders=service.reminders_view(store, subscription),
            )
        service.log_event(store, token, "settings")
        return RedirectResponse(f"/p/{token}/routine?new=1" if new else f"/p/{token}?saved=1", 303)

    # -- the questions about the week (D42) --------------------------------------

    @app.get("/p/{token}/routine", response_class=HTMLResponse)
    def routine(request: Request, token: str, new: int = 0) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return page(
            request,
            "routine.html",
            token=token,
            view=service.routine_view(subscription),
            new=bool(new),
            typed=[],
            tab="settings",
        )

    @app.post("/p/{token}/routine", response_class=HTMLResponse)
    async def save_routine(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=200)
        new = form.get("new") == "1"
        answers: list[dict] = []
        try:
            commute, answers = forms.routine_answers(form)
            service.update_subscription(
                store,
                token,
                lambda current: service.answer_routine(
                    current,
                    commute_minutes=commute,
                    answers=answers,
                    now=config.clock(),
                    study_time=str(form.get("study_time") or "") or None,
                    study_on_the_way=bool(form.get("study_on_the_way")) if form.get("way_shown") else None,
                ),
            )
        except service.ServiceError as error:
            view = service.routine_view(subscription)
            if str(form.get("commute") or "").isdigit():
                view["commute"] = int(str(form.get("commute")))
            if form.get("study_time"):
                view["study_time"] = str(form.get("study_time"))
            if form.get("way_shown"):
                view["study_on_the_way"] = bool(form.get("study_on_the_way"))
            return page(
                request,
                "routine.html",
                400,
                token=token,
                view=view,
                new=new,
                typed=answers,
                error=str(error),
                tab="settings",
            )
        service.log_event(store, token, "routine", str(sum(1 for a in answers if a["days"])))
        return RedirectResponse(f"/p/{token}/week?new=1" if new else f"/p/{token}/week?saved=1", 303)

    # -- before friends try it (D43): feedback, a copy of one's data, terms, errors --

    @app.get("/p/{token}/feedback", response_class=HTMLResponse)
    def feedback_form(request: Request, token: str, page_from: str = Query("", alias="from")) -> HTMLResponse:
        if load(token) is None:
            return missing(request)
        return page(
            request,
            "feedback.html",
            token=token,
            kinds=service.FEEDBACK_KINDS,
            values={"from": page_from[:200]},
            tab="settings",
        )

    @app.post("/p/{token}/feedback", response_class=HTMLResponse)
    async def feedback_send(request: Request, token: str) -> HTMLResponse:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=10)
        values = {k: str(form.get(k) or "") for k in ("kind", "body", "from")}
        try:
            service.send_feedback(
                store,
                token,
                kind=values["kind"],
                body=values["body"],
                page=values["from"],
                now=config.clock(),
            )
        except service.ServiceError as error:
            return page(
                request,
                "feedback.html",
                400,
                token=token,
                kinds=service.FEEDBACK_KINDS,
                values=values,
                error=str(error),
                tab="settings",
            )
        return page(
            request,
            "message.html",
            token=token,
            title=service.translate("Thank you"),
            message=service.translate(
                "Your message is on its way to the person who makes this app, who reads every one."
            ),
        )

    @app.get("/p/{token}/export")
    def export(request: Request, token: str) -> Response:
        """A copy of everything kept under the plan's address, as a file (D43)."""
        if load(token) is None:
            return missing(request)
        now = config.clock()
        data = service.export_data(store, token, now)
        return Response(
            json.dumps(data, ensure_ascii=False, indent=1),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="study-plan-{now:%Y-%m-%d}.json"'},
        )

    @app.get("/terms", response_class=HTMLResponse)
    def terms(request: Request) -> HTMLResponse:
        return page(request, "terms.html", contact=config.contact)

    @app.post("/errors")
    async def browser_error(request: Request) -> Response:
        """An error a page's script met in the browser (`ui.js`), for the owner's log
        (D43): where, what, and the line; at most a few per address and minute."""
        if not errors_in.allow(client(request)):
            return Response(status_code=429)
        if int(request.headers.get("content-length") or 0) > 4096:
            return Response(status_code=413)
        try:
            body = json.loads(await request.body())
        except ValueError:
            return Response(status_code=400)
        if not isinstance(body, dict):
            return Response(status_code=400)
        where = f"{body.get('file') or ''}:{body.get('line') or ''}:{body.get('column') or ''}"
        service.record_error(
            store,
            source="browser",
            route=service.page_of(str(body.get("page") or "")),
            kind=str(body.get("kind") or "Error")[:80],
            message=str(body.get("message") or ""),
            detail=f"{where}\n{body.get('stack') or ''}\n{request.headers.get('user-agent', '')[:160]}",
            now=config.clock(),
        )
        return Response(status_code=204)

    @app.get("/admin/{key}/inbox", response_class=HTMLResponse)
    def admin_inbox(request: Request, key: str) -> HTMLResponse:
        if not admin_key(key):
            return missing(request)
        return page(request, "admin_inbox.html", key=key, view=service.inbox_view(store))

    @app.post("/admin/{key}/feedback/{ident}/seen")
    def admin_feedback_seen(request: Request, key: str, ident: int) -> Response:
        if not admin_key(key):
            return missing(request)
        service.mark_feedback_seen(store, ident)
        return RedirectResponse(f"/admin/{key}/inbox", 303)

    # -- accounts that last (D35, D36) -------------------------------------------

    def relying_party(request: Request) -> tuple[str, str]:
        """The origin passkeys are made on, and the relying party's id (its host
        name): the public address when the deployment gives one, else the request's."""
        origin = base(request)
        return origin, urlsplit(origin).hostname or ""

    async def json_body(request: Request, limit: int = 16 * 1024) -> dict:
        if int(request.headers.get("content-length") or 0) > limit:
            raise service.InvalidInput("too long")
        try:
            body = await request.json()
        except ValueError as error:
            raise service.InvalidInput("not JSON") from error
        if not isinstance(body, dict):
            raise service.InvalidInput("not an object")
        return body

    @app.post("/p/{token}/passkeys/options")
    def passkey_options(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return JSONResponse({"message": "no such plan"}, 404)
        if not changes.allow(token):
            return JSONResponse({"message": service.translate("Too many requests; try later.")}, 429)
        _origin, rp_id = relying_party(request)
        return JSONResponse(service.passkey_options(store, subscription, rp_id=rp_id, now=config.clock()))

    @app.post("/p/{token}/passkeys")
    async def passkey_add(request: Request, token: str) -> Response:
        if load(token) is None:
            return JSONResponse({"message": "no such plan"}, 404)
        if not changes.allow(token):
            return JSONResponse({"message": service.translate("Too many requests; try later.")}, 429)
        origin, rp_id = relying_party(request)
        try:
            service.add_passkey(
                store,
                token,
                await json_body(request),
                origin=origin,
                rp_id=rp_id,
                user_agent=request.headers.get("user-agent", ""),
                now=config.clock(),
            )
        except service.SignInFailed as error:
            LOG.info("passkey refused: %s", error.__cause__)
            return JSONResponse(error.to_dict(), 400)
        except service.ServiceError as error:
            return JSONResponse(error.to_dict(), 400)
        return JSONResponse({"go": f"/p/{token}/settings?saved=passkey#account"})

    @app.post("/p/{token}/passkeys/{passkey}/remove")
    def passkey_remove(request: Request, token: str, passkey: str) -> Response:
        if load(token) is None:
            return missing(request)
        service.remove_passkey(store, token, passkey)
        return RedirectResponse(f"/p/{token}/settings?saved=passkey_removed#account", 303)

    @app.post("/p/{token}/recovery", response_class=HTMLResponse)
    def recovery_code(request: Request, token: str) -> HTMLResponse:
        """A new recovery code, shown once, in the answer to a form: never on a page
        a browser or the service worker would keep."""
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        code = service.make_recovery_code(store, token, config.clock())
        return page(request, "recovery.html", token=token, code=code, recovered=False, tab="settings")

    # -- a weekly budget from what is kept (D47) ---------------------------------

    @app.post("/p/{token}/budget")
    async def take_budget(request: Request, token: str) -> Response:
        """The budget Today offered, taken with one tap: the plan is made again."""
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=4)
        try:
            hours = float(str(form.get("hours") or "").replace(",", "."))
            if not 0 < hours <= 80:
                raise ValueError(hours)
        except ValueError:
            return page(
                request,
                "message.html",
                400,
                title=service.translate("Not saved"),
                message=service.translate("the weekly hours must be positive, or left out for no limit"),
            )
        service.update_subscription(
            store,
            token,
            lambda current: service.revise_subscription(
                current, preferences={"weekly_hours": hours}, now=config.clock(), fetch=config.fetch
            ),
        )
        service.log_event(store, token, "budget")
        return RedirectResponse(f"/p/{token}?saved=1", 303)

    @app.post("/p/{token}/budget/later")
    def budget_later(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        service.update_subscription(
            store, token, lambda current: service.budget_later(current, config.clock())
        )
        return RedirectResponse(f"/p/{token}", 303)

    # -- how an exam went (D56) ---------------------------------------------------

    @app.post("/p/{token}/exam")
    async def exam_answer(request: Request, token: str) -> Response:
        """How an exam went, asked on Today for three weeks after it; or "Rather
        not say"."""
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=6)
        fields = {k: str(v) for k, v in form.items()}
        try:
            service.update_subscription(
                store,
                token,
                lambda current: service.answer_exam(
                    current, fields.get("course", ""), fields, config.clock()
                ),
            )
        except service.ServiceError as error:
            return refused(request, token, error)
        service.log_event(store, token, "exam", "skipped" if fields.get("skip") else "answered")
        return RedirectResponse(f"/p/{token}?saved=exam", 303)

    @app.post("/p/{token}/account/later")
    def account_later(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        service.update_subscription(
            store, token, lambda current: service.account_later(current, config.clock())
        )
        return RedirectResponse(f"/p/{token}", 303)

    @app.post("/signin/options")
    def signin_options(request: Request) -> Response:
        if not signins.allow(client(request)):
            return JSONResponse({"message": service.translate("Too many requests; try later.")}, 429)
        _origin, rp_id = relying_party(request)
        return JSONResponse(service.signin_options(store, rp_id=rp_id, now=config.clock()))

    @app.post("/signin")
    async def signin(request: Request) -> Response:
        if not signins.allow(client(request)):
            return JSONResponse({"message": service.translate("Too many requests; try later.")}, 429)
        origin, rp_id = relying_party(request)
        try:
            token = service.sign_in_with_passkey(
                store, await json_body(request), origin=origin, rp_id=rp_id, now=config.clock()
            )
        except service.SignInFailed as error:
            LOG.info("sign-in refused: %s", error.__cause__)
            return JSONResponse(error.to_dict(), 400)
        except service.ServiceError as error:
            return JSONResponse(error.to_dict(), 400)
        return JSONResponse({"go": f"/p/{token}"})

    @app.post("/recover", response_class=HTMLResponse)
    async def recover(request: Request) -> HTMLResponse:
        if not signins.allow(client(request)):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=3)
        try:
            token, code = service.recover(store, str(form.get("code") or ""), config.clock())
        except service.SignInFailed as error:
            return page(request, "home.html", 400, signin_error=str(error), tz="Europe/Paris")
        return page(request, "recovery.html", token=token, code=code, recovered=True)

    @app.get("/p/{token}/semester", response_class=HTMLResponse)
    def semester(request: Request, token: str) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        return page(request, "semester.html", token=token, tab="settings", link="")

    @app.post("/p/{token}/semester", response_class=HTMLResponse)
    async def semester_start(request: Request, token: str) -> Response:
        """The next semester (D36): the same address, a new timetable."""
        if load(token) is None:
            return missing(request)
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD + 64 * 1024:
            return page(
                request,
                "semester.html",
                413,
                token=token,
                tab="settings",
                error=service.translate("That file is too large for a timetable."),
            )
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=1, max_fields=6)
        link = str(form.get("link") or "").strip() or None
        draw = form.get("draw") == "1"
        upload = form.get("file")
        ics = None
        if isinstance(upload, UploadFile) and upload.filename:
            ics = await upload.read(MAX_UPLOAD + 1)
            if len(ics) > MAX_UPLOAD:
                return page(
                    request,
                    "semester.html",
                    413,
                    token=token,
                    tab="settings",
                    error=service.translate("That file is too large for a timetable."),
                )
        try:
            if link is None and ics is None and not draw:
                raise service.InvalidInput(
                    service.translate("paste your timetable's link, or choose its file")
                )
            fresh = service.update_subscription(
                store,
                token,
                lambda current: service.start_next_semester(
                    current,
                    source_url=link,
                    ics=None if link else ics,
                    draw=draw,
                    now=config.clock(),
                    fetch=config.fetch,
                ),
            )
        except service.ServiceError as error:
            return page(
                request, "semester.html", 400, token=token, tab="settings", error=str(error), link=link or ""
            )
        if fresh is None:
            return missing(request)
        drawn = link is None and ics is None
        service.log_event(store, token, "semester", "link" if link else "draw" if drawn else "file")
        if drawn:
            return RedirectResponse(f"/p/{token}/timetable", 303)
        return RedirectResponse(f"/p/{token}/settings?new=1", 303)

    # -- reminders (D38) ---------------------------------------------------------

    @app.post("/p/{token}/push")
    async def push_on(request: Request, token: str) -> Response:
        """A device's push subscription, as the browser gave it (push.js)."""
        if load(token) is None:
            return JSONResponse({"message": "no such plan"}, 404)
        if not changes.allow(token):
            return JSONResponse({"message": service.translate("Too many requests; try later.")}, 429)
        try:
            service.add_push_device(
                store,
                token,
                await json_body(request, 8 * 1024),
                user_agent=request.headers.get("user-agent", ""),
                now=config.clock(),
            )
        except service.ServiceError as error:
            return JSONResponse(error.to_dict(), 400)
        return JSONResponse({"go": f"/p/{token}/settings?saved=reminders#reminders"})

    @app.post("/p/{token}/push/remove")
    async def push_off(request: Request, token: str) -> Response:
        """A device turned off: from the list (a form, by its id) or from the device
        itself (push.js, by its address)."""
        if load(token) is None:
            return missing(request)
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await json_body(request, 8 * 1024)
            except service.ServiceError as error:
                return JSONResponse(error.to_dict(), 400)
            service.remove_push_device(store, token, str(body.get("endpoint") or ""))
            return JSONResponse({"go": f"/p/{token}/settings?saved=reminders_off#reminders"})
        form = await request.form(max_files=0, max_fields=2)
        service.remove_push_device(store, token, str(form.get("device") or ""))
        return RedirectResponse(f"/p/{token}/settings?saved=reminders_off#reminders", 303)

    @app.post("/p/{token}/reminders")
    async def reminders_lead(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=2)
        try:
            minutes = int(str(form.get("lead") or "0"))
            service.update_subscription(
                store, token, lambda current: service.set_reminder_lead(current, minutes)
            )
        except (ValueError, service.ServiceError):
            return RedirectResponse(f"/p/{token}/settings#reminders", 303)
        service.log_event(store, token, "reminders", f"lead {minutes}")
        return RedirectResponse(f"/p/{token}/settings?saved=1#reminders", 303)

    @app.post("/p/{token}/push/test")
    def push_test(request: Request, token: str) -> Response:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        reached = service.send_test_reminder(store, subscription, **push_options())
        return RedirectResponse(
            f"/p/{token}/settings?saved={'test_sent' if reached else 'test_none'}#reminders", 303
        )

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
        return tasks_page(request, subscription, saved=_notice(saved or ""))

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

    @app.post("/p/{token}/tasks/understand", response_class=HTMLResponse)
    async def understand_task(request: Request, token: str) -> Response:
        """A task typed in one line, read into the new-task form's fields (D12). The
        sheet's script asks for JSON and fills its fields; without the script the
        answer is a page with the same form, filled in, to check and add."""
        subscription = load(token)
        if subscription is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=12)
        words = str(form.get("words") or "")
        wants_json = "application/json" in request.headers.get("accept", "")
        try:
            read = service.understand_task(store, subscription, words, now=config.clock())
        except service.ServiceError as error:
            if wants_json:
                return JSONResponse(error.to_dict(), 400)
            return tasks_page(request, subscription, 400, task_error=str(error))
        service.log_event(store, token, "understood", read["by"])
        if wants_json:
            return JSONResponse(read)
        return page(request, "task_new.html", token=token, read=read, words=words, tab="tasks")

    @app.post("/p/{token}/ai")
    async def ai_setting(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        form = await request.form(max_files=0, max_fields=3)
        on = form.get("on") == "1"
        try:
            service.update_subscription(store, token, lambda current: service.set_ai(current, on))
        except service.ServiceError as error:
            return refused(request, token, error)
        service.log_event(store, token, "ai", "on" if on else "off")
        return RedirectResponse(f"/p/{token}/settings?saved=ai#ai", 303)

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
                title=service.translate("Session not found"),
                message=service.translate(
                    "This session is no longer in your plan: the plan has changed since. "
                    "Your calendar shows the new one after its next refresh."
                ),
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
            return page(
                request,
                "message.html",
                400,
                title=service.translate("Not recorded"),
                message=str(error),
                token=token,
            )
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
            forget=token,  # this device's offline copies go too (pwa.js, D33)
            title=service.translate("Deleted"),
            message=service.translate(
                "Your plan, your timetable, your deadlines and everything you reported are "
                "deleted. Remove the calendar subscription from your calendar app too: it will "
                "stop updating now."
            ),
        )

    return app


def _html(text: str, /, **values: Any) -> Markup:
    """A translated sentence that holds markup (a link, a key in bold): the
    sentence is the catalogue's, trusted; the values are escaped."""
    return Markup(service.translate(text)).format(**values)


def _notice(saved: str) -> str | None:
    if saved == "1":
        return service.translate("Saved. The plan has changed to match.")
    if saved == "logged":
        return service.translate("Saved in your diary.")
    if saved == "exam":
        return service.translate("Thank you. Good luck with the next one.")
    if saved in NETWORK_NOTICES:
        return service.translate(NETWORK_NOTICES[saved])
    if saved in ("ai", "theme"):
        return service.translate("Saved.")
    if saved == "language":
        return service.translate("Language changed. Your sessions are described in it from now on.")
    if saved == "passkey":
        return service.translate(
            "Passkey added. Open your plan with it on the home page, on any device it syncs to."
        )
    if saved == "passkey_removed":
        return service.translate("Passkey removed. Remove it from your device's passwords too.")
    if saved == "bad_invite":
        return service.translate("That is not an invitation link or code.")
    if saved == "reminders":
        return service.translate("Reminders are on for this device.")
    if saved == "reminders_off":
        return service.translate("Reminders are off for that device.")
    if saved == "test_sent":
        return service.translate("Sent. It should arrive in a few seconds.")
    if saved == "test_none":
        return service.translate("No device received it. Turn reminders on again on your phone.")
    notice = TASK_NOTICES.get(saved)
    return service.translate(notice) if notice else None


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
