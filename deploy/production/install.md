# Рабочий сервер: установка

Отдельный сервер для настоящих данных. Стенд `crm.dobrov.pro` остаётся тестовым и обновляется сам; рабочий сервер обновляется только вручную (раздел 9).

Правила:
- секреты создаются на сервере и хранятся в защищённой заметке; в чат и в репозиторий не попадают;
- ключи и пароли стенда на рабочем сервере не используются — для копий нужен отдельный бакет и отдельные сервисные аккаунты;
- до восстановления копии на чистой ВМ (шаг 8) настоящие данные не вносятся.

## 1. Сервер и адрес

- Ubuntu 24.04, 2 ядра, **не меньше 4 ГБ памяти** (антивирус занимает около 1 ГБ), диск от 50 ГБ. Спокойнее 8 ГБ.
- Открыты порты 22, 80, 443.
- DNS: A-запись адреса рабочей системы → IP сервера. Сделать до шага 2: сертификат выпускается при первом запуске.

## 2. Настройка сервера

```bash
curl -fsSL https://raw.githubusercontent.com/falconitpro/gsho-costing/main/deploy/production/setup.sh -o /root/setup.sh
bash /root/setup.sh --domain crm.example.ru --email admin@example.ru
```

Скрипт создаёт файл подкачки 2 ГБ, ставит Docker, скачивает код в `/opt/gsho-costing`, создаёт настройки рабочего режима со своими секретами, собирает образы и запускает базу и антивирус. CRM он **не** запускает и демо-данные не добавляет.

Сразу сохраните секреты в защищённую заметку:

```bash
sudo cat /opt/gsho-costing/.env
sudo grep -E '^(SECRET_KEY|MFA_ENCRYPTION_KEY)=' /opt/gsho-costing/backend/.env
```

Без `MFA_ENCRYPTION_KEY` после потери сервера коды из приложения у всех сотрудников перестанут работать.

## 3. Антивирус

```bash
cd /opt/gsho-costing
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile scanner ps clamav
```

Дождитесь `healthy` (первый запуск скачивает базы, 3–10 минут).

## 4. База

Новая пустая база:

```bash
sudo bash /opt/gsho-costing/deploy/production/init-db.sh
```

Скрипт создаёт таблицы, применяет SQL `001…019` по порядку, добавляет справочники без демо-данных (схемы оплаты, заготовки компаний группы, маршруты) и печатает проверку. На базе, где уже есть сотрудники, он останавливается.

Перенос данных со стенда — отдельная инструкция (готовится): база стенда восстанавливается из копии и очищается от тестовых данных, затем `init-db.sh --existing`.

## 5. Первый пользователь и запуск

```bash
cd /opt/gsho-costing
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm --no-deps backend \
  python -m scripts.prod_init admin --email ВАШ_АДРЕС --name "Фамилия Имя"
```

Команда один раз печатает временный пароль. Затем запуск:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile scanner up -d backend frontend caddy clamav
curl -s https://АДРЕС/api/health
```

Ожидается `"env":"prod","auth":"password","file_scan":"clamav"`. Если backend не стартует, причина названа в журнале: `docker compose logs backend | tail -20`.

Войдите с временным паролем — система потребует задать свой. Затем «Настройки → Безопасность»: включите код из приложения и сохраните резервные коды.

## 6. Копии

По `deploy/backup/install.md`, с **новым** бакетом, новыми сервисными аккаунтами и новым паролем шифрования. Затем:

```bash
sudo systemctl start gsho-backup.service && sudo gsho-backup-status.sh --check
sudo sed -i 's/^BACKUP_REQUIRED=.*/BACKUP_REQUIRED=true/' /etc/gsho/deploy.env
```

## 7. Проверки

```bash
cd /opt/gsho-costing
docker compose exec backend python -m scripts.prod_init check      # «ИТОГ: готово к запуску»
docker compose exec backend python -m scripts.scanner_check        # «ИТОГ: антивирус готов»
docker compose exec backend python -m scripts.load_smoke           # «ИТОГ: PASS»
```

В браузере: на странице входа нет ссылки Microsoft 365; в шапке нет переключателя «Войти как»; загрузка обычного файла проходит.

## 8. Восстановление

По `deploy/backup/recovery-smoke.md` — копия рабочего сервера восстанавливается на чистой ВМ. Пока не пройдено, сотрудников не зовём.

## 9. Обновление

Только вручную и только версией, проверенной на стенде:

```bash
sudo bash /opt/gsho-costing/deploy/production/update.sh
```

Скрипт показывает, что изменится, спрашивает подтверждение, делает копию, применяет новые SQL, обновляет версию и печатает проверку. Если копия не создана — ничего не меняется.

## 10. Чего на рабочем сервере быть не должно

| Что | Как проверить |
|---|---|
| Таймер автоматического обновления | `systemctl list-timers \| grep gsho-autodeploy` — пусто |
| Старый контейнер локальных копий | `docker compose ps backup` — пусто |
| Демо-пользователи | «Настройки → Сотрудники»: нет адресов `@example.com` |
| Тестовый вход | `/api/health` показывает `"auth":"password"` |
