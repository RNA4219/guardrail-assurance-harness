---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

日本語で記載する。

# Spec Reconstructorでの作業

- 最初に [HUB.codex.md](HUB.codex.md)、[GUARDRAILS.md](GUARDRAILS.md)、[Birdseye](docs/birdseye/index.json) を読む。
- 詳細要件の正本は [docs/requirements.md](docs/requirements.md)。READMEやBlueprintに全要件を複製しない。
- Task Seedは [TASK.codex.md](TASK.codex.md)、検収は [EVALUATION.md](EVALUATION.md) に従う。
- 現在の製品実装状態と、ワークフロー検証の成功を区別する。未実装CLI・未実行テスト・人間の承認を捏造しない。
- 独立した読み取りは一つのexec内で並行実行し、書き込み・Git変更・生成後の検査は依存順に行う。
- サブエージェントは利用者から委任・並行作業の指示がある場合だけ使用する。
- 文書・Task・検収を更新したら `python -m tools.workflow generate`、`python -m tools.workflow check` を実行する。
- 実装やbenchmarkを始める前にTaskのscopeを明記する。この初期構成の検証は製品benchmarkではない。
- 原稿archiveを編集しない。移動時は対象がこのrepo内であることと移動先未存在を確認する。
- 外部Issue/PR作成、push、公開、repo設定変更はその操作が利用者の依頼に含まれる場合に行う。
