---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# ADR-0001: コピーした文書運用基盤をGAHへ整理する

状態: accepted（今回の文書整理における運用判断）。日付: 2026-09-10。製品設計や要求全体の承認ではない。

## 背景

GAHは要求レベルで、フォルダには別案件の文書・レビュー・検収がコピーされていた。利用者が文書整理とDGX Qwenの利用を依頼した。

## 決定

既存のWorkflow-Cookbook由来の文書様式、生成器、検証器を開発運用の土台として保持する。現行識別子は`guardrail-assurance-harness`、Intentは`INT-GAH-001`。過去案件の記録は[archive](../research/README.md)へ保存し、GAHの現行Task・Acceptance・Birdseyeから分離する。

要求原稿は内容を保持して整形し、要求草案・構成案・未決定事項を区別する。Qwenの下書きは親が照合・修正し、承認や検証の代替にしない。

## 代替案と影響

フォルダ名だけの置換では過去案件の合格記録が残る。すべてを削除して新規構成にすると来歴と既存の文書検証資産を失うため、原本を保存して現行文書を整理する。

[UPSTREAM](../UPSTREAM.md)の固定ツール・雛形・licenseは維持する。GAHでの構成検証は今回あらためて実行し、[Task](../tasks/TASK.docs-cleanup-09-10-2026.md)と[Acceptance](../acceptance/AC-20260910-03.md)に記録する。外部参照元の改修、製品設計、公開は対象外。
