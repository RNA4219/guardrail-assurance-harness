---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

日本語で記載する。

# GAHでの作業

- 最初に[HUB](HUB.codex.md)、[GUARDRAILS](GUARDRAILS.md)、[Birdseye](docs/birdseye/index.json)を読む。
- 現在は要求レベル。[要求草案](docs/requirements.md)を正本とし、構成案・技術候補・閾値を確定仕様や実装済み機能へ読み替えない。
- 設計・仕様・製品実装へ進む作業では[未決定事項](docs/open-questions.md)とTaskのscopeを先に確認する。
- Task Seedは[TASK.codex.md](TASK.codex.md)、技術検収は[EVALUATION](EVALUATION.md)に従う。
- 文書検証の成功、製品受入、人間の承認を区別する。未実行テスト・未実装CLI・外部設定を捏造しない。
- 独立した読み取りは一つのexec内で並行実行し、書き込み・Git変更・生成後の検査は依存順に行う。
- サブエージェントやモデルへの委任は利用者の指示がある場合に行う。モデル出力は下書きとして検証し、判定権限や実装事実を与えない。
- 文書・Task・検収を更新したら `python -m tools.workflow generate`、`python -m tools.workflow check` を実行する。
- archiveの原稿を編集しない。移動時はrepo内の対象・保存先を解決し、保存先未存在と保存前後のhash一致を確認する。
- コピー元のレビュー・Acceptance・証跡はGAHの実績に含めない。[資料来歴](docs/research/README.md)で区別する。
- 外部Issue/PR作成、push、公開、repo設定変更は、その操作が利用者の依頼に含まれる場合に行う。
