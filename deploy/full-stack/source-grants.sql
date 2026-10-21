-- Run as newapi_owner in new_api AFTER New API creates its source tables.
-- Pipe sql/source_pat_function.sql before this file in the same psql session.
\set ON_ERROR_STOP on
GRANT USAGE ON SCHEMA public TO cockpit_reader;
GRANT SELECT ON TABLE public.users, public.tokens, public.logs, public.options, public.channels TO cockpit_reader;
GRANT SELECT ("group", model, enabled, channel_id, priority, weight) ON TABLE public.abilities TO cockpit_reader;
GRANT EXECUTE ON FUNCTION public.statistics_ensure_user_pat(bigint,bigint,text) TO cockpit_reader;
