---
task_id: 20260915-01
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# Task: MVP後の拡張要件作成

## Objective

実案件での有用性、処理速度、導入・運用の改善を、追跡・検証できる追加要件へ具体化する。

## Scope

In: [拡張要件](../productization-requirements.md)14件、対応受入14件、数値目標、実施順、反例レビュー、既存ロードマップと入口の整合。

Out: 製品コード、正式Schema、実装、実案件選定・接続、性能試験、外部サービス設定、GitHub公開。既存MVPの要求7文書・受入証拠を改変しない。

## Requirements

- Behavior: 二つのUCとAI管理を維持し、合成検証の限界、処理効率、導入性を要件化する。
- I/O Contract: 入力は利用者の依頼、現行文書、受入証拠。出力はGAH-PR01〜14 / GAH-PAC01〜14、全NOT_RUN、実装時の開始条件と測定方法。
- Constraints: 非掲載指定を維持。数値は提案目標とし、既存の閾値・予算・権限を緩和しない。文書と製品の受入を区別する。
- Acceptance Criteria: IDと受入が1対1、全要件に測定可能な正常/不成立条件、MVP保護51ファイル不変、リンク・metadata・Task/Acceptance・生成物が整合。

## Affected Paths

Birdseye node IDはrepo相対pathと同一。

- `docs/productization-requirements.md` / `docs/extension-roadmap.md`
- `docs/reviews/productization-requirements-20260915.md`
- `README.md` / `HUB.codex.md` / `docs/open-questions.md` / `CHANGELOG.md`
- 本Task、[検収](../acceptance/AC-20260915-01.md)、[証跡](../evidence/productization-requirements-20260915/requirements-check.json)、Task/Acceptance索引、Birdseye生成物

## Local Commands

[RUNBOOK](../../RUNBOOK.md)に従う。

```sh
python -m tools.workflow generate
python -m tools.workflow check --report .ga/extension-requirements-20260915/workflow-check.json
```

## Deliverables

拡張要件、反例レビュー、ロードマップ・導線、Task/Acceptance、文書検査の結果。詳細仕様・実装は別工程。

## Plan

1. 現行要求・予算・実測の限界を確認する。
2. 追加要件と受入・測定条件・実施順を定義する。
3. 反例から受入の抜け道と過大表示を補正する。
4. 保護対象とID対応を照合し、文書生成・検査後に文書Taskを閉じる。

## Tests

要件の静的照合10項目、文書ワークフロー12項目が成功。保護51ファイル不変と非掲載情報0件を確認した。製品14条件は全NOT_RUN。実装差分がないため、全907試験や実Docker受入は今回再実行しない。

## Commands

`python -m tools.workflow generate` と `python -m tools.workflow check --report .ga/extension-requirements-20260915/workflow-check.json` は終了0。`git -c core.whitespace=cr-at-eol diff --check` で空白差分を確認する。要件ID・MVP保護対象の照合と非掲載検査は今回のprivate作業領域で実行し、公開用の集計証跡へ結ぶ。

## Notes

性能・工数・導入時間は委任を受けたCodexの提案値で、実測済みではない。対象の許可や実環境の選定はM0で記録する。親による要件レビューであり、サブエージェント・外部モデルのレビュー実績は付与しない。
