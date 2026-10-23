"""
The e-mail the service sends (DECISIONS.md D94): the link that confirms an address,
and the link that chooses a new password. Nothing else is ever sent.

Through any SMTP server named in the environment, with the standard library alone:

    CPS_SMTP_HOST      the server, e.g. smtp-relay.brevo.com or smtp.gmail.com
    CPS_SMTP_PORT      587 (STARTTLS, the default) or 465 (TLS from the start)
    CPS_SMTP_USER      the account's user name, if the server asks for one
    CPS_SMTP_PASSWORD  its password or app password
    CPS_MAIL_FROM      the sender, e.g. "Study plan <plan@example.org>"

Without CPS_SMTP_HOST nothing is sent: the app says so, and the demo shows the e-mail
on screen instead (`cps demo` only: it is made-up data). Plain text only: no image,
no tracking pixel, no link but the one the message is for.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str


class MailError(Exception):
    """The message could not be handed to the SMTP server."""


@dataclass(frozen=True)
class Smtp:
    host: str
    port: int
    user: str
    password: str
    sender: str

    @classmethod
    def from_env(cls) -> Smtp | None:
        host = os.environ.get("CPS_SMTP_HOST") or ""
        sender = os.environ.get("CPS_MAIL_FROM") or ""
        if not host or not sender:
            return None
        return cls(
            host=host,
            port=int(os.environ.get("CPS_SMTP_PORT") or 587),
            user=os.environ.get("CPS_SMTP_USER") or "",
            password=os.environ.get("CPS_SMTP_PASSWORD") or "",
            sender=sender,
        )

    def send(self, message: Message) -> None:
        mail = EmailMessage()
        mail["From"] = self.sender
        mail["To"] = message.to
        mail["Subject"] = message.subject
        mail["Message-ID"] = make_msgid(domain=self.sender.rpartition("@")[2].strip("> ") or None)
        mail.set_content(message.body)
        context = ssl.create_default_context()
        try:
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=context) as server:
                    self._deliver(server, mail)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=20) as server:
                    server.starttls(context=context)
                    self._deliver(server, mail)
        except (OSError, smtplib.SMTPException) as error:
            raise MailError(f"{type(error).__name__}: {error}") from error

    def _deliver(self, server: smtplib.SMTP, mail: EmailMessage) -> None:
        if self.user:
            server.login(self.user, self.password)
        server.send_message(mail)
