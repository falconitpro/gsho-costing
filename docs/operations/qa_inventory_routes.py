"""Инвентаризация маршрутов по 08 §1: upload/download/export/auth/notifications. Только чтение кода.
Запуск из backend/: PYTHONPATH=. python ../docs/operations/qa_inventory_routes.py > /tmp/inv.json (DATABASE_URL любой, БД не трогается)."""
import inspect, re, sys, json
from fastapi.routing import APIRoute
from app.main import app

def deps(dependant, acc):
    for d in dependant.dependencies:
        if d.call is not None:
            n = getattr(d.call, "__qualname__", None) or getattr(d.call, "__name__", None) or type(d.call).__name__
            acc.append(n)
        deps(d, acc)
    return acc

KIND = [("upload", r"UploadFile"), ("download", r"file_response\(|FileResponse\(|StreamingResponse\("),
        ("presign", r"generate_presigned_url|\.link\("), ("export", r"export|xlsx|\.zip|docx|pdf")]
def walk(rs):
    for r in rs:
        if hasattr(r, 'original_router'):
            yield from walk(r.original_router.routes)
        else:
            yield r
rows = []
for r in walk(app.routes):
    if not isinstance(r, APIRoute):
        continue
    src = inspect.getsource(r.endpoint)
    sig = str(inspect.signature(r.endpoint))
    kinds = [k for k, p in KIND[:3] if re.search(p, src + sig)]
    if not kinds and re.search(r"(export|download|\.xlsx|/file|/files|zip|pdf|docx)", r.path, re.I):
        kinds = ["export?"]
    auth_path = re.search(r"/auth|/login|/logout|/password|/oidc", r.path)
    notif = re.search(r"NotificationLog|notify\(", src)
    if not (kinds or auth_path or notif):
        continue
    d = deps(r.dependant, [])
    authdeps = sorted({x.replace("require_roles.<locals>.dep","require_roles") for x in d if x not in ("get_db",)})
    cats = kinds + (["auth"] if auth_path else []) + (["notify"] if notif else [])
    rows.append({"methods": ",".join(sorted(r.methods)), "path": r.path, "endpoint": f"{r.endpoint.__module__.replace('app.api.','')}.{r.endpoint.__name__}",
                 "kinds": cats, "auth": authdeps or ["— (нет)"],
                 "obj_check": sorted((set(re.findall(r"\b(can_\w+|_can_\w+|is_assignee|_check\w*|ensure_\w+|_visible\w*|\w+_or_404|_get_deal\w*|_load\w*|HTTPException\((?:403|404))", src)) | set(re.findall(r"\b(_?\w+)\(db, [^)\n]*\buser\b", src))) - {"audit", "notify", "log_action"})[:4],
                 "audit": bool(re.search(r"\baudit\(", src))})
json.dump(rows, sys.stdout, ensure_ascii=False, indent=0)
