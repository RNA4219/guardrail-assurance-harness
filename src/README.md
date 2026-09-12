---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 製品ソース

`gah/`に判定、厳格なJSON入力、SQLite保存・費用台帳、診断CLIを実装した。[詳細仕様](../docs/detail-spec.md)が部品APIと実装範囲の正本。起動はrepo rootから`python -m tools.gah_cli`を使用する。

`termination.py`と台帳v2で回収専用lease、取消し・停止確認、終了記録を追加した。[状態管理仕様](../docs/lifecycle-detail-spec.md)の境界を適用する。

Registryの採択、外部runner、OS認証・隔離、修復計画、adapterは未接続。[開発順序](../orchestration/development-plan.md)と[残件](../docs/open-questions.md)で追跡する。
