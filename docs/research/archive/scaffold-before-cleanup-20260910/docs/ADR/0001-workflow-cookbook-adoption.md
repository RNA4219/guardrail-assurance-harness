---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# ADR-0001: Workflow-Cookbook Tier 3: Fullを採用

状態: accepted（フォルダ・様式の採用）。日付: 2026-09-09。

## 背景

利用者がOSS名をspec-reconstructorに確定し、Workflow-Cookbookの様式・フォルダ構成へのフル準拠を指定した。従来は要件原稿と改訂前バックアップのみだった。

## 決定

[固定した導入元](../UPSTREAM.md) のTier 3: Fullを採用する。README、5文書、Task Seed、Acceptance、Birdseyeのindex/hot/caps、CI整合と更新手順を導入する。既存の詳細要件は [requirements](../requirements.md) に全文移設し、原稿を [archive](../research/README.md) に保持する。

上流の5雛形と8検証器を改変せず取り込み、MIT表示を保存する。文書索引・Birdseye生成はこのrepo用の標準ライブラリ実装とする。上流のランタイムやbenchmark固有の前提まで複製する必要はなく、製品実装との責務を分離する。

## 代替案と影響

空のTier3ディレクトリだけを置く案では、根拠の追跡と再生成が成立しない。上流全体の複製は本製品が必要としないruntime依存を増やす。選択した構成では単体で文書検証でき、上流変更の追従にはlock更新と再検証が必要となる。

構成の受入は [導入Task](../tasks/TASK.workflow-cookbook-adoption-09-09-2026.md) と [Acceptance](../acceptance/AC-20260909-01.md) で追跡する。GitHubの実設定、製品機能の完成、本体licenseの決定はこの採用判断に含まれない。
