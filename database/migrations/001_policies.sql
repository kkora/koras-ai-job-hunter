-- P1 local baseline. Executed only by the isolated migration administrator.
CREATE OR REPLACE FUNCTION app.current_user_id() RETURNS uuid
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION app.member_role(wid uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT m.role FROM app.memberships m
 WHERE m.workspace_id=wid AND m.user_id=app.current_user_id() AND m.active
$$;

CREATE OR REPLACE FUNCTION app.api_access(wid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT session_user='kajh_api'
   AND wid=nullif(current_setting('app.workspace_id',true),'')::uuid
   AND app.member_role(wid) IS NOT NULL
$$;

CREATE OR REPLACE FUNCTION app.worker_allowed(wid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT session_user='kajh_worker' AND EXISTS (
   SELECT 1 FROM app.worker_grants g
   WHERE g.workspace_id=wid AND g.role_name=session_user
 )
$$;

CREATE OR REPLACE FUNCTION app.can_review(uid uuid,wid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS (SELECT 1 FROM app.memberships m
   WHERE m.user_id=uid AND m.workspace_id=wid AND m.active
     AND m.role IN ('Owner','Reviewer'))
$$;

CREATE OR REPLACE FUNCTION app.resolve_identity(uid uuid,sid uuid)
RETURNS TABLE(workspace_id uuid,workspace_name text,email text,role text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT m.workspace_id,w.name::text,m.email::text,m.role::text
 FROM app.memberships m JOIN app.workspaces w ON w.id=m.workspace_id
 WHERE session_user='kajh_api' AND m.user_id=uid AND m.active
   AND EXISTS (SELECT 1 FROM auth.sessions s WHERE s.id=sid AND s.user_id=uid
     AND (s.not_after IS NULL OR s.not_after>clock_timestamp()))
   AND NOT EXISTS (SELECT 1 FROM app.revoked_sessions r WHERE r.session_id=sid)
 ORDER BY m.workspace_id
$$;

CREATE OR REPLACE FUNCTION app.revoke_session(uid uuid,sid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF session_user<>'kajh_api' OR app.current_user_id() IS DISTINCT FROM uid THEN
   RAISE insufficient_privilege;
 END IF;
 INSERT INTO app.revoked_sessions(session_id,user_id,revoked_at)
 VALUES(sid,uid,clock_timestamp()) ON CONFLICT DO NOTHING;
END;
$$;

CREATE OR REPLACE FUNCTION app.log_session_event(event_name text,wid uuid,uid uuid,rid text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF session_user<>'kajh_api' OR app.current_user_id() IS DISTINCT FROM uid
   OR event_name NOT IN ('auth.login','auth.logout')
   OR NOT EXISTS(SELECT 1 FROM app.memberships m WHERE m.workspace_id=wid AND m.user_id=uid AND m.active)
 THEN RAISE insufficient_privilege; END IF;
 INSERT INTO app.audit_events(id,workspace_id,actor,action,object_type,object_id,details,request_id,created_at)
 VALUES(gen_random_uuid(),wid,uid::text,event_name,'session',NULL,'{}'::jsonb,rid,clock_timestamp());
END; $$;

CREATE OR REPLACE FUNCTION app.consume_workflow_decision(rid uuid,did uuid,expected_hash text,expected_version integer)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r app.workflow_runs%ROWTYPE; o app.opportunities%ROWTYPE; d app.workflow_decisions%ROWTYPE; actual_hash text;
BEGIN
 IF session_user<>'kajh_worker' THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO r FROM app.workflow_runs WHERE id=rid FOR UPDATE;
 IF NOT FOUND OR NOT app.worker_allowed(r.workspace_id) THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO o FROM app.opportunities WHERE id=r.opportunity_id AND workspace_id=r.workspace_id FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO d FROM app.workflow_decisions WHERE id=did AND run_id=r.id AND workspace_id=r.workspace_id FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 actual_hash := encode(sha256(convert_to(r.thread_id||':'||o.version::text||':review','UTF8')),'hex');
 IF r.restore_hold OR r.graph_version<>'p1-v1' OR r.policy_version<>'kajh-eligibility-1.0'
   OR o.version<>expected_version OR d.content_version<>o.version
   OR d.payload_hash<>actual_hash OR expected_hash<>actual_hash
   OR d.decision<>'approved' OR NOT app.can_review(d.user_id,r.workspace_id)
 THEN RAISE insufficient_privilege; END IF;
 IF d.consumed THEN
   IF r.status IN ('approved','completed') THEN RETURN false; END IF;
   RAISE insufficient_privilege;
 END IF;
 IF d.expires_at<=clock_timestamp() OR o.lifecycle='expired'
   OR (o.expires_at IS NOT NULL AND o.expires_at<=clock_timestamp())
 THEN RAISE insufficient_privilege; END IF;
 UPDATE app.workflow_decisions SET consumed=true WHERE id=d.id;
 UPDATE app.workflow_runs SET status='approved',version=version+1,updated_at=clock_timestamp() WHERE id=r.id;
 INSERT INTO app.audit_events(id,workspace_id,actor,action,object_type,object_id,details,created_at)
 VALUES(gen_random_uuid(),r.workspace_id,'worker','workflow.local_review_consumed','workflow',r.id::text,
   jsonb_build_object('decision_id',d.id::text,'external_dispatch',false),clock_timestamp());
 RETURN true;
END; $$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM PUBLIC;
GRANT USAGE ON SCHEMA app TO kajh_api,kajh_worker;
GRANT EXECUTE ON FUNCTION app.current_user_id(),app.member_role(uuid),app.api_access(uuid)
 TO kajh_api;
GRANT EXECUTE ON FUNCTION app.worker_allowed(uuid) TO kajh_worker;
GRANT EXECUTE ON FUNCTION app.can_review(uuid,uuid) TO kajh_api,kajh_worker;
GRANT EXECUTE ON FUNCTION app.resolve_identity(uuid,uuid),app.revoke_session(uuid,uuid)
 TO kajh_api;
GRANT EXECUTE ON FUNCTION app.log_session_event(text,uuid,uuid,text) TO kajh_api;
GRANT EXECUTE ON FUNCTION app.consume_workflow_decision(uuid,uuid,text,integer) TO kajh_worker;

ALTER TABLE app.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.workspaces FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS api_scope ON app.workspaces;
CREATE POLICY api_scope ON app.workspaces TO kajh_api USING(app.api_access(id));
DROP POLICY IF EXISTS worker_scope ON app.workspaces;
CREATE POLICY worker_scope ON app.workspaces TO kajh_worker USING(app.worker_allowed(id));
GRANT SELECT ON app.workspaces TO kajh_api,kajh_worker;

ALTER TABLE app.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.memberships FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS self_membership ON app.memberships;
CREATE POLICY self_membership ON app.memberships TO kajh_api
 USING(user_id=app.current_user_id());
GRANT SELECT ON app.memberships TO kajh_api;

ALTER TABLE app.worker_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.worker_grants FORCE ROW LEVEL SECURITY;
ALTER TABLE app.revoked_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.revoked_sessions FORCE ROW LEVEL SECURITY;

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY[
  'vendor_profiles','search_profiles','profile_versions','sources','source_permissions',
  'jobs','source_runs','raw_observations','opportunities','opportunity_sources',
  'eligibility_evidence','saved_opportunities','workflow_runs','workflow_decisions',
  'audit_events','idempotency_keys'
 ] LOOP
  EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('DROP POLICY IF EXISTS api_read ON app.%I',t);
  EXECUTE format('CREATE POLICY api_read ON app.%I FOR SELECT TO kajh_api USING(app.api_access(workspace_id))',t);
  EXECUTE format('DROP POLICY IF EXISTS worker_scope ON app.%I',t);
  EXECUTE format('CREATE POLICY worker_scope ON app.%I TO kajh_worker USING(app.worker_allowed(workspace_id)) WITH CHECK(app.worker_allowed(workspace_id))',t);
 END LOOP;
END $$;

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['vendor_profiles','search_profiles','profile_versions','sources','source_permissions'] LOOP
  EXECUTE format('DROP POLICY IF EXISTS api_write ON app.%I',t);
  EXECUTE format('CREATE POLICY api_write ON app.%I FOR INSERT TO kajh_api WITH CHECK(app.api_access(workspace_id) AND app.member_role(workspace_id)=''Owner'')',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['search_profiles','sources'] LOOP
  EXECUTE format('DROP POLICY IF EXISTS api_update ON app.%I',t);
  EXECUTE format('CREATE POLICY api_update ON app.%I FOR UPDATE TO kajh_api USING(app.api_access(workspace_id) AND app.member_role(workspace_id)=''Owner'') WITH CHECK(app.api_access(workspace_id) AND app.member_role(workspace_id)=''Owner'')',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['opportunities','eligibility_evidence','jobs','workflow_runs','workflow_decisions','audit_events','idempotency_keys'] LOOP
  EXECUTE format('DROP POLICY IF EXISTS api_insert ON app.%I',t);
  EXECUTE format('CREATE POLICY api_insert ON app.%I FOR INSERT TO kajh_api WITH CHECK(app.api_access(workspace_id) AND app.member_role(workspace_id) IN (''Owner'',''Reviewer''))',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['opportunities','workflow_runs'] LOOP
  EXECUTE format('DROP POLICY IF EXISTS api_update ON app.%I',t);
  EXECUTE format('CREATE POLICY api_update ON app.%I FOR UPDATE TO kajh_api USING(app.api_access(workspace_id) AND app.member_role(workspace_id) IN (''Owner'',''Reviewer'')) WITH CHECK(app.api_access(workspace_id) AND app.member_role(workspace_id) IN (''Owner'',''Reviewer''))',t);
 END LOOP;
END $$;

DROP POLICY IF EXISTS api_saved ON app.saved_opportunities;
CREATE POLICY api_saved ON app.saved_opportunities TO kajh_api
 USING(app.api_access(workspace_id) AND user_id=app.current_user_id()
   AND app.member_role(workspace_id) IN ('Owner','Reviewer'))
 WITH CHECK(app.api_access(workspace_id) AND user_id=app.current_user_id()
   AND app.member_role(workspace_id) IN ('Owner','Reviewer'));

GRANT SELECT ON app.vendor_profiles,app.search_profiles,app.profile_versions,
 app.sources,app.source_permissions,app.jobs,app.source_runs,app.raw_observations,
 app.opportunities,app.opportunity_sources,app.eligibility_evidence,
 app.saved_opportunities,app.workflow_runs,app.workflow_decisions,
 app.audit_events,app.idempotency_keys TO kajh_api;
GRANT INSERT ON app.vendor_profiles,app.search_profiles,app.profile_versions,
 app.sources,app.source_permissions,app.jobs,app.eligibility_evidence,
 app.saved_opportunities,app.workflow_runs,app.workflow_decisions,
 app.audit_events,app.idempotency_keys TO kajh_api;
GRANT UPDATE ON app.search_profiles,app.sources,app.opportunities,app.workflow_runs TO kajh_api;
GRANT DELETE ON app.saved_opportunities TO kajh_api;

GRANT SELECT ON app.vendor_profiles,app.search_profiles,app.sources,
 app.source_permissions,app.workflow_decisions TO kajh_worker;
GRANT UPDATE(cursor) ON app.sources TO kajh_worker;
GRANT SELECT,INSERT,UPDATE,DELETE ON app.jobs,app.source_runs,
 app.raw_observations,app.opportunities,app.opportunity_sources,
 app.eligibility_evidence,app.workflow_runs TO kajh_worker;
GRANT INSERT,SELECT ON app.audit_events TO kajh_worker;
REVOKE UPDATE,DELETE ON app.audit_events FROM kajh_api,kajh_worker;
REVOKE UPDATE,DELETE ON app.vendor_profiles,app.profile_versions,app.source_permissions
 FROM kajh_api,kajh_worker;

CREATE INDEX IF NOT EXISTS opportunity_text_search ON app.opportunities
 USING gin(to_tsvector('simple',title||' '||organization||' '||description));
CREATE INDEX IF NOT EXISTS queue_claim ON app.jobs(status,available_at,created_at);
