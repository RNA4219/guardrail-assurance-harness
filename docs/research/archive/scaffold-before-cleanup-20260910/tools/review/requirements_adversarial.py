"""要件の判断不足を示す有限の反例モデル。製品実装や攻撃対象には接続しない。"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def cases():
    results = []

    def record(case_id, requirement, counterexample, observation, violation, guard):
        results.append({"case_id": case_id, "requirement": requirement,
                        "counterexample": counterexample, "observation": observation,
                        "unsafe_assumption_reproduced": bool(violation),
                        "proposed_guard_rejects_example": bool(guard)})

    gold = {"correct-a", "missed-b"}
    predictions = ["correct-a"] * 99 + ["wrong-c"]
    naive_precision = sum(p in gold for p in predictions) / len(predictions)
    unique = set(predictions)
    precision = len(unique & gold) / len(unique)
    recall = len(unique & gold) / len(gold)
    record("AR-01", "EVAL-02", "正解1件を99回、誤り1件、未回収gold1件",
           {"naive_precision": naive_precision, "semantic_precision": precision,
            "semantic_recall": recall, "duplicate_predictions": 98},
           naive_precision >= 0.98 and precision < 0.98,
           len(predictions) != len(unique) and precision < 0.98)

    input_files = {"src/service.ts", "evaluation/gold.json"}
    oracle_files = {"evaluation/gold.json"}
    contaminated = input_files & oracle_files
    record("AR-02", "EVAL-03", "解析入力に評価用goldを含める",
           {"input_oracle_overlap": sorted(contaminated)}, bool(contaminated),
           bool(contaminated))

    orderings = list(itertools.permutations(("run-a", "run-b")))
    selected = "run-b"
    traces = [{"completion_order": list(order), "last_finisher_selection": order[-1],
               "selection_pinned_to_request": selected} for order in orderings]
    record("AR-03", "RUN-02", "A開始→Bを選択して開始→B完了→A完了",
           {"traces": traces, "bad_completion_orders": sum(t["last_finisher_selection"] != selected for t in traces)},
           any(t["last_finisher_selection"] != selected for t in traces),
           all(t["selection_pinned_to_request"] == selected for t in traces))

    membership = {"A": {"shared-evidence", "a-only"}, "B": {"shared-evidence", "b-only"}}
    naive_deleted = membership["A"]
    safe_deleted = membership["A"] - membership["B"]
    epoch_at_worker_start, epoch_after_delete = 4, 5
    record("AR-04", "RETENTION-02", "AとBがEvidenceを共有し、A削除中に古いworkerが完了",
           {"naive_broken_B_refs": sorted(membership["B"] & naive_deleted),
            "unreferenced_only_deletion": sorted(safe_deleted),
            "stale_writer_epoch": epoch_at_worker_start,
            "current_deletion_epoch": epoch_after_delete},
           bool(membership["B"] & naive_deleted),
           not (membership["B"] & safe_deleted) and epoch_at_worker_start != epoch_after_delete)

    run_policy = checkpoint_policy = "v1"
    admitted_policies = {"v2"}
    record("AR-05", "POLICY-01", "v1で開始したrunの途中でv1を失効させる",
           {"checkpoint_matches_run": run_policy == checkpoint_policy,
            "policy_currently_admitted": run_policy in admitted_policies},
           run_policy == checkpoint_policy and run_policy not in admitted_policies,
           run_policy not in admitted_policies)

    marker = "SYNTHETIC_REVIEW_CANARY_NOT_A_CREDENTIAL"
    payload = {"sanitized_body": "value=REDACTED", "path": "config/" + marker + ".yaml"}
    record("AR-06", "METADATA-01", "本文だけ秘匿化し、合成markerをpathに残す",
           {"marker_in_body": marker in payload["sanitized_body"],
            "marker_in_metadata": marker in payload["path"]},
           marker not in payload["sanitized_body"] and marker in payload["path"],
           marker in json.dumps(payload))

    repos = [{"repository_id": "same-id", "root_label": "repo-A"},
             {"repository_id": "same-id", "root_label": "repo-B"}]
    naive_map = {r["repository_id"]: r for r in repos}
    record("AR-07", "IDENTITY-01", "異なるrootへ同じrepository_idを設定",
           {"input_repositories": len(repos), "dictionary_entries": len(naive_map)},
           len(naive_map) != len(repos), len({r["repository_id"] for r in repos}) != len(repos))

    worker_limits_mib = [512] * 32
    aggregate_limit_mib = 8192
    record("AR-08", "BUDGET-01", "各workerが512MiB以内でも32workerで総量超過",
           {"per_worker_mib": 512, "workers": 32, "aggregate_mib": sum(worker_limits_mib),
            "aggregate_limit_mib": aggregate_limit_mib},
           all(v <= 512 for v in worker_limits_mib) and sum(worker_limits_mib) > aggregate_limit_mib,
           sum(worker_limits_mib) > aggregate_limit_mib)

    artifact_states = ["UNSUPPORTED"] * 10
    parsed = sum(s == "PARSED" for s in artifact_states)
    required_parsed = 1  # このモデルの事前固定したFULL_STATIC完了policy
    record("AR-09", "RUN-03", "全10資産を会計したが、必須解析は0件",
           {"accounted": len(artifact_states), "parsed": parsed,
            "purpose": "FULL_STATIC", "required_parsed_by_model_policy": required_parsed},
           len(artifact_states) == 10 and parsed < required_parsed, parsed < required_parsed)

    snapshot = {"evidence": [{"id": "e1", "path": "internal/a.ts"}], "claims": [{"evidence_ref": "e1"}]}
    projection = {"evidence": [], "claims": [{"evidence_ref": "e1"}]}
    source_id, projection_id = digest(snapshot), digest(projection)
    exported_ids = {e["id"] for e in projection["evidence"]}
    dangling = [c["evidence_ref"] for c in projection["claims"] if c["evidence_ref"] not in exported_ids]
    record("AR-10", "OUTPUT-02", "policyでEvidenceを省いたexportを元snapshotと同一視",
           {"origin_hash_equals_projection_hash": source_id == projection_id,
            "dangling_references": dangling}, source_id != projection_id and bool(dangling),
           source_id != projection_id and bool(dangling))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="repo内の相対JSON出力先")
    args = parser.parse_args()
    results = cases()
    reproduced = all(r["unsafe_assumption_reproduced"] and r["proposed_guard_rejects_example"] for r in results)
    report = {"schema_version": 1, "executed_at": datetime.now(UTC).isoformat(timespec="seconds"),
              "scope": "finite counterexample models for requirements review",
              "status": "counterexamples_reproduced" if reproduced else "model_check_failed",
              "case_count": len(results), "product_validation": "not_run",
              "limitation": "意図的に単純化した不適切な判断モデル。製品実装の脆弱性・不具合の実証ではない。",
              "runner_sha256_lf": hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest(),
              "cases": results}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = (ROOT / args.output).resolve()
        if not target.is_relative_to(ROOT):
            parser.error("出力先はrepo内に限定")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output, encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "case_count": len(results),
                      "product_validation": "not_run"}, ensure_ascii=False))
    return 0 if reproduced else 1


if __name__ == "__main__":
    raise SystemExit(main())
