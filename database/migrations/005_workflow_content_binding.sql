-- Bind consumption to the exact canonical version assessed by this workflow.
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
 -- Serialize consumption with current provenance revocation and retention changes.
 PERFORM 1 FROM app.opportunity_sources l
 JOIN app.sources s ON s.id=l.source_id AND s.workspace_id=l.workspace_id
 JOIN app.raw_observations ob ON ob.id=l.observation_id AND ob.workspace_id=l.workspace_id
 WHERE l.opportunity_id=o.id AND l.workspace_id=o.workspace_id
 FOR SHARE OF s,ob;
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

