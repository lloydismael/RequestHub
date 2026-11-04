FROM python:3.12.13-alpine3.24@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

ARG APP_UID=10001
ARG APP_GID=10001

WORKDIR /app

# Per-build values (version, commit, date) are declared at the end of the file so
# they do not invalidate the cached dependency layers below on every commit.
COPY requirements.txt requirements.lock ./
RUN python -m pip install --upgrade pip setuptools wheel \
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

# OS packages are refreshed on every build so security fixes are never served from
# cache. Passing a new APK_REFRESH value invalidates only this layer and later ones.
ARG APK_REFRESH=manual
RUN echo "Refreshing OS packages (${APK_REFRESH})" \
    && apk upgrade --no-cache \
    && apk add --no-cache ca-certificates tini

RUN addgroup -S -g "${APP_GID}" app \
    && adduser -S -D -u "${APP_UID}" -G app -h /app app \
    && mkdir -p /app/staticfiles /app/media \
    && chown app:app /app /app/staticfiles /app/media

COPY --chown=app:app . .
RUN chmod +x /app/entrypoint.sh

ARG APP_VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Request Hub" \
      org.opencontainers.image.description="Django request and activity management portal" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/lloydismael/request-hub"

ENV APP_VERSION=${APP_VERSION} \
    APP_REVISION=${VCS_REF}

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; request = urllib.request.Request('http://127.0.0.1:8000/healthz/', headers={'X-Forwarded-Proto': 'https'}); urllib.request.urlopen(request, timeout=4)"

ENTRYPOINT ["/sbin/tini", "--", "/app/entrypoint.sh"]
