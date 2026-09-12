---
task_id: 20260909-01
intent_id: INT-SR-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Task: Workflow-Cookbook Fullの構成導入

## Objective

spec-reconstructorをWorkflow-Cookbook Tier 3: Fullの様式・構成に整え、単体で更新と検証ができる状態にする。

## Scope

5文書、Task/Acceptance/Birdseye、governance、CI定義、既存原稿の移設・保存、上流検証器の取り込み。製品runtime実装・本体license決定・GitHub公開は含まない。

## Requirements

[ADR-0001](../ADR/0001-workflow-cookbook-adoption.md)、[EVALUATION](../../EVALUATION.md)、[詳細要件](../requirements.md)。

## Affected Paths

root文書、docs、governance、.github、templates、tools、testsと開発領域のREADME。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report .ga/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

Full構成、生成物、回帰テスト、[検収記録](../acceptance/AC-20260909-01.md)、[CHANGELOG](../../CHANGELOG.md)。

## Plan

1. 上流のTier3と様式を固定。
2. 原稿を保存して文書・ディレクトリ・運用手順を作成。
3. 索引とBirdseyeを生成し標準checkerと回帰テストを実行。
4. 実測証跡を検収に結び、整合を再確認して完了。

## Tests

参照欠落、stale source、世代不整合、検収欠落の検出と生成の冪等性。

## Commands

2026-09-10 JST、Python 3.12.14 / Windowsでgenerateと統合check（12項目）が成功。
unittestは10件成功。CIに定義したbranch desired設定・gate matrix・local security postureの各CLIも終了コード0。
YAML 6ファイルを既存のPyYAML 6.0.3で追加検査し構文・trigger・権限・ActionのSHA固定を確認した。
詳細は [Acceptance](../acceptance/AC-20260909-01.md) と機械可読Evidenceに記録。

## Notes

Windowsのpython Store stubが使用不可のため、導入済みPython実体を使用する。GitHub上のCI実行・保護設定と製品受入は未実施。
