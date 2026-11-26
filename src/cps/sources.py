"""
Calendars from a link: a university timetable's export address (ADE,
Hyperplanning), Google Calendar's "secret address in iCal format", any `webcal://`
subscription.

Why a link and not only a file
------------------------------
A downloaded `.ics` is a snapshot: a timetable changes during a semester (rooms,
cancellations, an exam moved), and a file does not. A link can be read again, which
is what lets a published plan follow the timetable (`cps.feed`).

What fetching a URL on someone's behalf must not do
---------------------------------------------------
A server that fetches any URL a visitor types can be pointed at itself or at its
private network (server-side request forgery). So, unless `allow_private` says the
caller runs on the person's own machine:

* only `http`, `https` and `webcal` (read as `https`) are accepted;
* every address the host name resolves to must be public: loopback, private,
  link-local, multicast and reserved ranges are refused, and the check is repeated
  for every redirect;
* the answer is cut off at `MAX_BYTES` and the whole exchange at `TIMEOUT` seconds.

A check made before connecting can be defeated by a name that resolves differently a
moment later (DNS rebinding). This module does not pin the address it checked; a
deployment that fetches untrusted links should also block private ranges at the
network level. That is stated here rather than claimed away.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from .i18n import _

MAX_BYTES = 5 * 1024 * 1024  # a year of a busy timetable is well under 1 MB
TIMEOUT = 20.0
MAX_REDIRECTS = 5
USER_AGENT = "constrained-path-scheduler (calendar reader)"


class SourceError(Exception):
    """The link cannot be read. The message says why, in words a person can act on."""


def normalise(url: str) -> str:
    """`webcal://` is `https://` by another name; anything but http(s) is refused."""
    url = url.strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "webcal":
        parts = parts._replace(scheme="https")
    elif scheme not in ("http", "https"):
        raise SourceError(_("the link must start with https://, http:// or webcal://"))
    if not parts.hostname:
        raise SourceError(_("the link has no host name"))
    return urlunsplit(parts)


def check_public(url: str) -> None:
    """Refuse a URL whose host resolves to an address that is not public."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SourceError(_("the host {host} could not be found", host=repr(host))) from exc
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if not address.is_global or address.is_multicast:
            raise SourceError(
                _("the host {host} is not on the public internet, so it is not read", host=repr(host))
            )


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but check each new address as the first one was checked."""

    def __init__(self, allow_private: bool) -> None:
        self.allow_private = allow_private
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > MAX_REDIRECTS:
            raise SourceError(_("the link redirects too many times"))
        newurl = normalise(newurl)
        if not self.allow_private:
            check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(url: str, *, allow_private: bool = False, timeout: float = TIMEOUT) -> tuple[bytes, str]:
    """A public web page's body and its address after redirects (for its relative
    links): a teacher's course site, a shared document's export (D174). The same
    refusals as a calendar's; a page that is not text is refused too."""
    url = normalise(url)
    if not allow_private:
        check_public(url)
    opener = urllib.request.build_opener(_Redirects(allow_private))
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html, text/plain;q=0.8, */*;q=0.1"}
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
            final = response.geturl()
            kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except SourceError:
        raise
    except urllib.error.HTTPError as exc:
        raise SourceError(
            _("the link answered with an error ({code} {reason})", code=exc.code, reason=exc.reason)
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SourceError(_("the link could not be read ({reason})", reason=reason)) from exc
    if len(body) > MAX_BYTES:
        raise SourceError(_("the page behind the link is larger than {mb} MB", mb=MAX_BYTES // (1024 * 1024)))
    if kind and not kind.startswith("text/") and kind != "application/xhtml+xml":
        raise SourceError(_("the link does not lead to a web page"))
    return body, final


MOODLE_CONFIG_BYTES = 256 * 1024  # what a Moodle says about itself is a few kilobytes


def moodle_public_config(site: str, *, allow_private: bool = False, timeout: float = 8.0) -> dict:
    """What a Moodle says about itself before any sign-in (D175): its web service
    `tool_mobile_get_public_config`, the one Moodle's own app asks first, which names
    how the site signs people in (`typeoflogin`: 1 its own form, 2 and 3 a browser),
    the sign-in services it offers and whether it is open to apps. No password, no key:
    the same answer for anyone who asks. Refused as a calendar's link is."""
    url = normalise(site.rstrip("/") + "/lib/ajax/service-nologin.php?info=tool_mobile_get_public_config")
    if not allow_private:
        check_public(url)
    opener = urllib.request.build_opener(_Redirects(allow_private))
    body = json.dumps([{"index": 0, "methodname": "tool_mobile_get_public_config", "args": {}}]).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            answer = response.read(MOODLE_CONFIG_BYTES + 1)
    except SourceError:
        raise
    except urllib.error.HTTPError as exc:
        raise SourceError(
            _("the link answered with an error ({code} {reason})", code=exc.code, reason=exc.reason)
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SourceError(_("the link could not be read ({reason})", reason=reason)) from exc
    try:
        found = json.loads(answer[:MOODLE_CONFIG_BYTES])
        data = found[0]["data"] if isinstance(found, list) and found and not found[0].get("error") else None
    except (ValueError, KeyError, TypeError, AttributeError):
        data = None
    if not isinstance(data, dict):
        raise SourceError(_("this Moodle did not say how it signs in"))
    return data


MOODLE_ANSWER_BYTES = 8 * 1024 * 1024  # a course's contents: a large course is a few hundred KB


class MoodleError(SourceError):
    """Moodle answered, and said no: `code` is its own error code ("invalidtoken",
    "accessexception", "servicenotavailable"...), the message its words."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def moodle_call(
    site: str,
    key: str,
    function: str,
    params: dict[str, str | int] | None = None,
    *,
    allow_private: bool = False,
    timeout: float = TIMEOUT,
) -> object:
    """One read-only function of a Moodle's web service (D208), with the student's key, as
    Moodle's own app calls it: a POST to `webservice/rest/server.php`, so the key is never
    in an address a log could keep. Refused as a calendar's link is. Raises `MoodleError`
    when Moodle answers with an error, `SourceError` when it cannot be reached."""
    url = normalise(site.rstrip("/") + "/webservice/rest/server.php")
    if not allow_private:
        check_public(url)
    fields = {"wstoken": key, "wsfunction": function, "moodlewsrestformat": "json", **(params or {})}
    opener = urllib.request.build_opener(_Redirects(allow_private))
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode({k: str(v) for k, v in fields.items()}).encode(),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            answer = response.read(MOODLE_ANSWER_BYTES + 1)
    except SourceError:
        raise
    except urllib.error.HTTPError as exc:
        raise SourceError(
            _("the link answered with an error ({code} {reason})", code=exc.code, reason=exc.reason)
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SourceError(_("the link could not be read ({reason})", reason=reason)) from exc
    if len(answer) > MOODLE_ANSWER_BYTES:
        raise SourceError(
            _("Moodle's answer is larger than {mb} MB", mb=MOODLE_ANSWER_BYTES // (1024 * 1024))
        )
    try:
        data = json.loads(answer)
    except ValueError as exc:
        raise SourceError(_("Moodle did not answer as its web service does")) from exc
    if isinstance(data, dict) and (data.get("exception") or data.get("errorcode")):
        words = " ".join(str(data.get("message") or "").split())[:200]
        raise MoodleError(words or str(data.get("errorcode")), str(data.get("errorcode") or ""))
    return data


def fetch_file(url: str, *, limit: int, allow_private: bool = False, timeout: float = TIMEOUT) -> bytes:
    """A file's bytes (D208): a course's slides or notes from Moodle's web service, read and
    then dropped. Refused as a calendar's link is; a file larger than `limit` is refused
    before it is all read."""
    url = normalise(url)
    if not allow_private:
        check_public(url)
    opener = urllib.request.build_opener(_Redirects(allow_private))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with opener.open(request, timeout=timeout) as response:
            size = response.headers.get("Content-Length")
            if size and size.isdigit() and int(size) > limit:
                raise SourceError(_("the file is larger than {mb} MB", mb=limit // (1024 * 1024)))
            body = response.read(limit + 1)
    except SourceError:
        raise
    except urllib.error.HTTPError as exc:
        raise SourceError(
            _("the link answered with an error ({code} {reason})", code=exc.code, reason=exc.reason)
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SourceError(_("the link could not be read ({reason})", reason=reason)) from exc
    if len(body) > limit:
        raise SourceError(_("the file is larger than {mb} MB", mb=limit // (1024 * 1024)))
    return body


def fetch_calendar(url: str, *, allow_private: bool = False, timeout: float = TIMEOUT) -> bytes:
    """The body behind `url`, which should be an `.ics`. See the module docs for
    what is refused and why. Raises `SourceError` with a readable message."""
    url = normalise(url)
    if not allow_private:
        check_public(url)
    opener = urllib.request.build_opener(_Redirects(allow_private))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/calendar, */*"})
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
            final = response.geturl() or url
    except SourceError:
        raise
    except urllib.error.HTTPError as exc:
        raise SourceError(
            _("the link answered with an error ({code} {reason})", code=exc.code, reason=exc.reason)
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SourceError(_("the link could not be read ({reason})", reason=reason)) from exc
    if len(body) > MAX_BYTES:
        raise SourceError(
            _("the calendar behind the link is larger than {mb} MB", mb=MAX_BYTES // (1024 * 1024))
        )
    if b"BEGIN:VCALENDAR" not in body[:4096].upper():
        raise SourceError(not_a_calendar(final, body))
    return body


# AUDIT 138: what a link answered instead of a calendar, so the student knows what to do. The
# owner met the one sentence for every case. A sign-in page is told by where the redirects
# ended (a site's login, CAS, Shibboleth, SAML, OAuth address) or by a password field; a short
# answer that is no page (Moodle's export says "Invalid authentication" when its key no longer
# matches the password) is quoted as it came.
_SIGN_IN = re.compile(
    r"/(?:login|logon|signin|sign-in|cas|idp|sso|saml2?|shibboleth(?:\.sso)?|wayf|oauth2?|auth)(?:[/.?]|$)",
    re.IGNORECASE,
)
_PASSWORD_FIELD = re.compile(r"<input[^>]+type\s*=\s*[\"']?password", re.IGNORECASE)


def not_a_calendar(final: str, body: bytes) -> str:
    """Why `body`, the answer of a link that ended at `final`, is not a calendar, in
    words the student can act on."""
    text = body[:65536].decode("utf-8", "replace")
    if _SIGN_IN.search(urlsplit(final).path or "") or _PASSWORD_FIELD.search(text):
        return _(
            "the link opens a sign-in page, not a calendar: copy the link the platform shows "
            "for its calendar export, not the address of a page you are on"
        )
    said = " ".join(text.split())
    if said and len(said) <= 160 and "<" not in said:
        if "authentication" in said.lower() or "token" in said.lower():
            return _(
                "the platform answered «{answer}»: its calendar link no longer works (it changes "
                "when your password changes); get a new one and paste it here",
                answer=said,
            )
        return _("the platform answered «{answer}» instead of a calendar", answer=said)
    return _(
        "the link does not lead to a calendar (.ics); use the export or subscription address, "
        "not the address of the web page"
    )
