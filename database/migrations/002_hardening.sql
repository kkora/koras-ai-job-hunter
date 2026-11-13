-- Preserve the historical 001 result while narrowing personal saved records.
DROP POLICY IF EXISTS api_read ON app.saved_opportunities;
CREATE POLICY api_saved_read ON app.saved_opportunities
 FOR SELECT TO kajh_api
 USING(app.api_access(workspace_id) AND user_id=app.current_user_id());

-- A restored or future role cannot change append-only decisions/audit history.
REVOKE UPDATE,DELETE ON app.workflow_decisions,app.idempotency_keys
 FROM kajh_api,kajh_worker;
