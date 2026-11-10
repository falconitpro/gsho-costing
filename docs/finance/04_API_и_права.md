# API, серверные права и безопасность

## 1. Общие правила API

- Префикс нового контура: `/api/finance`.
- Роутер регистрируется в существующем `backend/app/main.py`.
- Аутентификация — только текущий `get_current_user`/cookie-session; отдельный токен или auth-provider не создавать.
- Авторизация выполняется в каждом endpoint и внутри сервисных запросов к связанным объектам.
- Новые денежные значения в JSON сериализуются как decimal strings, например `"125000.50"`, чтобы не терять точность. Старые compatibility endpoints могут сохранять числа прежнего формата.
- Даты — ISO `YYYY-MM-DD`, моменты — ISO 8601 с timezone.
- Валюта — ISO 4217 uppercase.
- Все списки: пагинация `limit/offset` или cursor, по умолчанию 100, максимум 500; в ответе `total`, `items`, `limit`, `offset`.
- Все mutation поддерживают `Idempotency-Key`; для bank import, post, allocate, reverse, FX он обязателен. Legacy адаптеры формируют серверный ключ из стабильного источника.
- Ошибки: 400 malformed request, 403 нет права на действие/раздел, 404 объект не найден или скрыт по object-level visibility, 409 конфликт состояния/дубль, 422 ошибка валидации с понятными полями.
- Не возвращать stack trace, SQL, secrets, полные банковские реквизиты или чужие финансовые строки в ошибках.

## 2. Два уровня доступа

### Доступ к функции

Определяется `CurrentUser.can(...)`/эффективной матрицей прав с учётом БД overrides и заместительства по существующим правилам.

### Доступ к объекту

После проверки функции сервис применяет:

- `can_see_deal` для сделки;
- единую legal visibility для договора, вынесенную из `_can_see` в переиспользуемый сервис, если потребуется;
- `parts_deals.can_see` или актуальную видимость запчастей;
- владение/назначение для `FinanceQuestion`;
- связь счёта с разрешённой компанией и право на финансовый контур.

Нельзя сначала загрузить произвольный ID и вернуть его содержимое, а затем проверить только фильтр UI. Для скрытого объекта предпочтителен 404, чтобы не раскрывать его существование.

## 3. Стартовая матрица

| Действие | Требуемое действующее право | Поведение по умолчанию |
|---|---|---|
| Открыть раздел, overview, календарь, долг, ликвидность | `finance_analytics >= READ` | КД/ФД/ЗГД |
| Экономика, себестоимость, маржа | `finance_analytics >= READ` **и** `cost >= READ` | КД/ФД/ЗГД |
| Закупочные условия/история цен, если выводится | Существующие `payment_terms`, `factory_price_history`, `cost` по полю | Не расширять через Finance |
| Создать/архивировать финансовый счёт | `execution_payments >= EDIT` | ФД/ЗГД |
| Подтвердить остаток | `execution_payments >= EDIT` | ФД/ЗГД |
| Ввести/провести/сторнировать банковскую операцию | `execution_payments >= EDIT` | ФД/ЗГД |
| Разнести/подтвердить разнесение | `execution_payments >= EDIT` | ФД/ЗГД |
| Импортировать выписку | `execution_payments >= EDIT` | ФД/ЗГД |
| Создать конвертацию | `execution_payments >= EDIT` | ФД/ЗГД |
| Ввести событие признания | Существующие права ввода план/факта по сделке и статье; ФД/ЗГД — полный финансовый контроль | Не давать глобальный ввод логисту/менеджеру |
| Закрыть план/факт | Существующее право ФД | Без изменений |
| Создать вопрос | `finance_analytics >= READ` и видимость объекта | КД/ФД/ЗГД |
| Ответить на вопрос | Назначенный пользователь/действующий заместитель; без `finance_analytics` — только scoped DTO | По назначению |
| Проверить ответ | `execution_payments >= EDIT` | ФД/ЗГД |
| Экспорт | То же право, что и экспортируемое представление | Без обхода |
| Запустить production rebuild/migration | Не публичный API; отдельная управляемая команда и владелец rollout | Не запускать автоматически |

Важно:

- КД с правом чтения не получает право редактировать банковский факт, если `execution_payments` ему не выдано.
- Административная роль сама по себе не означает доступ к финансовой аналитике.
- Изменение стартовой матрицы происходит только через существующий механизм настроек, не захардкоженным исключением в новом роутере.
- Frontend использует `me.perms`, но серверная проверка обязательна независимо от скрытых кнопок.

## 4. Защита чувствительных полей

Использовать два защитных слоя:

1. **Allowlist DTO** — endpoint формирует только разрешённые поля, не `model.__dict__` и не универсальный `serialize()` по всей записи.
2. **`filter_payload`** — дополнительный фильтр существующей системы для вложенных структур.

Дополнить denied fields/правила для новых имён, включая как минимум:

- `cost_total`, `cost_total_rub`, `items_rub`, `margin_pct`, `profit_rub`, `markup_pct`;
- `factory_price`, `factory_terms`, `purchase_conditions`, `finance_breakdown`;
- `cashflow`, `cash_gap`, `liquidity`, `balance`, `available_balance`, `projected_balance`;
- `calc_result`, `basis_snapshot` с чувствительными полями;
- `full_finance_export`.

Недостаточно добавить имена в список: нужны тесты на вложенные объекты, массивы, explanation trace, export и compatibility DTO.

### Особое правило вопросов

`context_snapshot` создаётся сервером и минимизируется. Ответственный без финансовых прав получает:

- текст вопроса;
- нечувствительное название объекта/сделки, если ему доступно;
- срок и ответственного;
- только конкретное поле, которое нужно уточнить и которое он вправе видеть в исходном модуле;
- форму ответа и файл.

Он не получает полную сумму сделки, маржу, себестоимость, чужие платежи, банковский остаток, закупочные условия и список других вопросов. При сомнении сумма исключается из контекста.

## 5. Чтение финансовых представлений

### `GET /api/finance/overview`

Query: `as_of`, `date_from`, `date_to`, `legal_entity_id`, `account_id`, `currency`, `deal_id`, `counterparty_id`, `responsible_id`, `status`.

Возвращает:

- `kpis` по валютам и RUB-эквиваленту;
- `upcoming`, `overdue`, `cash_gaps`, `unallocated`, `stale_accounts`, `open_questions`;
- `filters`, `fx`, `balance_status`, `trace_ref`.

Все KPI получаются из одного сервиса, который также использует рабочее место.

### `GET /api/finance/obligations`

Общий реестр календаря и дебиторки/кредиторки.

Query: общие фильтры + `tab=calendar|receivable|payable`, `direction`, `source_type`, `overdue`, `no_date`, `reconciliation`, `contract_id`, `batch_id`, `purchase_order_id`, `search`.

Каждая строка содержит:

```json
{
  "id": "…",
  "direction": "in",
  "nature": "contractual",
  "title": "Аванс по договору …",
  "source": {"type": "contract", "id": "…", "line_key": "…", "url": "…"},
  "deal": {"id": "…", "number": "…"},
  "legal_entity_id": "…",
  "counterparty": {"id": "…", "name": "…"},
  "amount": "1000000.00",
  "currency": "CNY",
  "confirmed_paid": "250000.00",
  "provisional_paid": "0.00",
  "remaining": "750000.00",
  "legal_due_on": "2026-12-01",
  "forecast_on": "2026-12-03",
  "event": {"code": "contract", "on": "2026-11-20", "is_fact": true},
  "status": "partially_paid",
  "days_overdue": 0,
  "sync_status": "ok",
  "flags": ["needs_reconciliation"]
}
```

Не добавлять поля, раскрывающие экономику сверх права пользователя.

### `GET /api/finance/obligations/{id}`

Возвращает карточку, все активные разнесения, связанные операции, историю синхронизации, этап и разрешённые ссылки. Юридические условия — read-only.

### `GET /api/finance/obligations/{id}/explanation`

Формула остатка, включённые/исключённые разнесения, дата статуса, source revision, курсы и причины флагов.

### `GET /api/finance/liquidity`

Query: `as_of`, `date_from`, `date_to`, `group_by=day|week`, `account_id`, `legal_entity_id`, `currency`, `scenario=forecast|base|fact`.

Возвращает:

- по каждой валюте: `opening_balance`, `balance_status`, `points`, `minimum`, `cash_gaps`;
- RUB-консолидацию с курсами;
- `undated`, `unallocated`, `needs_reconciliation` отдельно;
- состав конвертаций;
- `trace_ref`.

Неизвестный входящий остаток передаётся как `null` + `balance_status="unknown"`, не как `0`.

### `GET /api/finance/liquidity/explanation`

Принимает те же фильтры и конкретную точку/валюту. Возвращает входящий снимок, операции и обязательства каждой точки, исключения и курсы.

### `GET /api/finance/deals/{deal_id}/economy`

Требует object access + `finance_analytics` + `cost`.

Возвращает:

- `deal_version_id`, выбранный variant и сценарии;
- 18 статей;
- план/прогноз/факт выручки и расходов;
- cash отдельно от P&L;
- `cash_gap.plan_base`, `plan_pess`, `forecast`, `fact`;
- курсы, чувствительность, неустойки;
- `data_gaps`, `source_revisions`.

### `GET /api/finance/deals/{deal_id}/economy/explanation`

Трассировка конкретного показателя через query `metric`, `scenario`, `cost_item_code`. Произвольную формулу от клиента не принимать.

### `GET /api/finance/legal-exposure`

Возвращает обеспечение и неустойки из юридического сервиса. Query: сделка, договор, контрагент, вид, статус, направление, период.

Начисляемые суммы отделены от предъявленных/признанных и от включённых в cash forecast. Никаких mutation юридических решений через этот endpoint.

### `GET /api/finance/reconciliation`

Query: очередь/тип, сделка, ответственный, статус, период. Возвращает проблемы, severity, безопасный контекст, доступные действия и ссылки.

## 6. Счета и остатки

### `GET /api/finance/accounts`

Список разрешённых счетов, валюта, текущий/неизвестный остаток, дата последнего снимка, статус. Номер счёта/банк показываются только при соответствующем доступе к профилю.

### `POST /api/finance/accounts`

Body: `legal_entity_id`, `bank_profile_id`, `title`, `currency`, `note`.

Сервер, а не клиент, проверяет принадлежность профиля компании. Не принимать новые реквизиты банка в этом endpoint.

### `POST /api/finance/accounts/{id}/archive`

Архивирует с подтверждением и аудитом. При наличии будущих операций/прогноза возвращает предупреждение.

### `POST /api/finance/accounts/{id}/balance-snapshots`

Body: `as_of`, `amount`, `source`, `external_ref`, `attachment_id`, `note`, `Idempotency-Key`.

Снимок подтверждает пользователь с EDIT. Валюта наследуется от счёта, клиент её не подменяет.

### `GET /api/finance/accounts/{id}/balance-snapshots`

История остатков и подтверждений без перезаписи.

## 7. Банковские операции

### `GET /api/finance/transactions`

Фильтры: счёт, дата, направление, валюта, статус, разнесение, deal/obligation, external ref, import batch. Возвращает сумму, неразнесённый остаток, источник и flags.

### `POST /api/finance/transactions`

Создаёт ручную запись, по умолчанию `draft` или `needs_reconciliation`.

Body: `account_id`, `direction`, `amount`, `currency`, `booked_at`, `value_date`, `counterparty_name`, `purpose`, `external_ref`, `attachment_id`, `note`.

Проведение — отдельным действием. Нельзя одним непроверенным запросом обойти контроль и сразу получить posted без явного сценария.

### `POST /api/finance/transactions/{id}/post`

Проверяет EDIT, счёт, валюту, дубль external ref, сумму, блокировки бизнес-правил и идемпотентность. Возвращает posted transaction.

### `POST /api/finance/transactions/{id}/reverse`

Body: `reason`, `booked_at`, `Idempotency-Key`.

Создаёт сторно, не меняет исходную сумму и не удаляет запись. Причина обязательна.

### `POST /api/finance/transactions/{id}/allocations`

Body:

```json
{
  "items": [
    {"obligation_id": "…", "amount_tx": "100.00", "amount_obligation": "100.00", "fx_rate": null, "fx_on": null, "fx_source": null}
  ],
  "status": "provisional"
}
```

Подтверждение — `POST /api/finance/allocations/{id}/confirm`; сторнирование — `POST /api/finance/allocations/{id}/reverse` с причиной.

Сервер повторно проверяет доступ к каждому обязательству, направление, валюту, суммы и гонки. Клиент не может подтвердить allocation для непроведённой операции.

## 8. Импорт выписки

### `POST /api/finance/imports/preview`

Multipart file + `account_id` + mapping. Возвращает `import_batch_id`, распознанные строки, ошибки, дубли, итоги по валютам. Никаких posted операций.

### `POST /api/finance/imports/{id}/confirm`

Подтверждает выбранные валидные строки с `Idempotency-Key`. Повтор файла/hash и external refs не создаёт дубли. Итог: создано, пропущено, ошибки, требует сверки.

Поддерживаемые форматы ограничить реально реализованными CSV/XLSX. Не заявлять «любой банковский формат». Для неподдержанной структуры пользователь получает понятную ошибку и возможность изменить mapping.

## 9. Признание выручки/расхода

Предпочтительный deal-scoped контур:

- `GET /api/deals/{deal_id}/finance/recognition-events`;
- `POST /api/deals/{deal_id}/finance/recognition-events`;
- `POST /api/finance/recognition-events/{id}/reverse`.

Body создания: `kind`, `amount`, `currency`, `fx_*`, `recognized_on`, `cost_item_code`, `position_id`, `obligation_id`, `basis_type`, `document_type`, `document_number`, `document_date`, `attachment_id`, `comment`.

Сервер проверяет право ввода именно этой статьи/события по текущей логике план/факта. Прямого глобального POST для менеджера/логиста нет.

## 10. Конвертация

### `POST /api/finance/fx-operations`

Body: две существующие операции или две ноги с явными счетами, `sell_amount/currency`, `buy_amount/currency`, `rate`, `rate_source`, `executed_at`, `external_ref`.

Сервис атомарно проводит/связывает ноги, проверяет права на оба счёта и запрещает повторное использование ноги. Ответ содержит обе transaction ID.

### `GET /api/finance/fx-operations`

Список по счёту, дате и валюте. Конвертация не попадает в revenue/expense.

## 11. Вопросы и ответы

### `POST /api/finance/questions`

Body: `subject_type`, `subject_id`, `assignee_id`, `question`, `due_on`.

Сервер формирует `context_snapshot`; клиент не передаёт самовольный HTML/финансовый контекст. Текст выводится как plain text, чтобы исключить XSS.

### `GET /api/finance/questions`

Для `finance_analytics`: свои/все разрешённые вопросы, фильтры статуса и ответственного.

### `GET /api/finance/questions/my`

Для назначенного пользователя. Возвращает только scoped DTO своих вопросов. Не требует `finance_analytics`, но проверяет назначение/заместительство.

### `GET /api/finance/questions/{id}`

Финансовый пользователь с доступом к объекту получает полный разрешённый контекст; назначенный — только свой scoped DTO. Остальные — 404/403 без содержимого.

### `POST /api/finance/questions/{id}/answer`

Только назначенный/заместитель. Body: `answer`, `attachment_ids`. Статус → `answered`, уведомление инициатору/ФД.

### `POST /api/finance/questions/{id}/verify`

`execution_payments >= EDIT`. Body: `decision=verified|rejected`, `review_comment`, `source_update_ref`.

При verified без фактического обновления источника вопрос можно закрыть как «ответ проверен», но связанное расхождение остаётся открытым до синхронизации источника. Не показывать ложное «данные исправлены».

## 12. Экспорт

### `GET /api/finance/export`

Query: `kind=obligations|debt|liquidity|economy|reconciliation`, `format=xlsx`, те же фильтры, `deal_id` для экономики.

Сервер:

1. повторно проверяет права;
2. вызывает тот же read service;
3. применяет тот же DTO/field policy;
4. формирует файл с фильтрами, датой, курсами и листом пояснений;
5. пишет аудит экспорта.

Нельзя принимать от клиентa список скрытых полей для включения. Старый «менеджерский Excel» остаётся отдельным ограниченным механизмом; полный финансовый XLSX менеджеру недоступен.

## 13. Compatibility endpoints

### `/api/summary/money`

Сохранить права и общий response shape, но делегировать новому сервису. Семантику остатков и мультивалютности описать в отчёте. Не вычислять вторую кривую в `summary.py`.

### `/api/deals/{deal_id}/fact`

Сохранить текущие права и форму как adapter. `payment_*` не создаёт posted bank transaction; `cost_item` связывается с recognition; повторный `ref` идемпотентен.

### `/api/deals/{deal_id}/execution/stages/{stage_id}/actual`

Неплатёжные этапы — текущая логика. Платёжные — finance adapter с `needs_reconciliation`; подтверждённый банковский факт требует Finance. Для неоднозначной агрегированной строки — 409 с объяснением и ссылкой на сверку вместо произвольного распределения.

### `/api/legal/contracts/{cid}/facts`

`pay` делегируется finance adapter; `perf/pen` остаются в legal. Блокировки подписанного договора не затрагиваются, потому что факт оплаты не меняет юридические условия.

## 14. Аудит, логи и файлы

- AuditLog для create/post/reverse/allocate/import/export/question/sync/manual match;
- before/after в JSON с decimal strings;
- причина обязательна для сторнирования, ручного сопоставления, закрытия конфликта и изменения подтверждённого остатка;
- не логировать cookies, tokens, полные выписки, персональные данные сверх необходимого;
- скачивание attachment проходит текущую проверку роли;
- Content-Disposition файлов безопасен, formula injection в XLSX/CSV предотвращается для пользовательских строк, начинающихся с `=`, `+`, `-`, `@`.

## 15. Обязательные security-тесты

- прямой вызов каждого read/export endpoint ролями без права → 403;
- прямой вызов mutation КД без `execution_payments=EDIT` → 403;
- object-level чужая сделка/договор → 404/403 по принятой политике;
- менеджер не видит чувствительные поля в JSON, trace, tasks, notifications, export;
- назначенный ответственный видит только свой вопрос;
- повтор idempotency key не меняет итог;
- подделка `legal_entity_id`, `bank_profile_id`, `account_id`, `obligation_id` отклоняется;
- нельзя подтвердить allocation на draft/needs_reconciliation;
- нельзя скачать чужую выписку/первичку по прямому URL;
- старые summary/export не становятся обходом новых прав.
