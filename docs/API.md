# REST API

Базовый префикс `/api`. Аутентификация — cookie сессии (OIDC) или заголовок `X-Dev-User: <email>` в dev-режиме.
Все ответы проходят серверную фильтрацию полей по ролям (раздел 2 ТЗ): менеджер не получает себестоимость, маржу,
расчёты логиста и сервиса; логист и сервис — цену и финансовый блок. Интерактивная документация: `/docs` (Swagger UI).

## Каталог, справочники и администрирование (новое)

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| GET | `/catalog/tree`, `/catalog/executions/{id}`, `/catalog/pending` | любая; pending — КД/ЗГД | Каталог производитель → тип → модель → исполнение, история цен по правам |
| POST | `/catalog/executions/{id}/decision`; PATCH `/catalog/executions/{id}`, `/catalog/models/{id}` | КД, ЗГД | Подтверждение занесения, наименование под брендом компании |
| GET/POST | `/refs/machine-types`, `POST …/{id}/approve` | любая / КД, ЗГД | Типы машин (заводятся по ходу работы) |
| GET/PUT | `/refs/tech-param-templates?machine_type_id=` | любая / КД, ЗГД | Шаблоны технических характеристик и обязательность |
| GET/POST/DELETE | `/refs/glossary`, `POST …/{id}/confirm` | по праву `glossary` | Глоссарий RU↔CN |
| POST | `/deals/{id}/payment-terms` | КД, ЗГД | Условия оплаты клиента и завода, БГ на аванс, точка отсчёта срока поставки (задачи 1, 3) |
| GET/PUT/POST/DELETE | `/admin/access`, `…/{role}/{group}`, `…/overrides`, `…/preview` | администратор, ЗГД | Уровни доступа (задача 11) |

Группы доступа и уровни (0 — не видит, 1 — видит, 2 — редактирует) перечислены в `core/roles.FIELD_GROUPS`; `GET /auth/me` возвращает `perms` текущего пользователя.

## Вход и профиль

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| GET | `/auth/login` | — | Редирект на провайдер OIDC (Entra ID / Яндекс ID / Keycloak) |
| GET | `/auth/callback` | — | Обмен кода на токен, создание/обновление пользователя, роли из групп |
| GET | `/auth/me` | любая | Текущий пользователь и роли |
| POST | `/auth/logout` | любая | Выход |
| GET | `/auth/dev-users` | dev | Список пользователей для переключателя (только APP_ENV=dev) |

## Сделки (блок менеджера)

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| GET | `/deals?status=` | по видимости | Список сделок (менеджер — свои; логист — с «на расчёте логистики»; сервис — с флагом) |
| POST | `/deals` | менеджер, ЗГД | Создать сделку (номер авто `D-ГГГГ-NNNN`) |
| GET | `/deals/{id}` | по видимости | Карточка с позициями, вариантами, партиями и списком проверок перед отправкой |
| PATCH | `/deals/{id}` | менеджер сделки; КД/ЗГД — `client_payments`, `bank_guarantee` | Шапка, условия эксплуатации, флаг сервиса, пожелания клиента по оплате (`client_payment_wishes`), отметка «Альтернативные предложения» (`has_alternatives`) |
| POST | `/deals/{id}/duplicate` | менеджер, КД, ЗГД | Дублировать заявку (задача 9): `offer_id`, `copy_calcs` |
| GET | `/deals/{id}/related` | по видимости | Связанные заявки по одному запросу клиента |
| GET/PUT | `/deals/{id}/positions/{pid}/tech-params` | менеджер (КД, ЗГД) | Таблица технических характеристик (задача 4); `…/verify/{row}` — подтвердить машинный перевод |
| POST | `…/tech-params/import/preview`, `…/import/apply` (multipart: `mapping`, `target`, `selected_rows`, `offer_id`) | менеджер | Импорт файла клиента / ответа завода с предпросмотром и сопоставлением |
| POST | `…/tech-params/request` | менеджер | Двуязычный запрос одному или нескольким заводам (WeCom + e-mail, вложения клиента); `GET …/request/file` — скачать файл |
| GET/POST | `/deals/{id}/offers`, `GET …/compare`, `POST …/submit-compare`, `POST …/select` | менеджер; отбор — КД/ЗГД | Ответы заводов, сравнение «параметр × завод», отбор для КП (задача 9) |
| POST/GET/PATCH/DELETE | `/deals/{id}/positions/{pid}/images[/{iid}]` | менеджер, КД, ЗГД; `in_kp`/`kp_order` — КД/ЗГД | Изображения и чертежи (задача 10) |
| PATCH | `/deals/{id}/kp-settings` | КД, ЗГД | Бренд подачи КП и способ вложения изображений |
| POST/PATCH/DELETE | `/deals/{id}/positions/{pid}/local-options[/{oid}]` | менеджер, сервис, КД, ЗГД, закупки | Опции, реализуемые в России (задача 6) |
| GET/POST | `/deals/{id}/catalog/candidates`, `…/propose` | менеджер | Предложить занесение в каталог (задача 7) |
| GET | `/deals/{id}/execution/stages`; PATCH `…/contract`; POST `…/start`, `…/stages/{sid}/actual`, `…/terms-changed`, `…/close` | по ролям этапа; платежи — ФД; закрытие — КД | Стадия контракта и исполнения (задача 2) |
| POST | `/deals/{id}/positions` | менеджер | Добавить позицию вручную; сверка цены с историей (422 с `price_reference`, если нужен комментарий) |
| PATCH/DELETE | `/deals/{id}/positions/{pid}` | менеджер | Изменить/удалить позицию |
| POST | `/deals/{id}/variants`, DELETE `…/{vid}` | менеджер | Варианты комплектации/условий |
| POST | `/deals/{id}/rfq/preview` (multipart) | менеджер | Предпросмотр распознанных полей RFQ с подсветкой пустых обязательных |
| POST | `/deals/{id}/rfq/import` (multipart, `price_comment`) | менеджер | Импорт: позиция, каталог, история цен, лист ЗИП, файл в «01 Запрос и RFQ»; 422 при ошибках или отклонении цены без комментария; возвращает `catalog_diff` |
| POST | `/deals/{id}/positions/{pid}/catalog-update` | менеджер | Обновить каталог значениями RFQ по выбранным кодам |
| POST | `/deals/{id}/send-to-calc` | менеджер | Проверки → «На расчёте логистики» (+ сервис), задачи и уведомления |
| POST | `/deals/{id}/kp-sent` | менеджер | Факт отправки КП → «КП отправлено» |
| POST | `/deals/{id}/outcome` | менеджер | Исход `won / lost / cancelled` с причиной |
| GET | `/deals/{id}/feed` | по видимости | Лента: WeCom, письма, системные события |
| GET | `/deals/{id}/versions`, `…/{ver_id}` | по видимости | Версии расчёта (менеджеру — без полного Excel) |
| GET | `/deals/{id}/attachments` | по видимости | Файлы (без «04 Расчёт себестоимости» для не-руководства) |
| GET | `/deals/{id}/audit` | КД, ФД, ЗГД, админ | Журнал действий по сделке |

## Логистика и таможня

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| POST | `/deals/{id}/logistics/batches` | логист | Создать партию: маршрут, транспортные единицы, сроки, общие статьи, распределение (`cost/weight/volume`), позиции; сверка с историей — 422, если нужен комментарий |
| PATCH | `/deals/{id}/logistics/batches/{bid}` | логист | Изменить партию и состав |
| DELETE | `/deals/{id}/logistics/batches/{bid}` | логист | Удалить лишнюю партию: её позиции становятся нераспределёнными, строки платежей выводятся из итогов |
| GET | `/deals/{id}/competitors` | КД, ЗГД | Цены конкурентов по сделке (хронология, с удалёнными): без НДС / с НДС, RUB и валюта контракта по курсу ЦБ на дату сведений, отклонение нашего предложения; наши цены; валюта контракта |
| POST / PATCH / DELETE | `/deals/{id}/competitors[/{oid}]` | КД, ЗГД | Цена конкурента: `competitor` (необязательно), `on`, `equipment`, `qty`, `amount`, `currency`, `vat_included`, `vat_pct`, `variant_id`, `source`, `comment`; удаление мягкое |
| GET | `/deals/{id}/competitors/{oid}/history` | КД, ЗГД | История правок записи |
| GET | `/competitors?q=`, `/competitors/{cid}/offers` | КД, ЗГД | Справочник конкурентов; история предложений конкурента по всем сделкам с нашей ценой |
| POST / PATCH | `/deals/{id}/positions[/{pid}]` | менеджер | Позиция: `factory_id`, `model`, `unit_price` могут быть пустыми до ответа завода; обязательное при создании — по настройке `request_required_fields` (422 со списком `missing`) |
| GET / PUT | `/refs/request-header` | все / КД, ЗГД | Шапка заявки — общий для всех сделок перечень полей над таблицей характеристик |
| POST | `/refs/machine-types/{id}/approve`, `/reject` | КД, ЗГД | Утвердить новый тип (можно исправить название) / отклонить с `replace_with` — позиции переводятся на другой тип |
| GET | `/refs/tech-param-templates/file?machine_type_id=` | все | Шаблон технических характеристик в Excel (Блок, Код, Параметр, 参数 (CN), Ед., Обязательный) |
| POST | `/refs/tech-param-templates/parse` | КД, ЗГД | Разбор файла Excel в строки шаблона без сохранения: `rows`, `blocks`, `warnings` |
| PUT | `/deals/{id}/positions/{pid}/production-term` | КД, ЗГД | Срок производства: `value`, `unit` (`calendar_days`/`working_days`/`weeks`/`months`/`date`), `fixed_date`, `start_event`, обязательный `comment`; фиксирует срок (автоматические источники и менеджер не перезаписывают); после виз — визы сбрасываются |
| GET | `/deals/{id}/positions/{pid}/production-term/history` | все с доступом к сделке | Текущий срок (откуда, кем, когда) и история изменений |
| PUT | `/deals/{id}/logistics/positions/{pid}` | логист | Статьи позиции, код ТН ВЭД (ставки из справочника; ручная ставка — с комментарием), утильсбор, сертификация |
| GET | `/deals/{id}/logistics/history/{item}?route=&machine_type=` | логист | Последние 5 значений и среднее по статье |
| POST | `/deals/{id}/logistics/complete` | логист | Проверки (ТН ВЭД, партии; партия с расходами без позиций — 422; партия без стоимости доставки — 422 с `confirm: no_delivery_cost`, повтор с `{"no_delivery_cost": true}`) → история логистики → «Расчёт сформирован» или ожидание сервиса |

## Сервис до наработки

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| GET | `/deals/{id}/service/positions/{pid}/defaults` | сервис | Регламенты из каталога, интенсивность, лист ЗИП, ставки, нормативы региона |
| POST | `/deals/{id}/service/positions/{pid}/calc` | сервис | Расчёт: выполнения = ЦЕЛОЕ(наработка/периодичность), работы, выезды по месяцам, ЗИП, резервы; сохранение регламентов в каталог |
| POST | `/deals/{id}/service/complete` | сервис | Завершить блок |

## Экран КД, визация, КП

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| GET | `/deals/{id}/calc` | КД, ФД, ЗГД | 14 статей × 3 сценария по вариантам, денежный поток, курсы по платежам, чувствительность, комментарии логиста/менеджера, история цен завода и КП клиенту, предупреждение о цене ниже предыдущего КП |
| POST | `/deals/{id}/price` | КД, ЗГД | Цена `mode=amount|markup|margin`; флаг сервиса в КП; условия оплаты варианта; переопределение накладных (комментарий → виза ЗГД); после визы — сброс виз |
| POST | `/deals/{id}/compare-payments` | КД, ФД, ЗГД | Сравнение двух наборов условий оплаты одного варианта |
| POST | `/deals/{id}/scenario-override` | КД, ЗГД | ΔK/Δt сделки с комментарием |
| POST | `/deals/{id}/submit` | КД, ЗГД | Коммерческая виза → ФД/ЗГД по условиям или утверждение (версия, Excel, КП, черновик письма) |
| POST | `/deals/{id}/visa` | ФД, ЗГД | Виза `approved` + комментарий; `cd_insists` при отказе ФД → ЗГД |
| POST | `/deals/{id}/rework` | КД, ЗГД | Возврат на доработку: блок + причина → новая версия, задача автору блока |

## Справочники и клиенты

`GET/POST /refs/clients`, `GET /refs/clients/{id}` (воронка, средняя маржа — только руководству, последние КП),
`GET/POST /refs/factories`, `GET /refs/factories/{id}/prices?model=`, `GET /refs/equipment?q=`, `GET /refs/hs?q=` (поиск по коду и описанию),
`GET/POST /refs/payment-schemes`.

## Администрирование

`GET /admin/settings`, `PUT /admin/settings/{key}` (ФД — пороги, ΔK/Δt, стоимость денег, веса прогноза; админ/ЗГД — остальное; история `GET …/history`),
`GET/POST/PATCH /admin/users`, `POST /admin/hs/upload` (CSV, версии ставок с датами), `POST /admin/fx/load`, `POST /admin/fx/history`, `POST /admin/fx/manual`, `GET /admin/fx`,
`POST /admin/templates/{kind}`, `GET /admin/audit`, `GET /admin/integrations/log`.

## Задачи, файлы, план/факт, отчёты

`GET /tasks/my`, `GET /board` (руководство), `POST /tasks/check-overdue` (планировщик),
`GET /files/{path}` (локальное хранилище, роль-проверка папки 04),
`POST /deals/{id}/fact` (роли по статьям: логист 2–10, сервис 12, менеджер платежи, ФД финансы), `GET /deals/{id}/plan-fact` (отчёт по 14 статьям и итогам), `POST /deals/{id}/plan-fact/close` (ФД),
`GET /reports/estimate-accuracy` (ФД, ЗГД).

## На будущее (очередь 3): интеграция с 1С

* `GET /api/export/deals?status=approved&since=` — утверждённые сделки с версией расчёта и кодами статей.
* `GET /api/export/clients`, `/api/export/factories`.
* `POST /api/import/fact` — факт платежей и расходов (формат `plan_fact_entries`).
* Вебхуки событий: `deal.approved`, `deal.kp_sent`, `deal.won`.

## Дополнения 03.11.2026 (доработки по спецификации задач 4, 5, 7, 9, 10)

| Метод | Путь | Назначение |
| --- | --- | --- |
| POST | `/api/deals/{id}/positions/{pid}/tech-params/import/preview` | + поля формы `target`, `offer_id`: если файл того же формата уже загружался от клиента/завода — `mapping.source = "saved"` и запомненное сопоставление |
| GET | `/api/deals/{id}/positions/{pid}/rfq/file` | шаблон RFQ с требованиями заказчика из таблицы позиции (строки «в РФ» помечены) |
| POST | `/api/deals/{id}/offers/{offer_id}/images` | фото/чертежи из ответа завода к кандидату (источник «завод», логотип на проверку) |
| POST | `/api/deals/{id}/positions/{pid}/tech-params/request` | + `client_files_checked`: без него при выбранных файлах клиента — 422 |
| PATCH | `/api/deals/{id}` | + `keep_variant_id`: выключение альтернатив при ≥2 предложениях без выбора — 409 с `choose_variant` |
| GET | `/api/catalog/price-reference?factory_id&model&execution_id` | справка при вводе позиции: цены и сроки завода (менеджер/КД/ЗГД), цены клиентам (КД/ЗГД) |
| POST | `/api/catalog/executions/{id}/merge`, `/api/catalog/models/{id}/merge` | объединение дублей (ссылки и история цен переходят, дубль — в архив); КД/ЗГД |
| GET | `/api/catalog/archive` | архив каталога (восстановление — PATCH `status: approved`) |
| GET | `/api/deals`, `/api/board` | + `group`, `parent_number`/`group_number`, `related_count`/`related` — группировка связанных заявок |

Журнал интеграций: вызовы переводчика пишутся в `integration_log` (`system = translator`, провайдер, число строк, ошибка). PDF-чертёж в КП — миниатюрой первой страницы (PyMuPDF).

## Дополнения 03.11.2026 (каталог, клиенты по ИНН, заводы, настройки)

| Метод | Путь | Назначение |
| --- | --- | --- |
| POST | `/api/catalog/equipment` | добавить оборудование вручную (завод → тип → модель → исполнение, технические характеристики, цена); менеджер — на подтверждение КД/ЗГД, КД/ЗГД — сразу |
| POST | `/api/catalog/equipment/import/preview` | файл: опросник RFQ одной машины (`kind=rfq`, данные для формы) или список моделей (`kind=list`, сопоставление столбцов) |
| POST | `/api/catalog/equipment/import/apply` | добавить модели из списка (`mapping`, `default_factory_id`) |
| GET | `/api/refs/clients/lookup?inn=` | реквизиты по ИНН (ЕГРЮЛ/ЕГРИП через `COMPANY_REGISTRY_PROVIDER=dadata`, ключ `DADATA_API_KEY`); 422 — неверный ИНН, 503 — источник не подключён |
| POST | `/api/refs/clients/{id}/refresh-registry` | обновить реквизиты клиента из реестра |
| GET/POST/PUT | `/api/refs/factories`, `/api/refs/factories/{id}` | справочник заводов: реквизиты, контакты по направлениям (заявки, продажи, логистика, финансы, сервис, качество, руководство), WeChat/WeCom/почта/телефоны; банковские реквизиты — тем, кто видит цены завода |
| GET | `/api/refs/factories/contact-roles` | направления контактов |
| GET | `/api/admin/settings/meta` | названия, группы, единицы, пояснения и типы полей настроек |
| GET | `/api/deals/{id}/positions/{pid}/tech-params` | + `section` у строк (разделы опросника) и `req_status` (match / mismatch / none — соответствие значения требованию заказчика), `mismatched` |

Запрос заводу уходит контактам с направлением «Заявки и КП» (почта, WeCom); если таких нет — всем контактам завода. Импорт RFQ в позицию переносит и поля опросника, которых нет в файле завода (пустыми).

## Рабочее место логиста (D-073)

- `GET /api/deals/{id}/logistics/workspace` — выбранные поля по позициям, адреса, логистические компании, история запросов, ответы, `mail_enabled`, `can_edit`, `can_choose_fields`.
- `PUT /api/deals/{id}/logistics/addresses` — логист / ЗГД: `{pickup: {factory_id: адрес}, delivery_address, propose_to_client}` → `{proposed}`.
- `GET /api/deals/{id}/logistics/request/file?fmt=xlsx|pdf&answer_days=3` — типовой запрос на расчёт доставки.
- `POST /api/deals/{id}/logistics/request/send` — `{emails, message, answer_days, manual}`; без почты и без `manual` — 409.
- `POST /api/deals/{id}/logistics/quotes` (multipart: carrier, amount, currency, transit_days, valid_until, scope, comment, file) → 201; `DELETE .../quotes/{qid}` → 204.
- `GET/PUT /api/refs/logistics-fields` — поля для логиста (`?deal_id=` — варианты из шаблонов сделки); PUT — ЗГД.
- `GET/PUT /api/refs/logistics-carriers` — логистические компании; PUT — логист / ЗГД.
- `POST /api/refs/clients/{cid}/delivery-addresses/{aid}/approve|reject` — ЗГД.
- `GET /api/deals` — у строк «в обработке» поле `my_tasks: [{title, due, overdue, block}]`.

## Торги (D-074, только КД и ЗГД)

- `GET /api/deals/{id}/negotiation` — предложения (первая и текущая цена, условия), `decision` (маржа по сценариям, минимальная цена по порогу и безубыточная, запас), шаги, конкуренты, `analytics` (снижение, `bargain_coef`, `price_ratio`, шаги, дни, маржа было/стало).
- `POST /api/deals/{id}/negotiation/preview` — пересчёт без записи. Тело: `{prices: {variant_id: {mode: pct|amount|target, basis: net|gross, currency, value}}, terms: {variant_id: {client_payments, delivery_term}}}`.
- `POST /api/deals/{id}/negotiation/steps` — то же + `reason`, `comment`, `show_discount`, `confirm_below_threshold` → 201, шаг и редакция КП. Ниже порога без подтверждения — 422 `{confirm: "below_threshold"}`.
- `POST /api/deals/{id}/negotiation/steps/{sid}/send` — `{to, body, format, manual}`; без почты и без `manual` — 409.
- `PUT /api/deals/{id}/negotiation/steps/{sid}/answer` — `{client_answer}`.
- Цены конкурентов: новые поля `delivery_term`, `payment_terms`.

### Логистика: услуги и выбор (D-075)

- `PUT /api/deals/{id}/logistics/services` — `{services: [door_to_door, intl_freight, rf_delivery, insurance, customs, export]}`.
- `GET .../logistics/workspace` — добавлены `services`, `service_options`, `compare` (`rows`, `best`, `fastest`, `chosen`, `chosen_status`: approved | pending_gd | declined, `waiting`).
- `POST .../logistics/quotes/choose` — `{carrier, comment, need_gd}`; не самое дешёвое без причины — 422.
- `POST .../logistics/quotes/choose/decision` — ЗГД: `{approve, comment}`; отказ без причины — 422.
- `PUT /api/deals/{id}/negotiation/steps/{sid}/review` — «на рассмотрении клиента»: `{remind_on, assignee: manager|me, note}` → задача-напоминание (D-076).
- `POST /api/deals/{id}/negotiation/steps/{sid}/review/close` — `{client_answer}`; не на рассмотрении — 409.

## Файлы: просмотр и скачивание (D-077)

- `GET /api/files/{path}?view=1` — просмотр: изображения / видео inline; PDF и Word / Excel — `&info=1` → `{pages}`, `&page=N` → страница PNG. Неподдерживаемый тип — 415.
- `GET /api/files/{path}` — скачивание: группа `file_download` или одобренный запрос (24 ч), иначе 403 `{code: "download_restricted"}`.
- `POST /api/files-download-requests` — `{path, reason}` → 201 `{id, status}`; `GET /api/files-download-requests?status=pending` (ЗГД — все, остальные — свои); `POST /api/files-download-requests/{id}` — ЗГД: `{approve, comment}`.
- `PUT /api/deals/{id}/negotiation/client-rates` — КД / ЗГД: `{rates: {CNY: 11.5}, comment}`; пустой `rates` — снять. В `GET .../negotiation` — `client_rates`, `client_rates_history` (D-079).
- `PUT /api/deals/{id}/deal-currency` — КД / ЗГД до утверждения: `{currency}` (пусто — валюта предложения поставщика) → `{deal_currency, chosen, offer_currency}`; в `GET .../calc` — `deal_currency {code, chosen, offer}`. `POST .../price` — `currency` теперь валюта ввода; цена сохраняется в валюте сделки, ввод — в `price_input_currency` (D-080).
- `POST /api/deals/{id}/price` — `per_unit` (по умолчанию true): сумма за 1 шт., в цену — × количество; в `GET .../calc` — `units {variant_id: N}`. Торги: шаг `prices[*].per_unit` (по умолчанию true), в `GET .../negotiation` у предложения — `units` (D-085).
