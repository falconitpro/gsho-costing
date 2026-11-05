"""МойСклад JSON API 1.2: товары с изображениями и скачивание файлов. Токен — из MOYSKLAD_TOKEN (backend/.env)."""
from __future__ import annotations

import time
from typing import Any, Iterator


class MoySkladError(RuntimeError):
    pass


class MoySkladClient:
    def __init__(self, token: str, base: str = "https://api.moysklad.ru/api/remap/1.2", pause: float = 0.08):
        if not token:
            raise MoySkladError("Не задан токен МойСклад (MOYSKLAD_TOKEN в backend/.env на сервере)")
        import httpx
        self.base = base.rstrip("/")
        self.pause = pause          # лимит API — 45 запросов за 3 с на аккаунт
        self.http = httpx.Client(timeout=60, headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip", "Accept": "application/json;charset=utf-8",
                                                      "User-Agent": "gsho-costing parts import"})

    def _get(self, url: str, **params: Any):
        for attempt in range(5):
            r = self.http.get(url, params=params or None, follow_redirects=False)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("X-Lognex-Retry-After", "1000")) / 1000 + attempt)
                continue
            time.sleep(self.pause)
            return r
        raise MoySkladError("МойСклад: превышен лимит запросов")

    def products_with_images(self, page: int = 100) -> Iterator[tuple[int, int, dict]]:
        """(позиция, всего, товар) — товары с развёрнутым списком изображений (expand работает при limit ≤ 100)."""
        offset = 0
        while True:
            r = self._get(f"{self.base}/entity/product", limit=page, offset=offset, expand="images")
            if r.status_code == 401:
                raise MoySkladError("МойСклад: токен не принят (401) — проверьте MOYSKLAD_TOKEN")
            if r.status_code >= 400:
                raise MoySkladError(f"МойСклад: ошибка {r.status_code}: {r.text[:200]}")
            data = r.json()
            rows = data.get("rows") or []
            total = int((data.get("meta") or {}).get("size") or 0)
            for i, row in enumerate(rows):
                yield offset + i + 1, total, row
            offset += len(rows)
            if not rows or offset >= total:
                return

    def download(self, href: str) -> bytes:
        """Файл изображения: API отвечает 302 на временную ссылку хранилища — её открываем без токена."""
        r = self._get(href)
        if r.status_code in (301, 302, 303, 307, 308):
            import httpx
            loc = r.headers["location"]
            r2 = httpx.get(loc, timeout=120, follow_redirects=True)
            r2.raise_for_status()
            return r2.content
        if r.status_code >= 400:
            raise MoySkladError(f"МойСклад: файл не скачан ({r.status_code})")
        return r.content

    def images(self, product_id: str) -> list[dict]:
        r = self._get(f"{self.base}/entity/product/{product_id}/images")
        return (r.json().get("rows") or []) if r.status_code < 400 else []
