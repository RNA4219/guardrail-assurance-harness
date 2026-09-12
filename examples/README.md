---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 利用例

[component-assessment.v1.json](component-assessment.v1.json)は診断CLI用の合成集計値。98/100のrecallと20/20のmutation_scoreを初期方針で診断する。実モデルの評価集合や最低ケース数の受入例ではない。

実行手順は[RUNBOOK](../RUNBOOK.md)、入出力契約は[詳細仕様](../docs/detail-spec.md)を参照する。HEALTHYでもci_eligible=false、正常診断の終了コードは1。
