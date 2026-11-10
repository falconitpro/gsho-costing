# Отчёт этапа 1 — резервное копирование в Yandex Object Storage

## 1. Идентификация

- Этап: 1 — Yandex backup (`11` §3).
- Дата: 11.11.2026.
- Исполнитель: Claude (облачная песочница, синтетические данные, локальный репозиторий restic).
- Актуальный main перед работой: `740b25a` (merge #139).
- Ветка: `feat/yandex-backup`; PR #140 → `main`.
- Production deploy: не выполнялся. Production migrations: не применялись. Yandex bucket/ключи: не создавались.

## 2. Объём

### Реализовано

- **Скрипт копии** `deploy/backup/gsho-backup.sh`: lock → staging → `pg_dump --format=custom` → `pg_restore --list` → tar вложений → manifest + SHA-256 → `restic backup` → проверка снимка → `restic check` (по воскресеньям `--read-data-subset=5%`) → `forget --keep-daily 90 --keep-monthly 12 --prune` → атомарный статус. Ошибка на любом шаге — `failed` с кодом шага, staging удаляется, прежние снимки не трогаются.
- **systemd**: `gsho-backup.service` (oneshot, `EnvironmentFile=/etc/gsho/backup.env`) и `gsho-backup.timer` (ежедневно 19:00 по Москве, включая выходные, `Persistent=true`).
- **Вспомогательное**: `gsho-backup-status.sh` (сводка без секретов для отчёта), `gsho-restore-verify.sh` (восстановление снимка в пустой каталог и новую БД с проверкой сумм; отказывается работать на стенде), `gsho-backup.env.example` (только заглушки), `install.md` (пошагово для владельца), `recovery-smoke.md` (restore drill).
- **CRM**: таблица `backup_runs` (SQL `013`), право `backup_status` (ЗГД и администратор — чтение), `GET /api/operations/backups/status` и `/runs`, раздел «Настройки → Резервные копии», уведомления «в системе» об ошибке, предупреждении retention и отсутствии копии больше 26 ч; переход из колокольчика в раздел.
- **Деплой**: seed только при `APP_ENV=dev`; копия перед деплоем — по `BACKUP_REQUIRED=true` (выключено до gate), до `git reset` и сборки.
- **CI**: установка restic, shellcheck и pg-клиента, чтобы тесты скрипта не пропускались.

### Не вошло

- Ручной запуск из CRM (D-026) — вместо него команда для администратора.
- Удаление старого compose `backup` и отключение Timeweb — этап 8, после gate.
- `APP_ENV=recovery` в приложении — этап 3 (проверки окружения); в `recovery-smoke.md` описан обходной вариант.

### Отклонения от пакета

- D-021: SQL `013` вместо `010`.
- D-025: часовой пояс в `OnCalendar` — директивы `Timezone=` в systemd нет, пакетный вариант запускал бы копию в 22:00 МСК.
- D-027: в `backup_runs` добавлены `files_count`, `retention_status`; статус — через read-only mount, без writer API.
- D-028: `--group-by host` и постоянный путь staging под lock.

## 3. Изменённые файлы

| Путь | Изменение |
|---|---|
| `deploy/backup/*` (8 файлов) | новые: скрипты, unit-файлы, пример env, инструкции |
| `deploy/deploy-remote.sh` | pre-deploy копия по флагу; seed только в dev |
| `docker-compose.prod.yml` | `BACKUP_STATUS_DIR` + mount статуса `:ro` в backend |
| `backend/migrations/013_backup_operations.sql` | новая таблица и индексы |
| `backend/app/models/__init__.py` | `BackupRun` |
| `backend/app/services/backup_status.py` | разбор, история, состояние, уведомления |
| `backend/app/api/operations.py`, `main.py` | API; ежечасное обновление статуса в `_daily_jobs` |
| `backend/app/core/roles.py` | группа `backup_status` |
| `backend/app/api/notifications.py`, `frontend/src/components/NotificationBell.tsx` | событие `backup` и переход без сделки |
| `frontend/src/components/BackupStatusBlock.tsx`, `pages/SettingsHub.tsx`, `styles.css` | раздел «Резервные копии» |
| `backend/tests/test_backup_status.py`, `test_backup_script.py`, `test_deploy_remote.py` | 45 тестов |
| `.github/workflows/ci.yml` | restic/shellcheck/postgresql-client |
| `docs/DEPLOY_YANDEX_CLOUD.md` | строка о копиях: Timeweb сейчас, Yandex — после gate |
| `docs/operations/15`, `16`, `reports/01`, `qa_backup_ui.py`, `screens/b0*.png` | журналы, QA |

## 4. Модель данных и миграции

| Change set | Назначение | Чистая DB | Повторный прогон | Production |
|---|---|---|---|---|
| `013_backup_operations.sql` | `backup_runs`, 2 индекса, 2 CHECK | PASS | PASS (NOTICE «already exists», без изменений) | NOT APPLIED |

- Порядок rehearsal: схема по моделям `main@740b25a` → SQL `001`…`013` → второй проход.
- ORM/SQL parity (MIG-05): столбцы (тип, длина, nullable), индексы и ограничения `backup_runs` после SQL и после `create_all` — идентичны (`diff` пустой); `rehearsal.py schema` — `ok: true`.
- MIG-04: копия `013` с ошибкой между `CREATE TABLE` и `CREATE INDEX` → `division by zero`, таблицы нет; чистое применение после этого — успешно.
- На стенде с `APP_ENV=dev` таблицу создаст `init_db()` при выкате (как D-100).

## 5. API и права

| Endpoint | Право | Позитивный тест | Негативный тест |
|---|---|---|---|
| `GET /api/operations/backups/status` | `backup_status ≥ READ` | ЗГД, администратор — 200 | менеджер, ФД, КД, юрист, наблюдатель — 403 |
| `GET /api/operations/backups/runs` | то же; `limit ≤ 100` | пагинация, `total` | то же; `limit=100000` → 100 |

- Ответы не содержат ключей, пароля, URL с подписью, путей: проверено canary-строкой в `error_summary` и в лишних полях файла.
- Файл статуса: схема v1, ≤ 16 КБ, symlink игнорируется, неверные поля → «ошибка статуса», а не «успех»; успех без `snapshot_id` не принимается.

## 6. Фактические проверки

| Команда | Окружение | Результат | passed/failed/skipped | Длительность |
|---|---|---|---|---|
| `pytest -q tests/test_backup_status.py tests/test_backup_script.py tests/test_deploy_remote.py` | Python 3.12.3, PostgreSQL 16.15, restic 0.16.4, shellcheck 0.9.0, systemd 255 | PASS | 45 / 0 / 0 | ≈ 75 с |
| `pytest -q` (весь набор, чистая БД) | то же | 1 FAIL — pre-existing, D-030 | 241 / 1 / 1 | 217 с |
| `pytest -q` на неизменённом `main@740b25a` (контроль) | то же, отдельная БД | тот же FAIL | 196 / 1 / 1 | 144 с |
| `npm run build` | Node 22 | PASS | — | 3.6 с |
| `bash -n` + `shellcheck` для 4 скриптов | — | PASS | — | — |
| `systemd-analyze calendar '*-*-* 19:00:00 Europe/Moscow'` / `verify` | systemd 255 | 19:00:00 MSK; без «Unknown key» | — | — |

Тесты скрипта (BKP-01…07): успешный запуск (manifest, staging удалён, права файлов 0644, в репозитории нет открытого текста), восстановление в новую БД с `sha256sum -c` 3×OK, неверный пароль, недоступный репозиторий (прежний снимок цел), нет параметров (в статусе только имена переменных), lock (код 75, статус не тронут), `unsupported_storage`, `disk_full`, ошибка `pg_dump`, ошибка вложений, политика хранения на синтетических снимках с датами, совместимость файла статуса с парсером backend.

### Ручной прогон в песочнице

- Первый запуск случайно пришёлся на остановленную PostgreSQL: `failed / db_dump_failed`, staging пуст. Затем успешный: снимок `07fae0f5`, 4.8 с, БД 1.1 МБ, 2 файла.
- `gsho-restore-verify.sh`: 91 таблица, 13 пользователей, 43 сделки в восстановленной БД, 2 файла, 3 с.

### Browser QA (`docs/operations/qa_backup_ui.py`)

| Сценарий | 1440 | 1280 | 768 | 390 | Console/network |
|---|---|---|---|---|---|
| Не настроено | PASS | — | — | — | без ошибок |
| В порядке + история 8 запусков | PASS | PASS | PASS | PASS | без ошибок |
| Ошибка: баннер, уведомление → переход в раздел | PASS | — | — | PASS | без ошибок |
| Нет свежей копии > 26 ч | — | — | PASS | — | без ошибок |
| Менеджер: раздела нет, запросов к API статуса нет | PASS | — | — | — | без 403 |

- Кнопки «Обновить» и «Скопировать» реально работают (буфер обмена проверен). Горизонтальной прокрутки страницы нет; обрезанного текста в баннерах, KPI и блоке команды нет. Найдено и исправлено: длинный путь к runbook на 390 px вылезал за баннер.
- Скриншоты: `docs/operations/screens/b01…b04*.png` (синтетические данные).

## 8. Производительность и ресурсы

- Скрипт на синтетике: 4.8 с. На стенде время определит первый ручной запуск. Нагрузку (`Nice=10`, `IOSchedulingPriority=7`) и место под staging (`BACKUP_MIN_FREE_MB=1024`) проверить на ВМ.
- Статус в CRM: чтение до 200 файлов по ≤ 16 КБ раз в час и при открытии раздела.

## 9. Найденные и исправленные проблемы

| Проблема | Причина | Исправление | Проверка |
|---|---|---|---|
| Пакетный таймер запускал бы в 19:00 UTC | `Timezone=` не существует в systemd | пояс в `OnCalendar` (D-025) | `systemd-analyze verify`, тест |
| Политика хранения не удаляла бы старые снимки | группировка restic по путям | `--group-by host`, постоянный staging (D-028) | `test_retention_daily_and_monthly` |
| Файл истории создавался 0600 — backend в контейнере не прочитал бы | `cp` без сохранения прав при `umask 077` | `cp -p`, каталоги 0755 | тест прав 0644 |
| Текст баннера обрезался на 390 px | длинный путь без переноса | `overflow-wrap: anywhere` | QA с проверкой обрезки |

## 10. Открытые проблемы и риски

| ID | Severity | Описание | Владелец |
|---|---|---|---|
| R-4 | **high** | D-030: CI на main красный с 11.11 из-за даты, зашитой в финансовый тест → автодеплой стенда стоит. PR этапа 1 тоже будет красным | владелец: разрешить fix PR |
| R-5 | low | `test_legal_poa` упал 1 раз из 2 полных прогонов, не воспроизводится | наблюдаем |
| R-6 | medium | In-app тревога не сработает, если упал весь сервер; внешнего heartbeat нет (`03` §5) | следующее улучшение |
| R-7 | info | `docker compose exec` из systemd требует `COMPOSE_FILE` в env (есть в примере) и запущенных контейнеров `db`/`backend` | проверяется первым ручным запуском |

## 11. Действия владельца

- [ ] Решение по fix PR для D-030 (иначе ни этот, ни следующие PR не выкатятся на стенд).
- [ ] Review и merge PR этапа 1.
- [ ] Yandex Cloud и сервер — по `deploy/backup/install.md` §1–3. Секреты — в защищённой заметке + вторая копия пароля restic вне iCloud (D-024).
- [ ] Вернуть в диалог вывод `sudo gsho-backup-status.sh --check` после ручного запуска и после двух ежедневных (19:00).
- [ ] Затем restore drill на отдельной ВМ по `recovery-smoke.md`, отчёт без секретов.

## 12. Gate и итог

- Обязательные тесты этапа: PASS 45 / FAIL 0. Полный набор: 1 FAIL — pre-existing (D-030), на main тот же.
- Журнал `15` обновлён (D-024…D-030), `16` обновлено, runbook'и — `deploy/backup/install.md`, `recovery-smoke.md`.
- PR готов к review: да. Production gate (Gate 1): нет — нужны действия владельца.
- Timeweb можно отключать: **НЕТ**.

Итоговый статус: `PR_OPEN`.

```text
Сделан контур зашифрованных копий в Yandex Object Storage (restic, 19:00 МСК, перед деплоем по флагу) и статус в CRM. Проверено на синтетике: 45 тестов, восстановление в новую БД, SQL 013 ×2 и откат, браузер на 4 ширинах. Владельцу: fix PR для упавшего финансового теста, merge, создание бакета и ключей, первая копия.
```

## 13. Следующий этап

- Gate 1 ждёт владельца, но код этапа 2 от него не зависит.
- Этап 2 — `feat/security-core`, SQL `014`: сессии на сервере, пароли, TOTP, отключение сотрудника, пересмотр прав.
