FROM python:3.12-slim
WORKDIR /app
# DEBIAN_MIRROR/PIP_INDEX_URL — для сетей, где deb.debian.org/pypi недоступны (напр. http://mirror.yandex.ru/debian)
ARG DEBIAN_MIRROR=
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN if [ -n "$DEBIAN_MIRROR" ]; then sed -i "s#http://deb.debian.org/debian#$DEBIAN_MIRROR#g" /etc/apt/sources.list.d/debian.sources; fi && apt-get update && apt-get install -y --no-install-recommends libreoffice-writer-nogui fonts-dejavu && rm -rf /var/lib/apt/lists/*
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir --index-url $PIP_INDEX_URL -r requirements.txt -c constraints.txt
COPY . .
# D-151: пользователь без привилегий; данные (/app/data) отдаются ему при старте (entrypoint.sh)
# код на сервере может быть выгружен с правами 600 (umask root) — делаем его читаемым для app; .env в образ не попадает (.dockerignore)
RUN groupadd -g 10001 app && useradd -u 10001 -g 10001 -d /home/app -m -s /usr/sbin/nologin app \
    && chmod -R a+rX,go-w /app && chmod 755 /app/entrypoint.sh
ENV PYTHONPATH=/app HOME=/home/app
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
