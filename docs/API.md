# REST API

Базовый префикс `/api`. Аутентификация — cookie сессии (OIDC) или заголовок `X-Dev-User: <email>` в dev-режиме.
Все ответы проходят серверную фильтрацию полей по ролям (раздел 2 ТЗ): менеджер не получает себестоимость, маржу,
расчёты логиста и сервиса; логист и сервис — цену и финансовый блок. Интерактивная документация: `/docs` (Swagger UI).

## Каталог, справочники и администрирование (новое)

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| GET | `/catalog/tree`, `/catalog/executions/{id}`, `/catalog/pending` | любая; pending — КД/ГД | Каталог производитель → тип → модель → исполнение, история цен по правам |
| POST | `/catalog/executions/{id}/decision`; PATCH `/catalog/executions/{id}`, `/catalog/models/{id}` | КД, ГД | Подтверждение занесения, наименование под брендом компании |
| GET/POST | `/refs/machine-types`, `POST …/{id}/approve` | любая / КД, ГД | Типы машин (заводятся по ходу работы) |
| GET/PUT | `/refs/tech-param-templates?machine_type_id=` | любая / КД, ГД | Шаблоны теххарактеристик и обязательность |
| GET/POST/DELETE | `/refs/glossary`, `POST …/{id}/confirm` | по праву `glossary` | Глоссарий RU↔ZH |
| POST | `/deals/{id}/payment-terms` | КД, ГД | Условия оплаты клиента и завода, БГ на аванс, точка отсчёта срока поставки (задачи 1, 3) |
| GET/PUT/POST/DELETE | `/admin/access`, `…/{role}/{group}`, `…/overrides`, `…/preview` | администратор, ГД | Уровни доступа (задача 11) |

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
| POST | `/deals` | менеджер, ГД | Создать сделку (номер авто `D-ГГГГ-NNNN`) |
| GET | `/deals/{id}` | по видимости | Карточка с позициями, вариантами, партиями и списком проверок перед отправкой |
| PATCH | `/deals/{id}` | менеджер сделки; КД/ГД — `client_payments`, `bank_guarantee` | Шапка, условия эксплуатации, флаг сервиса, пожелания клиента по оплате (`client_payment_wishes`), отметка «Альтернативные предложения» (`has_alternatives`) |
| POST | `/deals/{id}/duplicate` | менеджер, КД, ГД | Дублировать заявку (задача 9): `offer_id`, `copy_calcs` |
| GET | `/deals/{id}/related` | по видимости | Связанные заявки по одному запросу клиента |
| GET/PUT | `/deals/{id}/positions/{pid}/tech-params` | менеджер (КД, ГД) | Таблица теххарактеристик (задача 4); `…/verify/{row}` — подтвердить машинный перевод |
| POST | `…/tech-params/import/preview`, `…/import/apply` (multipart: `mapping`, `target`, `selected_rows`, `offer_id`) | менеджер | Импорт файла клиента / ответа завода с предпросмотром и сопоставлением |
| POST | `…/tech-params/request` | менеджер | Двуязычный запрос одному или нескольким заводам (WeCom + e-mail, вложения клиента); `GET …/request/file` — скачать файл |
| GET/POST | `/deals/{id}/offers`, `GET …/compare`, `POST …/submit-compare`, `POST …/select` | менеджер; отбор — КД/ГД | Ответы заводов, сравнение «параметр × завод», отбор для КП (задача 9) |
| POST/GET/PATCH/DELETE | `/deals/{id}/positions/{pid}/images[/{iid}]` | менеджер, КД, ГД; `in_kp`/`kp_order` — КД/ГД | Изображения и чертежи (задача 10) |
| PATCH | `/deals/{id}/kp-settings` | КД, ГД | Бренд подачи КП и способ вложения изображений |
| POST/PATCH/DELETE | `/deals/{id}/positions/{pid}/local-options[/{oid}]` | менеджер, сервис, КД, ГД, закупки | Опции, реализуемые в России (задача 6) |
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
| GET | `/deals/{id}/audit` | КД, ФД, ГД, админ | Журнал действий по сделке |

## Логистика и таможня

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| POST | `/deals/{id}/logistics/batches` | логист | Создать партию: маршрут, транспортные единицы, сроки, общие статьи, распределение (`cost/weight/volume`), позиции; сверка с историей — 422, если нужен комментарий |
| PATCH | `/deals/{id}/logistics/batches/{bid}` | логист | Изменить партию и состав |
| PUT | `/deals/{id}/logistics/positions/{pid}` | логист | Статьи позиции, код ТН ВЭД (ставки из справочника; ручная ставка — с комментарием), утильсбор, сертификация |
| GET | `/deals/{id}/logistics/history/{item}?route=&machine_type=` | логист | Последние 5 значений и среднее по статье |
| POST | `/deals/{id}/logistics/complete` | логист | Проверки (ТН ВЭД, партии) → история логистики → «Расчёт сформирован» или ожидание сервиса |

## Сервис до наработки

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| GET | `/deals/{id}/service/positions/{pid}/defaults` | сервис | Регламенты из каталога, интенсивность, лист ЗИП, ставки, нормативы региона |
| POST | `/deals/{id}/service/positions/{pid}/calc` | сервис | Расчёт: выполнения = ЦЕЛОЕ(наработка/периодичность), работы, выезды по месяцам, ЗИП, резервы; сохранение регламентов в каталог |
| POST | `/deals/{id}/service/complete` | сервис | Завершить блок |

## Экран КД, визация, КП

| Метод | Путь | Роль | Описание |
| --- | --- | --- | --- |
| GET | `/deals/{id}/calc` | КД, ФД, ГД | 14 статей × 3 сценария по вариантам, денежный поток, курсы по платежам, чувствительность, комментарии логиста/менеджера, история цен завода и КП клиенту, предупреждение о цене ниже предыдущего КП |
| POST | `/deals/{id}/price` | КД, ГД | Цена `mode=amount|markup|margin`; флаг сервиса в КП; условия оплаты варианта; переопределение накладных (комментарий → виза ГД); после визы — сброс виз |
| POST | `/deals/{id}/compare-payments` | КД, ФД, ГД | Сравнение двух наборов условий оплаты одного варианта |
| POST | `/deals/{id}/scenario-override` | КД, ГД | ΔK/Δt сделки с комментарием |
| POST | `/deals/{id}/submit` | КД, ГД | Коммерческая виза → ФД/ГД по условиям или утверждение (версия, Excel, КП, черновик письма) |
| POST | `/deals/{id}/visa` | ФД, ГД | Виза `approved` + комментарий; `cd_insists` при отказе ФД → ГД |
| POST | `/deals/{id}/rework` | КД, ГД | Возврат на доработку: блок + причина → новая версия, задача автору блока |

## Справочники и клиенты

`GET/POST /refs/clients`, `GET /refs/clients/{id}` (воронка, средняя маржа — только руководству, последние КП),
`GET/POST /refs/factories`, `GET /refs/factories/{id}/prices?model=`, `GET /refs/equipment?q=`, `GET /refs/hs?q=` (поиск по коду и описанию),
`GET/POST /refs/payment-schemes`.

## Администрирование

`GET /admin/settings`, `PUT /admin/settings/{key}` (ФД — пороги, ΔK/Δt, стоимость денег, веса прогноза; админ/ГД — остальное; история `GET …/history`),
`GET/POST/PATCH /admin/users`, `POST /admin/hs/upload` (CSV, версии ставок с датами), `POST /admin/fx/load`, `POST /admin/fx/history`, `POST /admin/fx/manual`, `GET /admin/fx`,
`POST /admin/templates/{kind}`, `GET /admin/audit`, `GET /admin/integrations/log`.

## Задачи, файлы, план/факт, отчёты

`GET /tasks/my`, `GET /board` (руководство), `POST /tasks/check-overdue` (планировщик),
`GET /files/{path}` (локальное хранилище, роль-проверка папки 04),
`POST /deals/{id}/fact` (роли по статьям: логист 2–10, сервис 12, менеджер платежи, ФД финансы), `GET /deals/{id}/plan-fact` (отчёт по 14 статьям и итогам), `POST /deals/{id}/plan-fact/close` (ФД),
`GET /reports/estimate-accuracy` (ФД, ГД).

## На будущее (очередь 3): интеграция с 1С

* `GET /api/export/deals?status=approved&since=` — утверждённые сделки с версией расчёта и кодами статей.
* `GET /api/export/clients`, `/api/export/factories`.
* `POST /api/import/fact` — факт платежей и расходов (формат `plan_fact_entries`).
* Вебхуки событий: `deal.approved`, `deal.kp_sent`, `deal.won`.
