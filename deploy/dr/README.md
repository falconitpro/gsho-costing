# Резервный сервер crm2 в Yandex Cloud (D-084)

Тёплый резерв: если рабочий сервер `crm.dobrov.pro` (Timeweb) недоступен, CRM поднимается из последней копии на
отдельном адресе `crm2.dobrov.pro`. DNS основного адреса не меняется никогда. В обычное время ВМ резерва выключена.

| | |
| --- | --- |
| Потеря данных (RPO) | до 15 минут: рабочий сервер делает копию каждые 15 минут (`gsho-backup-frequent.timer`) |
| Время запуска (RTO) | 15–30 минут: запуск ВМ + `failover.sh`; DNS не меняется (статический IP, A-запись `crm2` постоянная) |
| Кто решает переключаться | владелец (или назначенный им второй человек) |
| Защита от расхождения данных | флаг в Object Storage → рабочий сервер переадресует `crm` → `crm2` (`flag-guard.sh`) |

Секреты (пароль restic, ключи SA, `.env` стенда) — только на серверах и в защищённой заметке владельца; в репозиторий,
CRM и чат не попадают.

**Скрипты резерва запускаются из `/usr/local/lib/gsho-dr`, а не из `/opt/gsho-costing/deploy/dr`.** Восстановление
переключает код на commit копии; в старом commit каталога `deploy/dr` может не быть, и скрипты пропали бы посреди работы
(найдено первой проверкой 19.11). Ставит и обновляет их `deploy/dr/install.sh`; `dr_restore_latest` отказывается
работать, если скрипты запущены из каталога кода.

## 1. Рабочий сервер: копии каждые 15 минут и защита

```bash
# после обычного деплоя (deploy/production/update.sh), когда код содержит deploy/dr/install.sh:
echo 'DR_FLAG_URL=https://storage.yandexcloud.net/<бакет флага>/failover.json' | sudo tee /etc/gsho/dr-guard.env
sudo bash /opt/gsho-costing/deploy/dr/install.sh --primary
systemctl list-timers 'gsho-*'
```

Ежедневная копия в 19:00 (`gsho-backup.timer`) остаётся полной: `restic check` и очистка. Частые копии их пропускают
(`BACKUP_LIGHT=1`, в отчёте `check_status=skipped`). Хранение: все снимки за 2 суток, дальше — 90 ежедневных и 12 ежемесячных.

Сбой Яндекса рабочему серверу не мешает: если флаг прочитать нельзя, `flag-guard.sh` ничего не меняет.

**Дубль копий в S3 Timeweb** (бакет `berghaus-crm-prod-copy`, холодный, приватный): раз в сутки после полной копии
`gsho-backup-mirror.timer` копирует репозиторий restic из Яндекса через `rclone copy` — только добавляет файлы, удаления
в источнике дубль не трогают. Это тот же репозиторий с тем же паролем restic. Включение: в панели Timeweb создать
пользователя бакета (чтение и запись), затем на рабочем `sudo apt-get install -y rclone && sudo gsho-backup-mirror.sh --setup`
и ввести его ключи (секрет вводится скрыто). Отчёт: `/var/lib/gsho-backup/status/mirror-last.json`.

## 2. Резервная ВМ (один раз)

1. **Yandex Cloud:** ВМ Ubuntu 24.04, зона `ru-central1-d`, Intel Ice Lake 2 vCPU · 20 % · 4 ГБ, диск HDD 20 ГБ,
   **статический** публичный IP, входящие 22, 80, 443. Статический — потому что у R01 TTL всей зоны 6 часов и правка
   публикуется до 40 минут: менять A-запись при аварии нельзя, она постоянная.
2. **Стенд в рабочем режиме:** `deploy/production/setup.sh --domain crm2.dobrov.pro --email <адрес> --yandex-mss`.
   Базу не инициализировать: данные приходят из копии.
3. **Секреты рабочего сервера** (с сервера на сервер, не через чат): `backend/.env` целиком (`SECRET_KEY`,
   `MFA_ENCRYPTION_KEY` — иначе не войти и не сработают коды MFA), `POSTGRES_PASSWORD` в `.env`. Затем в `backend/.env`
   резерва: `CORS_ORIGINS=https://crm2.dobrov.pro`, `OIDC_REDIRECT_URI=https://crm2.dobrov.pro/api/auth/callback`.
4. **Доступ к копиям** — файлы по `deploy/dr/dr.env.example`: `/etc/gsho/restore.env` (ключ `sa-crm-prod-restore`,
   только чтение), `/etc/gsho/dr.env` (ключ флага), `/etc/gsho/backup-failover.env` (отдельный бакет копий резерва).
5. **Сертификат `crm2.dobrov.pro`:** Let's Encrypt по HTTP из Яндекса не проходит — Yandex Certificate Manager с
   DNS-проверкой (постоянная CNAME `_acme-challenge.crm2` у R01), `CM_CERT_ID` в `.env`, `gsho-cert.timer` (ставит
   `install.sh --standby`). Роль `certificate-manager.certificates.downloader` на сертификат — сервисному аккаунту ВМ.
   Если стенд остановлен, `fetch-cert.sh` только кладёт файлы и не запускает CRM.
6. **Скрипты:** `sudo bash /opt/gsho-costing/deploy/dr/install.sh --standby` (сразу после `setup.sh`, пока код свежий).
   Таймеры копий на резерве не включаются — их включает `failover.sh`.
7. Проверка: `sudo bash /usr/local/lib/gsho-dr/drill.sh` → `cat /var/lib/gsho-dr/drill-last.json` (`"ok": true`). Выключить ВМ.

## 3. Переключение (авария)

1. Мониторинг прислал письмо «crm.dobrov.pro не отвечает». Проверить статус Timeweb. Решение — владельца:
   переключаемся, если рабочий недоступен дольше 30 минут и срок восстановления не известен.
2. Консоль Yandex Cloud → резервная ВМ → «Запустить».
3. На ВМ: `sudo bash /usr/local/lib/gsho-dr/failover.sh` → «да». Скрипт ставит флаг, восстанавливает последнюю
   копию, запускает стенд, включает копии резерва и печатает IP.
4. DNS не трогать: A-запись `crm2` постоянно указывает на статический IP ВМ. Проверка: `nslookup crm2.dobrov.pro`.
5. Сообщение сотрудникам — текст печатает `failover.sh` (адрес, время данных).

Если рабочий сервер оживёт сам, он увидит флаг и переадресует `crm` → `crm2`: устаревшая база данные не принимает.

## 4. Возврат на рабочий сервер (в нерабочее время, простой 10–15 минут)

1. Резерв: `sudo bash /usr/local/lib/gsho-dr/failback.sh --stage=standby` — останавливает приложение, делает финальную копию, печатает ID.
2. Рабочий: `sudo bash /usr/local/lib/gsho-dr/failback.sh --stage=primary --snapshot=<ID>` — восстанавливает её (нужен
   `/etc/gsho/restore-failover.env`). Пока стоит флаг, `crm` по-прежнему переадресует на `crm2`.
3. Резерв: `sudo bash /usr/local/lib/gsho-dr/failback.sh --stage=finish` — снимает флаг, останавливает стенд. Выключить ВМ.
4. Сотрудникам: «вернулись на crm.dobrov.pro».

## 5. Мониторинг и еженедельная проверка

**Развёртывание** (Cloud Shell консоли Яндекса, повторный запуск обновляет код):
`git clone -q https://github.com/falconitpro/gsho-costing && bash gsho-costing/deploy/dr/functions/deploy.sh`.
Создаёт SA `sa-crm-monitor`, бакет состояния `berghaus-crm-monitor-state`, функции `crm-monitor` (раз в 5 минут) и
`crm2-drill` (воскресенье 03:30 МСК), таймеры. Вручную в консоли (бакет → «Безопасность» → «Назначить роли»):
`sa-crm-monitor` — `storage.viewer` на `berghaus-crm-prod-backup` и `storage.editor` на `berghaus-crm-monitor-state`.

**Письма** — через Yandex Cloud Postbox с адреса `crm-monitor@notify.dobrov.pro` (DKIM — две CNAME
`*._domainkey.notify` у R01) по IAM-токену, без паролей. Получатели: evgeniy@dobrov.pro, e.dobrov@berg-haus.com,
s.volokhov@berg-haus.com. `crm-monitor` пишет, когда crm.dobrov.pro не отвечает, когда копии старше 50 минут, когда
резервная ВМ включена дольше 3 часов без флага и раз в 6 часов, пока стоит флаг; повторяет раз в час и сообщает
«восстановлено». Резервная ВМ сама пишет о переключении и о результате еженедельной проверки (`dr_notify`,
`DR_NOTIFY_TO`/`DR_NOTIFY_FROM` в `/etc/gsho/dr.env`, роль `postbox.sender` у её SA).

### Еженедельная проверка

Расписание в Yandex Cloud (Cloud Functions по таймеру, раз в неделю ночью) записывает в метаданные ВМ
`dr-drill-until=<сейчас + 30 мин>` и запускает её. `gsho-dr-drill.service` видит метку, выполняет `drill.sh --poweroff`:
последняя копия рабочего → стенд в режиме `APP_ENV=recovery` (без почты и фоновых заданий) → `/api/health`, число
пользователей и сделок → отчёт `/var/lib/gsho-dr/drill-last.json` → ВМ выключается. Ручной запуск ВМ (переключение)
метки не имеет и проверку не запускает.

Раз в квартал — учебное переключение с флагом и возвратом (§3–§4) в выходной.

Стоимость в покое — диск ВМ (≈ 100–200 ₽/мес); статический IP не нужен.
