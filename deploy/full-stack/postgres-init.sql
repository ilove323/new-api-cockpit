-- Fresh PostgreSQL data directory only. Secrets come from postgres.env.
\set ON_ERROR_STOP on
\getenv source_password SOURCE_DB_PASSWORD
\getenv reader_password READER_DB_PASSWORD
\getenv monitor_password MONITOR_DB_PASSWORD
CREATE ROLE newapi_owner LOGIN PASSWORD :'source_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE cockpit_reader LOGIN PASSWORD :'reader_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE cockpit_monitor LOGIN PASSWORD :'monitor_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE DATABASE new_api OWNER newapi_owner;
REVOKE CONNECT ON DATABASE new_api FROM PUBLIC;
GRANT CONNECT ON DATABASE new_api TO newapi_owner, cockpit_reader;
CREATE DATABASE new_api_cockpit OWNER cockpit_monitor;
REVOKE CONNECT ON DATABASE new_api_cockpit FROM PUBLIC;
GRANT CONNECT ON DATABASE new_api_cockpit TO cockpit_monitor;
