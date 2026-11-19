# Резервный сервер crm2 в Yandex Cloud (D-083)

Тёплый резерв: если рабочий сервер `crm.dobrov.pro` (Timeweb) недоступен, CRM поднимается из последней копии на
отдельном адресе `crm2.dobrov.pro`. DNS основного адреса не меняется никогда. В обычное время ВМ резерва выключена.

| | |
| --- | --- |
| Потеря данных (RPO) | до 15 минут: рабочий сервер делает копию каждые 15 минут (`gsho-backup-frequent.timer`) |
| Время запуска (RTO) | 15–30 минут: запуск ВМ + `failover.sh` |
| Кто решает переключаться | владелец (или назначенный им второй человек) |
| Защита от расхождения данных | флаг в Object Storage → рабочий сервер переадресует `crm` → `crm2` (`flag-guard.sh`) |

Секреты (пароль restic, ключи SA, `.env` стенда) — только на серверах и в защищённой заметке владельца; в репозиторий,
CRM и чат не попадают.

## 1. Рабочий сервер: копии каждые 15 минут и защита

```bash
cd /opt/gsho-costing && sudo git pull
sudo install -m 0750 deploy/backup/gsho-backup.sh /usr/local/sbin/gsho-backup.sh
sudo install -m 0644 deploy/backup/gsho-backup-frequent.service deploy/backup/gsho-backup-frequent.timer /etc/systemd/system/
sudo install -m 0644 deploy/dr/gsho-dr-guard.service deploy/dr/gsho-dr-guard.timer /etc/systemd/system/
sudo nano /etc/gsho/dr-guard.env      # DR_FLAG_URL=https://storage.yandexcloud.net/<бакет флага>/failover.json
sudo systemctl daemon-reload
sudo systemctl enable --now gsho-backup-frequent.timer gsho-dr-guard.timer
systemctl list-timers 'gsho-*'
```

Ежедневная копия в 19:00 (`gsho-backup.timer`) остаётся полной: `restic check` и очистка. Частые копии их пропускают
(`BACKUP_LIGHT=1`, в отчёте `check_status=skipped`). Хранение: все снимки за 2 суток, дальше — 90 ежедневных и 12 ежемесячных.

Сбой Яндекса рабочему серверу не мешает: если флаг прочитать нельзя, `flag-guard.sh` ничего не меняет.

## 2. Резервная ВМ (один раз)

1. **Yandex Cloud:** ВМ Ubuntu 24.04, зона `ru-central1-d`, Intel Ice Lake 2 vCPU · 20 % · 4 ГБ, диск HDD 20 ГБ,
   динамический публичный IP, группа безопасности: входящие 22, 80, 443.
2. **Стенд в рабочем режиме:** `deploy/production/setup.sh --domain crm2.dobrov.pro --email <адрес> --yandex-mss`.
   Базу не инициализировать: данные приходят из копии.
3. **Секреты рабочего сервера** (с сервера на сервер, не через чат): `backend/.env` целиком (`SECRET_KEY`,
   `MFA_ENCRYPTION_KEY` — иначе не войти и не сработают коды MFA), `POSTGRES_PASSWORD` в `.env`. Затем в `backend/.env`
   резерва: `CORS_ORIGINS=https://crm2.dobrov.pro`, `OIDC_REDIRECT_URI=https://crm2.dobrov.pro/api/auth/callback`.
4. **Доступ к копиям** — файлы по `deploy/dr/dr.env.example`: `/etc/gsho/restore.env` (ключ `sa-crm-prod-restore`,
   только чтение), `/etc/gsho/dr.env` (ключ флага), `/etc/gsho/backup-failover.env` (отдельный бакет копий резерва).
5. **Сертификат `crm2.dobrov.pro`:** Let's Encrypt по HTTP из Яндекса не проходит — Yandex Certificate Manager с
   DNS-проверкой (постоянная CNAME `_acme-challenge.crm2` у R01), `CM_CERT_ID` в `.env`, `gsho-cert.timer`
   (`deploy/fetch-cert.sh`). Сервисному аккаунту ВМ — роль `certificate-manager.certificates.downloader`.
6. **Скрипты:** `sudo install -m 0644 deploy/backup/gsho-backup*.service deploy/backup/gsho-backup*.timer deploy/dr/gsho-dr-drill.service /etc/systemd/system/`,
   `sudo install -m 0750 deploy/backup/gsho-backup.sh /usr/local/sbin/`, `sudo systemctl enable gsho-dr-drill.service`.
   Таймеры копий на резерве **не включать** — их включает `failover.sh`.
7. Проверка: `sudo bash deploy/dr/drill.sh` → `cat /var/lib/gsho-dr/drill-last.json` (`"ok": true`). Выключить ВМ.

## 3. Переключение (авария)

1. Мониторинг прислал письмо «crm.dobrov.pro не отвечает». Проверить статус Timeweb. Решение — владельца:
   переключаемся, если рабочий недоступен дольше 30 минут и срок восстановления не известен.
2. Консоль Yandex Cloud → резервная ВМ → «Запустить».
3. На ВМ: `sudo bash /opt/gsho-costing/deploy/dr/failover.sh` → «да». Скрипт ставит флаг, восстанавливает последнюю
   копию, запускает стенд, включает копии резерва и печатает IP.
4. У R01: A-запись `crm2` → этот IP (TTL 300), если её не обновил `DR_DNS_UPDATE_CMD`.
5. Сообщение сотрудникам — текст печатает `failover.sh` (адрес, время данных).

Если рабочий сервер оживёт сам, он увидит флаг и переадресует `crm` → `crm2`: устаревшая база данные не принимает.

## 4. Возврат на рабочий сервер (в нерабочее время, простой 10–15 минут)

1. Резерв: `sudo bash deploy/dr/failback.sh --stage=standby` — останавливает приложение, делает финальную копию, печатает ID.
2. Рабочий: `sudo bash deploy/dr/failback.sh --stage=primary --snapshot=<ID>` — восстанавливает её (нужен
   `/etc/gsho/restore-failover.env`). Пока стоит флаг, `crm` по-прежнему переадресует на `crm2`.
3. Резерв: `sudo bash deploy/dr/failback.sh --stage=finish` — снимает флаг, останавливает стенд. Выключить ВМ.
4. Сотрудникам: «вернулись на crm.dobrov.pro».

## 5. Еженедельная проверка

Расписание в Yandex Cloud (Cloud Functions по таймеру, раз в неделю ночью) записывает в метаданные ВМ
`dr-drill-until=<сейчас + 30 мин>` и запускает её. `gsho-dr-drill.service` видит метку, выполняет `drill.sh --poweroff`:
последняя копия рабочего → стенд в режиме `APP_ENV=recovery` (без почты и фоновых заданий) → `/api/health`, число
пользователей и сделок → отчёт `/var/lib/gsho-dr/drill-last.json` → ВМ выключается. Ручной запуск ВМ (переключение)
метки не имеет и проверку не запускает.

Раз в квартал — учебное переключение с флагом и возвратом (§3–§4) в выходной.

Стоимость в покое — диск ВМ (≈ 100–200 ₽/мес); статический IP не нужен.
