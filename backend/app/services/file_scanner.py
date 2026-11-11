"""Проверка загружаемых файлов (docs/operations/04 §8): клиент clamd (протокол INSTREAM) и защита от zip-бомб.

clamd — отдельный контейнер без публичного порта (deploy/clamav). Клиент не передаёт путь на диске: только поток байтов,
поэтому scanner не может прочитать произвольный файл сервера. Таймауты обязательны; любая ошибка — status=error
(fail-closed: файл не принимается). Признать файл чистым без ответа «OK» от clamd нельзя.
"""
from __future__ import annotations

import io
import re
import socket
import struct
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO, Optional

CHUNK = 64 * 1024
_FOUND = re.compile(r"^stream: (.+) FOUND$")
_VERSION = re.compile(r"^ClamAV ([\w.\-+]+)(?:/(\d+)/(.+))?$")   # без официальных баз — только «ClamAV 1.5.4»

# Офисные форматы и архивы: проверяются до любого разбора (импорт XLSX/DOCX), даже если антивирус выключен
ZIP_EXT = {".zip", ".xlsx", ".xlsm", ".docx", ".pptx", ".odt", ".ods"}
ZIP_MAX_ENTRIES = 5000
ZIP_MAX_UNPACKED = 500 * 1024 * 1024      # 500 МБ распакованного содержимого
ZIP_MAX_RATIO = 200                       # степень сжатия одного файла
ZIP_MAX_DEPTH = 3                         # вложенные архивы


@dataclass
class ScanResult:
    status: str                    # clean | infected | error
    signature: str = ""            # имя найденной сигнатуры (Eicar-Test-Signature, Heuristics.Limits.Exceeded…)
    error_code: str = ""           # timeout | unavailable | protocol | limits | zip_bomb
    engine_version: str = ""
    signature_version: str = ""


class ScannerError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _connect(host: str, port: int, timeout: float) -> socket.socket:
    try:
        return socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        raise ScannerError("unavailable", f"clamd недоступен: {type(e).__name__}") from e


def _recv_all(sock: socket.socket) -> str:
    buf = b""
    while True:
        part = sock.recv(4096)
        if not part:
            break
        buf += part
        if buf.endswith(b"\0") or buf.endswith(b"\n"):
            break
    return buf.rstrip(b"\0\n").decode("utf-8", "replace")


def command(host: str, port: int, cmd: str, timeout: float = 5.0) -> str:
    with _connect(host, port, timeout) as s:
        s.sendall(f"z{cmd}\0".encode())
        try:
            return _recv_all(s)
        except socket.timeout as e:
            raise ScannerError("timeout") from e


def version(host: str, port: int, timeout: float = 5.0) -> dict:
    """{'engine': '1.4.3', 'signatures': 27450, 'signatures_at': datetime|None}"""
    raw = command(host, port, "VERSION", timeout)
    m = _VERSION.match(raw)
    if not m:
        raise ScannerError("protocol", "неожиданный ответ VERSION")
    at = None
    if m.group(3):
        try:
            at = datetime.strptime(m.group(3).strip(), "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return {"engine": m.group(1), "signatures": int(m.group(2) or 0), "signatures_at": at}


def scan_stream(fh: BinaryIO, host: str, port: int, timeout: float = 60.0, max_bytes: Optional[int] = None) -> ScanResult:
    """INSTREAM: куски <длина uint32 BE><данные>, в конце нулевая длина. Ответ: «stream: OK» | «stream: X FOUND» | «… ERROR»."""
    try:
        ver = version(host, port, min(timeout, 5.0))
    except ScannerError as e:
        return ScanResult("error", error_code=e.code)
    eng, sigs = ver["engine"], str(ver["signatures"])
    try:
        with _connect(host, port, timeout) as s:
            s.sendall(b"zINSTREAM\0")
            sent = 0
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                sent += len(chunk)
                if max_bytes is not None and sent > max_bytes:
                    return ScanResult("error", error_code="limits", engine_version=eng, signature_version=sigs)
                s.sendall(struct.pack(">I", len(chunk)) + chunk)
            s.sendall(struct.pack(">I", 0))
            reply = _recv_all(s)
    except socket.timeout:
        return ScanResult("error", error_code="timeout", engine_version=eng, signature_version=sigs)
    except ScannerError as e:
        return ScanResult("error", error_code=e.code, engine_version=eng, signature_version=sigs)
    except OSError:
        return ScanResult("error", error_code="unavailable", engine_version=eng, signature_version=sigs)
    if reply == "stream: OK":
        return ScanResult("clean", engine_version=eng, signature_version=sigs)
    m = _FOUND.match(reply)
    if m:
        return ScanResult("infected", signature=m.group(1)[:120], engine_version=eng, signature_version=sigs)
    code = "limits" if "size limit" in reply.lower() else "protocol"
    return ScanResult("error", error_code=code, engine_version=eng, signature_version=sigs)


def zip_guard(fh: BinaryIO, name: str, depth: int = 0) -> Optional[str]:
    """None — архив в пределах; иначе причина отказа. Читает только оглавление и вложенные архивы, не распаковывая всё."""
    import os
    ext = os.path.splitext(name or "")[1].lower()
    if ext not in ZIP_EXT and depth == 0:
        return None
    pos = fh.tell()
    try:
        if not zipfile.is_zipfile(fh):
            return None   # не архив: дальше решают обработчик и антивирус
        fh.seek(pos)
        with zipfile.ZipFile(fh) as z:
            infos = z.infolist()
            if len(infos) > ZIP_MAX_ENTRIES:
                return f"в архиве больше {ZIP_MAX_ENTRIES} файлов"
            total = 0
            for i in infos:
                total += i.file_size
                if total > ZIP_MAX_UNPACKED:
                    return "распакованный объём больше 500 МБ"
                if i.compress_size and i.file_size / max(i.compress_size, 1) > ZIP_MAX_RATIO and i.file_size > 10 * 1024 * 1024:
                    return "подозрительно высокая степень сжатия (zip-бомба)"
                if os.path.splitext(i.filename)[1].lower() == ".zip":
                    if depth + 1 >= ZIP_MAX_DEPTH:
                        return "слишком глубокая вложенность архивов"
                    if i.file_size <= 50 * 1024 * 1024:
                        inner = io.BytesIO(z.read(i))
                        why = zip_guard(inner, i.filename, depth + 1)
                        if why:
                            return why
        return None
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError):
        return None if ext != ".zip" else "архив повреждён"
    finally:
        fh.seek(pos)
