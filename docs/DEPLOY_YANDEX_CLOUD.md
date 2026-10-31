# Тестовый стенд в Yandex Cloud

Результат: `https://<домен>` с HTTPS, закрытый паролем, автоматически обновляется при каждом push в `main` после зелёных тестов.
Стоимость: ВМ 2 vCPU / 4 ГБ / 30 ГБ SSD, прерываемая не нужна — около 1 500–2 500 ₽/мес (зависит от типа ядер); публичный IP ~ 200 ₽/мес.

## 1. Создать ВМ (консоль Yandex Cloud, 5 минут)

Compute Cloud → Создать ВМ:

| Параметр | Значение |
| --- | --- |
| Образ | Ubuntu 24.04 LTS |
| Платформа | Intel Ice Lake (standard-v3), 2 vCPU, 20 % гарантированной доли хватит для теста (дешевле); для боевого — 100 % |
| RAM | 4 ГБ |
| Диск | SSD 30 ГБ |
| Сеть | публичный IP **статический** (иначе сменится при перезапуске) |
| Доступ | логин `ubuntu`, ваш SSH-ключ (`cat ~/.ssh/id_ed25519.pub` на Mac; если ключа нет — `ssh-keygen -t ed25519`) |
| Группа безопасности | входящие TCP 22, 80, 443 |

Домен: в DNS (там, где ведётся berghaus.ru) добавить A-запись `test` → публичный IP ВМ. Пока запись не разойдётся (обычно минуты), Let's Encrypt не выдаст сертификат — поэтому DNS делаем первым шагом.

## 2. Ключ для автодеплоя (на Mac)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/gsho_deploy -N "" -C deploy@github
cat ~/.ssh/gsho_deploy.pub     # публичный — передаётся скрипту на сервере
cat ~/.ssh/gsho_deploy         # приватный — в секрет GitHub (шаг 4)
```

## 3. Настроить сервер одной командой

```bash
ssh ubuntu@<IP>
curl -fsSL https://raw.githubusercontent.com/falconitpro/gsho-costing/main/deploy/yandex-cloud-setup.sh | sudo bash -s -- \
  --domain test.berghaus.ru --email admin@berghaus.ru --password 'ПарольДляВходаНаСтенд' \
  --deploy-key "$(cat <<'K'
ssh-ed25519 AAAA...вставить публичный ключ из шага 2... deploy@github
K
)"
```

Скрипт ставит Docker, создаёт пользователя `deploy`, клонирует репозиторий в `/opt/gsho-costing`, генерирует пароль PostgreSQL и секреты, открывает 80/443, собирает и запускает стенд, заполняет dev-данные. Через ~5 минут: `https://test.berghaus.ru` (логин `berghaus`, пароль — из команды).

Если репозиторий приватный, перед запуском добавьте на сервер deploy key GitHub или используйте `--repo https://<token>@github.com/falconitpro/gsho-costing.git`.

## 4. Автодеплой из GitHub

GitHub → репозиторий → Settings → Secrets and variables → Actions → New repository secret:

| Секрет | Значение |
| --- | --- |
| `DEPLOY_HOST` | публичный IP или `test.berghaus.ru` |
| `DEPLOY_SSH_KEY` | содержимое приватного ключа `~/.ssh/gsho_deploy` целиком |

Дальше каждый push в `main`: тесты backend (с PostgreSQL) и сборка фронта → при успехе job `deploy` заходит на сервер и выполняет `deploy/deploy-remote.sh` (git pull, пересборка backend/frontend, seed). Ход виден во вкладке Actions. Без секретов job просто пропускается.

## 5. Эксплуатация

- Логи: `ssh ubuntu@<IP>`, затем `cd /opt/gsho-costing && docker compose logs -f backend`.
- Перезапуск вручную: `bash deploy/deploy-remote.sh` от пользователя `deploy`.
- Копии: контейнер `backup` ежедневно пишет дамп БД и архив файлов в том `backups` (90 дней). Для стенда достаточно; для боевого контура — выгружать дампы в Yandex Object Storage (адаптер S3 уже в проекте).
- Сброс тестовых данных: `docker compose down -v && bash deploy/deploy-remote.sh` (удалит базу и файлы, seed создаст заново).
- Смена пароля стенда: `docker run --rm caddy:2 caddy hash-password --plaintext 'новый'` → заменить `BASIC_AUTH_HASH` в `/opt/gsho-costing/.env` (каждый `$` в хеше удвоить: `$$`) → `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d caddy`.

## 6. Боевой контур (позже)

Отдельная ВМ или та же с другим доменом (`costing.berghaus.ru`): `AUTH_PROVIDER=entra`, `STORAGE_PROVIDER=sharepoint`, `MAIL_PROVIDER=outlook` в `backend/.env`, `APP_ENV=prod` (без dev-переключателя ролей и без seed), basic auth убрать, Managed PostgreSQL вместо контейнера, Object Storage для копий. 152-ФЗ: зона ru-central1 — данные в РФ.
