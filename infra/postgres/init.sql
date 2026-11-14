-- Runs once, on first initialisation of the Postgres data volume only.
-- Schema lives in Alembic migrations; this file is strictly for things that
-- must exist before migrations run.

-- Kept in sync with the baseline migration so a fresh container and a fresh
-- `alembic upgrade head` converge on the same state either way.
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
