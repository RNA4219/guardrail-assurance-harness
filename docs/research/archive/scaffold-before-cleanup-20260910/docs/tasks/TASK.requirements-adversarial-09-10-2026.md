---
task_id: 20260910-01
intent_id: INT-SR-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Task: 要件定義の敵対的レビュー

## Objective

仕様書の執筆前に要件を悪条件で検証し、実装・評価で異なる判断ができる穴を補強する。

## Scope

要件の再読、18シナリオの検討、10有限反例モデル、要件v0.3と受入対応表、原稿保存、レビュー・証跡・Birdseye更新。仕様書の具体化と製品runtime実装は含まない。

## Requirements

[要件定義](../requirements.md)、[レビュー](../reviews/requirements-adversarial-2026-09-10.md)。既存のM0/M1/M2と閾値を維持する。

## Affected Paths

docs/requirements.md（requirements）、docs/reviews（review）、docs/research（provenance）、docs/tasks、docs/acceptance、docs/evidence、docs/birdseye、tools/review、EVALUATION、CHANGELOG。

## Local Commands

```sh
python tools/review/requirements_adversarial.py --output docs/evidence/requirements-adversarial-20260910/models.json
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/requirements-adversarial-20260910/workflow-check.json
```

## Deliverables

v0.3、10件の補強と8件の既存要件確認、再実行可能な反例、[Acceptance](../acceptance/AC-20260910-01.md)。

## Plan

1. v0.2を全文保存し、反例ごとに既存要件の充足を確認。
2. 計算・順序・共有参照等の小さなモデルで反例を確認。
3. 判断規則をv0.3へ追加し、受入表と来歴を更新。
4. 文書整合・原稿差分・生成物を検証し技術検収。

## Tests

製品テストは未実施。反例モデル10件の再現と、既存ワークフローの文書・参照・生成物検査を行う。

## Commands

Python 3.12.14 / Windowsで反例モデル10件を再現（終了コード0）。
要件・来歴の突合14項目と統合文書検証12項目が成功。
旧稿のhash、追加要件の1対1対応、M0/M1/M2・既存閾値の維持、仕様書・設計書の未変更を確認。
最終証跡は [Acceptance](../acceptance/AC-20260910-01.md) へ接続した。

## Notes

Codexの単独レビュー。指摘は要件の判断規則不足であり、製品脆弱性の実証ではない。仕様書作成は利用者の指示で停止中。
