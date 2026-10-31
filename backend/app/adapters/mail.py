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
    """Яндекс 360: SMTP для исходящих, IMAP для чтения общего ящика."""

    def __init__(self, s):
        self.s = s

    def send(self, msg: OutgoingMail) -> str:   # pragma: no cover
        raise NotImplementedError("Альтернативный адаптер")

    def create_draft(self, msg: OutgoingMail) -> str:   # pragma: no cover
        raise NotImplementedError

    def fetch_inbox(self, mailbox: str, since_iso: str) -> list[IncomingMail]:   # pragma: no cover
        raise NotImplementedError
