---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# CI構成とGate対応

[policy](../governance/policy.yaml) のrequired_jobsと次の対応を同時に変更する。GitHub上ではまだ実行していない。

| logical ID | workflow | check/job | 検証 |
|---|---|---|---|
| governance-gate | .github/workflows/governance-gate.yml | governance | Full導入、Task/Acceptance、CI整合、desired branch設定 |
| python-ci | .github/workflows/test.yml | unit | 標準ライブラリunittest |
| docs-gate | .github/workflows/markdown.yml | docs-gate | 文書参照、雛形、生成物の更新漏れ |
| 補助posture | .github/workflows/security.yml | posture | セキュリティ文書と設定のローカル検査 |

checker_stages: adoption / task_acceptance / birdseye / docs / security_postureはいずれもenforce。失敗時は終了コード1でCIを失敗させる。CIは生成物を自動修復しない。

全jobはPython 3.12、ubuntu-latest、contents: read、固定SHAのcheckout/setup-pythonを用いる。pull_request・push・workflow_dispatchで実行し、変更pathによる必須checkの欠落を避ける。

[branch-protection.expected.json](../governance/branch-protection.expected.json) は上記3checkを要求する予定値。wrapperから上流check_branch_protectionへrepo固有mappingを渡して整合だけ確認する。リポジトリ公開時に権限を持つ管理者が適用し、実export・実行URLを別のAcceptanceへ保存する。

security-ciという上流の5scanner一式は製品runtime導入時に再評価する。現時点のposture成功をSAST・secret scan成功とは呼ばない。DependabotはActionsを週次確認する定義で、GitHub公開後に有効となる。

ローカル操作は [RUNBOOK](../RUNBOOK.md)、検査実装は [tools/workflow.py](../tools/workflow.py)。
