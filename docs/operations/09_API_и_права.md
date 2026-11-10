# API и права

## 1. Общий контракт

- Все routes, кроме существующего login/OIDC callback и явно публичного health, требуют действующую AuthSession и активного пользователя.
- Право проверяется на backend. Frontend `perm()` только скрывает недоступные элементы.
- Объектная авторизация обязательна даже при наличии module permission: deal, ticket, conversation, attachment, session.
- Чужой/несуществующий закрытый объект — 404; недостаточное действие над доступным объектом — 403; conflict state — 409; validation — 400/422 по текущему стилю.
- Mutation response содержит актуальный DTO, не только `{ok:true}`, если UI должен обновить состояние.
- Ошибки sanitized, с request/correlation ID. Не возвращать env, SQL, stack trace, S3 signed query.
- List: `limit` с server maximum и `offset`/cursor по текущему стилю; filters whitelisted.
- Recent MFA step-up требуется для offboard, MFA reset, повышения security/support прав, изменения backup/security config и resolve critical alert по итоговой политике.

## 2. Новые permission groups

Добавить в существующую `DEFAULT_MATRIX`, `effective_perms`, overrides и `filter_payload`; значения — текущие `READ/EDIT/0` semantics.

| Group | ЗГД | Администратор | Остальные активные роли | Назначение |
|---|---|---|---|---|
| `support_admin` | EDIT | 0 по умолчанию | 0 | чужие обращения и очередь |
| `security_admin` | EDIT | EDIT | 0 | sessions users, MFA reset, offboard, access review/config |
| `security_audit` | READ/EDIT по текущей role semantics | READ | 0 | security events/alerts без support/AI content |
| `assistant_use` | READ | READ | READ | личный AI; override может закрыть |
| `assistant_admin` | EDIT | 0 | 0 | provider status, quotas/retention metrics |
| `backup_status` | READ | READ | 0 | backup status/history без секретов |

Уточнение: `security_audit` использует READ для просмотра; acknowledge/resolve alerts можно ограничить `security_admin=EDIT`, чтобы аудитор не менял состояние расследования. Если владельцу нужен операционный аудитор, отдельный override EDIT и тест. Не размывать границу в коде.

- `security_admin` добавить в защищённые группы ЗГД/`PROTECTED`, чтобы предотвратить потерю последнего контура управления.
- Назначение security/support/assistant admin прав выполняется через существующий `access_admin`, но повышение собственных повышенных прав требует второго reviewer по `04`.
- `assistant_use=READ` не даёт finance/deal/legal данных сверх текущих прав.
- `backup_status=READ` не разрешает restore, изменение retention или просмотр env.
- Личные sessions/MFA/password доступны любому активному пользователю без `security_admin`; после offboarding — нет.

## 3. Backup API

| Метод и путь | Право | Назначение |
|---|---|---|
| `GET /api/operations/backups/status` | `backup_status=READ` | host last-run, последний успех, stale/check state, следующий timer если передан безопасно |
| `GET /api/operations/backups/runs` | `backup_status=READ` | paginated sanitized history |
| `POST /api/operations/backups/manual` | `security_admin=EDIT` + step-up | опциональный fixed manual trigger через controlled host mechanism; без shell args |

Если controlled manual trigger не реализован надёжно, последний endpoint не добавлять; UI показывает runbook для SSH. Статус не включает bucket key, RESTIC_PASSWORD, полный env, signed URLs. Manual trigger возвращает 202 с fixed run ID либо 409 already running; не сообщает успех до фактического окончания.

## 4. Личная безопасность

| Метод и путь | Доступ | Назначение |
|---|---|---|
| `GET /api/auth/sessions` | владелец | свои активные sessions, current flag |
| `DELETE /api/auth/sessions/{sid}` | владелец записи | revoke выбранной |
| `POST /api/auth/sessions/revoke-others` | владелец + recent auth по риску | revoke всех кроме текущей |
| `POST /api/auth/logout` | текущая session | revoke sid + clear cookie |
| `POST /api/auth/mfa/setup` | владелец + password/step-up | pending secret/otpauth |
| `POST /api/auth/mfa/confirm` | владелец + pending | activate + одноразовый показ recovery codes |
| `POST /api/auth/mfa/verify` | валидный mfa_pending | полноценная session |
| `POST /api/auth/mfa/recovery` | валидный mfa_pending | одноразовый code |
| `POST /api/auth/mfa/disable` | владелец + password + TOTP | если policy разрешает |
| `POST /api/auth/mfa/recovery/regenerate` | владелец + step-up | новые codes, старые недействительны |
| `POST /api/auth/change-password` | текущий пользователь | новая политика/history/revoke |

MFA login challenge endpoints не используют обычный CRM session, но строго ограничены назначением, expiry и attempts. `GET /api/auth/me` возвращает `mfa_required`, `mfa_enabled`, `password_must_change`, без secret.

## 5. Security administration

| Метод и путь | Право | Назначение |
|---|---|---|
| `GET /api/security/users/{id}/sessions` | `security_admin=READ/EDIT` | metadata активных входов |
| `DELETE /api/security/users/{id}/sessions/{sid}` | `security_admin=EDIT` + step-up | revoke выбранной |
| `POST /api/security/users/{id}/sessions/revoke-all` | EDIT + step-up | все входы |
| `POST /api/security/users/{id}/mfa/reset` | EDIT + step-up | reset + revoke + security alert |
| `POST /api/security/users/{id}/offboard` | EDIT + step-up | единая транзакция |
| `POST /api/security/users/{id}/reactivate` | EDIT + step-up | отдельное включение после review |
| `GET /api/security/events` | `security_audit=READ` | sanitized filters/list |
| `GET /api/security/alerts` | `security_audit=READ` | alerts, без чужих contents |
| `POST /api/security/alerts/{id}/ack` | security_admin EDIT либо явный audit EDIT | ack + comment |
| `POST /api/security/alerts/{id}/resolve` | security_admin EDIT + step-up для critical | resolve + comment |
| `GET /api/security/review-campaigns` | security_admin | список |
| `POST /api/security/review-campaigns` | security_admin EDIT | создать |
| `GET /api/security/review-campaigns/{id}` | security_admin | items/progress |
| `POST /api/security/review-items/{id}/decision` | назначенный reviewer | keep/change/disable с safeguard |

Self-revoke собственной текущей сессии разрешён через личный endpoint. Admin не может отключить себя через offboard или последнего активного security admin. Reactivate не восстанавливает старые sessions/overrides автоматически.

## 6. File API

Предпочтительный новый контракт:

| Метод и путь | Доступ | Назначение |
|---|---|---|
| `POST /api/files/uploads` | активный пользователь + entity permission | создать quarantine upload |
| `GET /api/files/uploads/{id}` | владелец/entity viewer | scan status без internal path |
| `GET /api/attachments/{id}/download` | повторная object authorization + clean/legacy | protected stream либо short presign <=300 sec |

Существующий `GET /api/files/{path:path}` сохранить для обратной совместимости, но пропустить через тот же authorization/scan/audit service. Нельзя позволить новому ID endpoint стать обходом `legal_docs`, `cost_files`, `can_see_deal`, support ownership или finance visibility.

Upload request не принимает рабочий `storage_path`, scan status, clean flag, public URL или bucket. DTO содержит `scan_status`, безопасное `file_name`, `size`, `sha256` при необходимости, `download_url` только protected/short-lived и не для pending/error/infected.

## 7. Support API

| Метод и путь | Доступ | Назначение |
|---|---|---|
| `POST /api/support/tickets` | активный пользователь | создать, idempotency key |
| `GET /api/support/tickets` | владелец | только свои, filters/pagination |
| `GET /api/support/tickets/{id}` | reporter/support_admin READ | ticket + messages + permitted attachments |
| `POST /api/support/tickets/{id}/messages` | reporter своего либо support_admin EDIT | добавить сообщение, transitions по правилам |
| `POST /api/support/tickets/{id}/transition` | участник по whitelist | resolved/closed/reopen/waiting |
| `POST /api/support/tickets/{id}/assign` | support_admin EDIT | назначить активного support admin/unassign |
| `POST /api/support/tickets/{id}/priority` | support_admin EDIT | фактический приоритет |
| `GET /api/support/admin/tickets` | support_admin READ | общая очередь |
| `GET /api/support/admin/metrics` | support_admin READ | только support counters |

Transition whitelist:

- reporter: свой любой не closed → closed с подтверждением; resolved → in_progress (`reopen`); waiting_reporter обрабатывается сообщением;
- specialist EDIT: new → in_progress; in_progress ↔ waiting_reporter; in_progress/waiting_reporter → resolved; resolved → closed/in_progress;
- system: offboard unassign без смены статуса.

Request содержит ожидаемую `version`/`updated_at` либо сервер проверяет допустимый текущий state, чтобы два специалиста не перезаписали переход. 409 требует reload.

## 8. Briefing API

| Метод и путь | Доступ | Назначение |
|---|---|---|
| `GET /api/briefing` | активный пользователь | персональные 3–5 + generated_at/warnings |
| `POST /api/briefing/items/{item_key}/pin` | владелец candidate | pin с source_signature |
| `DELETE /api/briefing/items/{item_key}/pin` | владелец | unpin |
| `POST /api/briefing/items/{item_key}/snooze` | владелец candidate | until + signature |
| `POST /api/briefing/items/{item_key}/hide` | владелец candidate | hide текущую signature |
| `POST /api/briefing/items/reset` | владелец | reset own selected/all states |

`item_key` не является доказательством доступа: сервер заново строит/проверяет candidate и source. Пользователь не может передать `task:чужой_id` и узнать его существование. State mutation не меняет исходную задачу/платёж/ticket.

## 9. Assistant API

| Метод и путь | Доступ | Назначение |
|---|---|---|
| `GET /api/assistant/status` | `assistant_use` | enabled/provider off/modes/retention/limits без secrets |
| `GET /api/assistant/conversations` | владелец | свои не deleted |
| `POST /api/assistant/conversations` | владелец | создать с default mode |
| `GET /api/assistant/conversations/{id}` | владелец | messages/sources |
| `DELETE /api/assistant/conversations/{id}` | владелец | delete по retention policy |
| `POST /api/assistant/messages` | `assistant_use` + owner + quota | question, mode, conversation_id |
| `POST /api/assistant/messages/{id}/feedback` | владелец conversation | rating/comment |
| `GET /api/assistant/admin/metrics` | `assistant_admin` | агрегаты, без prompt/content |

Tool names не являются публичным generic endpoint. Клиент не передаёт произвольный `tool`, `sql`, `user_id`, `deal_id` вне схемы вопроса; structured tool invocation происходит только на backend. При необходимости deterministic direct question API может иметь typed params, но object auth остаётся обязательной.

## 10. Уведомления

Расширить существующие app events, не добавляя channels:

- backup failed/stale;
- security alert/review due;
- support created/assigned/message/waiting/resolved/reopened;
- briefing обычно не создаёт дублирующие notifications для каждого элемента.

`NotificationLog.payload` — whitelist IDs/route/title, без закрытых amounts, ticket body, AI content, secrets. Внешние mail/WeCom для новых support/security/backup событий не вызываются в первом срезе.

## 11. Server-side authority matrix

| Данные | Обязательная проверка |
|---|---|
| Deal | `can_see_deal` + effective perms + overrides + filtered fields |
| Finance | `finance_section`/`finance_analytics` + `finance.access.Visibility` + contract/payment/cost/factory checks |
| Legal | legal visibility + deal + attachment category |
| Support | reporter или support_admin; attachment через parent ticket |
| AI | owner conversation + assistant_use + tool-specific permissions |
| Security | security_admin/audit, но нет support/AI contents |
| Backup | backup_status, sanitized fields only |
| Briefing | персональный builder, каждый source независимо ограничен |

Ни один общий endpoint, export, notification, AI tool или summary не должен обходить эту матрицу.
