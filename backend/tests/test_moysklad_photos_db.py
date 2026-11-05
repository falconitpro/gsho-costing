"""Перенос фото из МойСклад на живой PostgreSQL с подставным клиентом API: сопоставление по ms_id, докачка только новых."""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app import adapters  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Part  # noqa: E402
from app.services import moysklad_photos  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731


class FakeMS:
    def __init__(self, ms_id: str, images: list[str]):
        self.ms_id, self.imgs, self.downloads = ms_id, images, 0

    def _img(self, i):
        return {"meta": {"href": f"https://x/entity/product/{self.ms_id}/images/{i}", "downloadHref": f"https://x/download/{i}"}, "filename": f"{i}.png"}

    def products_with_images(self):
        yield 1, 3, {"id": self.ms_id, "images": {"meta": {"size": len(self.imgs)}, "rows": [self._img(i) for i in self.imgs]}}
        yield 2, 3, {"id": "no-such-product", "images": {"meta": {"size": 1}, "rows": [self._img("zz")]}}
        yield 3, 3, {"id": "no-images", "images": {"meta": {"size": 0}, "rows": []}}

    def images(self, pid):
        return []

    def download(self, href):
        self.downloads += 1
        from io import BytesIO
        from PIL import Image
        b = BytesIO()
        Image.new("RGB", (10, 10), "blue").save(b, format="PNG")
        return b.getvalue()


def test_photos_sync():
    seed()
    t = uuid.uuid4().hex
    with TestClient(app) as c:
        p = c.post("/api/parts/catalog", headers=H("cd@example.com"), json={"article": f"PH-{t[:6]}", "name_ru": "Фото-тест"}).json()
        db = SessionLocal()
        part = db.get(Part, p["id"])
        part.ms_id = t
        db.commit()
        db.close()
        fake = FakeMS(t, ["i1", "i2"])
        st = moysklad_photos.run(SessionLocal, fake, adapters.storage())
        assert (st["with_images"], st["not_found"], st["photos_added"], st["parts_updated"], st["errors"]) == (2, 1, 2, 1, 0)
        card = c.get(f"/api/parts/catalog/{p['id']}", headers=H("manager@example.com")).json()
        assert [f["ms_image_id"] for f in card["photo_files"]] == ["i1", "i2"]
        img = c.get(f"/api/parts/catalog/{p['id']}/photo/1", headers=H("manager@example.com"))
        assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
        # повторный запуск — докачивается только новое
        fake2 = FakeMS(t, ["i1", "i2", "i3"])
        st = moysklad_photos.run(SessionLocal, fake2, adapters.storage())
        assert st["photos_added"] == 1 and fake2.downloads == 1
        # без токена запуск из интерфейса не стартует; менеджеру — нет доступа
        assert c.post("/api/parts/import/moysklad-photos", headers=H("manager@example.com")).status_code == 403
        r = c.post("/api/parts/import/moysklad-photos", headers=H("cd@example.com"))
        assert r.status_code == 422 and "MOYSKLAD_TOKEN" in r.text
        assert c.get("/api/parts/import/moysklad-photos", headers=H("cd@example.com")).json()["token_set"] is False
        d = c.delete(f"/api/parts/catalog/{p['id']}/photo/0", headers=H("cd@example.com")).json()
        assert [f["ms_image_id"] for f in d["photo_files"]] == ["i2", "i3"]
