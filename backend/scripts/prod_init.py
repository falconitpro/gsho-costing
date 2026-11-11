"""Первичная подготовка рабочей базы (deploy/production/install.md). Запускает владелец вручную, на пустой базе.

    python -m scripts.prod_init check                    # настройки рабочего режима и состояние базы; ничего не меняет
    python -m scripts.prod_init schema                   # таблицы по моделям; после неё — SQL backend/migrations по порядку
    python -m scripts.prod_init reference                # справочники без демо-данных (схемы оплаты, компании группы, маршруты)
    python -m scripts.prod_init admin --email a@b --name "Фамилия Имя"   # первый пользователь (ЗГД) с временным паролем

Демо-данные (scripts.seed) в рабочую базу не попадают. Временный пароль печатается один раз и нигде не сохраняется;
при первом входе система потребует его сменить.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone

from sqlalchemy import func, inspect, select

ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # без похожих символов: l/1/I, O/0


def temp_password() -> str:
    """Четыре группы по 5 символов: удобно продиктовать и ввести; всегда есть буква и цифра."""
    while True:
        pw = "-".join("".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(4))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def check() -> int:
    from app.core import environment
    from app.core.config import get_settings
    from app.db import engine
    s = get_settings()
    bad = environment.config_problems(s)
    print(f"режим: APP_ENV={s.env}, вход={s.auth_provider}, проверка файлов={s.file_scan}, класс данных={s.classification}")
    for p in bad:
        print("  НЕ ГОТОВО:", p)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    need = {"users": "schema", "app_markers": "SQL 016b", "auth_sessions": "SQL 014", "support_tickets": "SQL 017", "ai_conversations": "SQL 018",
            "briefing_item_states": "SQL 019"}
    for t, src in need.items():
        if t not in tables:
            print(f"  НЕ ГОТОВО: нет таблицы {t} ({src})")
            bad.append(t)
    if "users" in tables:
        from app.db import SessionLocal
        from app.models import User
        with SessionLocal() as db:
            n = db.scalar(select(func.count()).select_from(User))
            gd = [u for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if "gd" in (u.roles or []) and u.password_hash]
            print(f"сотрудников: {n}; ЗГД с паролем: {len(gd)}")
            if not gd:
                print("  НЕ ГОТОВО: нет ЗГД с паролем — python -m scripts.prod_init admin --email … --name …")
                bad.append("admin")
    print("ИТОГ:", "готово к запуску" if not bad else f"не готово ({len(bad)})")
    return 0 if not bad else 1


def schema() -> int:
    from app.db import init_db
    init_db()
    print("schema: таблицы созданы. Дальше — SQL из backend/migrations по порядку (deploy/production/init-db.sh делает это сам)")
    return 0


def reference() -> int:
    from app.db import SessionLocal
    from scripts import reference as ref
    with SessionLocal() as db:
        ref.run(db)
        db.commit()
    print("reference: схемы оплаты, компании группы (заготовки названий), маршруты поставки — на месте")
    return 0


def admin(email: str, name: str, force: bool = False) -> int:
    from app.core.auth import hash_password
    from app.db import SessionLocal
    from app.models import User
    from app.services import password_policy
    email = email.strip().lower()
    if "@" not in email or not name.strip():
        print("нужны --email и --name")
        return 2
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        others = [x for x in db.scalars(select(User).where(User.is_active.is_(True))).all()
                  if "gd" in (x.roles or []) and x.password_hash and x.email != email]
        if others and not force:
            print("ЗГД с паролем уже есть — новых сотрудников и пароли задаёт он в «Настройки → Сотрудники». Аварийно: добавьте --force")
            return 1
        if u is not None and u.password_hash and not force:
            print("у этой учётной записи уже есть пароль. Аварийный сброс: добавьте --force (все её входы будут завершены)")
            return 1
        pw = temp_password()
        if u is None:
            u = User(external_id=email, email=email, display_name=name.strip(), roles=["gd"])
            db.add(u)
            db.flush()
        else:
            u.roles = sorted(set(u.roles or []) | {"gd"})
            u.is_active = True
        assert not password_policy.problems(pw, u)
        u.password_hash, u.password_set_at, u.password_must_change = hash_password(pw), datetime.now(timezone.utc), True
        u.failed_logins, u.locked_until = 0, None
        if inspect(db.get_bind()).has_table("auth_sessions"):
            from app.services import auth_session
            auth_session.revoke_all(db, u.id, "password", None)
        from app.services.deal_service import audit
        audit(db, None, None, "user", u.id, "bootstrap_admin", after={"email": email, "roles": u.roles, "by": "scripts.prod_init"})
        db.commit()
    print(f"Учётная запись ЗГД: {email}")
    print(f"Временный пароль (показывается один раз): {pw}")
    print("При первом входе система потребует задать свой пароль. Затем включите код из приложения: «Настройки → Безопасность».")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Подготовка рабочей базы")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("schema")
    sub.add_parser("reference")
    a = sub.add_parser("admin")
    a.add_argument("--email", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--force", action="store_true")
    ns = ap.parse_args(argv)
    if ns.cmd == "admin":
        return admin(ns.email, ns.name, ns.force)
    return {"check": check, "schema": schema, "reference": reference}[ns.cmd]()


if __name__ == "__main__":
    sys.exit(main())
