# Task Seed Template

雛形を `docs/tasks/TASK.<slug>-MM-DD-YYYY.md` に複製し、値を埋める。
本文は日本語、互換性のため見出しは以下を維持する。
次のmetadataをコードフェンスから出してファイル先頭のfrontmatterに移す。
複製先からの相対リンクへ修正する（例: RUNBOOKへのリンクは `../../RUNBOOK.md`）。

```yaml
---
task_id: YYYYMMDD-xx
intent_id: INT-GAH-001
owner: RNA4219
status: planned
last_reviewed_at: YYYY-MM-DD
next_review_due: YYYY-MM-DD
---
```

## Objective

一文の目的。

## Scope

In / Outを明記する。

## Requirements

Behavior / I/O Contract / Constraints / Acceptance Criteriaを記載する。
比較評価なら入力・toolchain・仮説・実行条件を固定し、結果artifactまで追跡する。

## Affected Paths

影響するpathとBirdseye node ID。

## Local Commands

[RUNBOOK](RUNBOOK.md) から存在するコマンドを記載する。

## Deliverables

文書・コード・Task・Acceptance・必要なADR。

## Plan

依存順の作業手順。

## Tests

期待する成功/失敗条件、実行結果。未実行は未実行と記載。

## Commands

実際のコマンドと終了状態。

## Notes

根拠、リスク、未確認事項、Follow-ups。
