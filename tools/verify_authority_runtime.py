"""明示実行専用。固定UIDの提案・検証・採択と拒否境界を実Dockerで確認する。"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.policy import initial_policy_profile
from tools.authority_runtime import AuthorityRuntime


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    folder = Path(args.output).resolve()
    if not folder.is_relative_to(ROOT):
        raise SystemExit("OUTPUT_OUTSIDE_WORKSPACE")
    folder.mkdir(parents=True, exist_ok=False)
    runtime = AuthorityRuntime(folder)
    sources = {**runtime.lock["source_sha256"], **{path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in ("tools/authority_runtime.py", "tools/verify_authority_runtime.py", "tools/prepare_authority_runtime.py")}}
    checks, observations = {}, []
    def check(name, passed):
        checks[name] = bool(passed)
        print(json.dumps({"check": name, "passed": bool(passed)}), flush=True)
        if not passed:
            raise AssertionError(name)
    def call(uid, value):
        result = runtime.client(uid, value)
        observations.append(result)
        (folder / "observations.json").write_text(json.dumps(observations, indent=2) + "\n", encoding="utf-8")
        return result
    def denied(uid, value, expected="AUTHORITY_DENIED"):
        result = call(uid, value)
        return result.get("kind") == "authority_error" and result.get("reason") == expected
    failure = None
    try:
        runtime.prepare()
        for uid in (12001, 12002, 12003, 12004):
            result = runtime.client(uid, probe=True)
            observations.append(result)
            (folder / "observations.json").write_text(json.dumps(observations, indent=2) + "\n", encoding="utf-8")
            check(f"identity_{uid}_isolation", result.get("uid") == uid and result.get("gid") == uid
                  and isinstance(result.get("groups"), list) and set(result["groups"]) <= {uid}
                  and all(result.get("checks", {}).values()) and len(result.get("checks", {})) == 7)
        policy = initial_policy_profile()
        policy["per_call"]["timeout_seconds"] = 110
        proposal = request("propose", "propose-1", proposal_id="proposal-1", series_id="policy-main", expected_generation=0, policy=policy)
        check("candidate_proposal_denied", denied(12002, proposal))
        forged = {**proposal, "actor_id": "manager", "context": "manager-context", "uid": 12001}
        check("candidate_self_claim_rejected", denied(12002, forged, "INVALID_REQUEST"))
        check("manager_self_validation_denied", denied(12001, request("validate", "self-validate", proposal_id="proposal-1", validation_id="validation-1")))
        weak = copy.deepcopy(proposal)
        weak["policy"]["global_api_budget"]["window_seconds"] = 1
        weak["request_id"] = "weak-request"
        check("weaker_policy_denied", denied(12001, weak, "INVALID_POLICY"))
        proposed = call(12001, proposal)
        check("manager_proposal_saved", proposed.get("action") == "propose" and proposed.get("proposal_id") == "proposal-1")
        validated = call(12003, request("validate", "validate-1", proposal_id="proposal-1", validation_id="validation-1"))
        check("independent_validation", validated.get("passed") is True and validated.get("validator_id") == "validator")
        adoption = request("adopt", "adopt-1", proposal_id="proposal-1", validation_id="validation-1", expected_generation=0)
        check("operator_adoption_denied", denied(12004, adoption))
        adopted = call(12001, adoption)
        check("policy_adopted", adopted.get("generation") == 1 and adopted.get("policy") == policy)
        current_request = request("current", "current-same-id", series_id="policy-main")
        current = call(12001, current_request)
        check("current_valid", current.get("valid") is True and current.get("generation") == 1)
        runtime.restart_broker()
        check("restart_preserves_receipt", call(12001, adoption) == adopted)
        altered = {**adoption, "expected_generation": 1}
        check("different_replay_rejected", denied(12001, altered, "REQUEST_CONFLICT"))
        proposal2 = request("propose", "propose-2", proposal_id="proposal-2", series_id="policy-main", expected_generation=1, policy=policy)
        call(12001, proposal2)
        call(12003, request("validate", "validate-2", proposal_id="proposal-2", validation_id="validation-2"))
        call(12004, request("revoke_validation", "revoke-v2", validation_id="validation-2"))
        check("revoked_validation_cannot_adopt", denied(12001,
            request("adopt", "adopt-2", proposal_id="proposal-2", validation_id="validation-2", expected_generation=1), "VALIDATION_REVOKED"))
        call(12004, request("revoke_validation", "revoke-v1", validation_id="validation-1"))
        check("same_current_id_rechecks_revocation", call(12001, current_request).get("valid") is False)
        check("replay_does_not_restore_validity", call(12001, adoption) == adopted)
        call(12004, request("revoke_actor", "revoke-manager", actor_id="manager"))
        check("revoked_manager_replay_denied", denied(12001, adoption, "AUTHORITY_REVOKED"))
        check("operator_can_read_invalid_current", call(12004, request("current", "operator-current", series_id="policy-main")).get("valid") is False)
    except Exception as error:
        failure = type(error).__name__
    finally:
        try:
            runtime.cleanup(remove_state=False)
            checks["owned_containers_stopped_removed"] = True
        except Exception:
            checks["owned_containers_stopped_removed"] = False
    checks["tested_image_matches_sources"] = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value
                                                for path, value in sources.items())
    record = {"schema_version": 1, "passed": failure is None and bool(checks) and all(checks.values()),
        "checks": checks, "failure_type": failure, "source_sha256": sources, "image_id": runtime.lock["image_id"],
        "full_mvp_accepted": False, "ci_eligible": False, "persistent_state_retained": True}
    (folder / "check.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "checks": checks, "failure_type": failure}), flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
