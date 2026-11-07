"""Безопасная выдача загруженных файлов и ограничение размера загрузок.

«Как страницу» (inline) браузеру отдаются только изображения, PDF и видео — типы без активного содержимого.
Всё остальное (в том числе SVG и HTML, которые могут нести скрипт) отдаётся как скачивание с типом
application/octet-stream: даже если сотрудник загрузил «руководство» в виде .html, оно не выполнится на домене системы.
Все ответы с файлами получают X-Content-Type-Options: nosniff и запрет встраивания.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

INLINE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".heic": "image/heic", ".pdf": "application/pdf", ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
}
FILE_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'",
                "Referrer-Policy": "no-referrer", "Cache-Control": "private, no-store"}

# Лимиты загрузок, байт: видео и фото приёмки — крупнее, документы — меньше
MB = 1024 * 1024
LIMIT_DOCUMENT = 50 * MB
LIMIT_MEDIA = 300 * MB
LIMIT_IMPORT = 30 * MB


def file_response(path: str, filename: str, inline: bool = True, background: Optional[BackgroundTask] = None) -> FileResponse:
    """FileResponse с безопасным типом: inline только для изображений, PDF и видео; остальное — скачивание."""
    ext = os.path.splitext(filename or path)[1].lower()
    media = INLINE_TYPES.get(ext)
    if inline and media:
        return FileResponse(path, filename=filename, media_type=media, content_disposition_type="inline", headers=FILE_HEADERS, background=background)
    media = media or ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == ".xlsx"
                      else "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if ext == ".docx"
                      else "application/octet-stream")
    return FileResponse(path, filename=filename, media_type=media, content_disposition_type="attachment", headers=FILE_HEADERS, background=background)


def check_size(file: UploadFile, limit: int = LIMIT_DOCUMENT) -> None:
    """Отклоняет файл больше лимита до записи на диск (по размеру тела; если размер неизвестен — проверяется при чтении)."""
    size = getattr(file, "size", None)
    if size is None:
        try:
            file.file.seek(0, os.SEEK_END)
            size = file.file.tell()
            file.file.seek(0)
        except Exception:   # поток без seek — проверит read_limited
            return
    if size > limit:
        raise HTTPException(413, f"Файл больше допустимого размера ({limit // MB} МБ)")


def read_limited(file: UploadFile, limit: int = LIMIT_DOCUMENT) -> bytes:
    """Читает файл целиком, не больше лимита."""
    check_size(file, limit)
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Файл больше допустимого размера ({limit // MB} МБ)")
    return data
