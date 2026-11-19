"""Сводка по рабочей базе для аудита процессов (D-096). Только чтение: транзакция READ ONLY, ничего не меняет.

Выводит только агрегаты — без имён клиентов, сумм сделок, контактов и паролей.
Запуск (с Mac, файл лежит в «Загрузках»):
  ssh root@217.198.9.112 'cd /opt/gsho-costing && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -' < ~/Downloads/audit_stats.py
"""
from __future__ import annotations

import statistics as st
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import text

from app.db import SessionLocal

TITLES = {
    "draft": "Черновик", "logistics_calc": "Логистика", "service_calc": "Сервис", "calc_ready": "Расчёт сформирован",
    "cd_approval": "Утверждение КД", "fd_visa": "Виза ФД", "gd_visa": "Виза ЗГД", "rework": "Доработка",
    "approved": "Утверждено (до отправки КП)", "kp_sent": "КП отправлено (ждём решения)", "won": "Выиграна",
    "lost": "Проиграна", "cancelled": "Отменена", "contract": "Заключение контракта", "execution": "Исполнение", "closed": "Закрыта",
}
ORDER = list(TITLES)
NOW = datetime.now(timezone.utc)


def days(a, b) -> float:
    return (b - a).total_seconds() / 86400.0


def q(db, sql, **kw):
    return db.execute(text(sql), kw).all()


def fmt(xs):
    if not xs:
        return "—"
    xs = sorted(xs)
    p75 = xs[min(len(xs) - 1, int(round(0.75 * (len(xs) - 1))))]
    return f"n={len(xs):3}  медиана {st.median(xs):5.1f}  p75 {p75:5.1f}  макс {xs[-1]:5.1f}"


def section(t):
    print(f"\n== {t}")


def main() -> None:
    db = SessionLocal()
    db.execute(text("SET TRANSACTION READ ONLY"))

    section("1. Сделки по статусам (сейчас)")
    rows = q(db, "select lower(status::text), count(*) from deals group by 1")
    cnt = dict(rows)
    for s in ORDER:
        if cnt.get(s):
            print(f"  {TITLES[s]:32} {cnt[s]:4}")
    print(f"  всего: {sum(cnt.values())}")
    for wk, n in q(db, "select to_char(date_trunc('week', created_at), 'DD.MM'), count(*) from deals "
                       "where created_at > now() - interval '10 weeks' group by date_trunc('week', created_at) order by date_trunc('week', created_at)"):
        print(f"  новых за неделю с {wk}: {n}")

    section("2. Сколько дней сделка стоит в каждом статусе (по журналу смены статусов, календарные дни)")
    created = dict(q(db, "select id, created_at from deals"))
    cur = {i: s for i, s in q(db, "select id, lower(status::text) from deals")}
    trans = defaultdict(list)
    for deal_id, at, b, a in q(db, "select deal_id, at, before->>'status', after->>'status' from audit_log "
                                   "where entity = 'deal' and action = 'status' and deal_id is not null order by at"):
        trans[deal_id].append((at, b, a))
    dur = defaultdict(list)
    open_age = defaultdict(list)
    rework_in = Counter()
    rework_deals = set()
    paths = Counter()
    for did, t0 in created.items():
        prev_at, prev_s = t0, "draft"
        seq = []
        for at, b, a in trans.get(did, []):
            s = b or prev_s
            if prev_at and at:
                dur[s].append(days(prev_at, at))
            if a == "rework":
                rework_in[s] += 1
                rework_deals.add(did)
            seq.append(a)
            prev_at, prev_s = at, a
        if prev_at and cur.get(did) == prev_s and prev_s not in ("lost", "cancelled", "closed"):
            open_age[prev_s].append(days(prev_at, NOW))
        if seq:
            paths[" → ".join(TITLES.get(x, x)[:12] for x in seq[:8])] += 1
    for s in ORDER:
        if dur.get(s):
            print(f"  {TITLES[s]:32} {fmt(dur[s])}")
    print("  -- открытые сделки: сколько дней уже стоят в текущем статусе")
    for s in ORDER:
        if open_age.get(s):
            print(f"  {TITLES[s]:32} {fmt(open_age[s])}")
    total = [days(created[d], t[-1][0]) for d, t in trans.items() if d in created and any(a == "kp_sent" for _, _, a in t)]
    first_kp = []
    for d, t in trans.items():
        kp = next((at for at, _, a in t if a == "kp_sent"), None)
        if kp and d in created:
            first_kp.append(days(created[d], kp))
    print(f"  от создания до первой отправки КП: {fmt(first_kp)}")

    section("3. Возвраты на доработку")
    print(f"  сделок с возвратом: {len(rework_deals)} из {len(created)}")
    for s, n in rework_in.most_common():
        print(f"  из «{TITLES.get(s, s)}»: {n}")
    print("  -- самые частые пути сделки (первые 8 смен статуса)")
    for p, n in paths.most_common(8):
        print(f"  {n:3} × {p}")

    section("4. Визы и утверждение")
    for step, role, ok, n in q(db, "select step, role, approved, count(*) from approvals where reset_at is null group by 1,2,3 order by 1,2,3"):
        print(f"  {step:12} {role:4} {'да ' if ok else 'нет'} {n:4}")
    reset = q(db, "select count(*) from approvals where reset_at is not null")[0][0]
    print(f"  виз сброшено из-за изменения цены: {reset}")
    selfapp = q(db, "select count(distinct a.deal_id) from approvals a join deal_versions v on v.id = a.version_id "
                    "where a.role = 'cd' and a.approved and v.created_by = a.user_id")[0][0]
    print(f"  КД утвердил версию, которую сам сформировал: {selfapp} сделок")

    section("5. Задачи")
    for role, open_, late in q(db, "select role, count(*) filter (where done_at is null), count(*) filter (where done_at is null and due < current_date) "
                                   "from tasks group by role order by 2 desc"):
        print(f"  {role:14} открыто {open_:4}  просрочено {late:4}")
    lead = [r[0] for r in q(db, "select extract(epoch from done_at - created_at) / 86400 from tasks where done_at is not null")]
    print(f"  время выполнения задачи, дни: {fmt([float(x) for x in lead])}")
    late_done = q(db, "select count(*) filter (where done_at::date > due), count(*) from tasks where done_at is not null")[0]
    print(f"  выполнено позже срока: {late_done[0]} из {late_done[1]}")
    for title, n in q(db, "select title, count(*) from tasks where done_at is null group by 1 order by 2 desc limit 10"):
        print(f"  открыта «{title[:60]}»: {n}")

    section("6. Торги и редакции КП")
    st_rows = q(db, "select deal_id, count(*), min(price_before_rub), min(price_rub) from negotiation_steps group by deal_id")
    print(f"  сделок с торгами: {len(st_rows)}; шагов: {sum(r[1] for r in st_rows)}")
    if st_rows:
        print(f"  шагов на сделку: {fmt([float(r[1]) for r in st_rows])}")
    disc = []
    for did, first, last in q(db, "select s.deal_id, (array_agg(s.price_before_rub order by s.no))[1], (array_agg(s.price_rub order by s.no desc))[1] "
                                  "from negotiation_steps s group by s.deal_id"):
        if first and last:
            disc.append((first - last) / first * 100)
    print(f"  снижение цены за торги, %: {fmt(disc)}")
    for reason, n in q(db, "select reason, count(*) from negotiation_steps group by 1 order by 2 desc"):
        print(f"  причина шага «{reason}»: {n}")
    rv = q(db, "select deal_id, count(*), count(*) filter (where sent_at is not null) from kp_revisions group by deal_id")
    print(f"  сделок с редакциями КП: {len(rv)}; редакций: {sum(r[1] for r in rv)}; из них отправлено клиенту: {sum(r[2] for r in rv)}")
    out = q(db, "select coalesce(outcome, '—'), count(*) from deals where outcome is not null group by 1")
    print("  исходы: " + ", ".join(f"{o} {n}" for o, n in out))
    reasons = q(db, "select left(outcome_reason, 50), count(*) from deals where outcome in ('lost','cancelled') and outcome_reason <> '' group by 1 order by 2 desc limit 8")
    for r, n in reasons:
        print(f"  причина проигрыша/отмены: «{r}» × {n}")

    section("7. Логистика и данные «на 1 машину / на всё»")
    pos = q(db, "select count(*), count(*) filter (where (logistics->>'per_unit')::boolean is true), "
                "count(*) filter (where logistics is not null and logistics::text not in ('{}', 'null')) from positions")[0]
    print(f"  позиций: {pos[0]}; с расходами: {pos[2]}; уже «на 1 машину»: {pos[1]}; старый режим «на всю позицию»: {pos[2] - pos[1]}")
    bt = q(db, "select count(*), count(*) filter (where (items->>'per_unit')::boolean is true) from batches")[0]
    print(f"  партий: {bt[0]}; «на 1 машину»: {bt[1]}; старый режим: {bt[0] - bt[1]}")
    lo = q(db, "select count(*), count(*) filter (where per_unit) from local_options")[0]
    print(f"  опций доукомплектования: {lo[0]}; «на 1 машину»: {lo[1]}; старый режим: {lo[0] - lo[1]}")
    bpd = [float(r[0]) for r in q(db, "select count(*) from batches group by deal_id")]
    print(f"  партий на сделку: {fmt(bpd)}")

    section("8. Валюта")
    for c, n in q(db, "select coalesce(upper(price_currency), 'не задана'), count(*) from variants where price_rub is not null group by 1 order by 2 desc"):
        print(f"  цена клиенту в {c}: {n} предложений")
    print(f"  валюта сделки выбрана вручную: {q(db, 'select count(*) from deals where deal_currency is not null')[0][0]} сделок")
    for c, n in q(db, "select currency, count(*) from client_price_history group by 1 order by 2 desc"):
        print(f"  история цен клиенту в {c}: {n} записей")

    section("9. Люди")
    roles = Counter()
    active7 = Counter()
    for r, seen, act in q(db, "select roles::text, last_seen_at, is_active from users"):
        if not act:
            continue
        for x in (r or "").strip("[]").replace('"', "").split(","):
            x = x.strip()
            if x:
                roles[x] += 1
                if seen and days(seen, NOW) <= 7:
                    active7[x] += 1
    for r, n in roles.most_common():
        print(f"  {r:14} {n:3} сотрудн.; заходили за 7 дней: {active7[r]}")
    un = [float(r[0]) for r in q(db, "select count(*) from notifications where read_at is null group by user_id")] if q(
        db, "select 1 from information_schema.columns where table_name='notifications' and column_name='read_at'") else []
    print(f"  непрочитанных уведомлений на сотрудника: {fmt(un)}")

    section("10. Скорость расчёта (полный пересчёт сделки, как на экране «Расчёт и утверждение»)")
    from app.models import Deal
    from app.services.deal_calc import run_calc
    times, fails = [], 0
    ids = [r[0] for r in q(db, "select id from deals where lower(status::text) not in ('lost','cancelled','closed','draft') order by updated_at desc limit 40")]
    slow = []
    for did in ids:
        deal = db.get(Deal, did)
        t = time.perf_counter()
        try:
            with db.begin_nested():
                run_calc(db, deal)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        except Exception:
            fails += 1
            continue
        dt = time.perf_counter() - t
        times.append(dt * 1000)
        slow.append((dt, deal.number, len(deal.positions)))
    print(f"  расчёт, мс: {fmt(times)}; ошибок расчёта: {fails}")
    for dt, num, npos in sorted(slow, reverse=True)[:3]:
        print(f"  самые медленные: {num} — {dt * 1000:.0f} мс, позиций {npos}")

    section("11. Объём")
    for tname in ("deals", "positions", "deal_versions", "kp_revisions", "negotiation_steps", "tasks", "audit_log", "attachments", "deal_feed", "notifications"):
        try:
            print(f"  {tname:18} {q(db, f'select count(*) from {tname}')[0][0]}")
        except Exception:
            db.rollback()
            db.execute(text("SET TRANSACTION READ ONLY"))
    db.rollback()
    print("\nготово: база не изменялась (транзакция только для чтения, откат)")


main()
