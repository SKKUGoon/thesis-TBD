-- Applied only to an empty, isolated evaluation database by the migration user.
CREATE SCHEMA supplier_sim;
CREATE TABLE supplier_sim.acceptance (
    order_key uuid PRIMARY KEY, scope_id uuid NOT NULL, content_hash text NOT NULL,
    supplier_ref text NOT NULL UNIQUE, body jsonb NOT NULL,
    accepted_at timestamptz NOT NULL, fault_response_lost boolean NOT NULL DEFAULT false
);
CREATE FUNCTION app.list_scope_ids() RETURNS SETOF uuid LANGUAGE sql SECURITY DEFINER
SET search_path=pg_catalog,app AS $$ SELECT id FROM app.scope $$;
REVOKE ALL ON FUNCTION app.list_scope_ids() FROM PUBLIC;
-- Private canonical synthetic JSON, committed together with delivery metadata.
CREATE TABLE app.artifact_payload (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), scope_id uuid NOT NULL,
    artifact_id uuid NOT NULL, body jsonb NOT NULL,
    UNIQUE(scope_id,id), UNIQUE(scope_id,artifact_id),
    FOREIGN KEY(scope_id,artifact_id) REFERENCES app.artifact(scope_id,id)
);
ALTER TABLE app.artifact_payload ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.artifact_payload FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON app.artifact_payload
USING (scope_id=nullif(current_setting('app.scope_id',true),'')::uuid)
WITH CHECK (scope_id=nullif(current_setting('app.scope_id',true),'')::uuid);
