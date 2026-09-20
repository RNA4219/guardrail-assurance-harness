from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import pilot, pilot_authority
from gah.adoption import AdoptionError, AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.productization import content_ref
from gah.policy import initial_policy_profile


def ref(kind, identifier):
    return content_ref(kind, identifier, {
        "schema_version": 1, "kind": kind, "id": identifier,
    })


def outer(kind, identifier, source_ref, requirements_ref, payload):
    return {
        "schema_version": 1,
        "kind": kind,
        "id": identifier,
        "requirement_ids": ["GAH-PR01"],
        "source_ref": source_ref,
        "requirements_ref": requirements_ref,
        "created_at": 1,
        "expires_at": 1000,
        "payload": payload,
    }


def make_documents():
    source = ref("snapshot_manifest", "source-1")
    requirements = ref("snapshot_manifest", "requirements-1")
    adapter = ref("adapter", "adapter-1")
    evaluator = ref("evaluator", "evaluator-1")
    resource = ref("resource_profile", "resource-1")
    retention = ref("retention", "retention-1")
    permission = ref("permission_grant", "permission-1")
    owner = ref("owner", "owner-1")
    recipe = ref("recipe", "recipe-1")
    target = ref("target", "target-1")

    def project(identifier, repository):
        revisions = [
            ref("repository_snapshot", identifier + "-before"),
            ref("repository_snapshot", identifier + "-after"),
        ]
        payload = {
            "owner_ref": owner,
            "repository_ref": ref("repository_identity", repository),
            "revision_refs": revisions,
            "revision_set_digest": pilot.revision_set_digest(revisions),
            "permission_ref": permission,
            "permission_scope": ["history_read", "fixed_inspection_read"],
            "recipe_ref": recipe,
            "adapter_ref": adapter,
            "capabilities": ["history_read", "fixed_inspection_read", "result_write"],
            "unsupported_capabilities": [],
            "secret_ref": None,
            "resource_profile_ref": resource,
            "retention_ref": retention,
            "immutable": True,
        }
        return outer("project_binding", identifier, source, requirements, payload)

    def target_binding(identifier, model_revision):
        payload = {
            "target_ref": target,
            "model_revision_ref": ref("model_revision", model_revision),
            "permission_ref": permission,
            "secret_ref": None,
            "recipe_ref": recipe,
            "adapter_ref": adapter,
            "evaluator_ref": evaluator,
            "capabilities": ["redacted_case_read", "bounded_model_eval", "result_write"],
            "unsupported_capabilities": [],
            "resource_profile_ref": resource,
            "redaction_profile_ref": ref("redaction_profile", "redaction-1"),
            "immutable": True,
        }
        return outer("evaluation_target_binding", identifier, source, requirements, payload)

    project_a = project("project-binding-a", "repository-a")
    project_b = project("project-binding-b", "repository-b")
    baseline = target_binding("target-binding-base", "model-base")
    candidate = target_binding("target-binding-candidate", "model-candidate")
    plan_payload = {
        "plan_revision": "plan-revision-1",
        "project_binding_refs": [
            content_ref("project_binding", project_a["id"], project_a),
            content_ref("project_binding", project_b["id"], project_b),
        ],
        "evaluation_target_ref": target,
        "baseline_target_binding_ref": content_ref(
            "evaluation_target_binding", baseline["id"], baseline,
        ),
        "candidate_target_binding_ref": content_ref(
            "evaluation_target_binding", candidate["id"], candidate,
        ),
        "selection_ref": ref("pilot_selection", "selection-1"),
        "baseline_ref": ref("pilot_baseline", "baseline-1"),
        "contract_ref": ref("evaluation_contract", "contract-1"),
        "registry_ref": ref("control_registry", "registry-1"),
        "case_set_ref": ref("case_set", "case-set-1"),
        "adapter_refs": [adapter],
        "evaluator_refs": [evaluator],
        "resource_profile_ref": resource,
        "retention_ref": retention,
        "owner_ref": owner,
        "permission_refs": [permission],
        "design_evidence_refs": [ref("evidence", "design-1")],
    }
    plan = outer("pilot_plan", "plan-1", source, requirements, plan_payload)
    return {
        "project_a": project_a,
        "project_b": project_b,
        "baseline": baseline,
        "candidate": candidate,
        "plan": plan,
    }


class PilotAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "authority.sqlite"
        self.clock = lambda: 100
        self.documents = make_documents()
        self.store = AdoptionStore(
            self.path,
            clock=self.clock,
            bootstrap_policy=initial_policy_profile(),
            validator_digest="b" * 64,
            extension=EvaluationExtension(),
        )
        self.addCleanup(self.store.close)

    @staticmethod
    def request(action, request_id, **fields):
        return {
            "schema_version": 1, "action": action, "request_id": request_id,
            **fields,
        }

    def dispatch(self, uid, request):
        return self.store.dispatch(uid, uid, request)

    def register(self):
        for index, name in enumerate(
            ("project_a", "project_b", "baseline", "candidate"), 1
        ):
            result = self.dispatch(
                12001,
                self.request(
                    "pilot_binding_register", "binding-request-" + str(index),
                    document=self.documents[name],
                ),
            )
            self.assertTrue(result["metadata_only"])
            self.assertFalse(result["ci_eligible"])
        result = self.dispatch(
            12001,
            self.request(
                "pilot_plan_register", "plan-request",
                document=self.documents["plan"],
            ),
        )
        self.assertTrue(result["metadata_only"])

    def validate(self):
        plan_ref = content_ref(
            "pilot_plan", "plan-1", self.documents["plan"],
        )
        return self.dispatch(
            12003,
            self.request(
                "pilot_plan_validate", "validation-request",
                plan_ref=plan_ref, validation_id="validation-1",
                expected_generation=0,
            ),
        )

    def adopt(self, validation):
        plan_ref = content_ref(
            "pilot_plan", "plan-1", self.documents["plan"],
        )
        return self.dispatch(
            12001,
            self.request(
                "pilot_plan_adopt", "adoption-request",
                plan_ref=plan_ref,
                validation_ref=validation["validation_ref"],
                adoption_id="adoption-1",
                expected_generation=0,
            ),
        )

    def test_fixed_actions_and_peer_roles(self):
        self.assertEqual(
            set(pilot_authority.FIELDS),
            {
                "pilot_binding_register", "pilot_plan_register",
                "pilot_plan_validate", "pilot_plan_adopt", "pilot_plan_current",
            },
        )
        self.assertEqual(pilot_authority.FRESH_ACTIONS, {"pilot_plan_current"})
        with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
            self.dispatch(
                12002,
                self.request(
                    "pilot_binding_register", "candidate-request",
                    document=self.documents["project_a"],
                ),
            )
        with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
            self.dispatch(
                12001,
                self.request(
                    "pilot_plan_validate", "manager-validate",
                    plan_ref=ref("pilot_plan", "plan-1"),
                    validation_id="validation-manager",
                    expected_generation=0,
                ),
            )

    def test_register_validate_adopt_current_and_metadata_boundary(self):
        self.register()
        before = self.dispatch(
            12004,
            self.request(
                "pilot_plan_current", "current-before",
                plan_ref=content_ref("pilot_plan", "plan-1", self.documents["plan"]),
            ),
        )
        self.assertFalse(before["adopted"])
        self.assertIn("NOT_STARTED", before["reasons"])

        validation = self.validate()
        self.assertEqual(validation["request_id"], "validation-request")
        private_key = pilot_authority.request_key(
            "validator", "pilot_plan_validate", "validation-request",
        )
        private_row = self.store._db.execute(
            "SELECT request_id FROM idempotency WHERE request_id=?",
            (private_key,),
        ).fetchone()
        self.assertIsNotNone(private_row)
        self.assertIsNone(self.store._db.execute(
            "SELECT request_id FROM idempotency WHERE request_id=?",
            ("validation-request",),
        ).fetchone())
        self.assertEqual(validation["validation"]["validated_by"], "validator")
        self.assertTrue(validation["validation"]["metadata_only"])
        self.assertFalse(validation["validation"]["external_refs_verified"])
        self.assertTrue(validation["validation"]["authority_required"])
        self.assertFalse(validation["validation"]["ci_eligible"])
        adoption = self.adopt(validation)
        self.assertTrue(adoption["adopted"])
        self.assertEqual(adoption["generation"], 1)
        self.assertFalse(adoption["adoption"]["product_run_authority"])
        self.assertFalse(adoption["adoption"]["ci_eligible"])

        current = self.dispatch(
            12004,
            self.request(
                "pilot_plan_current", "current-after",
                plan_ref=content_ref("pilot_plan", "plan-1", self.documents["plan"]),
            ),
        )
        self.assertTrue(current["adopted"])
        self.assertTrue(current["valid"])
        self.assertEqual(current["generation"], 1)
        self.assertFalse(current["product_run_authority"])
        self.assertFalse(current["external_refs_verified"])

    def test_expected_generation_and_immutable_artifact_conflict(self):
        self.register()
        with self.assertRaisesRegex(AdoptionError, "^GENERATION_CONFLICT$"):
            self.dispatch(
                12003,
                self.request(
                    "pilot_plan_validate", "wrong-generation",
                    plan_ref=content_ref("pilot_plan", "plan-1", self.documents["plan"]),
                    validation_id="validation-wrong",
                    expected_generation=1,
                ),
            )
        altered = dict(self.documents["project_a"])
        altered["payload"] = dict(altered["payload"])
        altered["payload"]["recipe_ref"] = ref("recipe", "recipe-2")
        with self.assertRaisesRegex(AdoptionError, "^PILOT_ARTIFACT_CONFLICT$"):
            self.dispatch(
                12001,
                self.request(
                    "pilot_binding_register", "binding-conflict",
                    document=altered,
                ),
            )

    def test_permission_generation_and_actor_revocation_invalidate_current(self):
        self.register()
        validation = self.validate()
        adoption = self.adopt(validation)
        current_request = self.request(
            "pilot_plan_current", "current-permission",
            plan_ref=content_ref("pilot_plan", "plan-1", self.documents["plan"]),
        )
        self.assertTrue(self.dispatch(12004, current_request)["valid"])
        self.store._db.execute(
            "UPDATE adoption_meta SET value=value+1 "
            "WHERE key='permission_generation'"
        )
        changed = self.dispatch(12004, self.request(
            "pilot_plan_current", "current-generation",
            plan_ref=content_ref("pilot_plan", "plan-1", self.documents["plan"]),
        ))
        self.assertFalse(changed["valid"])
        self.assertIn("AUTHORITY_REQUIRED", changed["reasons"])

        # Revocation is checked from the current revocations table on every read.
        self.store._db.execute(
            "INSERT INTO revocations(entity_type,entity_id,generation,observed_at) "
            "VALUES('actor','validator',1,100)"
        )
        revoked = self.dispatch(12004, self.request(
            "pilot_plan_current", "current-revoked",
            plan_ref=content_ref("pilot_plan", "plan-1", self.documents["plan"]),
        ))
        self.assertFalse(revoked["valid"])
        self.assertIn("AUTHORITY_REQUIRED", revoked["reasons"])

    def test_request_key_is_private_and_actor_bound(self):
        first = pilot_authority.request_key(
            "validator", "pilot_plan_validate", "same-request",
        )
        self.assertTrue(first.startswith("@pilot:"))
        self.assertNotEqual(
            first,
            pilot_authority.request_key(
                "manager", "pilot_plan_validate", "same-request",
            ),
        )
        self.assertNotEqual(
            first,
            pilot_authority.request_key(
                "validator", "pilot_plan_adopt", "same-request",
            ),
        )
        self.assertNotEqual(first, "same-request")

    def test_real_extension_has_no_arbitrary_action_path(self):
        with self.assertRaisesRegex(AdoptionError, "^INVALID_ACTION$"):
            self.dispatch(
                12001,
                {
                    "schema_version": 1,
                    "action": "pilot_unknown",
                    "request_id": "unknown",
                    "value": {},
                },
            )


if __name__ == "__main__":
    unittest.main()
