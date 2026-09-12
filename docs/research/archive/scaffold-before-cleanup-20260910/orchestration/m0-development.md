---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# 開発順序

| 順序 | 成果物 | 開始・終了条件 |
|---|---|---|
| 基盤 | Cookbook Full文書と検証 | [導入Acceptance](../docs/acceptance/AC-20260909-01.md) |
| M0契約 | snapshot / scope / Evidence / 診断のSchemaとfixture | 要件に追跡でき、正常・異常例を検査できる |
| M0 adapter | 要件で限定した言語・frameworkのsource-first解析 | 対応構文・対象外・失敗を列挙しfixture検証 |
| M0再構成 | 根拠付き仕様候補、矛盾・unknown | 根拠の参照、決定性、欠損表示を検査 |
| M0評価 | 隔離した評価入力・正解・結果 | 入力hashと条件を固定し製品の受入閾値を測定 |
| M1/M2 | 要件に定義した拡張 | M0の受入後、個別Taskで範囲確定 |

詳細範囲は [要件定義](../docs/requirements.md)。ここでは開発の依存順だけを管理する。各作業の開始時に [Task Seed](../TASK.codex.md) を作成する。
