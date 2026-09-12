---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Task Seed運用

[ルート雛形](../TASK.codex.md) を複製してdocs/tasksに配置する。ファイル名は TASK.<slug>-MM-DD-YYYY.md、task_idはYYYYMMDD-NNで一意にする。

1. Objective、Scope、Requirements、Affected Paths、Local Commands、Deliverables、Plan、Tests、Commands、Notesを具体化する。
2. statusをplanned → in_progress → doneへ更新する。中断はblockedと理由・再開条件を記録する。
3. done前に [Acceptance](acceptance/README.md) の証跡を揃える。ローカル技術検収と人による承認を明記して区別する。
4. [CHANGELOG](../CHANGELOG.md) と必要なADRを結び、generate/check/unittestを実行する。

[Task一覧](tasks/README.md) から現在の作業を選ぶ。製品開発順は [orchestration](../orchestration/development-plan.md)。
