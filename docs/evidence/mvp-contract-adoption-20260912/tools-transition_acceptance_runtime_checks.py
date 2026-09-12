"""固定fixtureで完成した新旧Evidenceからgen2採択を実brokerで検査する。"""
import re

from gah.run_contracts import content_ref
from gah.run_evidence import bound_bundle_digest


def verify(*, success, denied, check, runtime, candidate, preflight_request, final_receipts):
    def request(action, request_id, **fields):
        return {"schema_version": 1, "action": action, "request_id": request_id, **fields}

    candidate_id = candidate["candidate_id"]
    validate = request("contract_candidate_validate", "acceptance-validate-runtime",
        candidate_id=candidate_id, validation_id="acceptance-validation-runtime")
    for uid in (12001, 12002, 12004):
        check(f"acceptance_validate_role_{uid}_rejected", denied(uid, validate, "AUTHORITY_DENIED"))
    validated = success(12003, validate)
    check("acceptance_validation_saved", validated.get("candidate_id") == candidate_id
        and validated.get("validation_id") == validate["validation_id"] and validated.get("passed") is True
        and validated.get("adoption_verified") is False
        and re.fullmatch(r"[0-9a-f]{64}", validated.get("validation_digest", "")) is not None)
    adopt = request("contract_candidate_adopt", "acceptance-adopt-runtime", candidate_id=candidate_id,
        validation_id=validate["validation_id"], expected_contract_generation=1, expected_baseline_generation=1)
    for uid in (12002, 12003, 12004):
        check(f"acceptance_adopt_role_{uid}_rejected", denied(uid, adopt, "AUTHORITY_DENIED"))
    for field in ("expected_contract_generation", "expected_baseline_generation"):
        check(f"acceptance_{field}_cas_rejected", denied(12001,
            {**adopt, "request_id": "acceptance-stale-" + field, field: 2}, "GENERATION_CONFLICT"))
    adopted = success(12001, adopt)
    contract = candidate["runs"]["transition"]["next_contract"]
    check("acceptance_generation_two_adopted", adopted.get("generation") == 2
        and adopted.get("candidate_id") == candidate_id and adopted.get("adoption_verified") is True
        and adopted.get("validation_id") == validate["validation_id"]
        and adopted.get("contract_digest") == content_ref("evaluation_contract", contract["contract_id"], contract)["digest"])
    current_request = request("contract_current", "acceptance-current-runtime", series_id="fixture-contract-series")
    current = success(12004, current_request)
    check("acceptance_current_valid", current.get("generation") == 2 and current.get("valid") is True
        and current.get("contract") == contract and current.get("validation_id") == validate["validation_id"])
    runtime.restart_broker()
    check("acceptance_adoption_receipt_after_restart", success(12001, adopt) == adopted)
    check("acceptance_validation_receipt_after_restart", success(12003, validate) == validated)
    restarted = success(12004, current_request)
    check("acceptance_current_after_restart", restarted == current)
    for side, final in zip(("old", "new"), final_receipts):
        run_id = candidate["runs"][side]["bound_run"]["manifest"]["run_id"]
        replay = success(12004, request("evidence_finalize", "candidate-finalize-" + side, run_id=run_id))
        check(f"acceptance_{side}_terminal_keeps_candidate_purpose",
            {key: value for key, value in replay.items() if key not in {"action", "request_id"}} == final)
    check("acceptance_old_preflight_cannot_authorize_more_candidates",
        denied(12003, preflight_request, "BINDING_MISMATCH"))
    baseline = success(12004, request("baseline_use", "acceptance-baseline-use-runtime",
        series_id="fixture-baseline-series", expected_baseline_ref=preflight_request["expected_baseline_ref"],
        expected_contract_ref=preflight_request["expected_contract_ref"]))
    check("acceptance_baseline_generation_one_preserved", baseline.get("use") is True
        and baseline.get("generation") == 1)

    def after_source_revocation():
        revoked = success(12004, current_request)
        check("acceptance_source_revocation_invalidates_current", revoked.get("generation") == 2
            and revoked.get("valid") is False and revoked.get("contract") == contract)
        check("acceptance_source_revocation_preserves_adoption_receipt", success(12001, adopt) == adopted)
        for side, final in zip(("old", "new"), final_receipts):
            bound = candidate["runs"][side]["bound_run"]
            view = success(12004, request("evidence_current", "acceptance-revoked-evidence-" + side,
                run_id=bound["manifest"]["run_id"], expected_bundle_digest=bound_bundle_digest(bound)))
            check(f"acceptance_{side}_receipt_unchanged_after_source_revocation",
                view.get("receipt") == final and view.get("use") is False)

    return after_source_revocation
