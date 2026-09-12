---
task_id: 20260910-05
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Task: 二つの初期利用場面の反映

## Objective

利用者が確認したcoding agentの開発・CIとLLMガードレール評価の両方を要求へ反映し、それぞれの指標・比較条件・受入を明確にする。

## Scope

In: 要求案v0.2、受入条件、GAH-D01の確認記録、来歴・入口・Task・検収と文書検証。
Out: 製品の実装・実行、運用閾値の採択、評価データの作成・校正、外部環境・外部参照元の変更、公開。

## Requirements

2026-09-10の利用者補足「coding agentの開発・CI：制約と検査系の劣化を検知する」「LLMガードレール評価：検出性能・誤検知の変化を追う」「この二つでした」に基づく。[要求](../requirements.md)と[確認済み・未決定事項](../open-questions.md)を正本とする。

## Affected Paths

README/Blueprint/Hub、要求・受入・対応表・未決定事項、datasetsの入口、資料来歴、Task/Acceptance/Evidence/Birdseye。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/dual-usecases-20260910/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

二つの利用場面を扱う24要求・24受入条件、二つの最小シナリオ、比較可能性とLLMの役割の区別、確認済みD01と残るD02〜D04、改訂前12ファイルの保存版と技術検収。

## Plan

1. v0.1をraw hash付きで保存する。
2. 共通要求と利用場面別の指標・受入条件を補強し、関連文書を揃える。
3. 要求ID・対応・来歴・参照を検査し、既存ワークフローを確認する。

## Tests

要求・来歴の構造検査12項目、文書ワークフロー12項目、既存unittest10件が成功した。24要求と24受入条件、原稿17機能・14受入項目、保存12ファイルのhash、確認済みD01と未決定D02〜D04を確認した。製品受入シナリオは未実行。

## Commands

Python 3.12.14のbundled runtimeを実体パスで指定し、`-X utf8`付きでLocal Commandsを実行した。構造と保存稿はworkspace内の一回限りのPython検査で照合し、対象hashと結果を保存した。検査結果は[技術検収](../acceptance/AC-20260910-05.md)へ結ぶ。

## Notes

GAH-D01の確認を要求の全面承認に読み替えない。前回TaskとAcceptanceは当時の記録として変更しない。DGX Qwenの既存接続先はモデル一覧の取得2回ともResponseEndedとなったため、今回のモデル回答はない。[補助利用記録](../evidence/dual-usecases-20260910/qwen-assistance.json)を参照。
