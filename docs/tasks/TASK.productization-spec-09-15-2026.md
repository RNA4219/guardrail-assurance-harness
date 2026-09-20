---
task_id: 20260915-02
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# Task: 拡張仕様の作成とLuna監督レビュー

## Objective

拡張14要件を、実装担当が参照できる具体的な入出力・状態・測定・失敗条件の仕様へ落とし込む。

## Scope

In: 統合仕様、性能/CI・導入/運用・実案件評価の3分冊、設計ケース、Lunaの分担執筆と相互レビュー、親の統合検査と指摘反映、導線と検収。

Out: 製品実装、正式Schema、環境設定、実案件データ取得、実測、GitHub反映。前工程の要求・文書検収とMVPの要求・受入証拠を保持する。

## Requirements

- Behavior: 要求の言い換えに留めず、型/単位、操作・エラー、役割と再照合、処理手順、測定・集計式を具体化する。
- I/O Contract: 入力は拡張14要件と現行実装の契約。出力は4仕様、設計ケースと期待値、14条件の対応、レビュー処遇。製品試験は全NOT_RUN。
- Constraints: Lunaの担当は分冊ごとに限定、他者の差分を保持。親が既存CLI/wire/権限と照合する。非掲載情報を取得・再混入しない。
- Acceptance Criteria: 全14要求/受入が仕様と設計caseへ対応し、共通契約と分冊に矛盾なし。算術例とschema_version等の静的照合、保護対象不変、文書検査成功。文書完成と拡張受入を区別する。

## Affected Paths

Birdseye node IDは各repo相対pathと同じ。

- `docs/productization-spec.md`
- `docs/productization-performance-spec.md`
- `docs/productization-operations-spec.md`
- `docs/productization-pilot-spec.md`
- `docs/contracts/productization-spec-cases.v1.json`
- `docs/reviews/productization-spec-20260915.md`
- 本Task、[検収](../acceptance/AC-20260915-02.md)、索引/README/HUB/CHANGELOG、[照合証跡](../evidence/productization-spec-20260915/spec-check.json)

## Local Commands

[RUNBOOK](../../RUNBOOK.md)の文書生成と検査を実行する。

```sh
python -m tools.workflow generate
python -m tools.workflow check --report .ga/productization-spec-20260915/workflow-check.json
```

## Deliverables

[統合仕様](../productization-spec.md)を入口に分冊、設計ケース、レビューと技術検収へ到達できる文書群。

## Plan

1. 親が共通wire/権限/終了値と既存差分を固定する。
2. Lunaが性能・運用・pilotをそれぞれ執筆する。
3. 親のレビューと担当間の相互レビューで不整合・不足を補正する。
4. 設計ケース、要求対応、保護対象と非掲載、文書の生成/整合を確認して閉じる。

## Tests

仕様の静的照合14項目、算術例8項目、既存文書ワークフロー11テストと文書検査12項目が成功。保護対象320ファイル不変を確認した。全製品caseはNOT_RUN。製品実装変更がないため、長時間の全件回帰・Docker受入は本Taskで実行しない。

## Commands

`python -m tools.workflow generate`、`python -m tools.workflow check --report .ga/productization-spec-20260915/workflow-check.json`、`python -m unittest tests.test_workflow -v` は終了0。`git -c core.whitespace=cr-at-eol diff --check`も終了0。設計例の静的・算術照合はprivate作業領域で実施し、[証跡](../evidence/productization-spec-20260915/spec-check.json)へ保存した。

## Notes

モデルはLuna（gpt-5.6-luna）。親が実行事実と設計案を分離し、差分レビューと実際の文書検査を担当する。DGX Qwenは今回呼び出さない。具体のpilot対象・機材等の未確定入力を架空の実績で補完しない。
