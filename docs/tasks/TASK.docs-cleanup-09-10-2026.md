---
task_id: 20260910-03
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Task: GAHの要求段階に合わせた文書整理

## Objective

コピー元の案件とGAHの現行要求を分離し、現在地と読む順序がわかる文書群に整理する。

## Scope

In: 要求本文の体裁、入口文書、未決定事項、資料来歴、コピー元記録の退避、プロジェクト識別子、文書生成と検証の整合。
Out: 要求の採否決定、仕様の確定、製品実装、攻撃実行、外部公開、外部参照元の変更、外部出典の再調査。

## Requirements

[要求原稿](../requirements.md)、利用者の「まだ要求レベル」「コピーした土台を整理」「DGX Qwenを使う」という指示に従う。原稿とコピー元記録を保存し、過去案件の承認をGAHの承認に読み替えない。Qwenは文書の下書き・棚卸しを担当し、最終編集と検証はCodexが行う。

## Affected Paths

README.md、HUB.codex.md、BLUEPRINT.md、GUARDRAILS.md、RUNBOOK.md、EVALUATION.md、docs配下、orchestration、各実装予定領域のREADME、governance/policy.yaml、pyproject.toml、tools/workflow.py、tests/test_workflow.py。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check
python -m unittest discover -s tests -v
```

## Deliverables

現行文書、コピー元archive、要求原稿のhashと来歴、未決定事項、Qwen利用記録、技術検収と検証証跡。

## Plan

1. 編集前の要求原稿とコピー元構成を保存する。
2. DGX QwenにGAHの概要と入口文書の下書きを依頼する。
3. 要求本文を整形し、GAHの現在地へ文書と記録を合わせる。
4. 索引を生成し、来歴・参照・既存回帰テストを確認する。

## Tests

原稿保存・要求本文保持・旧案件分離など10項目、文書ワークフロー12項目、既存回帰テスト10件が成功。整理前148ファイルのraw hash、要求本文の表示変換を戻した内容の一致を確認した。製品試験は未実行。

## Commands

Python 3.12.14でLocal Commandsを実行。Windowsの実体パスを指定し、`-X utf8`を使用した。文書検査結果は `docs/evidence/docs-cleanup-20260910/workflow-check.json`、unittest出力は同ディレクトリの `unittest.log` に保存した。

編集時の原稿保持・保存hash確認はworkspace内の一回限りの作業scriptで実行し、[content-check.json](../evidence/docs-cleanup-20260910/content-check.json)へ比較方法と結果を記録した。DGX Qwenを2回呼び出し、下書きと校正メモを得た。採否は[整理記録](../reviews/docs-cleanup-20260910.md)と[利用記録](../evidence/docs-cleanup-20260910/qwen-assistance.json)を参照。

## Notes

文書整理の完了は要求承認・設計凍結・製品受入を意味しない。

[技術検収](../acceptance/AC-20260910-03.md)。出典52件の復元、12件の未決定事項は今後の作業として残す。
