---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
template_version: 1.0.0
---

# Changelog

GAHで実施した変更を記録する。コピー元の履歴は[整理前構成](docs/research/README.md)に保存し、GAHの版・実績として引き継がない。

## [Unreleased]

- 0003: 要求明確化案v0.2。利用者補足に基づきcoding agentの開発・CIとLLMガードレール評価の両方を初期対象として確認。検出率・見逃し率・FPR、期待ラベル、比較条件、LLMの役割、二つの受入シナリオを明確化。
  [Task](docs/tasks/TASK.dual-usecases-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-05.md) / [要求](docs/requirements.md)。

- 0002: 要求明確化案v0.1。目的・MVP/後期の範囲、24要求と24受入条件、原稿17機能・14受入項目の処遇を整理。判定不能・Mutation結果・baseline比較・HOLDの効力を具体化。
  [Task](docs/tasks/TASK.requirements-clarification-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-04.md) / [要求](docs/requirements.md)。

- 0001: 要求段階に合わせて文書を整理。要求本文を内容保持のまま整形し、未決定事項・出典台帳・資料来歴を追加。コピー元のレビューと検収をarchiveへ分離し、識別子と現行索引を更新。
  [Task](docs/tasks/TASK.docs-cleanup-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-03.md) / [整理記録](docs/reviews/docs-cleanup-20260910.md)。
