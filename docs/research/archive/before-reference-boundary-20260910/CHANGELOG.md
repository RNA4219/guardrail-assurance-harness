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

- 0006: 利用者提示のURLから外部参照元のcommit nonpublic-revisionを静的確認し、転用元の確認待ちを解消。評価運用の6範囲とGAH側で補う指標計測・データ・校正を対応付けた。要求v0.4の32要求と初期数値を維持。製品へのコード移植は未実施。
  [Task](docs/tasks/TASK.reference-confirmation-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-08.md) / [転用対応表](docs/reference-boundary.md)。

- 0005: 要求明確化案v0.4。利用者回答に基づきWARNINGのCI成功、生成AIの管理主体、opsのOSSからの評価資産転用方針を反映。委任された閾値・予算を初期運用方針v1へ具体化し、境界と受入例を更新。正確な転用元の特定と実際の転用は次工程。
  [Task](docs/tasks/TASK.operating-decisions-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-07.md) / [初期運用方針](docs/operating-policy.md)。

- 0004: 要求明確化案v0.3。敵対的な反例11観点から、集合指標と個別違反の判定、評価契約の独立性、重複・再試行、依存とCI対象、データ校正、推定の不確かさ、状態分離、実行境界、修復確認を補強。32要求・32受入条件と7件の後期拡張案へ反映。
  [Task](docs/tasks/TASK.requirements-adversarial-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-06.md) / [反例と処遇](docs/reviews/requirements-adversarial-20260910.md)。

- 0003: 要求明確化案v0.2。利用者補足に基づきcoding agentの開発・CIとLLMガードレール評価の両方を初期対象として確認。検出率・見逃し率・FPR、期待ラベル、比較条件、LLMの役割、二つの受入シナリオを明確化。
  [Task](docs/tasks/TASK.dual-usecases-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-05.md) / [要求](docs/requirements.md)。

- 0002: 要求明確化案v0.1。目的・MVP/後期の範囲、24要求と24受入条件、原稿17機能・14受入項目の処遇を整理。判定不能・Mutation結果・baseline比較・HOLDの効力を具体化。
  [Task](docs/tasks/TASK.requirements-clarification-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-04.md) / [要求](docs/requirements.md)。

- 0001: 要求段階に合わせて文書を整理。要求本文を内容保持のまま整形し、未決定事項・出典台帳・資料来歴を追加。コピー元のレビューと検収をarchiveへ分離し、識別子と現行索引を更新。
  [Task](docs/tasks/TASK.docs-cleanup-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-03.md) / [整理記録](docs/reviews/docs-cleanup-20260910.md)。
