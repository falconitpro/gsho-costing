#!/bin/sh
# D-151 (аудит 22.11.2026 п. 4.1): приложение работает не от root.
# Контейнер стартует от root только для того, чтобы отдать пользователю app каталоги данных
# (тома, созданные прежними версиями, принадлежат root), и сразу передаёт управление пользователю app.
set -eu
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/data/storage /app/data/quarantine /home/app
  for d in /app/data /home/app; do
    if [ "$(stat -c %u "$d")" != "10001" ] || [ -n "$(find "$d" -maxdepth 3 ! -user 10001 -print -quit 2>/dev/null)" ]; then
      chown -R 10001:10001 "$d"
    fi
  done
  exec setpriv --reuid=10001 --regid=10001 --init-groups -- "$@"
fi
exec "$@"
