#!/bin/sh
set -eu

# Restrict default file permissions for any files created at runtime.
umask 027

case "${RUN_COLLECTSTATIC:-true}" in
    true) python manage.py collectstatic --noinput ;;
    false) ;;
    *) echo "RUN_COLLECTSTATIC must be true or false" >&2; exit 1 ;;
esac
case "${RUN_MIGRATIONS:-true}" in
    true) python manage.py migrate --noinput ;;
    false) ;;
    *) echo "RUN_MIGRATIONS must be true or false" >&2; exit 1 ;;
esac

if [ "$#" -gt 0 ]; then
	exec "$@"
fi

exec gunicorn request_hub.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 4 \
    --threads 2 \
    --timeout 120 \
    --worker-class gthread \
    --max-requests 1000 \
    --max-requests-jitter 100
