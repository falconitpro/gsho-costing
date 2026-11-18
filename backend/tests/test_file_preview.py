"""Просмотр файлов сделки в системе и скачивание по праву или по запросу (D-077)."""
from tests.test_api_flow import client, _ok, H, MGR, LOG, GD, _png  # noqa: F401
from tests.test_logistics_workspace import _deal


def _upload(client, did, tmp_path):
    p = tmp_path / "foto.png"
    _png(p)
    q = _ok(client.post(f"/api/deals/{did}/logistics/quotes", headers=H(LOG), files={"file": ("foto.png", p.read_bytes(), "image/png")},
                        data={"carrier": "Фото", "scope": "intl_freight", "amount": "1"}), 201)
    return q["storage_path"]


def test_view_inline_and_download_rules(client, tmp_path):
    did, _, _ = _deal(client)
    path = _upload(client, did, tmp_path)
    r = client.get(f"/api/files/{path}?view=1", headers=H(MGR))
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png") and r.headers["content-disposition"].startswith("inline")
    r = client.get(f"/api/files/{path}", headers=H(MGR))
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")      # по умолчанию скачивание открыто
    # ЗГД закрывает скачивание для логиста: просмотр остаётся, скачивание — по запросу
    _ok(client.put("/api/admin/access/logistician/file_download", headers=H(GD), json={"level": 0}))
    try:
        assert client.get(f"/api/files/{path}?view=1", headers=H(LOG)).status_code == 200
        r = client.get(f"/api/files/{path}", headers=H(LOG))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "download_restricted"
        req = _ok(client.post("/api/files-download-requests", headers=H(LOG), json={"path": path, "reason": "для перевозчика"}), 201)
        assert req["status"] == "pending"
        assert _ok(client.post("/api/files-download-requests", headers=H(LOG), json={"path": path}), 201)["id"] == req["id"]   # без дублей
        assert client.post(f"/api/files-download-requests/{req['id']}", headers=H(LOG), json={"approve": True}).status_code == 403
        lst = _ok(client.get("/api/files-download-requests", headers=H(GD)))
        assert any(x["id"] == req["id"] and x["file_name"] == "foto.png" for x in lst)
        _ok(client.post(f"/api/files-download-requests/{req['id']}", headers=H(GD), json={"approve": True}))
        assert client.get(f"/api/files/{path}", headers=H(LOG)).status_code == 200
        assert client.post(f"/api/files-download-requests/{req['id']}", headers=H(GD), json={"approve": True}).status_code == 409
    finally:
        _ok(client.put("/api/admin/access/logistician/file_download", headers=H(GD), json={"level": 1}))


def test_view_unsupported_type(client, tmp_path):
    did, _, _ = _deal(client)
    q = _ok(client.post(f"/api/deals/{did}/logistics/quotes", headers=H(LOG), files={"file": ("data.bin", b"\x00\x01binary", "application/octet-stream")},
                        data={"carrier": "Бин", "scope": "intl_freight", "amount": "1"}), 201)
    assert client.get(f"/api/files/{q['storage_path']}?view=1", headers=H(MGR)).status_code == 415


def test_pdf_preview_as_pages(client, tmp_path):
    import pymupdf
    did, _, _ = _deal(client)
    pdf = tmp_path / "чертёж.pdf"
    d = pymupdf.open()
    for t in ("лист 1", "лист 2"):
        d.new_page().insert_text((72, 72), t)
    d.save(pdf)
    q = _ok(client.post(f"/api/deals/{did}/logistics/quotes", headers=H(LOG), files={"file": ("чертёж.pdf", pdf.read_bytes(), "application/pdf")},
                        data={"carrier": "Пдф", "scope": "intl_freight", "amount": "1"}), 201)
    path = q["storage_path"]
    assert _ok(client.get(f"/api/files/{path}?view=1&info=1", headers=H(MGR)))["pages"] == 2
    r = client.get(f"/api/files/{path}?view=1&page=2", headers=H(MGR))
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
