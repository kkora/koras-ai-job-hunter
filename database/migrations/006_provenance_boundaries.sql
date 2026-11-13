-- Tenant equality alone does not establish correct source/run/observation attribution.
ALTER TABLE app.jobs ADD CONSTRAINT jobs_source_identity UNIQUE(workspace_id,source_id,id);
ALTER TABLE app.source_runs ADD CONSTRAINT source_runs_source_identity UNIQUE(workspace_id,source_id,id);
ALTER TABLE app.raw_observations ADD CONSTRAINT raw_observations_source_identity UNIQUE(workspace_id,source_id,external_id,id);
ALTER TABLE app.source_runs ADD CONSTRAINT source_runs_job_source_fk
 FOREIGN KEY(workspace_id,source_id,job_id) REFERENCES app.jobs(workspace_id,source_id,id);
ALTER TABLE app.raw_observations ADD CONSTRAINT raw_observations_run_source_fk
 FOREIGN KEY(workspace_id,source_id,source_run_id) REFERENCES app.source_runs(workspace_id,source_id,id);
ALTER TABLE app.opportunity_sources ADD CONSTRAINT opportunity_sources_observation_identity_fk
 FOREIGN KEY(workspace_id,source_id,external_id,observation_id) REFERENCES app.raw_observations(workspace_id,source_id,external_id,id);
ALTER TABLE app.opportunities ADD CONSTRAINT opportunities_merged_workspace_fk
 FOREIGN KEY(workspace_id,merged_into) REFERENCES app.opportunities(workspace_id,id);
ALTER TABLE app.opportunities ADD CONSTRAINT opportunities_no_self_merge CHECK(merged_into IS NULL OR merged_into<>id);

-- Match fail-closed collection/retention policy at the SQL visibility boundary.
CREATE OR REPLACE FUNCTION app.source_visible(sid uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s app.sources%ROWTYPE; p jsonb; expires timestamptz;
BEGIN
 SELECT * INTO s FROM app.sources WHERE id=sid;
 IF NOT FOUND OR NOT (app.api_access(s.workspace_id) OR app.worker_allowed(s.workspace_id))
   OR s.adapter_type<>'synthetic' OR NOT s.enabled OR s.killed THEN RETURN false; END IF;
 p:=s.permission;
 IF p->>'revoked' IS DISTINCT FROM 'false'
   OR (p ? 'approved' AND p->>'approved' IS DISTINCT FROM 'true')
   OR (p ? 'status' AND p->>'status' IS DISTINCT FROM 'approved')
   OR NOT (p->>'approved' IS NOT DISTINCT FROM 'true' OR p->>'status' IS NOT DISTINCT FROM 'approved')
   OR p->>'commercial_use' IS DISTINCT FROM 'true'
   OR p->>'automated_collection' IS DISTINCT FROM 'true'
   OR p->>'private_display' IS DISTINCT FROM 'true'
   OR p->>'persistent_storage' IS DISTINCT FROM 'true'
   OR p->>'evidence_retention' IS DISTINCT FROM 'true'
   OR jsonb_typeof(p->'retention_days') IS DISTINCT FROM 'number'
   OR (p->>'retention_days') !~ '^[1-9][0-9]{0,3}$'
   OR jsonb_typeof(p->'quota_per_day') IS DISTINCT FROM 'number'
   OR (p->>'quota_per_day') !~ '^[1-9][0-9]*$'
   OR jsonb_typeof(p->'attribution') IS DISTINCT FROM 'string'
   OR length(btrim(p->>'attribution'))=0
   OR (p->>'expires_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
 THEN RETURN false; END IF;
 IF (p->>'retention_days')::integer>3650 THEN RETURN false; END IF;
 expires:=(p->>'expires_at')::timestamptz;
 RETURN expires>clock_timestamp();
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow OR invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
END; $$;
REVOKE ALL ON FUNCTION app.source_visible(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.source_visible(uuid) TO kajh_api,kajh_worker;
