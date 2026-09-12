---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
template_version: 1.0.0
---

# Evaluation

## Acceptance Criteria

現在の検収対象はGAHの文書整理と開発ワークフロー。要求の全面承認、設計凍結、製品の安全性・性能の受入は含まない。

- 現行入口がGAHと要求段階を明示し、未実装のCLI・Schemaを利用可能と書かない。
- 元の要求本文の内容とコピー元記録を保存し、来歴を追跡できる。
- 旧案件の完了Task・承認・証跡を現行の実績へ流用しない。
- 文書参照、metadata、TaskとAcceptance、索引、Birdseyeのhashと世代が整合する。
- 5標準文書の必須見出しとtemplate versionを保ち、固定したCookbookの検証を通す。
- Qwenの下書きと親の編集・実行検査を区別する。出典未復元、製品未実装、remote未検証を記録する。

## KPIs

| 指標 | 目的 | 文書検収の目標 |
|---|---|---|
| 元原稿と保存構成のhash | 来歴の保全 | 保存時と一致 |
| 要求本文の内容 | 体裁整理による欠落防止 | 表示変換を戻した本文が一致 |
| 参照切れ・古いcaps | 読む導線の整合 | 0件 |
| 現行Taskの対応 | 実施作業の追跡 | 対応する技術検収あり |
| adoption tier | 既存の運用構造 | 3 / Full |

製品のASR、FPR、Mutation Score、Coverage、SLO、MVP受入条件は[要求草案](docs/requirements.md)中の案。数値の採択・測定は未実施。

## Test Outline

既存unittestで文書生成の冪等性、source変更、参照欠落、壊れた世代、検収の欠落とID重複を確認する。文書整理では加えて原稿・保存構成のhashと旧案件記録の分離を確認する。製品のMutationやred-teamは実行しない。

## Verification Checklist

- [RUNBOOK](RUNBOOK.md)のgenerate/check/unittestが成功する。
- [Task](docs/tasks/README.md)、[Acceptance](docs/acceptance/INDEX.md)、[CHANGELOG](CHANGELOG.md)、Evidenceが対応する。
- 保存稿を現在の要求や承認と誤認させず、未実施範囲を明記する。
