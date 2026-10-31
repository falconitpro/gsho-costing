FROM python:3.12-slim
WORKDIR /app
# DEBIAN_MIRROR/PIP_INDEX_URL — для сетей, где deb.debian.org/pypi недоступны (напр. http://mirror.yandex.ru/debian)
ARG DEBIAN_MIRROR=
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN if [ -n "$DEBIAN_MIRROR" ]; then sed -i "s#http://deb.debian.org/debian#$DEBIAN_MIRROR#g" /etc/apt/sources.list.d/debian.sources; fi && apt-get update && apt-get install -y --no-install-recommends libreoffice-writer-nogui fonts-dejavu && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url $PIP_INDEX_URL -r requirements.txt
COPY . .
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
