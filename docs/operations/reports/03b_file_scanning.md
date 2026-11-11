# Отчёт этапа 3б — проверка загрузок антивирусом, класс данных

## 1. Идентификация

- Этап: 3, часть 3б (D-045). Часть 3а — #147 (слит).
- Дата: 11.11.2026. Исполнитель: Claude (песочница, синтетические данные; настоящий clamd 1.5.4 с тестовой сигнатурой).
- main перед работой: `66351a4` (merge #147). Ветка `feat/file-scanning`.
- Production deploy и migrations: не выполнялись. На стенде антивирус не включён — включает владелец (`deploy/clamav/install.md`).

## 2. Объём

### Реализовано

- **Единый вход для загрузок** (D-049): все multipart-запросы к API, включая будущие маршруты. Zip-бомбы и вложенность — всегда; ClamAV — при `FILE_SCAN=clamav`. Заражён — 422 без сохранения, образец не хранится; недоступен — 503 (fail-closed); импорты не разбирают непроверенный файл.
- **Статус файлов**: `attachments.scan_status` (clean / legacy_unscanned / infected / error), журнал проверок `attachment_scans` (хэш, размер, движок, версия баз, сигнатура, кто и куда загружал). Заблокированные файлы не выдаются (410, в журнале скачиваний — отказ).
- **Старые файлы**: `scripts/scan_storage.py` (dry-run, только attachments или всё хранилище), заражённые — в карантин вне хранилища.
- **Предупреждения**: угроза в файле (критическое), проверка недоступна, базы старше 48 ч (ежечасно).
- **Контейнер ClamAV** (профиль `scanner`, без публичного порта, 2 ГБ, зеркала баз), пересборка при деплое — только если владелец его уже запустил. `scripts/scanner_check.py` — проверка после включения.
- **Класс данных** (D-050): отметка в базе, отказ старта при боевых данных в тестовой среде; правила production и recovery; `recovery-smoke.md` на `APP_ENV=recovery`.
- **Экран**: вкладка «Проверка файлов» в «Журнал и предупреждения».

### Не вошло

- Замер памяти ClamAV с полными базами на ВМ стенда (FILE-08): в песочнице нет доступа к зеркалам и Docker Hub. Конфигурация проверена настоящим clamd/freshclam (разбор конфигов, путь к зеркалу), замер — после включения владельцем (`docker stats`, `free -h` в `install.md`).
- Отдельный endpoint `GET /api/files/uploads/{id}` (`09`): статус показывается в журнале проверок; при синхронной проверке «ожидающих» файлов у сотрудника нет.

## 3. Изменённые файлы

| Путь | Изменение |
|---|---|
| `backend/migrations/016b_file_scanning.sql` | `attachments.scan_status/scanned_at`, `attachment_scans`, `app_markers` |
| `backend/app/core/upload_gate.py` | прослойка загрузок, статус новых attachments |
| `backend/app/services/file_scanner.py` | клиент clamd (INSTREAM, VERSION), zip-бомбы |
| `backend/app/services/scanner_status.py` | состояние антивируса, ежечасная проверка |
| `backend/app/core/environment.py`, `config.py`, `main.py` | класс данных, настройки, запуск проверок |
| `backend/app/services/security_alerts.py`, `api/security.py` | правила file_infected / scanner_unavailable / scanner_stale, `GET /api/security/scanner` |
| `backend/app/api/tasks_files_planfact.py`, `catalog.py`, `core/download_audit.py` | 410 для заблокированных файлов |
| `backend/scripts/scan_storage.py`, `scanner_check.py` | проверка хранилища, проверка после включения |
| `deploy/clamav/*`, `docker-compose.prod.yml`, `deploy/deploy-remote.sh` | контейнер ClamAV, профиль, пересборка |
| `deploy/backup/recovery-smoke.md`, `backend/.env.example` | recovery, новые переменные |
| `frontend/src/pages/SecurityAuditPage.tsx`, `styles.css` | вкладка «Проверка файлов» |
| `backend/tests/test_file_scanning.py`, `test_deploy_remote.py` | 12 + 1 тест |

## 4. Миграции

| Change set | Чистая DB | Повторный прогон | Production |
|---|---|---|---|
| `016b_file_scanning.sql` | PASS | PASS | NOT APPLIED |

ORM/SQL parity: `attachment_scans` 20 столбцов / 5 индексов / 3 ограничения, `app_markers` 4/1/1, `attachments` 16/2/3 — без расхождений. MIG-04: ошибка посреди файла → ни таблиц, ни столбцов. На стенде (`APP_ENV=dev`) создаст `init_db()`.

## 6. Проверки

| Команда | Результат |
|---|---|
| `pytest -q` (весь набор, чистая PostgreSQL 16, `CLAMD_TEST_HOST` — настоящий clamd) | **316 passed / 0 failed / 1 skipped**, 219 с |
| `tests/test_file_scanning.py` | 12 / 0 / 0: FILE-01 (чистый → clean, связь с attachment, выдача), FILE-02 (EICAR → 422, не сохранён, хэш и сигнатура, событие, critical), FILE-03 (недоступен и таймаут → 503, импорт не разобран, повтор после восстановления), FILE-04 (legacy доступен, чужому 404, infected → 410), хранилище → карантин 0600, FILE-07 (zip-бомба 30 МБ в 30 КБ, вложенность), без входа не проверяется, новый маршрут проверяется без доработки, состояние и ежечасные предупреждения, настоящий clamd, опасные сочетания настроек, отметка базы |
| Конфиги `deploy/clamav` настоящими clamd/freshclam 1.5.4 | clamd стартует с лимитами; freshclam идёт на `packages.microsoft.com/clamav` (из песочницы закрыто) |
| `scripts/scanner_check.py` с настоящим clamd | clean / infected; «не готов» из-за отсутствия официальных баз — ожидаемо |
| `tsc --noEmit && vite build`, shellcheck | PASS |
| Браузер `qa_file_scanning_ui.py` (настоящий clamd): отказ EICAR, critical в списке, вкладка «Проверка файлов»; 1440/768/390 | без ошибок, без горизонтальной прокрутки |

## 10. Риски

| ID | Severity | Описание |
|---|---|---|
| R-16 | low | Память: замер на стенде 11.11.2026 после включения — clamd 965 МиБ (предел 2 ГиБ), на ВМ доступно 2,0 ГиБ без swap. Двойная загрузка баз выключена. Следить при росте нагрузки; при нехватке — увеличить ВМ |
| R-17 | low | Зеркала баз сторонние; подделать базы они не могут (подпись), но могут перестать обновляться — тогда «базы устарели» через 48 ч |
| R-18 | low | Синхронная проверка 300-МБ видео приёмки может занять до минуты; таймаут 120 с. Если начнутся отказы по таймауту — асинхронная проверка отдельным этапом |

## 12. После слияния (стенд, 11.11.2026)

- Владелец собрал и запустил контейнер, базы скачались с `packages.microsoft.com/clamav` (main 63, daily 28180, bytecode 339).
- `scanner_check`: обычный файл — clean, EICAR — `Eicar-Test-Signature`, «антивирус готов»; `FILE_SCAN=clamav` включён.
- Найдено: проверка здоровья вызывала `clamdscan`, которого нет в пакете `clamav-daemon` → контейнер оставался в `health: starting`. Исправлено в #149 (PING по TCP).

## 11. Действия владельца

- [ ] Review и merge.
- [ ] Включить антивирус по `deploy/clamav/install.md`, прислать вывод `scripts.scanner_check`, `docker stats`, `free -h`.
- [ ] По желанию — проверить старые файлы `scripts.scan_storage`.

Итоговый статус: `PR_OPEN`.
