# Карта интеграции с текущей CRM

Baseline аудита: `e652a88eb96d216bf464a4936e27d99572ef7996` (PR #131). Перед этапом сверить пути с актуальным `main`. Финансовый модуль уже существует: его сервисы переиспользуются, не создаются заново.

## 1. Этап 0 — обязательная разведка

1. Получить актуальный `origin/main`, commit, список PR после #131.
2. Запустить исходные backend tests на чистой PostgreSQL 16 и frontend build на synthetic/dev config.
3. Зафиксировать исходные ошибки отдельно; не приписывать их новому коду и не использовать как повод скрыть регрессии.
4. Инвентаризировать:
   - все `UploadFile`, `FileResponse`, `StreamingResponse`, `generate_presigned_url`, `.link(`;
   - все export/download/import endpoints;
   - login/OIDC/logout/password flows и все использования `get_current_user`;
   - все записи `users.is_active=false`;
   - все внешние notifications и `NotificationLog`;
   - scheduled jobs и deploy scripts.
5. Сохранить результат в отчёте этапа 0: путь, метод, текущая авторизация, требуемый scan/audit/offboard hook.

Не использовать результаты GitHub code search как доказательство отсутствия кода; проверять локальный exact tree через `find`/`grep`/`git grep`.

## 2. Backup — файлы и изменения

| Путь | Изменение |
|---|---|
| `docker-compose.yml` | на переходном этапе оставить старый `backup`; после restore gate удалить его отдельным PR. Добавить только необходимый read-only status mount, не публиковать новые порты |
| `docker-compose.prod.yml` | production env/mounts status без секретов; не хранить Yandex keys в файле |
| `deploy/backup/*` | новые scripts/service/timer/example/install/recovery docs по `03` |
| `deploy/deploy-remote.sh` | pre-deploy backup hook после gate; fail before update при обязательной ошибке; убрать безусловный production seed; migrations остаются ручными |
| `backend/app/models/__init__.py` | экспорт `BackupRun` либо отдельный operations models module по текущему стилю |
| `backend/migrations/010_backup_operations.sql` | аддитивная таблица/индексы |
| `backend/app/services/backup_status.py` | schema validation, host status projection, sanitized error, stale detection |
| `backend/app/api/operations.py` | read-only status/admin history; optional trigger только через безопасный fixed action |
| `backend/app/main.py` | router registration; periodic stale check без запуска shell backup из web process |
| `backend/app/api/notifications.py` | titles failed/stale backup |
| `frontend/src/pages/SettingsHub.tsx` | backup status только при `backup_status` |
| `docs/DEPLOY_YANDEX_CLOUD.md` | актуализировать: стенд Timeweb, постоянные копии Yandex; не утверждать, что CRM перенесена в Yandex |

Старый service `backup` нельзя удалить в первом PR до выполнения gate.

## 3. Security core — backend

| Путь | Изменение |
|---|---|
| `backend/app/core/auth.py` | `sid`, AuthSession validation, idle/absolute expiry, MFA claims/step-up, session revoke checks; сохранить `pv` |
| `backend/app/api/auth.py` | login MFA challenge/verify, TOTP setup/confirm/recovery, logout revoke, password policy/history/must-change |
| `backend/app/models/__init__.py` | новые security models и additive User/Task fields |
| `backend/app/services/auth_session.py` | issue/list/revoke/cleanup, last_seen throttle |
| `backend/app/services/mfa.py` | encrypted TOTP, recovery hashes, challenges, rate limit |
| `backend/app/services/offboarding.py` | единая транзакция отключения |
| `backend/app/services/access_review.py` | campaigns/items/snapshots/decisions |
| `backend/app/api/security.py` | own sessions/MFA admin/offboard/review endpoints |
| `backend/app/api/admin.py` | любые `is_active` transitions вызывают offboarding service; запрет обхода |
| `backend/app/api/team.py` | то же для team update/deactivate; отображение needs_reassignment |
| `backend/app/core/roles.py` | новые permission groups/defaults/PROTECTED, не ослаблять текущие права |
| `backend/app/services/access_service.py` | новые groups через существующие rules/overrides/cache; invalidate при security changes |
| `backend/migrations/011_security_core.sql` | аддитивная схема |
| `backend/requirements.txt` | pinned `pyotp`, `cryptography`, QR dependency только если реально используется; проверить совместимость |

OIDC flow в `core/auth.py`/`api/auth.py` сохранить state/nonce; MFA policy применяется ко всем способам входа по документированным claims.

## 4. Security audit & files — backend

| Путь | Изменение |
|---|---|
| `backend/app/core/files.py` | общий secure upload/scan gate, сохранить текущие safe headers/limits |
| `backend/app/adapters/storage.py` | внутреннее presigning отделено от DTO; <=300 sec, private bucket, no persistent public links |
| `backend/app/api/tasks_files_planfact.py` | `/api/files/{path:path}` и связанные file/export routes: centralized authorization, scan status, security events |
| остальные `backend/app/api/*.py` | закрыть все найденные upload/download/export bypass по инвентаризации |
| `backend/app/services/secure_files.py` | quarantine/promote/orphan cleanup/download audit |
| `backend/app/services/file_scanner.py` | clamd client, timeouts/limits/retry/error states |
| `backend/app/services/security.py` | events, thresholds, alerts, sanitization |
| `backend/app/api/security.py` | security alerts/events read/ack/resolve по правам |
| `backend/migrations/012_security_audit_files.sql` | events/alerts/scans/additive attachments |
| `docker-compose.yml` / prod override | private clamd service без публичного порта, resource limits; optional isolated worker profile |
| `backend/app/main.py` | startup safety checks, retention/cleanup jobs с locks; не делать неограниченные loops |
| `deploy/deploy-remote.sh` | явный APP_ENV/DATA_CLASSIFICATION safety, no automatic prod seed |
| tests | infected EICAR-like test fixture только как стандартная тестовая сигнатура по политике окружения; никаких реальных вредоносных samples |

Не заменять существующую защиту `file_response` на MIME sniffing после антивируса: оба слоя обязательны.

## 5. Support — backend

| Путь | Изменение |
|---|---|
| `backend/app/models/__init__.py` | SupportTicket/SupportMessage/SupportAttachment/sequence |
| `backend/app/services/support.py` | ownership, state machine, assign, number generation, DTO |
| `backend/app/services/support_notifications.py` | app-only NotificationLog, dedup; не вызывать общий external notify |
| `backend/app/api/support.py` | reporter/admin routers, secure attachments |
| `backend/app/main.py` | register support router |
| `backend/app/api/notifications.py` | support event titles/routes |
| `backend/app/services/offboarding.py` | unassign tickets + system event |
| `backend/migrations/013_support.sql` | tables/indexes |
| `backend/app/services/deal_service.py` | не менять общий `notify()` ради support; использовать app-only helper |

Support не требует `deal_id`; существующий Attachment допускает `deal_id=null`, entity `support_ticket`. Доступ к такому файлу не должен падать из-за обязательной `can_see_deal`.

## 6. Briefing — backend

| Путь | Изменение |
|---|---|
| `backend/app/api/workplace.py` | аддитивный `briefing` в `BLOCKS`, `ROLE_BLOCKS`; вызов builder из базового filtered context; не ломать money/legal/focus |
| `backend/app/services/briefing.py` | candidates, rank, dedup, states, DTO |
| `backend/app/api/briefing.py` | pin/snooze/hide/reset/refresh |
| `backend/app/services/finance/obligations.py` | только чтение/переиспользование; не переписывать формулы |
| `backend/app/services/finance/overview.py`, `debt.py`, `cashview.py` | источники фактов по существующей visibility |
| `backend/app/services/finance/access.py` | не обходить `Visibility` |
| `backend/app/models/__init__.py` | BriefingItemState |
| `backend/migrations/015_briefing.sql` | states/indexes |

Порядок файла `015` после `014` не означает, что этап AI обязан идти раньше: при внедрении briefing до AI можно применить `015` после `013`, а `014` добавить позже. Не переименовывать уже применённые change set’ы. Если владелец требует строго последовательный порядок, зарезервировать `014` и не применять `015` до AI; зафиксировать выбор до migrations.

## 7. AI — backend

| Путь | Изменение |
|---|---|
| `backend/app/adapters/llm.py` | переиспользовать `make_llm`; при необходимости только аддитивный timeout/error contract |
| `backend/app/services/assistant.py` | modes, quota, provider policy, orchestration, validation, fallback |
| `backend/app/services/assistant_tools.py` | allowlisted read-only tools с CurrentUser |
| `backend/app/services/assistant_help.py` | curated help index/search |
| `backend/app/api/assistant.py` | conversations/messages/status/feedback/admin metrics |
| `backend/app/models/__init__.py` | AI models |
| `backend/migrations/014_assistant.sql` | tables/indexes |
| `backend/app/api/workplace.py` | не создавать отдельную финансовую логику; workplace tool использует базовый filtered builder |
| `backend/app/services/finance/*` | только существующие query/build services |
| `docs/operations/help/*` | актуальные инструкции по CRM, версии/ссылки; без production-данных |

AI PR идёт после briefing по рекомендуемому плану, поэтому файл `014` создаётся в ветке AI, а `015` уже может существовать в main. Нумерация — уникальный идентификатор, не требование неестественного порядка разработки; production apply порядок фиксируется в журнале.

## 8. Frontend — общие изменения

| Путь | Изменение |
|---|---|
| `frontend/src/App.tsx` | routes `/support`, `/support/:id`, `/support/admin`, security/admin pages по правам; header Support и Assistant button; не добавлять второй layout |
| `frontend/src/api.ts` либо текущий API client | typed methods, credentials cookie, normalized errors, correlation ID |
| `frontend/src/types.ts` | DTO новых модулей |
| `frontend/src/index.css` / текущие CSS | аддитивные классы в существующих tokens; responsive/accessibility |
| `frontend/src/components/NotificationBell.tsx` | новые app event labels/deep links, без external mail |
| `frontend/src/components/InfoBar.tsx`, `FloatingScrollbar.tsx` | переиспользовать, не дублировать |

Если фактическая структура frontend отличается, найти существующие equivalents и записать изменение в журнал; не создавать параллельный `components/ui` только ради нового пакета.

## 9. Frontend — страницы

Новые:

```text
frontend/src/pages/SupportPage.tsx
frontend/src/pages/SupportTicketPage.tsx
frontend/src/pages/SupportAdminPage.tsx
frontend/src/pages/SecuritySettingsPage.tsx       # личные MFA/sessions
frontend/src/pages/SecurityAdminPage.tsx          # users/alerts/review по правам
frontend/src/components/AssistantDrawer.tsx
frontend/src/components/BriefingBlock.tsx
```

Изменяемые:

- `WorkplacePage.tsx`: `Data.briefing`, block label, `estimate`, renderer, центральная `WIDE`, refresh после mutations, default layout migration;
- `SettingsHub.tsx`: личная «Безопасность» всем; админские security/backup секции по отдельным permissions;
- employee/team cards: «Отключить доступ» с подтверждением и step-up, не обычная незаметная active checkbox;
- existing file/download components: получать protected link по ID, показывать pending/infected/error состояния;
- existing deal/finance pages: после действий invalidate briefing при необходимости.

## 10. Матрица миграций и PR

| PR | SQL | Обязательные проверки |
|---|---|---|
| backup | `010` | bash -n/shellcheck, timer calendar, synthetic S3/restic integration, status failure, no old backup removal |
| security core | `011` | auth regressions, MFA, revoke, offboard transaction, access review, double migration |
| security files | `012` | scan states, denied download, export audit, S3 expiry, resource limits |
| support | `013` | role/IDOR, numbering concurrency, app-only notification, attachments |
| briefing | `015` | visibility, finance parity, state/signature, layouts, performance |
| AI | `014` | tool auth, provider minimization, citations, fallback/quota |
| final QA | только исправления/новый номер при необходимости | all tests, browsers, restore gate, operational docs |

Если актуальный main уже содержит более новые migrations, продолжить фактическую максимальную нумерацию и записать mapping, не перезаписывать чужой `010`.

## 11. Что явно не трогать

- финансовые recognition/debt/cashview формулы и production-значения;
- mirror parts PR #131 и предыдущие бизнес-правки, кроме необходимых авторизационных hooks;
- дизайн login page;
- существующие Docker volumes `pgdata`, `storage`, старый `backups` до gate;
- production secrets и панели Timeweb/Yandex;
- mail/SMTP/WeCom channel preferences для support;
- сохранённые раскладки рабочего места, кроме аддитивного появления briefing.
