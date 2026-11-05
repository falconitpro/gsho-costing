"""Перенос фото запчастей из МойСклад: файлы изображений товаров → хранилище модуля, ссылки — в карточку (photo_files).

Повторный запуск докачивает только новые изображения (по id изображения МойСклад). Идёт в фоне; ход виден в STATE.
"""
from __future__ import annotations

import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any, Optional

STATE: dict[str, Any] = {"running": False}
_LOCK = threading.Lock()


def image_id(img: dict) -> str:
    href = ((img.get("meta") or {}).get("href") or "")
    return href.rstrip("/").rsplit("/", 1)[-1] if href else (img.get("filename") or "")


def new_images(existing: list[dict], images: list[dict]) -> list[dict]:
    """Изображения товара, которых ещё нет в карточке (сравнение по id изображения МойСклад)."""
    have = {x.get("ms_image_id") for x in existing or []}
    return [i for i in images if image_id(i) and image_id(i) not in have and (i.get("meta") or {}).get("downloadHref")]


def safe_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "._- " else "_" for c in (name or "photo.jpg"))
    return keep.strip()[:120] or "photo.jpg"


def shrink(path: str, max_side: int = 1600) -> None:
    """Большие фото уменьшаются до 1600 px по длинной стороне (для КП и просмотра хватает)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            if max(im.size) <= max_side:
                return
            im.thumbnail((max_side, max_side))
            fmt = im.format or "JPEG"
            if fmt == "JPEG" and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(path, format=fmt, quality=85)
    except Exception:   # не картинка или формат не поддержан — сохраняем как есть
        pass


def run(session_factory, client, storage, user_id: Optional[str] = None, only_missing: bool = True) -> dict:
    """Проход по всем товарам МойСклад с изображениями; товар ищется в модуле по коду МойСклад (ms_id)."""
    from sqlalchemy import select
    from ..models import IntegrationLog, Part
    st = STATE
    st.update(running=True, started_at=datetime.now(timezone.utc).isoformat(), finished_at=None, processed=0, total=0, with_images=0,
              parts_updated=0, photos_added=0, not_found=0, errors=0, last_error="", user_id=user_id)
    db = session_factory()
    try:
        for pos, total, row in client.products_with_images():
            st["processed"], st["total"] = pos, total
            imgs = ((row.get("images") or {}).get("rows")) or []
            if not imgs and int(((row.get("images") or {}).get("meta") or {}).get("size") or 0) > 0:
                imgs = client.images(row["id"])          # expand не развернул — список отдельно
            if not imgs:
                continue
            st["with_images"] += 1
            part = db.scalars(select(Part).where(Part.ms_id == row.get("id"))).first()
            if part is None:
                st["not_found"] += 1
                continue
            todo = new_images(part.photo_files or [], imgs) if only_missing else imgs
            added = []
            for n, img in enumerate(todo, start=len(part.photo_files or []) + 1):
                try:
                    data = client.download(img["meta"]["downloadHref"])
                    name = safe_name(img.get("filename") or f"{part.article}.jpg")
                    fd, path = tempfile.mkstemp(suffix=os.path.splitext(name)[1] or ".jpg")
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                    shrink(path)
                    try:
                        stored = storage.upload(path, f"Запчасти/Фото/{safe_name(part.article)}", "07_fact", f"{n:02d} {name}")
                    finally:
                        os.unlink(path)
                    added.append({"path": stored.path, "name": name, "ms_image_id": image_id(img), "size": stored.size,
                                  "at": datetime.now(timezone.utc).isoformat()})
                except Exception as e:   # одно фото не мешает остальным
                    st["errors"] += 1
                    st["last_error"] = f"{part.article}: {str(e)[:200]}"
            if added:
                part.photo_files = [*(part.photo_files or []), *added]
                st["parts_updated"] += 1
                st["photos_added"] += len(added)
                db.commit()
        db.add(IntegrationLog(system="moysklad", operation="photos", ok=True, details={k: v for k, v in st.items() if k != "running"}))
        db.commit()
    except Exception as e:
        st["last_error"] = str(e)[:300]
        st["errors"] += 1
        db.rollback()
        db.add(IntegrationLog(system="moysklad", operation="photos", ok=False, details={"error": str(e)[:300]}))
        db.commit()
    finally:
        st["running"] = False
        st["finished_at"] = datetime.now(timezone.utc).isoformat()
        db.close()
    return dict(st)


def start(session_factory, client, storage, user_id: Optional[str] = None) -> bool:
    with _LOCK:
        if STATE.get("running"):
            return False
        STATE["running"] = True
    threading.Thread(target=run, args=(session_factory, client, storage, user_id), daemon=True).start()
    return True
