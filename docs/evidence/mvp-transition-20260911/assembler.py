"""初回契約移行の事前検査について、実行済み証跡を不変な新規packetへ束ねる。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
OUT = ROOT / "docs/evidence/mvp-transition-20260911"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text("utf-8"))


def main():
    unit = read(LOCAL / "units-02/check.json")
    runtime = read(LOCAL / "baseline-runtime-02/check.json")
    previous = read(ROOT / "docs/evidence/mvp-adoption-20260911/verification.json")
    previous_runtime = read(ROOT / "docs/evidence/mvp-adoption-20260911/baseline-check.json")
    qwen = read(LOCAL / "qwen-transition-01/receipt.json")
    receipts = read(LOCAL / "baseline-runtime-02/execution-receipts.json")
    lock = read(ROOT / "config/authority-runtime.lock.json")
    sources = dict(unit["source_sha256"])
    for name, digest in runtime["source_sha256"].items():
        if name in sources:
            assert sources[name] == digest
        sources[name] = digest
    checks = {
        "unit_463_passed_previous_441_retained": unit["passed"] is True
            and unit["test_counts"] == {"total": 463, "previous_retained": 441, "new": 22}
            and set(previous["tests"]) <= set(unit["tests"]),
        "fixed_docker_52_passed": runtime["passed"] is True and len(runtime["checks"]) == 52
            and all(runtime["checks"].values()),
        "previous_40_docker_checks_retained": set(previous_runtime["checks"]) <= set(runtime["checks"]),
        "actual_fifteen_receipts": len(receipts) == 15 and all(
            item["stop_confirmed"] is True and item["cleanup_confirmed"] is True
            and item["isolation_config_verified"] is True and item["execution_status"] == "COMPLETED"
            and item["image_id"] == runtime["fixture_image_id"] and item["ci_eligible"] is False
            for item in receipts),
        "source_and_image_binding": runtime["image_id"] == lock["image_id"]
            and all(sha(ROOT / name) == digest for name, digest in sources.items()),
        "requirements_policy_examples_preserved": all(sha(ROOT / name) == value["previous_sha256"]
            == value["actual_sha256"] for name, value in unit["preserved"].items()),
        "qwen_design_review_complete": qwen["status"] == "complete" and qwen["finish_reason"] == "stop"
            and qwen["authority_evidence"] is False and qwen["usage"]["total_tokens"] == 4487,
        "full_mvp_and_ci_remain_unaccepted": runtime["full_mvp"] is False and runtime["ci_eligible"] is False
            and unit["full_mvp_accepted"] is False and unit["ci_eligible"] is False,
    }
    assert all(checks.values()), {name: value for name, value in checks.items() if not value}
    OUT.mkdir(exist_ok=False)
    files = {
        "unit-check.json": LOCAL / "units-02/check.json",
        "unittest.log": LOCAL / "units-02/unittest.log",
        "unit-checker.py": LOCAL / "verify_units_02.py",
        "runtime-check.json": LOCAL / "baseline-runtime-02/check.json",
        "runtime-observations.json": LOCAL / "baseline-runtime-02/observations.json",
        "execution-receipts.json": LOCAL / "baseline-runtime-02/execution-receipts.json",
        "runtime-deployment.json": LOCAL / "baseline-runtime-02/deployment.json",
        "runtime-manifest.json": LOCAL / "baseline-runtime-02/manifest.json",
        "prior-unit-check.json": LOCAL / "units-01/check.json",
        "prior-unit.log": LOCAL / "units-01/unittest.log",
        "prior-runtime-check.json": LOCAL / "baseline-runtime-01/check.json",
        "qwen-review-receipt.json": LOCAL / "qwen-transition-01/receipt.json",
        "qwen-submitted.txt": LOCAL / "qwen-transition-01/submitted-spec.txt",
        "qwen-response.txt": LOCAL / "qwen-transition-01/response.txt",
        "qwen-response-envelope.json": LOCAL / "qwen-transition-01/response-envelope.json",
        "authority-runtime.lock.json": ROOT / "config/authority-runtime.lock.json",
        "fixture-runtime.lock.json": ROOT / "config/fixture-runtime.lock.json",
        "transition-spec.txt": ROOT / "docs/contract-transition-detail-spec.md",
        "assembler.py": Path(__file__),
    }
    for name in ("contract_updates", "evaluation_authority", "run_contracts"):
        files["src-" + name + ".py"] = ROOT / ("src/gah/" + name + ".py")
    for name in ("test_contract_updates", "test_contract_preflight", "test_authority_readiness"):
        files[name + ".py"] = ROOT / ("tests/" + name + ".py")
    for name in ("authority_runtime", "verify_baseline_runtime"):
        files["tools-" + name + ".py"] = ROOT / ("tools/" + name + ".py")
    for name, source in files.items():
        with (OUT / name).open("xb") as stream:
            stream.write(source.read_bytes())
    result = {
        "schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(), "status": "passed",
        "scope": "initial_comparison_contract_preflight_with_saved_authority_evidence",
        "checks": checks, "test_counts": unit["test_counts"], "tests": unit["tests"],
        "docker_check_count": len(runtime["checks"]), "fixture_case_count": 15,
        "source_sha256": sources, "preserved": unit["preserved"],
        "image_id": runtime["image_id"], "fixture_image_id": runtime["fixture_image_id"],
        "evidence_sha256": {name: sha(OUT / name) for name in files},
        "review_disposition": [
            "Lunaの純粋条件検査・DB統合・Docker検証draftを親がレビューし、参照の実体照合と偽陽性のない検証へ修正した。",
            "Qwenの2指摘はsource対象と後続適用時の再照合を明記して反映。モデル照会は設計相談であり実行や採択の証明ではない。",
            "Lunaの再レビューで応答schema/fullref照合と不正JSONの固定error処理を補い、最終ソースで再検証した。",
            "途中版462テスト・52Dockerチェックの成功は保持し、最終463テスト・強化後52チェックと区別する。",
        ],
        "limitations": [
            "preflightは構造と現在前提の検査。generation 2採択validation・候補実行・旧条件回帰は未接続。",
            "baseline更新・通常UC-CI回帰・UC-LLMの認証/モデル資源・Finding/Plan・通常CI・全MVP受入は継続中。",
            "DB統合単体試験は固定workerをプロセス内で実行する。実Docker15件と混同しない。",
            "DBと管理基盤はローカル検証用。公開・push・実環境CI設定変更は未実施。",
        ],
        "full_mvp_accepted": False, "release_gate": "no_go", "ci_eligible": False,
    }
    with (OUT / "verification.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "checks": checks, "tests": unit["test_counts"],
                      "docker_checks": result["docker_check_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
