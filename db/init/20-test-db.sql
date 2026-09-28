-- Separate database for the integration test tier.
-- Runs on first init of the db volume, and again (idempotently) from `make test-integration`.
SELECT 'CREATE DATABASE transit_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'transit_test')\gexec

\connect transit_test
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
