---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# CI構成とGate対応

[policy](../governance/policy.yaml) のrequired_jobsと次の対応を同時に変更する。公開後の実行結果はGitHub上の各commitのChecksを参照する。初回公開準備時点ではリモート成功は未確認。

| logical ID | workflow | check/job | 検証 |
|---|---|---|---|
| governance-gate | .github/workflows/governance-gate.yml | governance | Full導入、Task/Acceptance、CI整合、desired branch設定 |
| python-ci | .github/workflows/test.yml | unit | 計画検査後、SQLite統合を含む全unittestを12ジョブで並行実行。各実行ジョブの上限180分 |
| docs-gate | .github/workflows/markdown.yml | docs-gate | 文書参照、雛形、生成物の更新漏れ |
| 補助posture | .github/workflows/security.yml | posture | セキュリティ文書と設定のローカル検査 |

checker_stages: adoption / task_acceptance / birdseye / docs / security_postureはいずれもenforce。失敗時は終了コード1でCIを失敗させる。CIは生成物を自動修復しない。

全jobはPython 3.12、ubuntu-latest、contents: read、固定SHAのcheckout/setup-pythonを用いる。pull_request・push・workflow_dispatchで実行し、変更pathによる必須checkの欠落を避ける。

[branch-protection.expected.json](../governance/branch-protection.expected.json) は上記3checkを要求する予定値。wrapperから上流check_branch_protectionへrepo固有mappingを渡して整合だけ確認する。リポジトリ公開時に権限を持つ管理者が適用し、実export・実行URLを別のAcceptanceへ保存する。

security-ciという上流の5scanner一式は製品runtime導入時に再評価する。現時点のposture成功をSAST・secret scan成功とは呼ばない。DependabotはActionsを週次確認する定義で、GitHub公開後に有効となる。

ローカル操作は [RUNBOOK](../RUNBOOK.md)、検査実装は [tools/workflow.py](../tools/workflow.py)。

固定source80の4分割は、最長7508秒・最短1936秒と偏りがあった。2026-09-15から [test_matrix](../tools/test_matrix.py) で全件を収集し、LLM通常監督・対象版変更・複合run・Finding再検証・後続契約・Mutation reviewの6モジュールを専用ジョブにする。残るモジュールは試験数を基準に6分割し、最大12ジョブを独立runnerで並行実行する。共有するmodule/class準備を分断しない。

計画段階で収集エラー・重複ID・専用モジュールの欠落を拒否する。計画のdigestを各ジョブで再照合し、全試験がちょうど1ジョブへ割り当てられたことを検査する。新規試験も自動収集する。各ジョブは全割当件数の実行と成功を確認し、件数・所要時間をログへ残す。fail-fastを無効にして独立ジョブを最後まで実行し、必須のunitは計画と全ジョブの成功を要求する。失敗・timeout・cancel・skipで成功へ進めない。

計画は `python -m tools.test_matrix --plan`、個別再実行は `python -m tools.test_matrix --lane llm-supervision` などで確認できる。旧 `tools.test_shard` は過去証跡の再現用に維持する。製品のfull評価予算5400秒や128MiB・0.5CPUのbroker制限は変更しない。907件の割当と分割機構の11試験をローカルで確認済み。GitHub上の12ジョブ実行と短縮時間は未確認で、180分は検証ジョブの上限である。MVP受入は固定source81で実施した。このCI構成変更は固定ソースへ反映せず、製品試験と追加CI分割試験の証拠を区別する。
