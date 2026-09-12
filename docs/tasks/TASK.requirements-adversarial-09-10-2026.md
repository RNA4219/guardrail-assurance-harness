---
task_id: 20260910-06
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Task: 要求定義の敵対的検証と改訂

## Objective

二つの利用場面を対象に、誤合格・誤不合格・評価不能・境界違反・修復未確認の反例を検討し、必要な改修案と拡張を要求・受入条件へ反映する。

## Scope

In: v0.2の保存、文書上の敵対的検証、リスク・反例・処遇、v0.3の32要求と受入条件、後期拡張案、関連文書・来歴・技術検収。DGX Qwenによる限定的な補助と親による照合。
Out: 製品実装、攻撃実行・脆弱性再現、製品のblack-box実行、運用閾値・人間の承認の捏造、外部環境変更・公開。

## Requirements

利用者の「要件定義の敵対的検証を行い、改修すべき案、拡張しておいた方がいいものを追加・反映する」という依頼。[要求](../requirements.md)と[未決定事項](../open-questions.md)を正本とする。manual-bb-test-harnessの根拠・境界・状態・oracleの整理を文書検討に使用する。

## Affected Paths

README/Blueprint/Hub、要求・受入・対応表・未決定事項・契約の入口、datasets、開発順序、資料来歴、レビュー・拡張案、Task/Acceptance/Evidence/Birdseye。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/requirements-adversarial-20260910/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

11観点と根拠・優先度・受入への処遇、32要求/32受入条件、8件のMVP追加要求、7件の後期拡張案、Qwen提案の全件照合記録、保存14ファイル、文書の検証証跡。

## Plan

1. v0.2を保存し、二つの利用場面の規則・状態・権限・データ・変更波及を反例へ分解する。
2. DGX Qwenの候補を原文へ照合し、重複・誤提案・設計方式の過剰な固定を除く。
3. 修正と追加要求を受入条件へ結び、拡張を着手条件付きで分ける。
4. 改訂後の整合性を再度照合し、文書・ID・来歴・既存ワークフローを検証する。

## Tests

構造・来歴の18項目、文書ワークフロー12項目、既存unittest10件が成功した。32要求/32受入条件、11観点と手動ケース案、7拡張案、原稿17機能/14受入項目、保存14ファイルのhashを確認した。製品の受入ケースは全件未実行。文書検査・Qwen回答を製品の動作証拠にしない。

## Commands

Python 3.12.14のbundled runtimeを実体パスで指定し、`-X utf8`付きでLocal Commandsを実行した。構造・ID・来歴・リスク算術はworkspace内の一回限りのPython検査で照合し、対象hashと結果を保存した。DGXの既存localhost接続からqwen3.8-flash-nextへ2回の要求文書検討を依頼した。出力は[反例と照合記録](../reviews/requirements-adversarial-20260910.md)と[補助記録](../evidence/requirements-adversarial-20260910/qwen-assistance.json)へ結ぶ。

## Notes

初期利用目的D01は確認済み。D02〜D04を維持し、評価契約・baselineの管理主体をD05として追加。製品未実装のため製品受入のGateはno_goだが、今回の文書改訂は完了条件と区別する。[技術検収](../acceptance/AC-20260910-06.md)を参照。
