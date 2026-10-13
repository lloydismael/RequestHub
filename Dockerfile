FROM python:3.14.8-alpine3.24@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72

ARG APP_VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Request Hub" \
      org.opencontainers.image.description="Django request and activity management portal" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/lloydismael/request-hub"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VERSION=${APP_VERSION}

ARG APP_UID=10001
ARG APP_GID=10001

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates tini \
    && python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -r requirements.lock \
    && python -m pip check \
    && rm -rf \
        /usr/local/bin/pip* \
        /usr/local/bin/wheel \
        /usr/local/lib/python3.12/site-packages/_distutils_hack \
        /usr/local/lib/python3.12/site-packages/distutils-precedence.pth \
        /usr/local/lib/python3.12/site-packages/pip \
        /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.12/site-packages/pkg_resources \
        /usr/local/lib/python3.12/site-packages/setuptools \
        /usr/local/lib/python3.12/site-packages/setuptools-*.dist-info \
        /usr/local/lib/python3.12/site-packages/wheel \
        /usr/local/lib/python3.12/site-packages/wheel-*.dist-info

COPY . .

RUN addgroup -S -g "${APP_GID}" app \
    && adduser -S -D -u "${APP_UID}" -G app -h /app app \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R app:app /app \
    && chmod +x /app/entrypoint.sh

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=4)"

ENTRYPOINT ["/sbin/tini", "--", "/app/entrypoint.sh"]
