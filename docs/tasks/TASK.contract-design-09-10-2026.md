---
task_id: 20260910-11
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Task: 契約・評価設計の具体化

## Objective

確認済みの32要求と初期運用値を、独立した契約・判定・評価設計の初版へ接続する。

## Scope

In: 論理構成、評価契約、結果照合、CI返却、AI管理、予算と証拠の境界、機械可読な初期値と算術例、32要求の設計対応、入口の更新、DGX Qwenによる草案検討と親の照合。
Out: 製品runtime・正式Schema・外部adapter実装、本番操作、外部参照元資産の利用、要求や数値の無断緩和、製品受入の成功宣言。

## Requirements

Behavior: HEALTHY/WARNINGのCI成功条件、優先順、必須欠損・権限・内容・鮮度・保存失敗を混同しない。
I/O Contract: 入力契約、予定試行、正規化結果、Evidence、判定、採択記録を対応付ける。JSON例は設計資料として明示する。
Constraints: [要求](../requirements.md)と[初期値](../operating-policy.md)を維持し、[情報非掲載と独立設計](../reference-boundary.md)を適用する。
Acceptance Criteria: 32要求の対応漏れ0、数値と境界例の整合、文書ワークフロー成功、設計上の未実装事項と検証範囲を明示する。

## Affected Paths

README、AGENTS、BLUEPRINT、HUB、EVALUATION、docs/design.md、docs/spec.md、docs/contracts、docs/ADR、docs/open-questions.md、orchestration/development-plan.md、Task/Acceptance/Evidence、Birdseye。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/contract-design-20260910/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

設計初版、契約初版、初期値JSON、境界例、評価設計、判断記録、要求対応表、文書の技術検収。

## Plan

1. 既存の方針と設計への残事項を照合し、変更前の現行文書をworkspace内へ保存する。
2. 古い抽出案を訂正し、論理契約・CI・管理・評価設計と初期値を記述する。
3. DGX Qwenの反例候補を要求本文と照合し、必要な修正と処遇を記録する。
4. 数値・対応・リンク・ワークフローを確認し、技術検収と生成索引を確定する。

## Tests

設計資料の静的・算術検査111項目が成功し、43件の設計例を照合した。32要求・32受入条件・初期運用方針のbytesを維持し、32要求の設計対応を確認した。文書ワークフロー12項目と既存unittest10件も成功。製品試験はNOT_RUN。

## Commands

bundled Python 3.12で設計検査、workflow generate/check、unittestを実行。初回checkは出力先のreport自身がまだ存在しないため一件の参照欠落となり、report作成後の再検査で成功した。編集toolの事前検証で二回止まった際は、部分適用がないことをhashと未存在で確認してから修正した。

DGX Qwenは既存接続で二回照会。初回は本文nullで保存不能、二回目は本文を取得したが出力上限で打切り。得られた候補だけを親が要求と照合し、分母0を成功にする誤提案等を不採用にした。最初のusageは未保存。詳細は[照合記録](../reviews/contract-design-20260910.md)。

## Notes

利用者の「では続けて」を、直前までに整えた要求から次工程の契約・評価設計へ進む指示として扱った。今回の設計はGAHの要求から独立に作成し、外部参照元は取得・入力していない。[技術検収](../acceptance/AC-20260910-11.md)と設計凍結・製品受入を区別する。次工程の正式Schema、保存・認証・隔離、adapter、実データ、保持設定は[7項目](../open-questions.md)で追跡する。
