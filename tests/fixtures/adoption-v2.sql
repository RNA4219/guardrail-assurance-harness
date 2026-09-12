-- GAH自作の空v2採択DB。実運用データやcredentialは含まない。
BEGIN TRANSACTION;
CREATE TABLE adoption_config(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "adoption_config" VALUES('bootstrap_digest','7311fbb7327517ed67c5583c0577ff2db366297532218097e1e257b245a19c2e');
INSERT INTO "adoption_config" VALUES('validator_digest','acc76f697b442719da8f9465adaf26c6edfd29e02f716bb1faa54c2ff28b1448');
INSERT INTO "adoption_config" VALUES('extension_digest','42aebfed356613bfe6c78f00757c06a23251ba3fb9eede237a1358116853ccc9');
CREATE TABLE adoption_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT INTO "adoption_meta" VALUES('schema_version',2);
INSERT INTO "adoption_meta" VALUES('last_clock',-1);
INSERT INTO "adoption_meta" VALUES('permission_generation',0);
CREATE TABLE current_profiles(
                        series_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, proposal_id TEXT NOT NULL,
                        validation_id TEXT NOT NULL, policy_json TEXT NOT NULL, policy_digest TEXT NOT NULL,
                        adopted_at INTEGER NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL);
CREATE TABLE eval_calibrations(id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, permission_generation INTEGER NOT NULL);
CREATE TABLE eval_current(series_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, proposal_id TEXT NOT NULL, validation_id TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL);
CREATE TABLE eval_objects(kind TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(kind,id));
CREATE TABLE eval_proposals(id TEXT PRIMARY KEY, series_id TEXT NOT NULL, generation INTEGER NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL);
CREATE TABLE eval_runs(run_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, manifest_digest TEXT NOT NULL, plan_json TEXT NOT NULL, plan_digest TEXT NOT NULL, contract_series_id TEXT NOT NULL, contract_generation INTEGER NOT NULL);
CREATE TABLE eval_validations(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, proposal_digest TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, permission_generation INTEGER NOT NULL);
CREATE TABLE idempotency(
                        request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, actor_id TEXT NOT NULL,
                        context TEXT NOT NULL, response_json TEXT, response_digest TEXT);
CREATE TABLE proposals(
                        proposal_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, expected_generation INTEGER NOT NULL,
                        policy_json TEXT NOT NULL, policy_digest TEXT NOT NULL, created_at INTEGER NOT NULL,
                        actor_id TEXT NOT NULL, context TEXT NOT NULL, bootstrap_digest TEXT NOT NULL,
                        validator_digest TEXT NOT NULL);
CREATE TABLE resource_bindings(operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
        entry_digest TEXT NOT NULL, scenario TEXT NOT NULL, UNIQUE(run_id,entry_digest),
        FOREIGN KEY(operation_id) REFERENCES resource_operations(operation_id));
CREATE TABLE resource_events(event_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
        event_digest TEXT NOT NULL, response_json TEXT NOT NULL, response_digest TEXT NOT NULL,
        FOREIGN KEY(operation_id) REFERENCES resource_operations(operation_id));
CREATE TABLE resource_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT INTO "resource_meta" VALUES('last_clock',-1);
CREATE TABLE resource_operations(operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
        owner_epoch INTEGER NOT NULL, reservation_json TEXT NOT NULL, reservation_digest TEXT NOT NULL,
        intended_at INTEGER, stopped_at INTEGER, released INTEGER NOT NULL, usage_json TEXT, usage_digest TEXT,
        settled_at INTEGER, cost_micros INTEGER, conflicted INTEGER NOT NULL, exposure_micros INTEGER NOT NULL,
        FOREIGN KEY(run_id) REFERENCES resource_runs(run_id));
CREATE TABLE resource_runs(run_id TEXT PRIMARY KEY, manifest_digest TEXT NOT NULL,
        policy_json TEXT NOT NULL, policy_digest TEXT NOT NULL, profile TEXT NOT NULL, created_at INTEGER NOT NULL,
        deadline INTEGER NOT NULL, owner_id TEXT NOT NULL, owner_epoch INTEGER NOT NULL, lease_until INTEGER NOT NULL,
        cancelled INTEGER NOT NULL, breached INTEGER NOT NULL, closed_at INTEGER);
CREATE TABLE revocations(
                        entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, generation INTEGER NOT NULL,
                        observed_at INTEGER NOT NULL, PRIMARY KEY(entity_type,entity_id));
CREATE TABLE validations(
                        validation_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, proposal_digest TEXT NOT NULL,
                        expected_generation INTEGER NOT NULL, observed_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                        actor_id TEXT NOT NULL, context TEXT NOT NULL, permission_generation INTEGER NOT NULL,
                        bootstrap_digest TEXT NOT NULL, validator_digest TEXT NOT NULL);
COMMIT;
PRAGMA user_version = 2;
