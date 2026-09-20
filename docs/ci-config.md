---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-21
next_review_due: 2026-10-21
---

# CI構成とGate対応

[policy](../governance/policy.yaml) のrequired_jobsと次の対応を同時に変更する。公開後の実行結果はGitHub上の各commitのChecksを参照する。初回公開準備時点ではリモート成功は未確認。

| logical ID | workflow | check/job | 検証 |
|---|---|---|---|
| governance-gate | .github/workflows/governance-gate.yml | governance | Full導入、Task/Acceptance、CI整合、desired branch設定 |
| python-ci | .github/workflows/test.yml | unit | 計画検査後、SQLite統合を含む全unittestを13ジョブで並行実行。partitioned-transitionは上限240分、ほかは180分 |
| docs-gate | .github/workflows/markdown.yml | docs-gate | 文書参照、雛形、生成物の更新漏れ |
| 補助posture | .github/workflows/security.yml | posture | セキュリティ文書と設定のローカル検査 |

checker_stages: adoption / task_acceptance / birdseye / docs / security_postureはいずれもenforce。失敗時は終了コード1でCIを失敗させる。CIは生成物を自動修復しない。

全jobはPython 3.12、ubuntu-latest、contents: read、固定SHAのcheckout/setup-pythonを用いる。pull_request・push・workflow_dispatchで実行し、変更pathによる必須checkの欠落を避ける。

[branch-protection.expected.json](../governance/branch-protection.expected.json) は上記3checkを要求する予定値。wrapperから上流check_branch_protectionへrepo固有mappingを渡して整合だけ確認する。リポジトリ公開時に権限を持つ管理者が適用し、実export・実行URLを別のAcceptanceへ保存する。

security-ciという上流の5scanner一式は製品runtime導入時に再評価する。現時点のposture成功をSAST・secret scan成功とは呼ばない。DependabotはActionsを週次確認する定義で、GitHub公開後に有効となる。

履歴付きCIではactions: readも必要とする。planだけが既定ブランチの成功pushの履歴を読み、固定SHAのupload/download-artifactで同じ履歴と計画を全jobへ配布する。履歴は分割の推定にだけ使い、成功の根拠にはしない。[結合仕様](productization-implementation-spec.md)に版・出典・再配送・全laneの照合を示す。

ローカル操作は [RUNBOOK](../RUNBOOK.md)、検査実装は [tools/workflow.py](../tools/workflow.py)。

固定source80の4分割は、最長7508秒・最短1936秒と偏りがあった。2026-09-15から [test_matrix](../tools/test_matrix.py) で全件を収集し、LLM通常監督・対象版変更・複合run・Finding再検証・後続契約・Mutation reviewを専用ジョブにした。2026-09-21から分割保存の移行統合を加え、専用ジョブは7モジュールとする。残るモジュールは同条件の実測時間（履歴不足時は試験数から推定）を基準に6分割し、最大13ジョブを独立runnerで並行実行する。共有するmodule/class準備を分断しない。

計画段階で収集エラー・重複ID・専用モジュールの欠落を拒否する。計画のdigestを各ジョブで再照合し、全試験がちょうど1ジョブへ割り当てられたことを検査する。新規試験も自動収集する。各ジョブは全割当件数の実行と成功を確認し、件数・所要時間をログへ残す。fail-fastを無効にして独立ジョブを最後まで実行し、必須のunitは計画と全ジョブの成功を要求する。失敗・timeout・cancel・skipで成功へ進めない。

計画は `python -m tools.test_matrix --plan`、個別再実行は `python -m tools.test_matrix --lane llm-supervision` などで確認できる。旧 `tools.test_shard` は過去証跡の再現用に維持する。製品のfull評価予算5400秒や128MiB・0.5CPUのbroker制限は変更しない。初期分割時には907件の割当と分割機構の11試験をローカルで確認した。現在の時間枠はpartitioned-transitionの240分、ほかの180分であり、検証ジョブの上限である。MVP受入は固定source81で実施した。このCI構成変更は固定ソースへ反映せず、製品試験と追加CI分割試験の証拠を区別する。
2026-09-21のfixture修正commit 130c879では、[PR側の全1,682件と集約unit](https://github.com/RNA4219/guardrail-assurance-harness/actions/runs/35529155066)が成功した。分割保存の移行統合moduleは9,871.202秒、これを含むregression-3全体は10,696.711秒だった。一方、[同一commitのpush側](https://github.com/RNA4219/guardrail-assurance-harness/actions/runs/35529124803)は同moduleの完了前に180分上限で打ち切られた。この実測に基づき同moduleをpartitioned-transitionへ独立させ、そこだけ上限240分を確保する。400/800件のケース、assert、全件成功を要求するunitの条件は維持する。新構成の実行結果はそのcommitのChecksで確認する。
