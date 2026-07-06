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
  export SECRET_KEY="$(cat /run/secrets/ctfd_secret_key)"
fi

if [ -f /run/secrets/ctfd_db_password ]; then
  DB_PASSWORD="$(cat /run/secrets/ctfd_db_password)"
  export DATABASE_URL="mysql+pymysql://ctfd:${DB_PASSWORD}@ctfd-db/ctfd"
fi

exec /opt/CTFd/docker-entrypoint.sh "$@"
