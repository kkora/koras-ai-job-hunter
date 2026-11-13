-- JSON strings are not grants; preserve strict rights and an acyclic lock order.
-- Match fail-closed collection/retention policy at the SQL visibility boundary.
CREATE OR REPLACE FUNCTION app.source_visible(sid uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s app.sources%ROWTYPE; p jsonb; expires timestamptz;
BEGIN
 SELECT * INTO s FROM app.sources WHERE id=sid;
 IF NOT FOUND OR NOT (app.api_access(s.workspace_id) OR app.worker_allowed(s.workspace_id))
   OR s.adapter_type<>'synthetic' OR NOT s.enabled OR s.killed THEN RETURN false; END IF;
 p:=s.permission;
 IF p->'revoked' IS DISTINCT FROM 'false'::jsonb
   OR (p ? 'approved' AND p->'approved' IS DISTINCT FROM 'true'::jsonb)
   OR (p ? 'status' AND p->>'status' IS DISTINCT FROM 'approved')
   OR NOT (p->'approved' IS NOT DISTINCT FROM 'true'::jsonb OR p->>'status' IS NOT DISTINCT FROM 'approved')
   OR p->'commercial_use' IS DISTINCT FROM 'true'::jsonb
   OR p->'automated_collection' IS DISTINCT FROM 'true'::jsonb
   OR p->'private_display' IS DISTINCT FROM 'true'::jsonb
   OR p->'persistent_storage' IS DISTINCT FROM 'true'::jsonb
   OR p->'evidence_retention' IS DISTINCT FROM 'true'::jsonb
   OR jsonb_typeof(p->'retention_days') IS DISTINCT FROM 'number'
   OR (p->>'retention_days') !~ '^[1-9][0-9]{0,3}$'
   OR jsonb_typeof(p->'quota_per_day') IS DISTINCT FROM 'number'
   OR (p->>'quota_per_day') !~ '^[1-9][0-9]*$'
   OR jsonb_typeof(p->'attribution') IS DISTINCT FROM 'string'
   OR length(btrim(p->>'attribution'))=0
   OR jsonb_typeof(p->'expires_at') IS DISTINCT FROM 'string'
   OR (p->>'expires_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
 THEN RETURN false; END IF;
 IF (p->>'retention_days')::integer>3650 THEN RETURN false; END IF;
 expires:=(p->>'expires_at')::timestamptz;
 RETURN expires IS NOT NULL AND expires>clock_timestamp();
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow OR invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
END; $$;
REVOKE ALL ON FUNCTION app.source_visible(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.source_visible(uuid) TO kajh_api,kajh_worker;

CREATE OR REPLACE FUNCTION app.consume_workflow_decision(rid uuid,did uuid,expected_hash text,expected_version integer)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r app.workflow_runs%ROWTYPE; o app.opportunities%ROWTYPE; d app.workflow_decisions%ROWTYPE; actual_hash text;
BEGIN
 IF session_user<>'kajh_worker' THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO r FROM app.workflow_runs WHERE id=rid;
 IF NOT FOUND OR NOT app.worker_allowed(r.workspace_id) THEN RAISE insufficient_privilege; END IF;
 -- Global lock order: sources, observations, opportunity, then workflow.
 -- The bounded P1 workspace registry is shared-locked so new provenance cannot
 -- be attached by ingestion while an approval is checked and consumed.
 PERFORM 1 FROM app.sources WHERE workspace_id=r.workspace_id ORDER BY id FOR SHARE;
 PERFORM 1 FROM app.raw_observations ob JOIN app.opportunity_sources l
   ON l.observation_id=ob.id AND l.workspace_id=ob.workspace_id
   WHERE l.opportunity_id=r.opportunity_id AND l.workspace_id=r.workspace_id
   ORDER BY ob.id FOR SHARE OF ob;
 SELECT * INTO o FROM app.opportunities WHERE id=r.opportunity_id AND workspace_id=r.workspace_id FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO r FROM app.workflow_runs WHERE id=rid AND opportunity_id=o.id AND workspace_id=o.workspace_id FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO d FROM app.workflow_decisions WHERE id=did AND run_id=r.id AND workspace_id=r.workspace_id FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 actual_hash := encode(sha256(convert_to(r.thread_id||':'||o.version::text||':review','UTF8')),'hex');
 IF jsonb_typeof(r.budget->'opportunity_version') IS DISTINCT FROM 'number'
   OR r.budget->>'opportunity_version' IS DISTINCT FROM o.version::text
   OR r.restore_hold OR r.graph_version<>'p1-v1' OR r.policy_version<>'kajh-eligibility-1.0'
   OR o.version<>expected_version OR d.content_version<>o.version
   OR d.payload_hash<>actual_hash OR expected_hash<>actual_hash
   OR d.decision<>'approved' OR NOT app.can_review(d.user_id,r.workspace_id)
 THEN RAISE insufficient_privilege; END IF;
 IF d.consumed THEN
   IF r.status IN ('approved','completed') THEN RETURN false; END IF;
   RAISE insufficient_privilege;
 END IF;
 IF d.expires_at<=clock_timestamp() OR o.lifecycle IN ('expired','purged','closed')
   OR (o.expires_at IS NOT NULL AND o.expires_at<=clock_timestamp())
 THEN RAISE insufficient_privilege; END IF;
 IF NOT EXISTS (
   SELECT 1 FROM app.opportunity_sources l
   JOIN app.sources s ON s.id=l.source_id AND s.workspace_id=l.workspace_id
   JOIN app.raw_observations ob ON ob.id=l.observation_id AND ob.workspace_id=l.workspace_id
   WHERE l.opportunity_id=o.id AND l.workspace_id=o.workspace_id
     AND s.enabled AND NOT s.killed AND app.source_visible(s.id)
     AND ob.expires_at>clock_timestamp()
 ) OR EXISTS (
   SELECT 1 FROM app.opportunity_sources l
   LEFT JOIN app.raw_observations ob ON ob.id=l.observation_id AND ob.workspace_id=l.workspace_id
   WHERE l.opportunity_id=o.id AND l.workspace_id=o.workspace_id
     AND (ob.id IS NULL OR ob.expires_at<=clock_timestamp() OR app.source_visible(l.source_id) IS NOT TRUE)
 ) THEN RAISE insufficient_privilege; END IF;
 UPDATE app.workflow_decisions SET consumed=true WHERE id=d.id;
 UPDATE app.workflow_runs SET status='approved',version=version+1,updated_at=clock_timestamp() WHERE id=r.id;
 INSERT INTO app.audit_events(id,workspace_id,actor,action,object_type,object_id,details,created_at)
 VALUES(gen_random_uuid(),r.workspace_id,'worker','workflow.local_review_consumed','workflow',r.id::text,
   jsonb_build_object('decision_id',d.id::text,'external_dispatch',false),clock_timestamp());
 RETURN true;
END; $$;


REVOKE ALL ON FUNCTION app.consume_workflow_decision(uuid,uuid,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.consume_workflow_decision(uuid,uuid,text,integer) TO kajh_worker;
