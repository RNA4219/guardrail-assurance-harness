---
task_id: 20260911-02
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# Task: 状態・権限・境界の組合せ検討

## Objective

設計v0.2の初回採択・再開・複合障害を反例で検討し、実装の解釈が分かれる欠落を補正する。

## Scope

In: bootstrap、採択時の有効性、runの所有世代と再開、費用丸め、保存/送信前のデータ境界、終了状態の優先順、手動ケース設計と期待値の照合。
Out: 製品実装、外部対象への操作、正式Schema・実環境での状態遷移試験、外部参照元の取得・掲載。

## Requirements

Behavior: 判定優先順・CI方針・AI管理・初期値を保ち、前提と状態を組み合わせても成功条件を緩めない。
I/O Contract: 初回用契約、採択要求、所有世代、費用、出力データ、終了記録に根拠を結ぶ。
Constraints: [要求](../requirements.md)と[初期方針](../operating-policy.md)を正本とし、[参照境界](../reference-boundary.md)を維持する。
Acceptance Criteria: 指摘と修正・手動ケースを追跡でき、既存68例と追加例を設計資料として照合する。未実行を成功にしない。

## Affected Paths

docs/design.md、docs/contracts、docs/spec.md、docs/open-questions.md、docs/ADR、入口、Task/Acceptance/Review/Evidence、Birdseye。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/state-review-20260911/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

[設計v0.3](../design.md)、[初回・採択・再開・終了の補足契約](../contracts/lifecycle-contract.md)、[レビューと手動確認計画](../reviews/state-review-20260911.md)、[32の追加例](../contracts/state-examples.v3.json)、[技術検収](../acceptance/AC-20260911-02.md)。根拠・リスク・優先度・手動ケース・工数・Gate・briefを記録した。

## Plan

1. manual-bb-test-harnessの観点で、状態・規則・役割・データの組合せを整理する。
2. 要求に根拠のある反例を修正し、根拠不足の論点は探索項目へ分ける。
3. DGX Qwenを限定的な照合に利用し、親が妥当性を確認する。
4. 設計例と文書を検証し、製品試験未実行を保ったまま技術検収する。

## Tests

静的・算術照合217項目が成功。既存68例と追加32例、終了の5条件32組合せを文書上の期待値として照合した。32要求・32受入条件・初期方針と数値・既存例のbytesを維持した。文書ワークフロー12項目、既存unittest10件が成功。非掲載情報の再混入は本文・配置とも0件。

追加の製品手動ケースは全件NOT_RUN。製品Gateは未実装・未実行のためno_go。文書技術検収とは区別する。

## Commands

同梱Python 3.12で次を実行した。

```sh
python -X utf8 .ga/state-review-20260911/verify_state.py
python -X utf8 -m tools.workflow generate
python -X utf8 -m tools.workflow check --report docs/evidence/state-review-20260911/workflow-check.json
python -X utf8 -m unittest discover -s tests -v
```

[設計照合](../evidence/state-review-20260911/review-check.json)、[文書検査](../evidence/state-review-20260911/workflow-check.json)、[テストlog](../evidence/state-review-20260911/unittest.log)、[非掲載検査](../evidence/state-review-20260911/privacy-check.json)。生成後に再検査し、完了Taskと検収の対応を確認する。

## Notes

利用者の「まだなんかありそう」を継続的な見直し・改修の指示として扱う。スキルのGateは製品リリースの準備状況に限定し、文書改修の許可を追加で求めるものではない。

DGX QwenはGAHの編集前2文書と6論点のみを照合し、入力6517/出力115 tokenで完了。既存規則の存在だけを理由に不足なしとした3回答は反例を解決しないため採用せず、親が設計を補正した。製品挙動の独立検証とは扱わない。外部参照元の再取得・持込み・名称掲載は行っていない。
