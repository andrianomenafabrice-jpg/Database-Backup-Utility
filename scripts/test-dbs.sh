#!/usr/bin/env bash
set -euo pipefail
PG=dbbackup-pg; MYSQL=dbbackup-mysql; MONGO=dbbackup-mongo

wait_ready() {
  until docker exec "$PG" pg_isready -h 127.0.0.1 -U postgres -d testdb >/dev/null 2>&1; do sleep 1; done
  echo "  PostgreSQL prêt"
  until docker exec -e MYSQL_PWD=secret "$MYSQL" mysqladmin ping -h 127.0.0.1 -uroot --silent >/dev/null 2>&1; do sleep 2; done
  echo "  MySQL prêt"
  until docker exec "$MONGO" mongosh --quiet -u root -p secret --authenticationDatabase admin --eval "db.runCommand({ping:1}).ok" >/dev/null 2>&1; do sleep 2; done
  echo "  MongoDB prêt"
  sleep 2
}

up() {
  docker rm -f "$PG" "$MYSQL" "$MONGO" >/dev/null 2>&1 || true
  docker run -d --name "$PG" -e POSTGRES_PASSWORD=secret -e POSTGRES_DB=testdb -p 5433:5432 postgres:16 >/dev/null
  docker run -d --name "$MYSQL" -e MYSQL_ROOT_PASSWORD=secret -e MYSQL_DATABASE=testdb -p 3307:3306 mysql:8 >/dev/null
  docker run -d --name "$MONGO" -e MONGO_INITDB_ROOT_USERNAME=root -e MONGO_INITDB_ROOT_PASSWORD=secret -p 27018:27017 mongo:7 >/dev/null
  echo "Conteneurs lancés, attente de la disponibilité..."
  wait_ready
  echo "Tout est prêt."
}

down() {
  docker rm -f "$PG" "$MYSQL" "$MONGO" >/dev/null 2>&1 || true
  echo "Conteneurs supprimés."
}

case "${1:-}" in
  up) up ;;
  wait) wait_ready ;;
  down) down ;;
  *) echo "Usage : bash scripts/test-dbs.sh {up|wait|down}"; exit 1 ;;
esac
