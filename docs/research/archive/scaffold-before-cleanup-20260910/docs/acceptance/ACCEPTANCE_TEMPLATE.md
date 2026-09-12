# Acceptance雛形

このファイルをAC-YYYYMMDD-NN.mdへ複製し、次のmetadataを先頭のfrontmatterへ移す。placeholderを実際の値へ置換する。

```yaml
---
acceptance_id: AC-YYYYMMDD-NN
task_id: YYYYMMDD-NN
intent_id: INT-SR-001
owner: RNA4219
status: draft
reviewed_at: YYYY-MM-DD
reviewed_by: 実際の検査者
last_reviewed_at: YYYY-MM-DD
next_review_due: YYYY-MM-DD
---
```

## Scope

対象Task、対象差分、含まない範囲。

## Acceptance Criteria

測定・確認できる成立条件。

## Evidence

実行日時、環境、入力・対象、コマンド、終了状態、結果へのリンク。

## Verification Result

pass/fail、残る課題、未実施範囲、自己検査と人の承認の区別。
