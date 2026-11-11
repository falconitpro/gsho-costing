"""Подготовка браузерной проверки безопасности (этап 2): синтетические сотрудники. Только синтетическая БД.
Запуск из backend/: PYTHONPATH=. python ../docs/operations/qa_security_prep.py > /tmp/qa_sec.json"""
import json
import uuid
from datetime import date, datetime, timezone

from app.core.auth import hash_password
from app.db import SessionLocal
from app.models import Deal, MfaCredential, Task, User
from app.services import mfa
from scripts.seed import run as seed

PW = "Tundra-river-2026"
seed()
out = {"pw": PW}
with SessionLocal() as db:
    def mk(key, name, roles=("manager",), **kw):
        tag = uuid.uuid4().hex[:6]
        u = User(email=f"qa.{key}.{tag}@example.com", external_id=f"qa.{key}.{tag}", display_name=name, roles=list(roles),
                 password_hash=hash_password(PW), password_set_at=datetime.now(timezone.utc), **kw)
        db.add(u); db.flush()
        out[key] = {"id": u.id, "email": u.email, "name": name}
        return u
    mk("wizard", "Настройкин Иван")
    b = mk("mfa", "Кодов Пётр")
    secret = mfa.new_secret()
    ct, kid = mfa.encrypt(secret)
    db.add(MfaCredential(user_id=b.id, secret_ciphertext=ct, key_id=kid, enabled_at=datetime.now(timezone.utc)))
    mfa.regenerate_recovery(db, b.id)
    out["mfa"]["secret"] = secret
    c = mk("target", "Уходящий Семён")
    deal = db.query(Deal).first()
    db.add(Task(deal_id=deal.id, role="manager", assignee_id=c.id, title="QA: задача отключаемого", due=date.today()))
    mk("temp", "Временный Олег", password_must_change=True)
    from app.models import AuthSession
    adm = db.query(User).filter(User.email == "admin@example.com").one()
    for row in db.query(AuthSession).filter(AuthSession.user_id == adm.id).all():   # сценарий проверяет диалог подтверждения
        row.last_step_up_at = None
    db.commit()
print(json.dumps(out, ensure_ascii=False))
