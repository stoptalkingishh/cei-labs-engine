#!/bin/sh
# docker/ctfd/docker-entrypoint-wrapper.sh
#
# CTFd's upstream image (ENTRYPOINT /opt/CTFd/docker-entrypoint.sh) reads plain
# env vars (SECRET_KEY, DATABASE_URL, ...). Docker Swarm secrets are mounted as
# files under /run/secrets/<name> instead, so this wrapper turns each mounted
# secret file into the env var CTFd/the DB actually expect, then hands off to
# the real entrypoint unmodified.
set -eu

if [ -f /run/secrets/ctfd_secret_key ]; then
  # Assign, then export, as a separate step: `export X="$(cmd)"` declares the
  # variable as part of an assignment whose exit status is the command's, so a
  # failing `cat` is masked by the export succeeding and CTFd then starts with
  # an empty SECRET_KEY. The password below already took this form.
  SECRET_KEY="$(cat /run/secrets/ctfd_secret_key)"
  export SECRET_KEY
fi

if [ -f /run/secrets/ctfd_db_password ]; then
  DB_PASSWORD="$(cat /run/secrets/ctfd_db_password)"
  export DATABASE_URL="mysql+pymysql://ctfd:${DB_PASSWORD}@ctfd-db/ctfd"
fi

exec /opt/CTFd/docker-entrypoint.sh "$@"
