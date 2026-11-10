# Установка резервного копирования в Yandex Object Storage

Пошагово для владельца. Полное описание — `docs/operations/03_Резервное_копирование_Yandex_Object_Storage.md`, восстановление — `recovery-smoke.md` и `docs/operations/13` §4. Старый compose-сервис `backup` во время установки **не трогать** (D-007).

Что нужно заранее: доступ к консоли Yandex Cloud, SSH на сервер стенда (`/opt/gsho-costing`), защищённое хранилище для секретов (D-024).

## 1. Yandex Cloud (консоль, 15–20 минут)

1. **Object Storage → Создать бакет.** Имя — уникальное, без секретов (например `berghaus-gsho-backup-<суффикс>`). Класс — Standard. Доступ на чтение объектов, список объектов и чтение настроек — **закрытый**. Максимальный размер — разумный лимит (например 50 ГБ).
2. В бакете: **Версионирование → включить.** Object Lock **не включать** (03 §10).
3. **Сервисные аккаунты → создать** `sa-gsho-backup` и `sa-gsho-restore`. Ролей на каталог не назначать.
4. В бакете: **Безопасность → Права доступа → Назначить роли**:
   - `sa-gsho-backup` → `storage.editor` (только на этот бакет);
   - `sa-gsho-restore` → `storage.viewer` (только на этот бакет).
5. Для каждого SA: **Создать новый ключ → Статический ключ доступа.** Секретная часть показывается один раз — сразу в хранилище. Ключ restore на сервер стенда **не кладётся**.
6. Сгенерировать пароль restic (на своём компьютере): `openssl rand -base64 48` — и тоже сразу в хранилище.

В диалог, тикеты и скриншоты ключи и пароль не попадают.

## 2. Сервер стенда (SSH, 10 минут)

```bash
# restic (Ubuntu/Debian); проверить версию — нужна 0.16 или новее
sudo apt-get update && sudo apt-get install -y restic
restic version

# скрипты и unit-файлы из репозитория
cd /opt/gsho-costing
sudo install -m 0750 deploy/backup/gsho-backup.sh /usr/local/sbin/gsho-backup.sh
sudo install -m 0750 deploy/backup/gsho-backup-status.sh /usr/local/sbin/gsho-backup-status.sh
sudo install -m 0644 deploy/backup/gsho-backup.service deploy/backup/gsho-backup.timer /etc/systemd/system/

# каталоги: статус читает CRM (без секретов), staging — временный
sudo install -d -m 0755 /var/lib/gsho-backup /var/lib/gsho-backup/status
sudo install -d -m 0700 /var/lib/gsho-backup/staging

# секреты: файл создаётся редактором, НЕ через echo (не попадёт в history)
sudo install -d -m 0700 /etc/gsho
sudo cp deploy/backup/gsho-backup.env.example /etc/gsho/backup.env
sudo chmod 0600 /etc/gsho/backup.env
sudo nano /etc/gsho/backup.env     # вписать имя бакета, пароль restic и ключ sa-gsho-backup
```

## 3. Инициализация и первая копия

```bash
# инициализация репозитория (один раз); env читается из файла, в командной строке секретов нет
sudo bash -c 'set -a; . /etc/gsho/backup.env; set +a; restic init'

# проверка расписания: ожидается «Next elapse: … 19:00:00 MSK»
systemd-analyze calendar '*-*-* 19:00:00 Europe/Moscow'

sudo systemctl daemon-reload
sudo systemctl enable --now gsho-backup.timer
systemctl list-timers gsho-backup.timer

# первая ручная копия (то же, что сделает таймер в 19:00)
sudo systemctl start gsho-backup.service
journalctl -u gsho-backup.service -n 20 --no-pager
sudo gsho-backup-status.sh --check
```

В CRM статус появится после выката этого PR: **Настройки → Резервные копии** (видят ЗГД и администратор). Backend читает `/var/lib/gsho-backup/status` только для чтения (`docker-compose.prod.yml`).

**Вернуть в диалог** вывод `sudo gsho-backup-status.sh --check`: там только даты, ID снимков, размеры и статусы.

## 4. Обязательная копия перед деплоем — только после restore gate

До прохождения gate (две ежедневные копии подряд + восстановление на отдельной ВМ, 03 §7) деплой работает как раньше. После gate:

```bash
# deploy-пользователю разрешить ровно одну команду без пароля
echo 'deploy ALL=(root) NOPASSWD: /usr/local/sbin/gsho-backup.sh --reason=pre_deploy' | sudo tee /etc/sudoers.d/gsho-backup >/dev/null
sudo chmod 0440 /etc/sudoers.d/gsho-backup && sudo visudo -c

# включить обязательную копию: деплой остановится до git reset, если копия не удалась
echo 'BACKUP_REQUIRED=true' | sudo tee /etc/gsho/deploy.env >/dev/null
```

Если автодеплой (`autodeploy.sh`) запускается от root, sudoers не нужен.

## 5. Проверки прав бакета (03 §11, BKP-08)

С ключом **restore** на recovery-ВМ: `restic snapshots` работает, `restic backup` и `restic forget` — `Access Denied`. Анонимно: `curl -sI https://storage.yandexcloud.net/<ИМЯ_БАКЕТА>/` → `403`. Результаты — в отчёт без ключей.

## 6. Что не делать

- не удалять compose-сервис `backup` и volume `backups` до gate и отдельного подтверждения;
- не отключать копии в панели Timeweb до gate;
- не выполнять `docker compose down -v`;
- не включать Object Lock на единственном production-репозитории;
- не хранить ключ restore на сервере стенда.
