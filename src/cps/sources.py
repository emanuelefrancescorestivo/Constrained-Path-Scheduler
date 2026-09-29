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
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

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
        raise SourceError("the link must start with https://, http:// or webcal://")
    if not parts.hostname:
        raise SourceError("the link has no host name")
    return urlunsplit(parts)


def check_public(url: str) -> None:
    """Refuse a URL whose host resolves to an address that is not public."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SourceError(f"the host {host!r} could not be found") from exc
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if not address.is_global or address.is_multicast:
            raise SourceError(f"the host {host!r} is not on the public internet, so it is not read")


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but check each new address as the first one was checked."""

    def __init__(self, allow_private: bool) -> None:
        self.allow_private = allow_private
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > MAX_REDIRECTS:
            raise SourceError("the link redirects too many times")
        newurl = normalise(newurl)
        if not self.allow_private:
            check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
    except SourceError:
        raise
    except urllib.error.HTTPError as exc:
        raise SourceError(f"the link answered with an error ({exc.code} {exc.reason})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SourceError(f"the link could not be read ({reason})") from exc
    if len(body) > MAX_BYTES:
        raise SourceError(f"the calendar behind the link is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if b"BEGIN:VCALENDAR" not in body[:4096].upper():
        raise SourceError(
            "the link does not lead to a calendar (.ics); use the export or subscription "
            "address, not the address of the web page"
        )
    return body
