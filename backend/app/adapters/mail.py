"""Почта: уведомления и КП (исходящие), чтение общего ящика (входящие)."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

log = logging.getLogger("mail")


@dataclass
class OutgoingMail:
    to: list[str]
    subject: str
    body_html: str
    attachments: list[str] = field(default_factory=list)   # локальные пути
    sender: str = ""                                        # от имени менеджера (Graph: Mail.Send as user)


@dataclass
class IncomingMail:
    external_id: str
    sender: str
    subject: str
    body: str
    received_at: str
    attachments: list[tuple[str, bytes]] = field(default_factory=list)


class MailAdapter(ABC):
    @abstractmethod
    def send(self, msg: OutgoingMail) -> str:
        """Отправляет письмо, возвращает внешний идентификатор."""

    @abstractmethod
    def create_draft(self, msg: OutgoingMail) -> str:
        """Создаёт черновик (менеджер отправляет из Outlook сам); возвращает ссылку на черновик."""

    @abstractmethod
    def fetch_inbox(self, mailbox: str, since_iso: str) -> list[IncomingMail]: ...


class ConsoleMail(MailAdapter):
    sent: list[OutgoingMail] = []

    def send(self, msg: OutgoingMail) -> str:
        log.info("MAIL → %s: %s", msg.to, msg.subject)
        self.sent.append(msg)
        return f"console-{len(self.sent)}"

    def create_draft(self, msg: OutgoingMail) -> str:
        log.info("MAIL DRAFT → %s: %s", msg.to, msg.subject)
        return "console-draft"

    def fetch_inbox(self, mailbox: str, since_iso: str) -> list[IncomingMail]:
        return []


class OutlookGraphMail(MailAdapter):
    """Microsoft Graph: Mail.Send (от имени менеджера), Mail.Read для общего ящика (sales@)."""

    def __init__(self, s):
        self.s = s

    def send(self, msg: OutgoingMail) -> str:   # pragma: no cover
        # POST /users/{sender}/sendMail {message:{subject, body:{contentType:'HTML'}, toRecipients, attachments[fileAttachment]}}
        raise NotImplementedError("Очередь 2: отправка через Graph")

    def create_draft(self, msg: OutgoingMail) -> str:   # pragma: no cover
        # POST /users/{sender}/messages → id; затем PUT attachments; ссылка webLink открывает черновик в Outlook
        raise NotImplementedError("Очередь 2")

    def fetch_inbox(self, mailbox: str, since_iso: str) -> list[IncomingMail]:   # pragma: no cover
        # GET /users/{mailbox}/mailFolders/inbox/messages?$filter=receivedDateTime ge {since}&$expand=attachments
        raise NotImplementedError("Очередь 2")


class YandexSmtpMail(MailAdapter):
    """SMTP (Яндекс 360 или любой почтовый сервер): исходящие письма с вложениями. Черновиков в SMTP нет."""

    def __init__(self, s):
        self.s = s

    def send(self, msg: OutgoingMail) -> str:
        import mimetypes
        import os
        import smtplib
        import ssl
        from email.message import EmailMessage
        from email.utils import make_msgid
        if not self.s.smtp_user or not self.s.smtp_password:
            raise RuntimeError("Почта не настроена: задайте SMTP_USER и SMTP_PASSWORD на сервере")
        m = EmailMessage()
        m["From"] = self.s.smtp_from or self.s.smtp_user
        m["To"] = ", ".join(a for a in msg.to if a)
        m["Subject"] = msg.subject
        if msg.sender and msg.sender != m["From"]:
            m["Reply-To"] = msg.sender
        mid = make_msgid()
        m["Message-ID"] = mid
        m.set_content("Письмо в формате HTML.")
        m.add_alternative(msg.body_html, subtype="html")
        for path in msg.attachments:
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            main, sub = ctype.split("/", 1)
            with open(path, "rb") as f:
                m.add_attachment(f.read(), maintype=main, subtype=sub, filename=os.path.basename(path))
        ctx = ssl.create_default_context()
        if self.s.smtp_port == 465:
            with smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, context=ctx, timeout=30) as smtp:
                smtp.login(self.s.smtp_user, self.s.smtp_password)
                smtp.send_message(m)
        else:
            with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30) as smtp:
                smtp.starttls(context=ctx)
                smtp.login(self.s.smtp_user, self.s.smtp_password)
                smtp.send_message(m)
        return mid

    def create_draft(self, msg: OutgoingMail) -> str:
        return ""   # в SMTP черновиков нет — письмо отправляется кнопкой «Отправить клиенту»

    def fetch_inbox(self, mailbox: str, since_iso: str) -> list[IncomingMail]:   # pragma: no cover
        return []
