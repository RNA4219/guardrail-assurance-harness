---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 設計の入口（未着手）

現在は要求整理の段階で、実装設計は未作成。[要求草案](requirements.md)中の図や構成案は、設計を検討するための資料である。

構成案は、Manifest・判定・Checkpoint等の共通部分を`assurance-core`、Registry・Mutation・劣化検知・修復計画をGAH、外部評価との接続をadapterへ分けるもの。外部参照元からの抽出範囲・移行順・依存関係は未決定で、共通coreはまだ存在しない。

設計着手時は[未決定事項](open-questions.md)から対象を選び、状態遷移、失敗・不明の扱い、[データ契約](contracts/README.md)、証拠と権限の境界を具体化する。判断は[ADR](ADR/README.md)、作業は[Task](TASKS.md)で記録する。現時点で方式選定や設計凍結は行わない。
