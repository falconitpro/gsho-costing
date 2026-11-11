#!/bin/sh
# Первый запуск: скачать базы (около 250 МБ), затем обновлять 4 раза в сутки; clamd — основной процесс контейнера.
set -eu
chown -R clamav:clamav /var/lib/clamav /run/clamav /var/log/clamav
if [ ! -s /var/lib/clamav/main.cvd ] && [ ! -s /var/lib/clamav/main.cld ]; then
  until freshclam --config-file=/etc/clamav/freshclam.conf --foreground; do
    echo "freshclam: базы не скачались, повтор через 60 с"; sleep 60
  done
fi
freshclam --config-file=/etc/clamav/freshclam.conf --daemon
exec clamd --config-file=/etc/clamav/clamd.conf
