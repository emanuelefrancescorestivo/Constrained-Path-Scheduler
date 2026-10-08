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
import re
import secrets
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
from markupsafe import Markup
from starlette.datastructures import UploadFile

from cps import service

from . import forms
from .limits import Limiter

HERE = Path(__file__).parent
# A plan's pages and its sessions' pages: the language is the plan's.
PLAN_PATH = re.compile(r"^/(?:p|s)/([A-Za-z0-9_-]{22,64})(?:/|$)")
LOG = logging.getLogger("cps.web")
MAX_UPLOAD = 5 * 1024 * 1024  # the same bound as a timetable read from a link
MAX_POST_UPLOAD = 4 * 3 * 1024 * 1024 + 64 * 1024  # four photos of 3 MB, and the fields
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
    return f"{service.format_day(moment.date(), 'short')} {moment.year}, {moment:%H:%M}"


# What a task change says once it is done, by the `saved` query parameter.
NETWORK_NOTICES = {
    "joined": "Welcome. Here is what students are sharing; follow the ones you like.",
    "profile": "Profile saved.",
    "posted": "Posted.",
    "left": "You have left the network. Your diary is still here, visible only to you.",
    "reported": "Thank you. The report is with the owner of this service.",
    "blocked": "Blocked. Neither of you sees the other any more; undo it in People.",
}
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
    admin_token: str | None = None  # the owner's review page, /admin/<token> (D20); none, no page

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
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["outcome_words"] = OUTCOME_WORDS
    templates.env.globals["familiarity"] = FAMILIARITY
    templates.env.filters["nice_date"] = _nice_date
    templates.env.filters["hours"] = service.format_number
    templates.env.globals["_"] = service.translate
    templates.env.globals["_n"] = service.translate_plural
    templates.env.globals["day_word"] = service.day_word
    templates.env.globals["_h"] = _html
    templates.env.globals["lang"] = service.current_language
    templates.env.globals["languages"] = service.LANGUAGES

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
            title=service.translate("Not found"),
            message=service.translate(
                "This address is not, or no longer, a study plan. Plans are deleted 30 days "
                "after their last exam or deadline, or when their owner deletes them."
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

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return page(request, "home.html", tz="Europe/Paris")

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request) -> HTMLResponse:
        return page(request, "privacy.html", contact=config.contact, retention=service.RETENTION.days)

    @app.get("/guidelines", response_class=HTMLResponse)
    def guidelines(request: Request) -> HTMLResponse:
        return page(request, "guidelines.html")

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
            if link is None and ics is None:
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
        service.log_visit(store, token)
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
            "review": service.review_for_today(subscription, now, logged=logged),
            "focus": subscription.options.get("focus"),
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
            reported=service.translate(OUTCOME_WORDS[reported]) if reported in OUTCOME_WORDS else None,
            saved=_notice(saved),
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
        return page(request, "_panel.html", token=token, **panel_context(subscription))

    @app.get("/p/{token}/progress", response_class=HTMLResponse)
    def progress(request: Request, token: str, saved: str = "") -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        service.log_visit(store, token)
        now = config.clock()
        logged = service.logged_sessions(store, subscription)
        return page(
            request,
            "progress.html",
            token=token,
            view=service.progress_view(subscription, now, logged=logged),
            review=service.weekly_review(subscription, now, logged=logged),
            diary=service.diary_view(store, subscription, now),
            saved=_notice(saved),
            tab="progress",
        )

    # ------------------------------------------------------------ focus sessions

    @app.get("/p/{token}/focus", response_class=HTMLResponse)
    def focus(request: Request, token: str) -> HTMLResponse:
        subscription = load(token)
        if subscription is None:
            return missing(request)
        service.log_visit(store, token)
        return page(
            request,
            "focus.html",
            token=token,
            focus=service.focus_view(subscription, config.clock()),
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
        service.log_visit(store, token)
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
            service.toggle_kudos(store, token, post_id)
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

    @app.get("/p/{token}/profile", response_class=HTMLResponse)
    def profile(request: Request, token: str) -> HTMLResponse:
        if load(token) is None:
            return missing(request)
        current = service.profile_of(store, token)
        return page(
            request, "profile.html", token=token, profile=current, values=current or {}, tab="community"
        )

    @app.post("/p/{token}/profile", response_class=HTMLResponse)
    async def profile_save(request: Request, token: str) -> Response:
        if load(token) is None:
            return missing(request)
        if not changes.allow(token):
            return too_many(request)
        form = await request.form(max_files=0, max_fields=8)
        fields = {k: str(v) for k, v in form.items()}
        before = service.profile_of(store, token)
        try:
            saved = service.save_profile(store, token, fields)
        except service.ServiceError as error:
            return page(
                request,
                "profile.html",
                400,
                token=token,
                profile=before,
                values=fields,
                error=str(error),
                tab="community",
            )
        service.log_event(store, token, "profile", "new" if before is None else "changed")
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
        service.log_event(store, token, "report", kind)
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
    if saved in NETWORK_NOTICES:
        return service.translate(NETWORK_NOTICES[saved])
    if saved == "language":
        return service.translate("Language changed. Your sessions are described in it from now on.")
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
