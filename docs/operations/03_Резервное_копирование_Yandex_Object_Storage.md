# Резервное копирование: Yandex Object Storage

## 1. Итоговая схема

Постоянное хранение переносится с Timeweb в **приватный бакет Yandex Object Storage**. На сервере остаются только временные staging-файлы, журнал и последний статус. Данные шифруются на сервере через `restic` до отправки. Облачное server-side encryption может быть дополнительным слоем, но не заменяет `RESTIC_PASSWORD`.

Расписание: **каждый день в 19:00 Europe/Moscow**, включая выходные. Дополнительная копия — перед обновлением. Retention: 90 daily и 12 monthly snapshots.

Важно: сначала доказывается восстановление из Яндекса, только затем отключаются копии Timeweb.

## 2. Что владелец подготавливает заранее

- доступ владельца/администратора облака Yandex Cloud;
- платёжный аккаунт и каталог, в котором будет бакет;
- отдельный приватный секретный менеджер для RESTIC_PASSWORD и restore key;
- SSH-доступ к production Timeweb для установки systemd units;
- отдельная чистая recovery-ВМ без доступа сотрудников и внешних webhooks;
- достаточное свободное место на production для staging: оценить `pg_dump` + архив вложений + запас 20%; при нехватке использовать отдельный encrypted staging disk, а не отключать копию.

Имена-примеры, не секреты:

```text
Бакет: <УНИКАЛЬНОЕ_ИМЯ_БАКЕТА>
Backup SA: sa-gsho-backup
Restore SA: sa-gsho-restore
EnvironmentFile: /etc/gsho/backup.env
Репозиторий: s3:https://storage.yandexcloud.net/<УНИКАЛЬНОЕ_ИМЯ_БАКЕТА>/gsho-prod
```

## 3. Пошаговая настройка Yandex Cloud

### Шаг 1. Создать отдельный бакет

В консоли Yandex Cloud:

1. Выбрать нужный каталог → **Object Storage** → **Создать бакет**.
2. Задать глобально уникальное имя, например с некратким суффиксом компании; имя не должно раскрывать секреты.
3. Выбрать подходящий класс хранения. Для первого контура — Standard; перевод в ледяной класс возможен только после проверки restic operations и стоимости retrieval.
4. Ограничить максимальный размер по разумному прогнозу, чтобы получить защиту от неожиданного роста расходов.
5. Не включать публичный доступ на чтение, список объектов или чтение настроек.
6. Включить **версионирование** как дополнительную защиту от случайной перезаписи/удаления.
7. Не включать Object Lock в первичном rollout. См. раздел 10.

Официальная инструкция: https://yandex.cloud/ru/docs/storage/operations/buckets/create

Для создания бакета пользователю нужна минимум роль `storage.editor`; для bucket policy через консоль/CLI — `storage.configurer`; для Object Lock — `storage.admin`. Эти права нужны администратору настройки, а не ежедневному backup-процессу.

### Шаг 2. Создать два сервисных аккаунта

Создать `sa-gsho-backup` и `sa-gsho-restore`. Не использовать личный аккаунт владельца и не назначать широкие роли на весь каталог.

В Object Storage открыть **бакет → Безопасность → Права доступа → Назначить роли**:

- `sa-gsho-backup` — `storage.editor` **только на этот бакет**;
- `sa-gsho-restore` — `storage.viewer` **только на этот бакет**.

Официальная инструкция назначения IAM-роли на бакет: https://yandex.cloud/ru/docs/storage/operations/buckets/iam-access

Почему не `storage.uploader`: restic читает репозиторий и список объектов, создаёт/удаляет locks, а `forget --prune` удаляет ненужные объекты. `storage.uploader` не разрешает удаление и поэтому недостаточен. `storage.admin` ежедневному процессу не нужен.

Если в каталоге уже есть широкие inherited bindings, проверить фактические права: назначение роли только на бакет не отменяет более широкое разрешение, выданное на каталог/облако.

### Шаг 3. Создать статические ключи

Для каждого SA создать отдельный static access key. Пример CLI:

```bash
yc iam access-key create --service-account-name sa-gsho-backup
yc iam access-key create --service-account-name sa-gsho-restore
```

Секретная часть показывается один раз. Сразу сохранить её в secret manager; не отправлять в Claude, GitHub, CRM, support ticket или screenshot.

Официальный пример создания SA и ключа: https://yandex.cloud/ru/docs/storage/tools/rclone

Ключ backup используется только на production host. Ключ restore хранится отдельно и появляется только на recovery-ВМ на время восстановления. На production restore key не нужен.

### Шаг 4. Проверить приватность и политику

- ACL не содержит `All users`/`All authenticated users` для чтения/записи;
- публичное чтение бакета, объектов и настроек закрыто;
- bucket policy не содержит `Principal: "*"` с разрешением доступа;
- ресурсы бакета и объектов различаются:

```text
arn:aws:s3:::<ИМЯ_БАКЕТА>
arn:aws:s3:::<ИМЯ_БАКЕТА>/*
```

Официальные материалы:

- https://yandex.cloud/ru/docs/storage/concepts/policy
- https://yandex.cloud/ru/docs/storage/operations/buckets/policy
- https://yandex.cloud/ru/docs/storage/security/

Prefix-level policy можно добавить позднее как hardening после негативных тестов. Не добавлять непроверенный Deny, который сломает `ListBucket`, locks или prune. Для ограничения листинга префиксом официальный механизм использует `s3:ListBucket` и условие `StringLike` для `s3:prefix`.

## 4. Установка server-side контура

### 4.1 Файлы репозитория

Добавить:

```text
deploy/backup/gsho-backup.sh
deploy/backup/gsho-backup-status.sh
deploy/backup/gsho-backup.service
deploy/backup/gsho-backup.timer
deploy/backup/gsho-backup.env.example
deploy/backup/install.md
deploy/backup/recovery-smoke.md
```

`gsho-backup.env.example` содержит только имена и пустые/angle-bracket значения. Реальный `/etc/gsho/backup.env`: владелец root, группа минимально необходимая, mode `0600`, вне repo и web root.

Переменные:

```text
RESTIC_REPOSITORY=s3:https://storage.yandexcloud.net/<БАКЕТ>/gsho-prod
AWS_ACCESS_KEY_ID=<ИЗ_SECRET_MANAGER>
AWS_SECRET_ACCESS_KEY=<ИЗ_SECRET_MANAGER>
AWS_DEFAULT_REGION=ru-central1
RESTIC_PASSWORD=<ДЛИННЫЙ_СЛУЧАЙНЫЙ_ИЗ_SECRET_MANAGER>
BACKUP_STATUS_DIR=/var/lib/gsho-backup
BACKUP_STAGING_DIR=/var/lib/gsho-backup/staging
BACKUP_COMPOSE_DIR=<КАТАЛОГ_ПРОЕКТА>
BACKUP_TIMEZONE=Europe/Moscow
```

Пароль RESTIC должен быть независимым длинным случайным значением. Потеря пароля означает невозможность расшифровать копии даже при доступе к бакету. Проверить, что секрет доступен владельцу при полной потере production.

### 4.2 Алгоритм `gsho-backup.sh`

Скрипт должен быть проверен `shellcheck` и `bash -n` и реализовывать:

1. `#!/usr/bin/env bash`, `set -Eeuo pipefail`, `umask 077`;
2. `flock -n` на отдельном lock file; повторный запуск завершается с понятным `already_running`;
3. trap на ошибку: sanitized status `failed`, удаление staging, сохранение exit code;
4. создание нового staging `mktemp -d` внутри ограниченного каталога;
5. чтение версии приложения/change set без вывода env;
6. `pg_dump --format=custom` из контейнера/PostgreSQL с использованием существующего protected DB auth, без пароля в командной строке;
7. `pg_restore --list` для проверки читаемости custom dump;
8. архивирование локальных файлов; если provider не local — использовать отдельный проверенный адаптер либо завершиться `unsupported_storage`, не создавать ложный success;
9. SHA-256 каждого артефакта и записать manifest;
10. `restic backup` staging с тегами reason/date/commit; не логировать секреты;
11. получить snapshot ID через `restic snapshots --json --latest 1` и сверить успех;
12. `restic check` по принятому режиму: быстрый metadata check после run, периодический более глубокий check с `--read-data-subset` по отдельному расписанию/окну;
13. `restic forget --keep-daily 90 --keep-monthly 12 --prune` только после успешной копии;
14. атомарно записать `last-run.json.tmp` → `fsync` → `mv`;
15. best-effort отправить sanitized status в backend internal writer; недоступность БД не меняет успех S3 backup на failed, но отмечается `status_sync_pending`;
16. удалить staging через trap после успешного завершения;
17. exit 0 только при фактически завершённых обязательных шагах.

Не добавлять `RESTIC_PASSWORD` в `ps`, URL, JSON logs или командную строку. Не использовать `env`/`set -x` в production run.

### 4.3 Systemd service и timer

`gsho-backup.service`:

- `Type=oneshot`;
- `EnvironmentFile=/etc/gsho/backup.env`;
- `ExecStart=/usr/local/sbin/gsho-backup.sh --reason=daily`;
- разумные `TimeoutStartSec`, `Nice`, `IOSchedulingClass` при необходимости;
- `NoNewPrivileges=true`, `PrivateTmp=true` и другие hardening directives только после проверки доступа к Docker socket/volumes; не делать service неработоспособным декоративным sandbox;
- логи journald, без echo secrets.

`gsho-backup.timer`:

```ini
[Unit]
Description=Daily encrypted GSHO CRM backup to Yandex Object Storage

[Timer]
OnCalendar=*-*-* 19:00:00
Timezone=Europe/Moscow
Persistent=true
RandomizedDelaySec=0
Unit=gsho-backup.service

[Install]
WantedBy=timers.target
```

Проверки:

```bash
sudo systemd-analyze calendar '*-*-* 19:00:00' --timezone=Europe/Moscow
sudo systemctl daemon-reload
sudo systemctl enable --now gsho-backup.timer
systemctl list-timers gsho-backup.timer
```

Ручной первый запуск выполняет владелец: `sudo systemctl start gsho-backup.service`. Для pre-deploy отдельный вызов script с `--reason=pre_deploy`, не изменяя timer.

## 5. Интеграция со статусом CRM

На host сохранять `last-run.json` с полями из `BackupRun`. Backend получает его через:

- предпочтительно read-only mount отдельного status directory и периодический parser с JSON schema/size limit; либо
- internal status writer на loopback/защищённой сети с отдельным rotating token и без права выполнять команды.

Нельзя давать публичный API произвольной записи статуса. UI использует последний host status и не называет его свежим, если файл устарел. При полном падении сервера in-app alert тоже недоступен; рекомендуется внешний dead-man’s switch/heartbeat в следующем эксплуатационном улучшении, но в этом пакете он не выдаётся за реализованный.

## 6. Переход от старого Compose backup

Текущий сервис `backup` создаёт ежедневный `pg_dump`, архив файлов и оставляет их в volume `backups` до 90 дней. Он запускается от момента старта контейнера и не гарантирует 19:00.

Во время настройки:

1. Оставить старый `backup` работающим.
2. Настроить Яндекс и выполнить ручную копию.
3. Не удалять старые архивы, пока не пройден restore gate.

После gate:

1. Удалить service `backup` из `docker-compose.yml` в отдельном commit/PR.
2. Пересоздать только нужные Compose services без `-v` и без удаления volumes.
3. В панели Timeweb владелец вручную отключает платные/provider backups, если они включены. Код не управляет панелью.
4. Убедиться, что Яндекс timer продолжает работать после перезагрузки.
5. Старые архивы в Docker volume удалить контролируемо по отдельному runbook и явной команде владельца, не раньше дополнительного срока страховки, согласованного владельцем. `pgdata`, `storage` и рабочие volumes не трогать.
6. В документации изменить: постоянное хранилище — Yandex; локальный volume `backups` больше не используется для постоянных копий.

## 7. Обязательный restore gate

Отключение разрешено только при выполнении всех пунктов:

- [ ] manual backup успешно загружен и виден в `restic snapshots`;
- [ ] минимум две последовательные ежедневные копии успешны, без ручной подмены статуса;
- [ ] `restic check` успешен;
- [ ] manifest и SHA-256 артефактов проверены;
- [ ] чистая recovery-ВМ создана отдельно;
- [ ] восстановлена PostgreSQL в отдельный новый cluster, production DSN не используется;
- [ ] вложения восстановлены в отдельный volume, hashes совпали;
- [ ] health/read-only smoke checks успешны;
- [ ] mail, WeCom, LLM, public ingress, deploy timers отключены;
- [ ] recovery credentials и production backup writer отсутствуют;
- [ ] отчёт восстановления сохранён без секретов;
- [ ] владелец явно написал подтверждение отключения Timeweb.

## 8. Краткий сценарий восстановления

Полный runbook — `13`. Базовая последовательность:

1. На recovery-ВМ установить pinned versions Docker/PostgreSQL/restic, настроить отдельный env с restore SA и RESTIC_PASSWORD.
2. `restic snapshots`, выбрать конкретный проверенный snapshot, не обязательно latest.
3. `restic restore <SNAPSHOT> --target <RECOVERY_DIR>`.
4. Проверить manifest SHA-256, `pg_restore --list`.
5. Создать новый PostgreSQL 16 cluster и восстановить custom dump через `pg_restore` с параметрами, не затрагивающими другой сервер.
6. Распаковать вложения в новый recovery volume.
7. Применить недостающие SQL change set’ы в контролируемом порядке; записать версию.
8. Запустить backend/frontend в `APP_ENV=recovery`, без внешних интеграций.
9. Проверить schema/table counts, attachments hashes, health и синтетический smoke.
10. После отчёта уничтожить recovery-ВМ/данные по отдельной процедуре, не оставляя production copy в общей среде.

Запрещено `docker compose down -v` на production и восстановление поверх рабочего PostgreSQL для «проверки».

## 9. Pre-deploy backup

`deploy/deploy-remote.sh` меняется поэтапно:

- до настройки: feature flag `BACKUP_REQUIRED=false` либо явный отсутствующий hook не блокирует текущий deploy;
- после restore gate: `BACKUP_REQUIRED=true`; hook вызывает проверенный host script `--reason=pre_deploy`, ждёт успеха и проверяет свежий status;
- при неудаче обязательной копии deploy останавливается до `git reset`/build/migration;
- безусловный `docker compose exec … python -m scripts.seed >/dev/null || true` удаляется из production flow. Synthetic seed запускается только явно в dev;
- migrations не запускаются автоматически вместе с build. Отдельная команда владельца после backup/rehearsal.

Нельзя имитировать pre-deploy backup простым созданием локального tar: постоянная копия должна попасть в Яндекс.

## 10. Versioning, lifecycle и Object Lock

- Версионирование включено для дополнительной защиты, но restic уже управляет собственной структурой репозитория.
- Lifecycle текущих объектов restic не задавать произвольным сроком «90 дней»: это может разрушить более старые monthly snapshots.
- Правила для неактуальных версий/incomplete multipart вводить после теста с `forget --prune`, издержек и восстановления.
- Object Lock включается только в версионируемом бакете. Заблокированную версию нельзя удалить до окончания блокировки, поэтому `restic forget --prune` может накапливать объекты или завершаться ошибкой.
- Если нужен ransomware hardening: отдельный тестовый bucket/repository, согласованные retention/lock сроки, тест prune, расчёт стоимости и повторный restore. Не экспериментировать на единственной production-копии.

Инструкции:

- https://yandex.cloud/ru/docs/storage/operations/buckets/configure-object-lock
- https://yandex.cloud/ru/docs/storage/operations/buckets/lifecycles

## 11. Негативные проверки доступа

Выполнить с безопасным тестовым объектом внутри backup prefix:

- restore SA может читать необходимые объекты и не может записывать/удалять;
- backup SA не может менять IAM, bucket policy, ACL и создавать другие бакеты;
- оба SA не имеют доступа к другим бакетам;
- анонимный GET объекта и list bucket отклонены;
- неверный secret даёт failed backup, UI показывает sanitized error;
- отсутствие сети/S3 не оставляет status success;
- нехватка staging space завершается failed и не удаляет прежние snapshots;
- повторный одновременный запуск блокируется lock’ом;
- ошибка prune не отменяет факт успешно созданного snapshot, но требует отдельного warning/failed retention статуса и не должна скрываться.

## 12. Эксплуатационные показатели

- последний успешный daily snapshot моложе 26 часов;
- последний check успешен;
- минимум ежеквартальный restore drill, для первичного перехода — обязательный;
- ключи backup и restore разные; rotation проверена;
- RESTIC_PASSWORD доступен вне production;
- свободное место staging и бакета контролируется;
- отчёты не содержат secrets, signed URLs и production-данных.
