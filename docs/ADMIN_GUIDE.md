# Руководство администратора (краткое)

## Развёртывание

```bash
cp backend/.env.example backend/.env      # заполнить секреты
docker compose up --build -d
docker compose exec backend python -m scripts.seed          # dev-данные: пользователи по ролям, клиент, заводы, ТН ВЭД, схемы оплаты
docker compose exec db psql -U gsho -d gsho -f /dev/stdin < backend/migrations/001_audit_immutable.sql
```

Интерфейс: `http://<сервер>/`, API и Swagger: `http://<сервер>:8000/docs`.

## Переключение провайдеров (раздел 13 ТЗ)

| Настройка | Значения | Что нужно |
| --- | --- | --- |
| `AUTH_PROVIDER` | `entra` (M365), `yandex`, `keycloak`, `dev` | Регистрация приложения, `OIDC_CLIENT_ID/SECRET`, redirect URI `/api/auth/callback`, `OIDC_GROUP_ROLE_MAP` = `<group-id>:manager,<group-id>:cd,…` |
| `STORAGE_PROVIDER` | `sharepoint`, `yandex_disk`, `s3`, `local` | Graph: `GRAPH_TENANT_ID`, `GRAPH_DRIVE_ID` (SharePoint-сайт организации), права Files.ReadWrite.All; S3: endpoint/bucket/ключи |
| `MAIL_PROVIDER` | `outlook`, `yandex_smtp`, `console` | Graph: Mail.Send, Mail.Read для `MAIL_SHARED_MAILBOX` |
| `MESSENGER_PROVIDER` | `wecom`, `console` | `WECOM_CORP_ID`, `WECOM_AGENT_ID`, `WECOM_SECRET` |

Смена хранилища сопровождается утилитой миграции папок сделок (копирование с проверкой sha256 и перезапись ссылок в `attachments`) — пишется в очереди 2.

## Справочники и настройки

* **Ставки и нормативы** — `/settings` в интерфейсе или `PUT /api/admin/settings/{key}`; каждое изменение с комментарием и историей. Ключи и значения по умолчанию — `backend/app/services/settings_service.py`.
* **ТН ВЭД** — CSV `code;description;duty_pct;vat_pct;util_fee;licence;certification` → `POST /api/admin/hs/upload?valid_from=`; старые ставки закрываются датой.
* **Курсы ЦБ** — загружаются ежедневно; история с 2014 г.: `POST /api/admin/fx/history?currency=CNY&start=2014-01-01`; ручной курс: `POST /api/admin/fx/manual`.
* **Шаблоны** (RFQ, КП, Excel, письма, WeCom, логотипы) — `POST /api/admin/templates/{kind}`; текущий шаблон RFQ v1 можно сгенерировать: `python -c "from app.services.rfq_template import build_rfq_template; build_rfq_template('RFQ_v1.xlsx')"`.
* **Ступени визации** — ключ `approval_ladder`: список `{name, role, condition, sla_days}`, условия: `always`, `margin_below_threshold`, `cash_gap_above_limit`, `deal_above_gd_limit`, `fd_declined`, `overhead_override` (через `|`).
* **Пользователи и роли** — `/api/admin/users`; несколько ролей на пользователя; наблюдателю — `observer_scope.deal_ids`.

## Резервное копирование

Контейнер `backup` делает ежедневный дамп БД и архив файлов локального хранилища в том `backups`, хранит 90 дней. При SharePoint файлы защищены средствами M365; дампы БД по-прежнему обязательны.

## Журналы

* Действия пользователей — `GET /api/admin/audit` (таблица `audit_log`, только вставка).
* Интеграции — `GET /api/admin/integrations/log`.
* Уведомления — таблица `notifications` (статусы queued/sent/failed).
