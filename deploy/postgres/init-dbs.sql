-- Idempotent PostgreSQL database initialization for tomori (#141).
-- Automatically executed on first startup via /docker-entrypoint-initdb.d/01-init-dbs.sql.
-- Safe to re-run manually:
--   psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 25432 -U tomori -d tomori_dev -f deploy/postgres/init-dbs.sql

SELECT format('CREATE DATABASE %I', 'tomori_dev')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'tomori_dev')\gexec

SELECT format('CREATE DATABASE %I', 'tomori_test')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'tomori_test')\gexec
