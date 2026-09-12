---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# 資料来歴

| 資料 | 現在の場所 | 扱い |
|---|---|---|
| 改訂済み要件 deep-research-report (12).md | [requirements](../requirements.md) | 要件の正本。移設時はfrontmatterのみ追加 |
| 改訂前バックアップ | [archive](archive/requirements-before-review-2026-09-09.md) | 原本byteを保持、編集しない |
| 敵対的レビュー前のv0.2 | [2026-09-10保存稿](archive/requirements-before-adversarial-2026-09-10.md) | frontmatterを含む全文byteを保持、編集しない |

[source-manifest.json](source-manifest.json) に移設時のSHA-256と元ファイル名を保存する。改訂前原稿は継続的にraw hashを検査する。改訂済み要件は今後の更新を許容し、移設時本文hashを履歴として残す。

archiveは現在の要件として参照せず、Birdseye・現行文書のmetadata検査から除外する。

[敵対的レビュー](../reviews/requirements-adversarial-2026-09-10.md) でv0.3へ改訂した。
review_archivesの各hashは検収時に突合し、結果を当該Evidenceへ保存する。
