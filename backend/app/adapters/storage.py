"""Хранилище файлов сделок.

Единый интерфейс: создать папку, загрузить файл, получить ссылку, задать права.
Структура папок (раздел 13): Клиент / Год / Сделка №–Клиент–Объект / 01 Запрос и RFQ … 07 Исполнение.
Подпапка «04 Расчёт себестоимости» закрывается для менеджера (в SharePoint — правами на папку,
в Яндекс Диске и S3 — только серверной фильтрацией и отсутствием ссылок у роли).
"""
from __future__ import annotations

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

DEAL_SUBFOLDERS = {
    "01_rfq": "01 Запрос и RFQ",
    "02_logistics": "02 Логистика",
    "03_service": "03 Сервис",
    "04_cost": "04 Расчёт себестоимости",
    "05_kp": "05 КП",
    "06_mail": "06 Переписка",
    "07_fact": "07 Исполнение (факт)",
    "08_legal": "08 Документы клиента и юридические",
}
RESTRICTED_FOR_MANAGER = {"04_cost"}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class StoredFile:
    path: str        # путь/идентификатор в хранилище
    url: str         # ссылка для пользователя
    sha256: str
    size: int


class StorageAdapter(ABC):
    def deal_folder(self, client: str, year: int, deal_no: str, site: str) -> str:
        safe = lambda s: "".join(ch for ch in s if ch not in '\\/:*?"<>|').strip()
        tail = f"–{safe(site)}" if site and safe(site) else ""
        return f"{safe(client)}/{year}/{safe(deal_no)}–{safe(client)}{tail}"

    @abstractmethod
    def ensure_deal_folders(self, folder: str) -> dict[str, str]:
        """Создаёт папку сделки с подпапками; возвращает ключ → путь."""

    @abstractmethod
    def upload(self, local_path: str, folder: str, subfolder_key: str, file_name: str) -> StoredFile: ...

    @abstractmethod
    def link(self, stored_path: str) -> str: ...

    @abstractmethod
    def set_permissions(self, folder: str, subfolder_key: str, allowed_user_ids: list[str]) -> None: ...

    @abstractmethod
    def download(self, stored_path: str, local_path: str) -> str: ...


class LocalStorage(StorageAdapter):
    """Локальная файловая система (dev и резерв). Права — только серверная фильтрация."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def ensure_deal_folders(self, folder: str) -> dict[str, str]:
        out = {}
        for key, name in DEAL_SUBFOLDERS.items():
            p = self.root / folder / name
            p.mkdir(parents=True, exist_ok=True)
            out[key] = str(p.relative_to(self.root))
        return out

    def upload(self, local_path: str, folder: str, subfolder_key: str, file_name: str) -> StoredFile:
        dest_dir = self.root / folder / DEAL_SUBFOLDERS[subfolder_key]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / file_name
        n = 1
        while dest.exists():   # файл с таким именем уже есть — не перезаписываем: прежняя запись в базе должна открывать прежний файл
            n += 1
            stem, ext = os.path.splitext(file_name)
            dest = dest_dir / f"{stem} ({n}){ext}"
        shutil.copyfile(local_path, dest)
        rel = str(dest.relative_to(self.root))
        return StoredFile(path=rel, url=self.link(rel), sha256=sha256_file(str(dest)), size=os.path.getsize(dest))

    def link(self, stored_path: str) -> str:
        return f"/api/files/{stored_path}"

    def set_permissions(self, folder: str, subfolder_key: str, allowed_user_ids: list[str]) -> None:
        return None   # права — серверной фильтрацией /api/files

    def download(self, stored_path: str, local_path: str) -> str:
        shutil.copyfile(self.root / stored_path, local_path)
        return local_path


class SharePointStorage(StorageAdapter):
    """SharePoint / OneDrive через Microsoft Graph (первая очередь).

    Требуемые права приложения: Files.ReadWrite.All, Sites.ReadWrite.All. Токен — client credentials.
    Полная реализация — в очереди 1 внедрения; здесь контракт и вызовы Graph.
    """

    def __init__(self, s):
        self.s = s
        self.base = f"https://graph.microsoft.com/v1.0/drives/{s.graph_drive_id}"

    def _token(self) -> str:   # pragma: no cover
        import httpx
        r = httpx.post(f"https://login.microsoftonline.com/{self.s.graph_tenant_id}/oauth2/v2.0/token", data={
            "grant_type": "client_credentials", "client_id": self.s.oidc_client_id,
            "client_secret": self.s.oidc_client_secret, "scope": "https://graph.microsoft.com/.default"}, timeout=20)
        r.raise_for_status()
        return r.json()["access_token"]

    def _h(self):   # pragma: no cover
        return {"Authorization": f"Bearer {self._token()}"}

    def ensure_deal_folders(self, folder: str) -> dict[str, str]:   # pragma: no cover
        import httpx
        out = {}
        for key, name in DEAL_SUBFOLDERS.items():
            path = f"{folder}/{name}"
            parent = f"{self.base}/root:/{folder}:/children"
            httpx.post(parent, headers=self._h(), json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "replace"}, timeout=30)
            out[key] = path
        return out

    def upload(self, local_path: str, folder: str, subfolder_key: str, file_name: str) -> StoredFile:   # pragma: no cover
        import httpx
        path = f"{folder}/{DEAL_SUBFOLDERS[subfolder_key]}/{file_name}"
        with open(local_path, "rb") as f:
            r = httpx.put(f"{self.base}/root:/{path}:/content", headers=self._h(), content=f.read(), timeout=120)
        r.raise_for_status()
        item = r.json()
        return StoredFile(path=item["id"], url=item.get("webUrl", ""), sha256=sha256_file(local_path), size=item.get("size", 0))

    def link(self, stored_path: str) -> str:   # pragma: no cover
        import httpx
        r = httpx.get(f"{self.base}/items/{stored_path}", headers=self._h(), timeout=20)
        r.raise_for_status()
        return r.json().get("webUrl", "")

    def set_permissions(self, folder: str, subfolder_key: str, allowed_user_ids: list[str]) -> None:   # pragma: no cover
        # Graph: POST /drives/{id}/items/{itemId}/invite с recipients и roles=["read"|"write"]; наследование отключается
        # для «04 Расчёт себестоимости», чтобы менеджер не получил доступ.
        return None

    def download(self, stored_path: str, local_path: str) -> str:   # pragma: no cover
        import httpx
        r = httpx.get(f"{self.base}/items/{stored_path}/content", headers=self._h(), follow_redirects=True, timeout=120)
        r.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(r.content)
        return local_path


class YandexDiskStorage(StorageAdapter):
    """Яндекс Диск (REST API). Права на подпапку 04 — через закрытые ссылки и роли системы."""

    def __init__(self, s):
        self.s = s

    def ensure_deal_folders(self, folder: str) -> dict[str, str]:   # pragma: no cover
        raise NotImplementedError("Адаптер Яндекс Диска: очередь 2 (переключение провайдера)")

    def upload(self, local_path, folder, subfolder_key, file_name):   # pragma: no cover
        raise NotImplementedError

    def link(self, stored_path):   # pragma: no cover
        raise NotImplementedError

    def set_permissions(self, folder, subfolder_key, allowed_user_ids):   # pragma: no cover
        return None

    def download(self, stored_path, local_path):   # pragma: no cover
        raise NotImplementedError


PRESIGN_TTL = 300   # секунд: срок подписанной ссылки на файл в S3 (04 §9)


class S3Storage(StorageAdapter):
    """S3-совместимое хранилище (Yandex Object Storage / MinIO в РФ) — рекомендуемая альтернатива."""

    def __init__(self, s):
        self.s = s

    def _client(self):   # pragma: no cover
        import boto3
        return boto3.client("s3", endpoint_url=self.s.s3_endpoint, aws_access_key_id=self.s.s3_access_key,
                            aws_secret_access_key=self.s.s3_secret_key)

    def ensure_deal_folders(self, folder: str) -> dict[str, str]:
        return {k: f"{folder}/{v}" for k, v in DEAL_SUBFOLDERS.items()}   # в S3 папки виртуальны

    def upload(self, local_path, folder, subfolder_key, file_name):   # pragma: no cover
        key = f"{folder}/{DEAL_SUBFOLDERS[subfolder_key]}/{file_name}"
        self._client().upload_file(local_path, self.s.s3_bucket, key)
        # в базе — только адрес системы: подписанная ссылка создаётся при каждом скачивании после проверки прав (04 §9)
        return StoredFile(path=key, url=self.link(key), sha256=sha256_file(local_path), size=os.path.getsize(local_path))

    def link(self, stored_path):
        """Постоянный адрес — маршрут системы с проверкой прав; не подписанная ссылка хранилища."""
        return f"/api/files/{stored_path}"

    def presigned(self, stored_path: str, file_name: str, inline: bool = False) -> str:
        """Подписанная ссылка на 5 минут (04 §9, FILE-06); вызывать только после проверки прав на объект.
        Небезопасные для показа типы — всегда «скачать» (response-content-disposition=attachment)."""
        from urllib.parse import quote
        disp = "inline" if inline else "attachment"
        return self._client().generate_presigned_url(
            "get_object", ExpiresIn=PRESIGN_TTL,
            Params={"Bucket": self.s.s3_bucket, "Key": stored_path, "ResponseContentDisposition": f"{disp}; filename*=UTF-8''{quote(file_name)}",
                    "ResponseCacheControl": "private, no-store"})

    def set_permissions(self, folder, subfolder_key, allowed_user_ids):
        return None   # доступ только через presigned-ссылки, выдаваемые сервером по ролям

    def download(self, stored_path, local_path):   # pragma: no cover
        self._client().download_file(self.s.s3_bucket, stored_path, local_path)
        return local_path
