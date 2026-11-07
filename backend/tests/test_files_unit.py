"""Выдача файлов: inline только изображения, PDF и видео; остальное — скачивание."""
from app.core.files import file_response


def test_file_response_types(tmp_path):
    for name, inline, media in (("a.html", False, "application/octet-stream"), ("b.svg", False, "application/octet-stream"),
                                ("c.png", True, "image/png"), ("d.pdf", True, "application/pdf"), ("e.xlsx", False, None)):
        p = tmp_path / name
        p.write_bytes(b"x")
        r = file_response(str(p), name)
        assert r.media_type == media if media else True
        assert ("inline" in r.headers["content-disposition"]) is inline, name
        assert r.headers["x-content-type-options"] == "nosniff"


