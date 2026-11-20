# Матрица сохранности: поэлементная опись и сверка с базовой версией

Составлена автоматически из кода (`frontend/src/**/*.tsx`) скриптом сверки. Базовая версия — `bd6d1ef` (до «матрёшки»), текущая — рабочая ветка.
Выборка по шаблонам: запросы `api.*`, заголовки `<th>`, подписи `<button>`, параметры адреса, права `can()/perm()`, заголовки `<h1–h3>`, ключи localStorage. Подписи, собранные из выражений, показаны с «…»; элементы без текста (значки) в опись не попадают.

## Сверка по всему интерфейсу

- файлов в базовой версии: 102, в текущей: 105; удалённых файлов: 0
- элементов базовой версии проверено: 2278
- найдено по тексту подписи (элемент строится из списка, а не записан отдельным тегом): 18
- не найдено в текущей версии: 0

## Финансы

Маршруты: `/finance?tab=…, /finance/questions/:id`.

| Компонент | Изменён относительно базовой версии | Новое место / способ открытия |
|---|---|---|
| `pages/FinancePage.tsx` | +2 / −0 строк | прежнее; добавлен путь |
| `pages/FinanceQuestionPage.tsx` | нет, совпадает побайтно | прежнее |
| `components/FinanceDebt.tsx` | нет, совпадает побайтно | прежнее |
| `components/FinanceEconomy.tsx` | нет, совпадает побайтно | прежнее |
| `components/FinanceExposure.tsx` | нет, совпадает побайтно | прежнее |
| `components/FinanceIntragroup.tsx` | нет, совпадает побайтно | прежнее |
| `components/FinanceReconciliation.tsx` | нет, совпадает побайтно | прежнее |
| `components/FinanceExportButton.tsx` | нет, совпадает побайтно | прежнее |
| `components/CashflowChart.tsx` | нет, совпадает побайтно | прежнее |

### `pages/FinancePage.tsx`

- **Заголовки блоков** (10): Банковские операции; Без согласованной даты; Ближайшие платежи; Конвертации; Неразнесённые операции; Ожидается, срок не согласован; Прогноз остатков; Просрочено; Счета и остатки; Финансы
- **Колонки таблиц** (40): Банк; Банковский №; Дата; Документ; Заявка; Источник; Клиент; Комиссия; Компания группы; Контрагент; Контрагент / назначение; Куплено; Курс; Курс (если валюта другая); Менеджер; Направление; Не разнесено; Ожидаемая дата; Оплачено; Остаток; Остаток на конец; Остаток строки; Остаток, ≈ RUB; Подтверждён; Позиций; Поступления; Продано; Сделка / документ; Списания; Срок; Срок по договору; Статус; Стр.; Строк; Строка; Строка графика; Сумма; Сумма из операции, …; Счёт; Что
- **Кнопки** (19): {busy ? "Загружаю…" : `Загрузить и провести ($…)`}; Добавить; Конвертация; Обновить; Операция; Открыть записи на сверке; Отменить; Отозвать; Перейти к счетам и операциям; Подробнее; Подтвердить; Подтвердить остаток; Показать все; Показать все (…); Показать строки на сверке; Разобрать; Сторнировать; Счёт; состав (…)
- **Запросы API** (17): GET /api/finance/accounts; GET /api/finance/accounts/options; GET /api/finance/fx-operations; GET /api/finance/liquidity; GET /api/finance/liquidity/explanation; GET /api/finance/meta; GET /api/finance/obligations; GET /api/finance/obligations/{…}; GET /api/finance/obligations/{…}/explanation; GET /api/finance/overview; GET /api/finance/transactions; GET /api/finance/transactions/{…}; GET /api/finance/undated; POST /api/finance/accounts; POST /api/finance/allocations/{…}/{…}; POST /api/finance/transactions/{…}/assign-account; POST /api/finance/transactions/{…}/withdraw
- **Параметры адреса** (25): account; ccy; contract_id; currency; deal_id; direction; entity; from; group; horizon; inactive; min; nointra; open; origin; q; reconciliation; source; status; tab; to; tx; tx_status; unallocated; withdrawn
- **Права** (3): cost; finance_analytics; finance_section

### `pages/FinanceQuestionPage.tsx`

- **Заголовки блоков** (1): Уточнение по «Финансам»
- **Кнопки** (1): Отправить ответ
- **Запросы API** (3): GET /api/finance/questions/{…}; POST /api/finance/questions/{…}/answer; UPLOAD /api/finance/questions/{…}/files

### `components/FinanceDebt.tsx`

- **Заголовки блоков** (1): Ожидаемые суммы без согласованной даты
- **Колонки таблиц** (9): Валюта; Временно разнесено; Ожидается; Оплачено; Остаток; Срок по договору; Статус; Сумма; … / строка
- **Запросы API** (1): GET /api/finance/debt
- **Параметры адреса** (2): direction; group

### `components/FinanceEconomy.tsx`

- **Заголовки блоков** (9): Деньги (cash); Как получена цифра{t ? `: $…` : ""}; Кассовый разрыв; Курсы и чувствительность; Не учтено (…); Неустойки; Признание выручки и расходов; Результат (P&amp;L); Учтено (…)
- **Колонки таблиц** (11): Дата; Основание; Отклонение; План; План, …; Признано; Прогноз; Статус; Статья себестоимости; Сумма; Что
- **Кнопки** (3): Закрыть; Признать; сторно
- **Запросы API** (6): GET /api/deals/{…}/fact/form; GET /api/deals/{…}/finance/recognition-events; GET /api/finance/deals/{…}/economy; GET /api/finance/deals/{…}/economy/explanation; GET /api/finance/economy/deals; POST /api/finance/recognition-events/{…}/reverse
- **Параметры адреса** (1): deal

### `components/FinanceExposure.tsx`

- **Заголовки блоков** (3): {side === "receivable" ? "Неустойки к получению" : "Неустойки к уплате; Неустойки; Обеспечение
- **Колонки таблиц** (12): Банк; Вид; Дней; Договор; Договор / сделка; За что; Контрагент; Претензия; Прогноз; Срок; Статус; Сумма
- **Запросы API** (1): GET /api/finance/legal-exposure
- **Параметры адреса** (3): deal; direction; pstatus

### `components/FinanceIntragroup.tsx`

- **Заголовки блоков** (2): По парам «плательщик → получатель»; Сальдо между компаниями группы
- **Колонки таблиц** (11): Валюта; Временно разнесено; Должник; Кредитор; Начислено; Оплачено; Остаток; Плательщик → получатель; Просрочено; Сальдо; Строк
- **Запросы API** (1): GET /api/finance/intragroup

### `components/FinanceReconciliation.tsx`

- **Заголовки блоков** (1): Уточнение: …
- **Колонки таблиц** (6): Важность; Дата; Объект; Ответственный; Сумма; Что не так
- **Кнопки** (2): Закрыть карточку; Отправить
- **Запросы API** (5): GET /api/finance/questions/assignees; GET /api/finance/questions/context; GET /api/finance/questions/{…}; GET /api/finance/reconciliation${queue ; POST /api/finance/questions
- **Параметры адреса** (2): question; queue

### `components/FinanceExportButton.tsx`

- **Кнопки** (1): {busy ? "Готовлю…" : `⇩ $…`}

### `components/CashflowChart.tsx`


## Юридический блок и контрагенты

Маршруты: `/legal?tab=…`.

| Компонент | Изменён относительно базовой версии | Новое место / способ открытия |
|---|---|---|
| `pages/LegalPage.tsx` | +26 / −2 строк | прежнее; добавлены путь и обзор `/legal` (новый вид) |
| `components/ContractsBlock.tsx` | нет, совпадает побайтно | прежнее |
| `components/ContractScans.tsx` | нет, совпадает побайтно | прежнее |
| `components/ClaimsBlock.tsx` | нет, совпадает побайтно | прежнее |
| `components/PoaBlock.tsx` | нет, совпадает побайтно | прежнее |
| `components/LegalTemplates.tsx` | нет, совпадает побайтно | прежнее |
| `components/TemplateEditor.tsx` | нет, совпадает побайтно | прежнее |
| `components/LegalRules.tsx` | нет, совпадает побайтно | прежнее |
| `components/LegalProfileBlock.tsx` | нет, совпадает побайтно | прежнее |
| `components/LegalSaleWizard.tsx` | нет, совпадает побайтно | прежнее |
| `pages/CounterpartiesPage.tsx` | +1 / −1 строк | прежнее; переключение — с историей |
| `pages/ClientsPage.tsx` | +3 / −0 строк | прежнее; добавлена прямая ссылка `&client=<id>` |
| `pages/FactoriesPage.tsx` | нет, совпадает побайтно | прежнее |
| `components/ClientRequisites.tsx` | нет, совпадает побайтно | прежнее |
| `components/NewSupplierInline.tsx` | нет, совпадает побайтно | прежнее |

### `pages/LegalPage.tsx`

- **Заголовки блоков** (2): Контрагенты; Юридический блок
- **Запросы API** (1): GET /api/legal/own
- **Параметры адреса** (1): tab
- **Сохранённые настройки (localStorage)** (1): legal_own_open

### `components/ContractsBlock.tsx`

- **Колонки таблиц** (37): База; Версия; Вид; Дата; Дата события; Дней; Дни; Доля, %; За что; Загрузил; Значение; Исполнено; Как в договоре; Компания; Контрагент; Кто должен; Кто платит; Льгота, дн.; Номер; Обязательство; Подписан; После события; Предел; Предел, знач.; Просрочка; Событие; Срок; Срок оплаты; Ставка; Статус; Сумма; Условия оплаты; Файл; Через, дн.; Что; или дата; или сумма
- **Кнопки** (20): {old ? "скрыть старые версии" : `показать старые версии ($…)`}; Аннулировать; Вернуть в проект; Взять из расчёта сделки; Входящая претензия; Договор; Зарегистрировать подписанный; Исполнен; Исправить ошибку в данных; На доработку; На согласование; Отмена; Отменить; Отменить изменения; Отметить подписанным; Передать на подпись; Претензия контрагенту; Расторгнут; Создать; снять отметку
- **Запросы API** (18): DEL /api/legal/contracts/{…}; DEL /api/legal/contracts/{…}/files/{…}; GET /api/legal/contracts; GET /api/legal/contracts/catalog; GET /api/legal/contracts/{…}; GET /api/legal/contracts/{…}/claims; GET /api/legal/contracts/{…}/packages; GET /api/refs/clients; GET /api/refs/factories; POST /api/legal/contracts; POST /api/legal/contracts/{…}/facts; POST /api/legal/contracts/{…}/pull-terms; POST /api/legal/contracts/{…}/status; POST /api/legal/contracts/{…}/{…}; POST /api/legal/packages/{…}/status; PUT /api/legal/contracts/{…}; UPLOAD /api/legal/contracts/{…}/files; UPLOAD /api/legal/packages/{…}/signed-scan
- **Параметры адреса** (2): new; open

### `components/ContractScans.tsx`

- **Заголовки блоков** (1): Сканы подписанных контрактов
- **Запросы API** (3): DEL /api/deals/{…}/documents/{…}; GET /api/deals/{…}/contract-scans; UPLOAD /api/deals/{…}/contract-scans

### `components/ClaimsBlock.tsx`

- **Колонки таблиц** (12): Вид; Дней; Договор · контрагент; Номер; Основание; Ответ до; Отправлена; Срок; Ставка; Статус; Сумма; Суть
- **Кнопки** (12): В суд; Вернуть в черновик; Закрыть; Записать; На подпись КД / ЗГД; Отмена; Отменить; Отправлена; Пересчитать на сегодня; Подписать; Создать; Строка
- **Запросы API** (5): GET /api/legal/claims; GET /api/legal/claims/{…}; POST /api/legal/claims/{…}/{…}; POST /api/legal/contracts/{…}/claims; UPLOAD /api/legal/claims/{…}/files
- **Параметры адреса** (1): open

### `components/PoaBlock.tsx`

- **Колонки таблиц** (12): Вид; Выдана; Дата; Действует до; Загрузил; Компания; Копии; Номер; Представитель; Размер; Статус; Файл
- **Кнопки** (9): Аннулировать черновик; Далее; Доверенность; Назад; Отмена; Отозвать; Оформить; Полномочие; Сохранить черновик
- **Запросы API** (12): DEL /api/legal/poa/{…}/files/{…}; GET /api/legal/poa; GET /api/legal/poa/catalog; POST /api/legal/poa; POST /api/legal/poa/{…}/cancel; POST /api/legal/poa/{…}/check; POST /api/legal/poa/{…}/issue; POST /api/legal/poa/{…}/renew; POST /api/legal/poa/{…}/revoke; POST /api/legal/poa/{…}/use-as-basis; PUT /api/legal/poa/{…}; UPLOAD /api/legal/poa/{…}/files

### `components/LegalTemplates.tsx`

- **Кнопки** (2): Проверить; в архив
- **Запросы API** (4): GET /api/legal/templates; GET /api/legal/templates/{…}/test-check; PUT /api/legal/templates/{…}; UPLOAD /api/legal/templates

### `components/TemplateEditor.tsx`

- **Колонки таблиц** (4): Ед.; Код; Обяз.; Параметр
- **Кнопки** (9): + блок; + блок в конец; + параметр; Добавить в конец; Заменить строки шаблона; Отмена; Отменить правки; Сохранить шаблон; ⬆ Загрузить из Excel
- **Запросы API** (2): GET /api/refs/tech-param-templates; POST /api/refs/machine-types
- **Параметры адреса** (1): tpl

### `components/LegalRules.tsx`

- **Заголовки блоков** (2): ИИ-анализ договоров; Правила анализа
- **Запросы API** (3): GET /api/legal/ai; POST /api/legal/ai/test; PUT /api/legal/ai

### `components/LegalProfileBlock.tsx`

- **Колонки таблиц** (7): Выдана; Дата; Действует до; Доверенность: кому; Документ; Полномочия; Скан
- **Кнопки** (6): Заполнить по ИНН; Отмена; Сохранить; Сохранить профиль; скрыть; … · КПП …
- **Запросы API** (13): DEL /api/legal/profiles/{…}; DEL /api/legal/profiles/{…}/documents/{…}; GET /api/legal/dictionaries; GET /api/legal/profiles; POST /api/legal/banks/{…}/archive; POST /api/legal/banks/{…}/verify; POST /api/legal/profiles; POST /api/legal/profiles/{…}/banks; POST /api/legal/profiles/{…}/check; POST /api/legal/profiles/{…}/fill-by-inn; PUT /api/legal/banks/{…}; PUT /api/legal/profiles/{…}; UPLOAD /api/legal/profiles/{…}/documents

### `components/LegalSaleWizard.tsx`

- **Заголовки блоков** (1): Договор заказчика: рабочая копия + протокол разногласий + техническая
- **Колонки таблиц** (14): Было; В копию; Кол-во; Критичность; Оборудование; Опции; Пункт; Редакция БЕРГ-ХАУС; Редакция заказчика; Риск (служебно); Срок / гарантия; Станет; Уверенность; Что
- **Кнопки** (1): Загрузить
- **Запросы API** (4): GET /api/deals/{…}/legal/sale/options; POST /api/deals/{…}/legal/sale/build; POST /api/deals/{…}/legal/sale/preview; UPLOAD /api/deals/{…}/legal/sale/upload

### `pages/CounterpartiesPage.tsx`

- **Параметры адреса** (1): type
- **Сохранённые настройки (localStorage)** (1): counterparties_kind

### `pages/ClientsPage.tsx`

- **Заголовки блоков** (3): История КП (все версии); Клиенты; …{card.is_active === false &&
- **Колонки таблиц** (22): В работе; Выиграно; Дата; Заявок ЗЧ; Итого за кол-во; КП по ЗЧ, RUB; Клиент; Кол-во; Менеджер; Оборудование; Оплата; Открытых; Последняя заявка; Последняя сделка; Результат; Сделка; Сделок ГШО; Срок; Сумма выигранных, RUB; Файл; Цена за ед. без НДС; Юр. профиль
- **Кнопки** (4): Добавить; выбрать; очистить; сбросить
- **Запросы API** (9): DEL /api/refs/clients/{…}; GET /api/refs/clients; GET /api/refs/clients/lookup; GET /api/refs/clients/{…}; POST /api/refs/clients; POST /api/refs/clients/{…}/delivery-addresses/{…}/approve; POST /api/refs/clients/{…}/delivery-addresses/{…}/reject; POST /api/refs/clients/{…}/refresh-registry; POST /api/refs/clients/{…}/restore
- **Параметры адреса** (2): client; new
- **Сохранённые настройки (localStorage)** (1): clients_seg

### `pages/FactoriesPage.tsx`

- **Заголовки блоков** (5): Банковские реквизиты; Контакты по направлениям; Модели в каталоге и цены; Поставщики; … {!cur.is_active &&
- **Колонки таблиц** (20): Базис; В работе; Выиграно; Дата; Заказов ЗЧ; Источник; Контакты; Модель; Открытых; Последний заказ; Последняя сделка; Поставщик; Рейтинг; Сделок ГШО; Срок производства; Сумма выигранных; Сумма заказов; Тип; Цена; Юр. профиль
- **Кнопки** (3): Контакт; закрыть; удалить контакт
- **Запросы API** (13): DEL /api/refs/factories/{…}; GET /api/parts/entities; GET /api/parts/routes; GET /api/refs/factories; GET /api/refs/factories/contact-roles; GET /api/refs/factories/{…}; GET /api/refs/factories/{…}/prices; POST /api/refs/factories; POST /api/refs/factories/{…}/approve; POST /api/refs/factories/{…}/reject; POST /api/refs/factory-changes/{…}/approve; POST /api/refs/factory-changes/{…}/reject; PUT /api/refs/factories/{…}
- **Права** (1): factory_price_history
- **Сохранённые настройки (localStorage)** (1): suppliers_kind

### `components/ClientRequisites.tsx`

- **Заголовки блоков** (1): Реквизиты для договоров
- **Кнопки** (2): Набор; Сохранить

### `components/NewSupplierInline.tsx`

- **Кнопки** (2): Завести; отмена
- **Запросы API** (1): POST /api/refs/factories

## Снабжение, склад, сервис и запчасти

Маршруты: `/supply?tab=…, /catalog?s=parts, /deals/parts/:id`.

| Компонент | Изменён относительно базовой версии | Новое место / способ открытия |
|---|---|---|
| `pages/PartsSetupPage.tsx` | +23 / −3 строк | прежнее; добавлены путь и обзор `/supply` (новый вид) |
| `components/PartsPurchase.tsx` | нет, совпадает побайтно | прежнее |
| `components/PartsBatches.tsx` | нет, совпадает побайтно | прежнее |
| `components/PartsWarehouse.tsx` | нет, совпадает побайтно | прежнее |
| `components/PartsClaims.tsx` | нет, совпадает побайтно | прежнее |
| `components/PartsReports.tsx` | нет, совпадает побайтно | прежнее |
| `components/PartsFleet.tsx` | нет, совпадает побайтно | прежнее |
| `components/PartsCatalog.tsx` | нет, совпадает побайтно | прежнее |
| `components/PartsRequests.tsx` | +2 / −0 строк | прежнее; добавлен путь |
| `components/PartsRequestCreate.tsx` | нет, совпадает побайтно | прежнее |

### `pages/PartsSetupPage.tsx`

- **Заголовки блоков** (8): Версии; ГШО: наценка компании группы на технику; Действующая политика; Исключения по группе товара; Исключения по поставщику; Компании группы; Маршруты поставки; Снабжение и склад
- **Колонки таблиц** (30): % к продаже; Базис; В рублях; Валюта; Валюта учёта; Версия; Визы; Выпуск; Действует с; Код; Компания; Маржа компании; Маршрут; Место; Название; Наценка; Отсрочка, дн; Перевозка, дн; Покупатель; Правило; Причина; Продавец; Продавец → покупатель; Продаёт клиентам; Склад; Статус; Страна; Ступени: базис, валюта, выпуск; Сумма; Цена
- **Кнопки** (10): Компания; Обзор; Посчитать; Сохранить; Сохранить маршрут; Сохранить черновик; Ступень; изменить; отмена; удалить
- **Запросы API** (15): GET /api/parts/entities; GET /api/parts/markup-policies; GET /api/parts/meta; GET /api/parts/routes; GET /api/refs/factories; POST /api/parts/entities; POST /api/parts/markup-policies; POST /api/parts/markup-policies/simulate; POST /api/parts/markup-policies/{…}/approve; POST /api/parts/markup-policies/{…}/reject; POST /api/parts/markup-policies/{…}/submit; POST /api/parts/routes; PUT /api/parts/entities/{…}; PUT /api/parts/markup-policies/{…}; PUT /api/parts/routes/{…}
- **Параметры адреса** (1): tab

### `components/PartsPurchase.tsx`

- **Заголовки блоков** (6): Внутригрупповые заказы по маршруту; Заказы; К заказу; Подтверждение поставщика; Строки и статус поставки; … · … → …
- **Колонки таблиц** (20): Артикул; Ближайшая дата на складе; Дата; Заявка; Клиент; Кол-во; Маршрут; На складе ≈; Наименование; Номер; Номер у поставщика; Покупатель; Поставщик; Продавец → покупатель; Срок, дн; Статус; Строк; Сумма; Цена; Цена поставщика
- **Кнопки** (1): отправлен вручную
- **Запросы API** (10): GET /api/parts/po; GET /api/parts/po/candidates; GET /api/parts/po/meta; GET /api/parts/po/{…}; POST /api/parts/po; POST {…}/cancel; POST {…}/confirm; POST {…}/lines/status; POST {…}/mark-sent; POST {…}/send

### `components/PartsBatches.tsx`

- **Заголовки блоков** (5): Можно собрать в партию; Партии; Расходы партии; Строки; …{b.forwarder && ` · $…`}
- **Колонки таблиц** (14): Вес, кг; Заказ; Заявка; Кол-во; На складе ≈; Номер; Отправлена; Позиция; Поставщик; Прибытие ≈; Расходы, ₽; Статус; Строк; Экспедитор
- **Кнопки** (1): + расход
- **Запросы API** (4): GET /api/parts/batches; GET /api/parts/batches/candidates; POST /api/parts/batches; PUT /api/parts/batches/{…}

### `components/PartsWarehouse.tsx`

- **Заголовки блоков** (8): {wh ? `Остатки: $…` : "Остатки по складам"}; Движения остатков; Ждём поступления; Инвентаризация; Отклонения поставки; Пополнение склада; Приёмка по …; … · …
- **Колонки таблиц** (31): Артикул; В пути; Вид; Всего; Дата; Документ; Ед.; Заказать; Итого свободно; Кол-во; Кто; Мин.; Минимум; Наименование; Номер; Ожидается; Отбор; По закупке; Посчитано; Разница; Расхождений; Резерв; Свободно; Склад; Статус; Строк; Учёт; Цена; Ячейка; резерв; свободно / всего
- **Кнопки** (10): Движения; Заказать на склад; Инвентаризация; Остатки по складам; Отклонения; Пополнение; Принять; Приёмка; назад; показать ещё (…)
- **Запросы API** (19): GET /api/parts/stock/balances; GET /api/parts/stock/deviations; GET /api/parts/stock/inventories; GET /api/parts/stock/inventories/{…}; GET /api/parts/stock/meta; GET /api/parts/stock/moves; GET /api/parts/stock/receivable; GET /api/parts/stock/replenishment; POST /api/parts/po; POST /api/parts/stock/deviations/{…}/claim-sent; POST /api/parts/stock/deviations/{…}/close; POST /api/parts/stock/inventories; POST /api/parts/stock/inventories/{…}/approve; POST /api/parts/stock/inventories/{…}/cancel; POST /api/parts/stock/inventories/{…}/submit; POST /api/parts/stock/receipts; PUT /api/parts/stock/deviations/{…}; PUT /api/parts/stock/inventories/{…}/counts; UPLOAD /api/parts/stock/deviations/{…}/photos

### `components/PartsClaims.tsx`

- **Заголовки блоков** (4): Гарантийные претензии к заводам; Компенсация; Новая претензия; … · …
- **Колонки таблиц** (12): Артикул; Дата; Завод; Клиент / машина; Кол-во; Компенсировано, ₽; Наименование; Номер; Ответ до; Расход, ₽; Статус; Стоимость замены, ₽
- **Кнопки** (3): Претензия; Создать; отмена
- **Запросы API** (10): GET /api/parts/claims; GET /api/refs/clients; GET /api/refs/factories; POST /api/parts/claims; POST /api/parts/claims/{…}/answer; POST /api/parts/claims/{…}/close; POST /api/parts/claims/{…}/compensations; POST /api/parts/claims/{…}/compensations/{…}/received; POST /api/parts/claims/{…}/sent; UPLOAD /api/parts/claims/{…}/photos
- **Права** (1): cost

### `components/PartsReports.tsx`

- **Колонки таблиц** (34): Артикул; Без движения, дн; В каталоге; Вовремя, %; Выручка, ₽; Дата; Завод; Закупочная стоимость; Заявка; Заявок; Клиент; Клиентов; Кол-во; Компенсировано, ₽; Маржа, %; Маржа, ₽; Наибольшая задержка, дн; Наименование; Остаток; Отгружено за год; Отгрузка; Отказов; Отклонение в среднем, дн; Поставщик; Претензий; Признано, %; Расход, ₽; С ответом; Себестоимость, ₽; Склад; Срок компенсации, дн; Строк; Строк принято; Сумма, ₽

### `components/PartsFleet.tsx`

- **Заголовки блоков** (6): Документы машины; Запчасти для этой машины; Наработка; Скоро ТО — предложите комплект; ТО-комплекты модели; … …{m.serial && ` · зав. № $…`}
- **Колонки таблиц** (14): Артикул; Зав. №; Клиент; Машина; Менеджер; Место; Наименование; Наработка, м·ч; Примерно; Свободно; Следующее ТО; ТО, м·ч; Узел; м·ч/день
- **Кнопки** (8): + комплект; Заявка на ТО-комплект; Заявка на комплект; Машина; Сохранить; Сохранить комплект; изменить; отмена
- **Запросы API** (16): GET /api/parts/fleet/machines; GET /api/parts/fleet/machines/{…}; GET /api/parts/fleet/machines/{…}/documents; GET /api/parts/fleet/machines/{…}/parts; GET /api/parts/fleet/models; GET /api/parts/fleet/reminders; GET /api/refs/clients; POST /api/parts/fleet/kits; POST /api/parts/fleet/kits/from-deal/{…}; POST /api/parts/fleet/machines; POST /api/parts/fleet/machines/{…}/hours; POST /api/parts/fleet/machines/{…}/kit-request; POST /api/parts/fleet/reminders/{…}/dismiss; POST /api/parts/fleet/sync-deals; PUT /api/parts/fleet/kits/{…}; PUT /api/parts/fleet/machines/{…}

### `components/PartsCatalog.tsx`

- **Заголовки блоков** (9): {cur.id ? `$… — $…` : "Новая запчасть"} {cur.s; Закупка; Логистика; Номера; Основное; Применимость; Склад; Файлы и сделки; Цены
- **Колонки таблиц** (11): Артикул; В пути; Группа; Доступно; Минимальный остаток; Наименование; Остаток; Производитель / OEM; Резерв; Склад; Цена продажи
- **Кнопки** (10): Загрузить остатки; Запчасть; Перенести; Перенос из МойСклад; Проверить; вперёд; всё равно сохранить новую; закрыть; назад; удалить
- **Запросы API** (13): DEL /api/parts/catalog/{…}/photo/{…}; GET /api/parts/catalog; GET /api/parts/catalog/{…}; GET /api/parts/meta/catalog; GET /api/parts/routes; GET /api/parts/warehouses; GET /api/refs/clients; GET /api/refs/factories; POST /api/parts/catalog${force ; POST /api/parts/import/moysklad-photos; PUT /api/parts/catalog/{…}; UPLOAD /api/parts/import/moysklad-products; UPLOAD /api/parts/import/stock
- **Права** (1): factory_price_history

### `components/PartsRequests.tsx`

- **Заголовки блоков** (7): Запросы поставщикам; Заявка на запчасти …; Итого по строкам в работе; Коммерческое предложение; Отгрузка клиенту; Сравнение ответов; … · … {r.urgent &&
- **Колонки таблиц** (38): Аналог; В заявке; В резерве; Валюта; Дата; Действует до; Запрос; Запрос клиента; Источник; Итого; КП; Клиент; Кол-во; Комментарий; Маржа; Машина; Мин. партия; Наименование; Наличие; Номер; Номер у поставщика; Ответ; Отгружено; Отгрузить; Подобрано; Позиция; Порог; Поставщик; Себест., ₽/ед; Срок ответа; Срок, дн; Статус; Строк; Сумма, ₽; Цена; Цена …; Что изменилось; Язык
- **Кнопки** (3): Заявка; закрыть; свернуть
- **Запросы API** (35): DEL /api/parts/requests/{…}; DEL {…}/lines/{…}; GET /api/parts/catalog; GET /api/parts/requests; GET /api/parts/requests/meta; GET /api/parts/requests/{…}; GET /api/parts/requests/{…}/chain; GET /api/parts/rfq; GET /api/parts/rfq/{…}; GET /api/parts/stock/requests/{…}/shippable; GET /api/refs/clients; GET /api/refs/factories; GET {…}/quotes; PATCH {…}/lines/{…}; POST /api/parts/claims; POST /api/parts/requests/kp/{…}/sent; POST /api/parts/requests/{…}/chain/approve; POST /api/parts/rfq/{…}/chain; POST /api/parts/rfq/{…}/close; POST /api/parts/rfq/{…}/mark-sent; POST /api/parts/rfq/{…}/send; POST /api/parts/stock/requests/{…}/client-order; POST /api/parts/stock/requests/{…}/shipments; POST {…}/approve; POST {…}/close; POST {…}/kp; POST {…}/lines; POST {…}/lines/{…}/choose; POST {…}/lines/{…}/split; POST {…}/match; POST {…}/reopen; POST {…}/reprice; POST {…}/rfq; PUT /api/parts/rfq/{…}/answers; UPLOAD /api/parts/rfq/{…}/import
- **Права** (1): cost

### `components/PartsRequestCreate.tsx`

- **Заголовки блоков** (3): 1. Клиент и машина; 2. Позиции; Новая заявка на запасные части
- **Колонки таблиц** (6): Ед.; Каталог; Кол-во; Наименование; Наличие на складах; Номер
- **Кнопки** (6): Вставить из письма / Excel; Из каталога; Из файла заказа клиента; Разобрать и добавить; добавить; отмена
- **Запросы API** (5): GET /api/parts/catalog; GET /api/parts/fleet/machines; POST /api/parts/requests; POST /api/parts/requests/preview-lines; UPLOAD /api/parts/requests/parse-file
