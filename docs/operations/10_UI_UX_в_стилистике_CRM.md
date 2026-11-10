# UI/UX в стилистике CRM

## 1. Общие правила

Использовать текущую CRM, а не создавать новый визуальный продукт.

- Цвета: `--accent: #ff3131`, `--bg: #fafafa`, существующие text/border/success/warning tokens.
- Карточки: белые, текущие radius/shadow/borders; классы `.card`, `.tabs`, `.badge`, `.btn`, `.page-head`, `.section-head`, `.kpi`.
- Навигация, шапка, `NotificationBell`, `InfoBar`, `FloatingScrollbar` сохраняются и расширяются.
- Не переносить рекламный стиль страницы входа: `#ef3035` как отдельную тему, Noto Sans, минеральную текстуру, декоративные плоскости.
- Иконки — из существующего набора/подхода. Не подключать новую библиотеку ради одной иконки.
- Читаемость важнее декоративности. Текст не размещать поверх фото/чертёжей без контрастной подложки; новые фоновые изображения не требуются.
- Все новые элементы имеют loading/empty/error/success/disabled states и реальные обработчики.

## 2. Точки входа

### Шапка

- «Поддержка» — текстовая/иконочная кнопка с unread badge только для своих обращений либо admin queue по праву.
- «ИИ»/«Помощник» — открывает drawer при `assistant_use`. При provider off кнопка остаётся, потому что help/CRM deterministic functions доступны; tooltip честно описывает состояние.
- Notification bell использует существующий dropdown и deep links.
- Security critical alert не показывать красным глобальным баннером всем сотрудникам: только пользователям с соответствующим правом.

### Рабочее место

1. `focus` остаётся.
2. `briefing` — следующий центральный блок по умолчанию.
3. Остальные блоки сохраняют порядок из пользовательской layout.
4. Компактные CTA «Спросить помощника» и «Сообщить о проблеме» не дублируются несколько раз внутри каждой карточки.

### Настройки

`SettingsHub`:

- «Безопасность» — личный раздел для каждого: MFA, recovery, sessions, смена пароля;
- «Безопасность сотрудников» — отдельная административная секция по `security_admin`;
- «Журнал и предупреждения» — по `security_audit`;
- «Пересмотр прав» — по `security_admin`;
- «Резервные копии» — по `backup_status`;
- «Помощник» — технические настройки по `assistant_admin`, без чужих диалогов.

Не объединять всё в одну страницу с десятком вкладок, доступных по одному admin-флагу.

## 3. Ежедневная сводка

### Desktop

Белая card с header:

- заголовок «Ваши приоритеты»;
- короткое приветствие/дата, без искусственного энтузиазма;
- `generated_at` и icon-button «Обновить» с accessible label;
- счётчик отложенных, если есть.

Элемент — не тяжёлая вложенная карточка, а строка/компактный блок с divider:

- severity badge;
- title, максимум 2–3 строки с раскрытием при необходимости;
- reason и due;
- advice отдельным спокойным текстом;
- источник и сумма/валюта при наличии;
- primary text link «Открыть»;
- overflow menu «Закрепить», «Отложить», «Скрыть».

Critical использует accent/error sparingly, не заливает всю карточку красным. Warning и info различимы не только цветом — текстовый badge/icon.

Footer: «Ещё N…» и ссылка, «Показать скрытые/отложенные».

### Mobile

На 390 px элементы одной колонкой. Основное действие «Открыть» доступно без overflow; pin/snooze/hide — в menu. Дата/сумма не обрезаются. Нет горизонтального scroll у текста; таблицы не использовать.

### Состояния

- skeleton не имитирует выдуманные элементы;
- empty: «Срочных дел по вашим задачам и срокам нет»;
- partial warning: ненавязчивая строка с retry;
- error только блока, остальное рабочее место доступно;
- snooze modal содержит дату и пояснение, что срок источника не меняется;
- hide требует лёгкого подтверждения/undo toast, не модальное окно на каждый click.

## 4. ИИ drawer

### Размеры

- Desktop: fixed right drawer, ширина примерно 420 px, max `100vw`; не сдвигает всю CRM в нечитаемую узкую колонку.
- 1280/1440: основная страница может затемняться лёгким overlay либо оставаться доступной по существующему drawer pattern; выбрать один вариант и проверить focus.
- 768: drawer до 420 px либо full-screen при недостатке места.
- 390: full-screen dialog с безопасными отступами и sticky composer.

### Структура

Header: название, режим (`CRM`, `Справка`, `Общий вопрос`), provider/fallback status, history, close.

Body:

- welcome/empty с 2–3 реальными quick prompts;
- сообщения пользователя/assistant;
- sources компактными ссылками под ответом;
- warnings и «данные на …»;
- retry/error без потери вопроса.

Composer:

- textarea, auto-resize до разумного max;
- Enter send, Shift+Enter newline;
- send disabled во время запроса либо реализован cancel;
- лимит символов;
- подсказка «Не вводите пароли, ключи и другие секреты».

History: список только своих диалогов, delete confirmation. Focus trap, Esc close, возврат focus на кнопку, screen reader labels. При external provider выключен general mode disabled с объяснением, CRM/help остаются доступными.

## 5. Поддержка

### `/support`

Page head: «Поддержка» + primary button «Новое обращение».

Список своих tickets в текущих card/table patterns:

- номер, тема, категория, статус, последнее обновление, unread;
- filters tabs «Активные», «Ждут меня», «Решённые», «Все»;
- empty state с реальной кнопкой создания;
- mobile cards вместо широкой таблицы.

Форма создания — отдельная страница/modal по текущему стилю. Поля с labels, не только placeholders. Технические метаданные в collapsible «Что будет передано»; пользователь может убрать route/client metadata до submit, кроме необходимого app version? Лучше разрешить отключение optional metadata и зафиксировать выбор.

### `/support/:id`

- header номер/тема/status/priority;
- двухколоночный desktop: переписка основная, metadata справа; mobile одной колонкой;
- сообщения не выглядят как публичный чат с неизвестными иконками; author label и timestamp;
- system messages узкие и нейтральные;
- attachments с scan/download state;
- composer sticky только если не closed;
- status actions справа вверху: «Проблема осталась», «Закрыть обращение» по допустимым переходам.

### `/support/admin`

Доступна только по support_admin. KPI используют `.kpi`, очередь — tabs/filters/table на desktop и cards на mobile. Назначение/приоритет в карточке сохраняются явной кнопкой либо controlled auto-save с success/error; нельзя показывать сохранение при failed request.

## 6. Личная безопасность

### MFA

Шаги wizard:

1. объяснение и «Начать»;
2. QR + ручной ключ с copy button; не показывать в общем screenshot/screen share warning;
3. ввод кода;
4. recovery codes с download/copy/print только по явному действию и подтверждением «Я сохранил коды».

Не показывать секрет повторно после завершения. При required MFA страница настройки доступна даже когда остальные routes заблокированы.

### Sessions

Текущая сессия отмечена. Каждая строка: устройство/browser, последняя активность, создана, IP prefix, MFA. «Завершить» для других, «Завершить все, кроме текущей» с подтверждением. Нет кнопки revoke current в общем списке без ясного logout поведения.

### Пароль

Форма текущего/нового/повтора, checklist требований, server errors. Не отображать strength как ложную гарантию. Success сообщает, что другие входы завершены.

## 7. Security admin

### Сотрудник

Danger zone «Доступ сотрудника»:

- активные sessions;
- MFA reset;
- «Отключить доступ» — отдельная destructive кнопка с modal: ФИО, последствия, counts задач/tickets, ввод подтверждения и step-up MFA;
- результат перечисляет фактические counts из backend;
- reactivate отдельно, без автоматического восстановления старых прав.

### Алерты

Filters severity/status/user/type/date. Detail показывает sanitized metadata и ссылку на связанные security events, но не support/AI contents. Ack/resolve comment обязателен; resolved alert остаётся в истории.

### Пересмотр прав

Progress, due, reviewer, items. Snapshot diff current vs review start. Change ведёт к реальным controls и требует сохранения; нельзя отметить `change`, не указав, что изменено. Собственные повышенные права имеют блок «Требуется второй reviewer».

## 8. Backup status

Только факты:

- последний успех, последний запуск, snapshot short ID, reason, database/files sizes, check, retention, next run;
- banner при failed/stale;
- sanitized error code + ссылка на runbook;
- manual run button только при реальном controlled endpoint, иначе «Команда для администратора сервера» с копируемой fixed командой без секретов;
- статус «Отключение Timeweb разрешено» появляется только после записи всех gate checks и подтверждения владельца, не вычисляется по одному успешному backup.

Не отображать bucket secrets, full env, RESTIC_PASSWORD, restore key, access key ID без необходимости.

## 9. Адаптивность и доступность

Проверяемые ширины: 1440, 1280, 768, 390 px.

- Минимальный touch target около 44 px для важных mobile controls.
- Focus visible; все actions доступны с клавиатуры.
- Modals/drawer имеют title, focus trap, Esc и возврат focus.
- Цвет не единственный носитель severity/status.
- Контраст соответствует текущему читаемому UI; muted text не становится слишком светлым.
- Таблицы на mobile превращаются в cards либо получают явный контролируемый scroll; основная страница не получает двойной горизонтальный scroll.
- Long ticket subject, AI response, filename и русские длинные слова не ломают layout (`overflow-wrap`).
- Spinner не остаётся бесконечно после ошибки; toast не является единственным местом критичной ошибки.

## 10. Browser QA каждого этапа

Для каждого нового экрана:

1. открыть через реальный nav/CTA, не только прямым URL;
2. выполнить основной happy path;
3. проверить validation, server error, empty, loading, forbidden;
4. проверить deep link и refresh;
5. проверить 4 ширины и keyboard;
6. проверить console/network: нет unexpected 4xx/5xx, mixed content, source maps errors и secrets;
7. убедиться, что кнопки без реализованного действия отсутствуют;
8. сохранить скриншоты desktop/mobile и краткий checklist в отчёте, без production-данных.
