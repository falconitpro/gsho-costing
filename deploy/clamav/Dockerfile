# Антивирус для проверки загрузок (docs/operations/04 §8.2, D-049). Та же база, что у backend (python:3.12-slim) —
# отдельный образ из Docker Hub не нужен; пакеты — с зеркала Debian (DEBIAN_MIRROR, как у backend).
FROM python:3.12-slim
ARG DEBIAN_MIRROR=
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
      sed -i "s#http://deb.debian.org/debian#${DEBIAN_MIRROR}#g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; fi \
 && apt-get update && apt-get install -y --no-install-recommends clamav-daemon clamav-freshclam ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/clamav /var/lib/clamav /var/log/clamav && chown -R clamav:clamav /run/clamav /var/lib/clamav /var/log/clamav
COPY clamd.conf freshclam.conf /etc/clamav/
COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh
EXPOSE 3310
HEALTHCHECK --interval=60s --timeout=10s --start-period=900s --retries=3 CMD clamdscan --config-file=/etc/clamav/clamd.conf --ping 3 || exit 1
ENTRYPOINT ["/entrypoint.sh"]
