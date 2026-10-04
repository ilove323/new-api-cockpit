-- Run as the New API PostgreSQL table owner in the New API database.
-- Grant EXECUTE to the application's source reader role separately.
-- This is NOT a monitoring-database migration.
CREATE OR REPLACE FUNCTION public.statistics_ensure_user_pat(operator_id bigint, target_id bigint, candidate text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE actor_role integer; target_role integer; existing text; target_status integer;
BEGIN
 SELECT role INTO actor_role FROM public.users
 WHERE id=operator_id AND status=1 AND deleted_at IS NULL;
 IF actor_role IS NULL OR actor_role<10 THEN RAISE EXCEPTION 'invalid operator'; END IF;
 SELECT role,status,access_token INTO target_role,target_status,existing FROM public.users
 WHERE id=target_id AND deleted_at IS NULL FOR UPDATE;
 IF target_role IS NULL OR target_status<>1 THEN RAISE EXCEPTION 'unavailable target'; END IF;
 IF actor_role<>100 AND actor_role<=target_role AND operator_id<>target_id THEN
  RAISE EXCEPTION 'insufficient permission';
 END IF;
 IF existing IS NOT NULL AND existing<>'' THEN RETURN existing; END IF;
 IF candidate IS NULL OR length(candidate) NOT IN (28,32) OR candidate !~ '^[A-Za-z0-9+/]+={0,2}$' THEN
  RAISE EXCEPTION 'invalid candidate';
 END IF;
 IF EXISTS(SELECT 1 FROM public.users WHERE access_token=candidate) THEN
  RAISE unique_violation USING MESSAGE='PAT collision';
 END IF;
 IF EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name='access_token_created_at') THEN
  EXECUTE 'UPDATE public.users SET access_token=$1, access_token_created_at=extract(epoch from clock_timestamp())::bigint WHERE id=$2 AND (access_token IS NULL OR access_token='''')' USING candidate,target_id;
 ELSE
  UPDATE public.users SET access_token=candidate WHERE id=target_id AND (access_token IS NULL OR access_token='');
 END IF;
 SELECT access_token INTO existing FROM public.users WHERE id=target_id;
 RETURN existing;
END $$;
REVOKE ALL ON FUNCTION public.statistics_ensure_user_pat(bigint,bigint,text) FROM PUBLIC;
-- Example (replace with your existing reader role):
-- GRANT EXECUTE ON FUNCTION public.statistics_ensure_user_pat(bigint,bigint,text) TO statistics_reader;
