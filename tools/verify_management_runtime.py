"""明示実行専用。実モデルの限定提案と認証済みPolicyProfile採択を接続する。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.contracts import decode_document
from gah.docker_runner import capture_bounded
from gah.management import build_request
from gah.policy import validate_policy_profile
from gah.wire import canonical_bytes
from tools.authority_runtime import AuthorityRuntime
from tools.verify_authority_runtime import request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    folder = Path(args.output).resolve()
    if not folder.is_relative_to(ROOT):
        raise SystemExit("OUTPUT_OUTSIDE_WORKSPACE")
    folder.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    sources = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in
               ("src/gah/management.py", "tools/management_provider.py", "tools/verify_management_runtime.py",
                "tools/authority_runtime.py", "tools/verify_authority_runtime.py")}
    record = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
        "passed": False, "scope": "bounded_generative_policy_proposal", "ci_eligible": False,
        "full_mvp_accepted": False, "source_sha256": sources, "checks": {},
        "provider": {"model": build_request()["model"], "attempt_count": 1, "request_limit": 1, "max_tokens": 128,
                     "process_deadline_seconds": 55, "retry_count": 0,
                     "request_sha256": hashlib.sha256(canonical_bytes(build_request())).hexdigest()},
        "budget_ledger_integration": "NOT_IMPLEMENTED", "candidate_context_shared": False}
    (folder / "check.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "USERPROFILE", "HOME", "TEMP", "TMP") if key in os.environ}
    provider = capture_bounded([sys.executable, "-E", "-X", "utf8", "-m", "tools.management_provider"],
                              timeout=55, cwd=ROOT, environment=environment, limit=65536)
    record["provider"]["process_result"] = {"exit_code": provider.returncode, "reason": provider.reason}
    runtime = None
    observations = []
    try:
        if provider.reason:
            raise ValueError("PROVIDER_PROCESS_FAILED")
        proposal = decode_document(provider.stdout)
        if provider.returncode != 0 or proposal.get("kind") != "management_proposal":
            # 拒否出力・provider例外の本文を証跡へ複製しない。
            known = {"DUPLICATE_KEY", "NON_INTEGER_NUMBER", "NONFINITE_NUMBER", "INPUT_TYPE",
                     "RESPONSE_TOO_LARGE", "BOM", "INVALID_UTF8", "INVALID_JSON", "INVALID_RESPONSE",
                     "INVALID_CONTENT", "INVALID_SELECTION", "INVALID_TIMEOUT", "INVALID_REASON",
                     "INVALID_USAGE", "POLICY_INVALID", "MODEL_MISMATCH", "INVALID_CHOICES",
                     "INVALID_FINISH_REASON", "PROVIDER_UNAVAILABLE"}
            reason = proposal.get("reason")
            record["provider"]["failure_reason"] = reason if isinstance(reason, str) and reason in known else "INVALID_PROVIDER_PROTOCOL"
            raise ValueError("PROVIDER_PROPOSAL_UNAVAILABLE")
        policy = validate_policy_profile(proposal["policy"])
        record["provider"].update({"status": "complete", "usage": proposal["usage"], "selection": proposal["selection"]})
        record["checks"]["model_proposal_validated"] = True
        runtime = AuthorityRuntime(folder / "deployment")
        runtime.prepare()
        def call(uid, value):
            result = runtime.client(uid, value)
            observations.append(result)
            (folder / "observations.json").write_bytes(canonical_bytes(observations))
            if result.get("kind") != "policy_adoption_result" or result.get("action") != value["action"]:
                raise ValueError("ADOPTION_STEP_FAILED")
            return result
        proposed = call(12001, request("propose", "ai-propose", proposal_id="ai-policy", series_id="ai-policy-main", expected_generation=0, policy=policy))
        validated = call(12003, request("validate", "ai-validate", proposal_id="ai-policy", validation_id="ai-validation"))
        adopted = call(12001, request("adopt", "ai-adopt", proposal_id="ai-policy", validation_id="ai-validation", expected_generation=0))
        current = call(12004, request("current", "ai-current", series_id="ai-policy-main"))
        record["checks"].update({"independent_validator": validated.get("passed") is True and validated.get("validator_id") == "validator",
            "proposal_digest_bound": proposed.get("proposal_digest") == hashlib.sha256(canonical_bytes(policy)).hexdigest()
                == validated.get("proposal_digest") == adopted.get("proposal_digest"),
            "generation_one_adopted": adopted.get("generation") == 1 and adopted.get("policy") == policy,
            "operator_observed_valid_current": current.get("valid") is True and current.get("policy") == policy})
        record["image_id"] = runtime.lock["image_id"]
        record["source_sha256"].update(runtime.lock["source_sha256"])
    except Exception as error:
        # 固定した例外型だけを保存し、モデル由来の値をエラーメッセージへ混ぜない。
        record["failure_type"] = type(error).__name__
        record["provider"].setdefault("status", "unavailable")
    finally:
        if runtime is not None:
            try:
                runtime.cleanup(remove_state=False)
                record["checks"]["owned_containers_stopped_removed"] = True
            except Exception:
                record["checks"]["owned_containers_stopped_removed"] = False
        record["checks"]["tested_sources_unchanged"] = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value
            for path, value in record["source_sha256"].items())
        record["passed"] = "failure_type" not in record and record["provider"].get("status") == "complete" and all(record["checks"].values())
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (folder / "check.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "checks": record["checks"], "provider_status": record["provider"].get("status"),
                      "failure_type": record.get("failure_type")}), flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
