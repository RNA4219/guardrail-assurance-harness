---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
template_version: 1.0.0
---

# Evaluation

## Acceptance Criteria

この段階の判定対象はWorkflow-Cookbook Tier 3基盤の維持と要件・文書変更の技術検収である。

- 5つの標準文書はtemplate version 1.0.0と必須見出しを保つ。
- docs/tasks、docs/acceptance、Birdseye index/hot/capsが実データを持つ。
- 完了Task、approved Acceptance、CHANGELOGとEvidenceが追跡できる。
- 生成物を再生成でき、参照欠落・古いhash・壊れた世代を検査が検出する。
- Cookbookのadoption、onboarding、acceptance、freshness、CI整合検査を通す。
- GitHub設定や製品機能の検証結果は未実施と明示する。
- 要件変更は指摘・根拠・変更した契約・受入条件を追跡でき、未変更のscopeと閾値を保持する。
- 反例モデルの再現、文書上の判定、製品実装の試験を区別する。

## KPIs

| 指標 | 目的 | 目標 |
|---|---|---|
| adoption tier | 構造準拠 | 3 / Full |
| 参照切れ・古いcaps | 導線の信頼性 | 0件 |
| done Taskの検収対応 | 完了の追跡 | 100% |
| 元資料の保存 | 移設による欠落防止 | 原稿archiveのhash一致 |

製品の精度・Coverage・性能目標は [要件定義](docs/requirements.md) の受入基準を使用する。未計測。

## Test Outline

- Unit: stale source、リンク欠落、世代不整合、検収欠落の検出。
- Integration: 標準のCookbook checkerとこのrepoの文書・設定の突合。
- Smoke: 生成→再生成の冪等性→checkをローカルで確認。

## Verification Checklist

- [RUNBOOK](RUNBOOK.md) のcheckとunittest成功。
- [Task一覧](docs/tasks/README.md) から対象Taskとその検収記録を更新し、過去の検収を現在の実装証拠へ読み替えない。
- benchmark・公開・remote設定を検証したと誤記しない。
