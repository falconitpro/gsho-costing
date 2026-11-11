# Антивирус для загрузок — включение на стенде

Код уже в системе, но проверка выключена (`FILE_SCAN=off`): файлы принимаются как раньше и помечаются «не проверялся». Ниже — как включить. Секретов здесь нет.

Ресурсы (проверено на стенде 11.11.2026): ВМ 3,8 ГБ, свободно около 2,9 ГБ, swap нет. ClamAV с базами занимает около 1,2–1,5 ГБ; контейнеру задан предел 2 ГБ, а обновление баз идёт без второй копии в памяти (`ConcurrentDatabaseReload no`).

Базы: официальный `database.clamav.net` отвечает 403 для российских адресов, поэтому `freshclam.conf` берёт их с зеркал `packages.microsoft.com/clamav` (основное) и `clamav-mirror.ru` (запасное). Базы подписаны ClamAV, freshclam проверяет подпись — зеркало не может подменить базы.

## 1. Запустить контейнер (на сервере стенда, по SSH)

```bash
cd /opt/gsho-costing
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile scanner up -d --build clamav
```

Первый запуск скачивает базы (около 250 МБ) — 5–15 минут. Ход:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile scanner exec clamav tail -n 20 /var/log/clamav/freshclam.log
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile scanner ps clamav     # дождаться (healthy)
```

## 2. Включить проверку в CRM

В `backend/.env` добавить (или изменить) строки:

```text
FILE_SCAN=clamav
CLAMD_HOST=clamav
```

и перезапустить backend:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d backend
```

## 3. Проверить

```bash
docker compose exec backend python -m scripts.scanner_check
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'
free -h
```

Ожидается: «обычный файл: clean», «тестовая сигнатура EICAR: infected», «ИТОГ: антивирус готов». Пришлите вывод в диалог — в нём нет секретов. В CRM: «Настройки → Журнал и предупреждения → Проверка файлов».

### Проверка под нагрузкой (один раз, FILE-08)

Пока идёт ручная копия (в другом окне), запустите:

```bash
docker compose exec backend python -m scripts.load_smoke
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'
free -h
```

Скрипт создаёт файлы в памяти и отправляет их только в антивирус; в базу и хранилище ничего не пишет. Ожидается «ИТОГ: PASS». Пришлите вывод в диалог.

## 4. Старые файлы (необязательно, можно позже)

```bash
docker compose exec backend python -m scripts.scan_storage --dry-run   # сколько файлов
docker compose exec backend python -m scripts.scan_storage              # проверить «не проверялся»
```

Заражённый файл переносится в карантин (вне хранилища), перестаёт выдаваться, приходит критическое предупреждение. Файлы не удаляются.

## Если антивирус перестал отвечать

Новые файлы не принимаются (сотрудник видит «Проверка временно недоступна»), приходит предупреждение «Проверка файлов недоступна». Так задумано: непроверенный файл в систему не попадает. Посмотреть: `... --profile scanner logs --tail 50 clamav`, перезапустить: `... --profile scanner restart clamav`. Временно вернуть приём без проверки — `FILE_SCAN=off` и перезапуск backend (на рабочем сервере с `APP_ENV=prod` так нельзя — backend не стартует).

## Выключить совсем

`FILE_SCAN=off` в `backend/.env`, перезапуск backend, затем `... --profile scanner stop clamav`.
