"""WeCom (企业微信): уведомления сотрудникам, переписка с заводами, получение файлов."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

log = logging.getLogger("messenger")


@dataclass
class ChatMessage:
    external_id: str
    chat_id: str
    sender: str
    text: str
    at: str
    files: list[tuple[str, str]] = field(default_factory=list)   # (имя, media_id)


class MessengerAdapter(ABC):
    @abstractmethod
    def notify(self, user_ids: list[str], text: str) -> None: ...

    @abstractmethod
    def send_to_external(self, external_contact_id: str, text: str, deal_no: str) -> str: ...

    @abstractmethod
    def fetch_messages(self, since_cursor: str) -> tuple[list[ChatMessage], str]: ...

    @abstractmethod
    def download_media(self, media_id: str, local_path: str) -> str: ...


class ConsoleMessenger(MessengerAdapter):
    sent: list[tuple[list[str], str]] = []

    def notify(self, user_ids: list[str], text: str) -> None:
        log.info("WECOM → %s: %s", user_ids, text)
        self.sent.append((user_ids, text))

    def send_to_external(self, external_contact_id: str, text: str, deal_no: str) -> str:
        log.info("WECOM EXT → %s [%s]: %s", external_contact_id, deal_no, text)
        return "console-msg"

    def fetch_messages(self, since_cursor: str) -> tuple[list[ChatMessage], str]:
        return [], since_cursor

    def download_media(self, media_id: str, local_path: str) -> str:
        return local_path


class WeComMessenger(MessengerAdapter):
    """Корпоративное приложение WeCom (юрлицо в КНР есть — регистрация напрямую).

    API: gettoken(corpid, corpsecret); message/send (уведомления агенту); externalcontact — чат с внешними
    контактами заводов от корпоративного аккаунта; msgaudit / media/get — получение файлов из чата.
    """

    def __init__(self, s):
        self.s = s
        self.base = "https://qyapi.weixin.qq.com/cgi-bin"

    def _token(self) -> str:   # pragma: no cover
        import httpx
        r = httpx.get(f"{self.base}/gettoken", params={"corpid": self.s.wecom_corp_id, "corpsecret": self.s.wecom_secret}, timeout=20)
        r.raise_for_status()
        return r.json()["access_token"]

    def notify(self, user_ids: list[str], text: str) -> None:   # pragma: no cover
        import httpx
        httpx.post(f"{self.base}/message/send", params={"access_token": self._token()}, json={
            "touser": "|".join(user_ids), "msgtype": "text", "agentid": int(self.s.wecom_agent_id), "text": {"content": text}}, timeout=20)

    def send_to_external(self, external_contact_id: str, text: str, deal_no: str) -> str:   # pragma: no cover
        raise NotImplementedError("Очередь 2: чат с заводами (externalcontact)")

    def fetch_messages(self, since_cursor: str) -> tuple[list[ChatMessage], str]:   # pragma: no cover
        raise NotImplementedError("Очередь 2")

    def download_media(self, media_id: str, local_path: str) -> str:   # pragma: no cover
        raise NotImplementedError("Очередь 2")
