# Отчёт этапа 0 — baseline и инвентаризация

## 1. Идентификация

- Этап: 0 — baseline и защита исходного состояния (`11` §2).
- Дата: 11.11.2026.
- Исполнитель: Claude (облачная песочница, синтетические данные).
- Baseline пакета: `main@e652a88eb96d216bf464a4936e27d99572ef7996` (PR #131).
- Актуальный main перед работой: `30c4390faa8b389196e049c58b052a6d6c826a86` — merge PR #138 от 10.11.2026 23:06 МСК.
- Ветка: `chore/operations-baseline` (только документы и read-only скрипт инвентаризации).
- PR и base: #139 → `main`.
- Production deploy: не выполнялся. Production migrations: не применялись.

## 2. Объём

### Сделано

- Пакет v1.0.0 (18 документов, `manifest.json`, `checksums.sha256`) скопирован в `docs/operations/` без изменения смысла; SHA-256 сверены до копирования — 19/19 OK.
- Сверка main с baseline, двукратная репетиция SQL change set'ов, исходные backend-тесты и frontend build на чистой PostgreSQL 16.
- Инвентаризация auth/upload/download/export/notify/deploy/backup точек: `reports/00_inventory_routes.md`, генератор `qa_inventory_routes.py`.

### Не сделано на этапе 0 (нужен владелец)

- `11` §2 п.6 — размер БД/файлов, backup volumes, ресурсы ВМ: нужен доступ к серверу. Команда для владельца — в §11; в диалог возвращаются только числа.
- `11` §2 п.7 — план хранения секретов владельца: вопрос в §11.

### Отклонения от пакета

- Нумерация SQL сдвинута на +3 (D-021): в main уже есть `010`–`012`.

## 3. Расхождение main с baseline

После `e652a88` слиты PR #132–#138: зеркальные запросы 3 и 4, юридический ИИ и удаление черновиков, правила цепочки, рамочные договоры, раскладка правил, UX запчастей. 84 файла, +3429/−157.

| Пересечение с пакетом | Что в main | Влияние |
|---|---|---|
| `backend/migrations/` | заняты `010_mirror_contracts`, `011_chain_rules`, `012_framework_contracts` | новые номера пакета — `013`–`018` (D-021) |
| `api/workplace.py` | +3 строки: фильтр зеркальных пакетов `can_see_mirror_docs` | сводка (этап 5) строится поверх этого фильтра, не обходит его |
| `adapters/llm.py` | `LLMProviderError`, `_raise_for`, подсказки 403 YandexGPT | ИИ-окно (этап 6) переиспользует этот контракт ошибок |
| `core/roles.py` | группа `intragroup_chain` получила default `{"group_chain"}` | новые группы безопасности добавляются рядом, существующие не ослабляются |
| `models/__init__.py` | `Deal.chain_approval`, `Contract.mirror_kind`, `Contract.is_framework`, `PartRequest.chain_approval` | аддитивно, с моделями пакета не конфликтует |

Других пересечений с модулями пакета (`auth`, `files`, `storage`, `notifications`, `deploy`, `docker-compose`) нет. Этапы не останавливаются.

## 4. Модель данных и миграции

| Change set | Чистая DB | Повторный прогон | Production |
|---|---|---|---|
| `001`–`012` (все существующие) | PASS, 12/12 | PASS, 12/12 | NOT APPLIED этим пакетом |

- Порядок: схема по моделям baseline `e652a88` (`init_db()` из git worktree), затем `psql -v ON_ERROR_STOP=1 -f` по 001…012, затем второй проход.
- Сверка схемы с текущими моделями (`docs/finance/qa/rehearsal.py schema`, только чтение): отсутствующих таблиц 0, отсутствующих столбцов 0, `ok: true`.
- Новые collisions: только номера 010–012, см. D-021.

## 6. Фактические проверки

| Команда | Окружение | Результат | passed/failed/skipped | Длительность |
|---|---|---|---|---|
| `cd backend && python -m pytest -q` | Python 3.12.3, PostgreSQL 16.15 (чистый кластер), `APP_ENV=dev AUTH_PROVIDER=dev STORAGE_PROVIDER=local`, fastapi 0.162.4 | PASS | 197 / 0 / 1 | 148.5 с |
| `cd frontend && npm install && npm run build` (`tsc --noEmit && vite build`) | Node 22.23 (в CI — Node 20) | PASS | — | 3.3 с |
| SQL 001–012 × 2 | та же PostgreSQL 16 | PASS | 24 / 0 / 0 | — |

- Pre-existing предупреждения, не ошибки: бандл фронтенда 1.2 МБ (предупреждение Vite о чанке > 500 кБ); `InsecureKeyLengthWarning` PyJWT на тестовом 20-байтовом ключе; SAWarning о зарезервированном имени `registry` у `Client`.
- Падающих pre-existing тестов нет. Browser QA на этапе 0 не требуется: код не менялся.

## 9. Находки инвентаризации

| # | Находка | Где | Этап, который закрывает |
|---|---|---|---|
| F-1 | Журнал скачиваний почти отсутствует: из 31 маршрута скачивания `audit()` вызывает 1 (`/api/legal/profiles/{pid}/card`) | `reports/00_inventory_routes.md` | 3, SQL `015` |
| F-2 | Presigned S3-ссылки живут 3600 с, пакет требует ≤ 300 с | `adapters/storage.py:211` | 3 |
| F-3 | 32 маршрута принимают `UploadFile`; антивирусной проверки и карантина нет | инвентаризация | 3 |
| F-4 | Сессия — stateless JWT в HttpOnly cookie (`pv`, скользящий срок 8 ч / 30 дней «Запомнить меня»); серверного реестра нет, отдельную сессию отозвать нельзя | `core/auth.py:137–231` | 2, SQL `014` |
| F-5 | `get_current_user` на каждом запросе проверяет `user.is_active`, поэтому отключение действует сразу. Путей отключения три: PATCH сотрудника (`admin.py:141`) и два fallback'а удаления (`admin.py:176`, `:190`). Единого offboarding-сервиса нет | `api/admin.py` | 2 |
| F-6 | Общий `notify()` рассылает по каналам пользователя, включая e-mail (`adapters.mail`) и WeCom | `services/deal_service.py:95–123` | 4: app-only helper, `notify()` не трогать |
| F-7 | `deploy-remote.sh` на каждом деплое безусловно выполняет `python -m scripts.seed \|\| true`; миграций и pre-deploy backup нет | `deploy/deploy-remote.sh:9` | 1 (seed убрать; hook — выключен до настройки Яндекса) |
| F-8 | Текущий бэкап — compose-сервис `backup`: `pg_dump \| gzip` + `tar` в локальный volume `backups`, хранение 90 дней, цикл `sleep 86400` без привязки ко времени суток | `docker-compose.yml:35–53` | 1 (не удалять до gate), 8 |
| F-9 | Фоновые задания — один цикл `_daily_jobs` в процессе API (курсы ЦБ, SLA запчастей и др.) | `main.py` | 1/3: stale-check и cleanup встраивать с блокировкой, без shell-бэкапа из веб-процесса |
| F-10 | `GET /api/auth/roles` отдаёт без входа список кодов и названий ролей, без данных пользователей | `api/auth.py:187` | 2: оценить, low |
| F-11 | CI на push в `main` деплоит на тестовый стенд (SSH или `autodeploy.sh`) | `.github/workflows/ci.yml` | учитывать: merge PR = выкат на стенд |

Выборочная ручная проверка 11 скачиваний без явной `can_*`-проверки: все загружают объект через функции, которые принимают `user` (`get_deal_or_404`, `_pos`, `_unit`, `_pkg`, `_claim`, `_kp_or_404`). Исключение — пустой шаблон RFQ (`/api/deals/rfq/template/file`), он не содержит данных. Обхода объектной авторизации не найдено; полную проверку с IDOR-тестами делает этап 3.

## 10. Открытые риски

| ID | Severity | Описание | Владелец |
|---|---|---|---|
| R-1 | medium | F-11: каждый merge в main сразу выкатывается на стенд, поэтому миграции пакета нужно применять на стенде вручную до merge PR, который их требует (как в D-100 финансов) | владелец |
| R-2 | medium | F-7: безусловный seed на каждом деплое; исправляется в PR этапа 1 | Claude, этап 1 |
| R-3 | info | Ресурсы ВМ не измерены, а ClamAV на 4 ГБ RAM нужно проверять | владелец → этап 3 |

## 11. Действия владельца

- [ ] Review и merge PR `chore/operations-baseline` (только документы).
- [ ] На сервере выполнить и вернуть в диалог только вывод (без имён файлов с данными, без секретов):
  ```bash
  nproc; free -h | sed -n 2p; df -h / | tail -1
  docker system df -v | grep -E 'pgdata|storage|backups'
  docker compose exec -T db psql -U gsho -d gsho -Atc "select pg_size_pretty(pg_database_size('gsho'))"
  ```
- [ ] Сказать, где будут храниться RESTIC_PASSWORD и ключи Yandex SA: менеджер паролей, Yandex Lockbox или файл `EnvironmentFile` с правами 600 на сервере. Сами значения в диалог не присылать.

## 12. Gate и итог

- Baseline воспроизводим: да (197/0/1, build ok, SQL 24/24).
- Список pre-existing issues: §6, §9.
- Неизвестных migration collisions нет: известный collision 010–012 записан в D-021.
- Журнал `15` обновлён (D-021…D-023), состояние `16` обновлено.
- Timeweb можно отключать: **НЕТ**.

Итоговый статус: `PR_OPEN`.

```text
Baseline снят на main@30c4390: backend 197 passed / 1 skipped, frontend build ok, SQL 001–012 дважды без ошибок, схема = модели. Инвентаризированы 112 маршрутов и deploy/backup, найдено 11 точек, которые закрывают этапы 1–4. Владельцу: merge docs-PR, ресурсы ВМ, место хранения секретов.
```

## 13. Следующий этап

- Этап 1 — `feat/yandex-backup`, SQL `013_backup_operations.sql`.
- Зависимости: для кода и тестов нет (synthetic restic); production-проверки — после того как владелец создаст bucket и ключи.
- Если до начала этапа 1 в main появятся новые merge — повторить pytest, build, SQL ×2 и проверить, не занят ли номер `013`.
