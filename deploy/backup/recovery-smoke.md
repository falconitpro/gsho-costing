# Проверка восстановления (restore drill) на изолированной ВМ

Выполняется для gate отключения Timeweb (03 §7, BKP-10) и повторно на итоговой схеме (BKP-11). Полный runbook — `docs/operations/13` §4. Восстановление поверх рабочей БД и на сервере стенда **запрещено**; `gsho-restore-verify.sh` останавливается, если видит запущенный backend стенда.

## 1. Recovery-ВМ

- Отдельная ВМ (Yandex Compute, Ubuntu 24.04, 2 vCPU / 4 ГБ, диск ≥ 3× размера копии), без доступа сотрудников; входящий трафик — только SSH владельца.
- Установить: `restic` (≥ 0.16), `postgresql-16` (или `postgresql-client-16` + контейнер `postgres:16`), `python3`. Версия `pg_restore` — та же, что у сервера (16): клиент 17+ добавляет в восстановление `SET transaction_timeout`, и PostgreSQL 16 его отвергает; `gsho-restore-verify.sh` это проверяет.
- Секреты: пароль restic и ключ **sa-gsho-restore** (только чтение). Ключа sa-gsho-backup здесь быть не должно.

```bash
sudo install -d -m 0700 /etc/gsho
sudo nano /etc/gsho/restore.env      # RESTIC_REPOSITORY, RESTIC_PASSWORD, AWS_ACCESS_KEY_ID/SECRET (restore SA), AWS_DEFAULT_REGION=ru-central1
sudo chmod 0600 /etc/gsho/restore.env
```

## 2. Восстановление и проверка

```bash
git clone https://github.com/falconitpro/gsho-costing /opt/recovery-src   # только скрипты, не стенд
sudo bash -c 'set -a; . /etc/gsho/restore.env; set +a; restic snapshots --compact --tag gsho'
# выбрать снимок по дате и записать его ID в отчёт

sudo -u postgres createuser --superuser "$USER" 2>/dev/null || true
sudo -E bash -c 'set -a; . /etc/gsho/restore.env; set +a; \
  PGHOST=/var/run/postgresql /opt/recovery-src/deploy/backup/gsho-restore-verify.sh \
  --snapshot=<ID_СНИМКА> --target=/srv/recovery/run1 --pg-restore-to=gsho_recovery'
```

Скрипт печатает: снимок, `sha256sum -c` по всем артефактам, manifest (commit, схема, размеры), оглавление дампа, число таблиц/пользователей/сделок в восстановленной БД, число распакованных файлов. Если любая сумма не совпала — скрипт останавливается, данные не запускаются.

## 3. Запуск приложения на восстановленной копии (smoke)

Backend из того же commit, что в manifest, в режиме `APP_ENV=recovery` (D-050): приложение само проверит, что почта, мессенджер и запись в S3 выключены, и не запустит фоновые задания (напоминания, уведомления, импорт курсов). Восстановленная база получит отметку `recovery` — тестовый стенд на ней потом не запустится.

```text
APP_ENV=recovery  AUTH_PROVIDER=dev  STORAGE_PROVIDER=local  STORAGE_LOCAL_ROOT=/srv/recovery/run1/.../storage
MAIL_PROVIDER=console  MESSENGER_PROVIDER=console  TRANSLATOR_PROVIDER=<не задавать ключи>  SECRET_KEY=<новый случайный>
FILE_SCAN=off   # в recovery антивирус не обязателен: новые файлы в копию не загружаем
DATABASE_URL=postgresql+psycopg://<recovery-user>@localhost/gsho_recovery
```

Переключателя ролей вне `APP_ENV=dev` нет — вход только по паролю. Если backend не стартует с сообщением «UnsafeEnvironment», в тексте указано, какую настройку исправить.

Если копия снята с базы, где ещё нет `app_markers` (до SQL `016b`), сначала примените `backend/migrations/016b_file_scanning.sql` к восстановленной базе — recovery без отметки не запускается.

Проверить: `GET /api/health`, вход под учётной записью владельца, открытие 2–3 сделок и вложений. Исходящий трафик ВМ лучше закрыть группой безопасности.

## 4. Отчёт (вернуть в диалог)

Без ключей, URL с подписью и содержимого данных:

- дата, ID снимка, commit и схема из manifest;
- результат `sha256sum -c` (3 × OK);
- таблиц / пользователей / сделок в восстановленной БД;
- число файлов вложений;
- время восстановления (RTO) и возраст снимка (RPO);
- проверки прав restore-ключа (backup/forget → Access Denied).

После приёмки ВМ и данные удалить (13 §4.5).
