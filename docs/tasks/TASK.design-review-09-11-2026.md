---
task_id: 20260911-01
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# Task: 設計初版の再検討

## Objective

設計v0.1を要求と反例から見直し、誤った成功や実装解釈の分岐を防ぐ改訂へ反映する。

## Scope

In: 判定の確定・失効、予算の種類と精算、キャッシュ・段階再試行、UC-CIの対象、境界例、対応文書と検収。
Out: 製品コード、正式Schema、実モデルの性能測定、外部対象の操作、参照元資産の取得・移植・名称掲載。

## Requirements

Behavior: 元の要求・閾値・予算・WARNING許容・AI管理を維持し、曖昧な契約を具体化する。
I/O Contract: 観測、確定receipt、現在の利用可否を分け、予算・再利用・初期状態の根拠を明示する。
Constraints: [要求](../requirements.md)、[初期値](../operating-policy.md)、[参照境界](../reference-boundary.md)を維持する。
Acceptance Criteria: 根拠と修正を対応付け、新しい期待例と既存例、32要求の対応、文書検証を確認する。製品試験成功とは呼ばない。

## Affected Paths

docs/design.md、docs/contracts、docs/spec.md、docs/ADR、docs/open-questions.md、入口、Task/Acceptance/Review/Evidence、Birdseye。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/design-review-20260911/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

設計v0.2、反例と修正のレビュー記録、境界例、静的・算術検証と技術検収。

## Plan

1. 対象と要求を照合し、変更前文書をworkspace内へ保存する。
2. 具体的な反例を確認し、契約・評価設計・期待例を補正する。
3. DGX Qwenを補助に用い、出力を親が検証して処遇を記録する。
4. 変更範囲と数値・対応・文書を確認し、検収と索引を確定する。

## Tests

静的・算術照合117項目、文書ワークフロー12項目、既存unittest10件が成功した。元の43例と追加25例、32要求の対応、初期値等の不変を確認。名称等の再混入は本文・配置とも0件。製品試験はNOT_RUN。

## Commands

bundled Python 3.12でレビュー検査、workflow generate/check、unittestを実行した。応答文字列のhashを保存ファイルのhashとして照合した初回検査で一件失敗したため、Windows改行変換を区別して応答文字列と保存bytesのhashをそれぞれ記録し、再検査で成功した。

DGX Qwenは固定した編集前の3文書を一回照会し、応答は完了した。出力は既存規則の要約が中心であり、独立した発見数へ加算せず、親が要求と具体例で確認した。[レビュー](../reviews/design-review-20260911.md)と[技術検収](../acceptance/AC-20260911-01.md)に結果を記録した。

## Notes

利用者の「見直して」と、継続している文書改修・DGX Qwen利用の指示を適用する。前回の算術検査は単純な例の整合確認であり、状態遷移や実装の正しさの証明ではない。
