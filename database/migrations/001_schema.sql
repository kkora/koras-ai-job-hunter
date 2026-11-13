-- Frozen initial schema; superseded only by numbered migrations.


CREATE TABLE app.revoked_sessions (
	session_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (session_id)
);

CREATE TABLE app.workspaces (
	name VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE app.audit_events (
	actor VARCHAR(200) NOT NULL, 
	action VARCHAR(100) NOT NULL, 
	object_type VARCHAR(100) NOT NULL, 
	object_id VARCHAR(200), 
	details JSONB NOT NULL, 
	request_id VARCHAR(100), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.idempotency_keys (
	actor VARCHAR(200) NOT NULL, 
	scope VARCHAR(200) NOT NULL, 
	key VARCHAR(200) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	response JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, actor, scope, key), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.memberships (
	workspace_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (workspace_id, user_id), 
	CHECK (role IN ('Owner','Reviewer','Auditor')), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.opportunities (
	title VARCHAR(300) NOT NULL, 
	organization VARCHAR(200) NOT NULL, 
	organization_domain VARCHAR(250), 
	category VARCHAR(40) NOT NULL, 
	description TEXT NOT NULL, 
	country VARCHAR(10), 
	workplace VARCHAR(30) NOT NULL, 
	eligibility VARCHAR(30) NOT NULL, 
	lifecycle VARCHAR(30) NOT NULL, 
	facts JSONB NOT NULL, 
	skills JSONB NOT NULL, 
	version INTEGER NOT NULL, 
	dedupe_key VARCHAR(64), 
	merged_into UUID, 
	first_seen TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_seen TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, id), 
	CHECK (eligibility IN ('Eligible','Ineligible','Unknown','Needs Review')), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.search_profiles (
	name VARCHAR(200) NOT NULL, 
	version INTEGER NOT NULL, 
	filters JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.sources (
	name VARCHAR(200) NOT NULL, 
	adapter_type VARCHAR(40) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	killed BOOLEAN NOT NULL, 
	permission JSONB NOT NULL, 
	permission_version INTEGER NOT NULL, 
	cursor TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, id), 
	UNIQUE (workspace_id, name), 
	CHECK (adapter_type = 'synthetic' OR enabled = false), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.vendor_profiles (
	version INTEGER NOT NULL, 
	synthetic BOOLEAN NOT NULL, 
	profile JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, version), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.worker_grants (
	workspace_id UUID NOT NULL, 
	role_name VARCHAR(100) NOT NULL, 
	PRIMARY KEY (workspace_id, role_name), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.eligibility_evidence (
	opportunity_id UUID NOT NULL, 
	rule VARCHAR(100) NOT NULL, 
	outcome VARCHAR(30) NOT NULL, 
	details JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id, opportunity_id) REFERENCES app.opportunities (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.jobs (
	source_id UUID, 
	kind VARCHAR(40) NOT NULL, 
	payload JSONB NOT NULL, 
	idempotency_key VARCHAR(200) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	attempts INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	available_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	lease_until TIMESTAMP WITH TIME ZONE, 
	lease_token UUID, 
	error TEXT, 
	result JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, id), 
	UNIQUE (workspace_id, idempotency_key), 
	FOREIGN KEY(workspace_id, source_id) REFERENCES app.sources (workspace_id, id), 
	CHECK (attempts >= 0 AND max_attempts BETWEEN 1 AND 10), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.profile_versions (
	profile_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	body JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, profile_id, version), 
	FOREIGN KEY(workspace_id, profile_id) REFERENCES app.search_profiles (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.saved_opportunities (
	user_id UUID NOT NULL, 
	opportunity_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, user_id, opportunity_id), 
	FOREIGN KEY(workspace_id, opportunity_id) REFERENCES app.opportunities (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.source_permissions (
	source_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	body JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, source_id, version), 
	FOREIGN KEY(workspace_id, source_id) REFERENCES app.sources (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.source_runs (
	source_id UUID NOT NULL, 
	job_id UUID, 
	status VARCHAR(30) NOT NULL, 
	cursor_before TEXT, 
	cursor_after TEXT, 
	counts JSONB NOT NULL, 
	error TEXT, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, id), 
	FOREIGN KEY(workspace_id, source_id) REFERENCES app.sources (workspace_id, id), 
	FOREIGN KEY(workspace_id, job_id) REFERENCES app.jobs (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.workflow_runs (
	opportunity_id UUID NOT NULL, 
	job_id UUID, 
	thread_id VARCHAR(200) NOT NULL, 
	graph_version VARCHAR(30) NOT NULL, 
	policy_version VARCHAR(30) NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	budget JSONB NOT NULL, 
	error TEXT, 
	restore_hold BOOLEAN NOT NULL, 
	requested_by UUID, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, id), 
	UNIQUE (thread_id), 
	FOREIGN KEY(workspace_id, opportunity_id) REFERENCES app.opportunities (workspace_id, id), 
	FOREIGN KEY(workspace_id, job_id) REFERENCES app.jobs (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.raw_observations (
	source_id UUID NOT NULL, 
	source_run_id UUID NOT NULL, 
	external_id VARCHAR(200) NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	payload JSONB NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, id), 
	UNIQUE (workspace_id, source_id, external_id, payload_hash), 
	FOREIGN KEY(workspace_id, source_id) REFERENCES app.sources (workspace_id, id), 
	FOREIGN KEY(workspace_id, source_run_id) REFERENCES app.source_runs (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.workflow_decisions (
	run_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	decision VARCHAR(30) NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	content_version INTEGER NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	consumed BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id, run_id) REFERENCES app.workflow_runs (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE TABLE app.opportunity_sources (
	opportunity_id UUID NOT NULL, 
	source_id UUID NOT NULL, 
	observation_id UUID NOT NULL, 
	external_id VARCHAR(200) NOT NULL, 
	original_url TEXT NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	workspace_id UUID NOT NULL, 
	id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, source_id, external_id), 
	FOREIGN KEY(workspace_id, opportunity_id) REFERENCES app.opportunities (workspace_id, id), 
	FOREIGN KEY(workspace_id, source_id) REFERENCES app.sources (workspace_id, id), 
	FOREIGN KEY(workspace_id, observation_id) REFERENCES app.raw_observations (workspace_id, id), 
	FOREIGN KEY(workspace_id) REFERENCES app.workspaces (id)
);

CREATE INDEX ix_app_audit_events_created_at ON app.audit_events (created_at);

CREATE INDEX ix_app_audit_events_workspace_id ON app.audit_events (workspace_id);

CREATE INDEX ix_app_idempotency_keys_workspace_id ON app.idempotency_keys (workspace_id);

CREATE INDEX ix_app_opportunities_country ON app.opportunities (country);

CREATE INDEX ix_app_opportunities_dedupe_key ON app.opportunities (dedupe_key);

CREATE INDEX ix_app_opportunities_eligibility ON app.opportunities (eligibility);

CREATE INDEX ix_app_opportunities_expires_at ON app.opportunities (expires_at);

CREATE INDEX ix_app_opportunities_last_seen ON app.opportunities (last_seen);

CREATE INDEX ix_app_opportunities_workspace_id ON app.opportunities (workspace_id);

CREATE INDEX ix_app_search_profiles_workspace_id ON app.search_profiles (workspace_id);

CREATE INDEX ix_app_sources_workspace_id ON app.sources (workspace_id);

CREATE INDEX ix_app_vendor_profiles_workspace_id ON app.vendor_profiles (workspace_id);

CREATE INDEX ix_app_eligibility_evidence_workspace_id ON app.eligibility_evidence (workspace_id);

CREATE INDEX ix_app_jobs_available_at ON app.jobs (available_at);

CREATE INDEX ix_app_jobs_status ON app.jobs (status);

CREATE INDEX ix_app_jobs_workspace_id ON app.jobs (workspace_id);

CREATE INDEX ix_app_profile_versions_workspace_id ON app.profile_versions (workspace_id);

CREATE INDEX ix_app_saved_opportunities_workspace_id ON app.saved_opportunities (workspace_id);

CREATE INDEX ix_app_source_permissions_workspace_id ON app.source_permissions (workspace_id);

CREATE INDEX ix_app_source_runs_workspace_id ON app.source_runs (workspace_id);

CREATE INDEX ix_app_workflow_runs_status ON app.workflow_runs (status);

CREATE INDEX ix_app_workflow_runs_workspace_id ON app.workflow_runs (workspace_id);

CREATE INDEX ix_app_raw_observations_expires_at ON app.raw_observations (expires_at);

CREATE INDEX ix_app_raw_observations_workspace_id ON app.raw_observations (workspace_id);

CREATE INDEX ix_app_workflow_decisions_workspace_id ON app.workflow_decisions (workspace_id);

CREATE INDEX ix_app_opportunity_sources_workspace_id ON app.opportunity_sources (workspace_id);
