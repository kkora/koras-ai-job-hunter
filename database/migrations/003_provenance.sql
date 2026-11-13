-- A usable source URL is not permission; all display rights must be current.
CREATE FUNCTION app.source_visible(sid uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s app.sources%ROWTYPE; p jsonb; expires timestamptz;
BEGIN
 SELECT * INTO s FROM app.sources WHERE id=sid;
 IF NOT FOUND OR NOT (app.api_access(s.workspace_id) OR app.worker_allowed(s.workspace_id))
   OR s.adapter_type<>'synthetic' THEN RETURN false; END IF;
 p:=s.permission;
 IF p->>'revoked' IS DISTINCT FROM 'false'
   OR NOT (p->>'approved' IS NOT DISTINCT FROM 'true' OR p->>'status' IS NOT DISTINCT FROM 'approved')
   OR p->>'private_display' IS DISTINCT FROM 'true'
   OR p->>'persistent_storage' IS DISTINCT FROM 'true'
   OR p->>'evidence_retention' IS DISTINCT FROM 'true'
   OR (p->>'expires_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
 THEN RETURN false; END IF;
 expires:=(p->>'expires_at')::timestamptz;
 RETURN expires>clock_timestamp();
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN false;
END; $$;
REVOKE ALL ON FUNCTION app.source_visible(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.source_visible(uuid) TO kajh_api,kajh_worker;

-- Reversible source reconciliation remains owner-controlled and workspace-bound.
DROP POLICY IF EXISTS api_insert ON app.opportunities;
CREATE POLICY api_insert ON app.opportunities FOR INSERT TO kajh_api
 WITH CHECK(app.api_access(workspace_id) AND app.member_role(workspace_id)='Owner');
GRANT INSERT ON app.opportunities TO kajh_api;
CREATE POLICY api_link_update ON app.opportunity_sources FOR UPDATE TO kajh_api
 USING(app.api_access(workspace_id) AND app.member_role(workspace_id)='Owner')
 WITH CHECK(app.api_access(workspace_id) AND app.member_role(workspace_id)='Owner');
GRANT UPDATE(opportunity_id) ON app.opportunity_sources TO kajh_api;
