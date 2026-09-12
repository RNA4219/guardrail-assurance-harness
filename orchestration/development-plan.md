---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 開発順序案

現在は[契約・評価設計v0.3](../docs/design.md)から[コア詳細仕様v1](../docs/detail-spec.md)へ進み、判定・永続化・費用台帳・診断CLIを実装した。[監督レビュー](../docs/reviews/runtime-core-20260911.md)にDGX Qwen/Lunaの処遇と部品検証を記録する。次は全オブジェクト/採択と回収の状態契約、管理AI認証・隔離provider、固定fixtureとadapterを段階的に接続する。以下は全体の着手順であり、全MVPの完了記録ではない。

| 段階 | 成果物 | 次へ進む際に確認すること |
|---|---|---|
| 要求整理 | 現行原稿、入口、来歴、未決定事項 | コピー元の記録とGAHの要求を分離できる |
| 要求の明確化 | [32要求](../docs/requirements.md)、[受入条件](../docs/acceptance-criteria.md)、[原稿との対応](../docs/requirements-traceability.md)、[初期運用方針](../docs/operating-policy.md) | WARNINGのCI成功・数値委任・外部参照元の参照境界・生成AI管理を反映し、残る具体化を設計へ渡せる |
| 契約・評価設計 | 評価契約と管理AIの権限、GAH独自のSchema、状態と判定、adapter、評価ケース | [参照境界](../docs/reference-boundary.md)を守り、GAHの要求から初期値の境界、個別事象/集合指標、再試行、依存先、推定の不明、実行境界と再検証を具体化する |
| 実装準備 | 正式Schema、契約試験、保存/認証/隔離方式、adapterの固定版 | [残る実装事項](../docs/open-questions.md)を満たし、設計例を実行可能な試験へ変換する |
| 状態管理コア | [v2の回収・取消し・終了とDB移行](../docs/lifecycle-detail-spec.md) | 部品の復旧試験を通し、認証・実停止・dispatch照合の未接続を保持する |
| MVP実装 | Registry、Mutation CI、Decay、Plan | 合意した最小フローを端から端まで実行できる |
| MVP検証 | 実測指標、Evidence、技術検収 | 制御と検査系の意図的な劣化を区別して検知できる |
| 後期拡張 | [拡張案](../docs/extension-roadmap.md)、adapter拡充、署名証拠、段階展開・復旧 | 各候補の着手条件を満たし、別Taskで範囲と権限・承認条件を確定する |

外部参照元の共通core抽出と移植案は撤回する。MVPで想定するgeneric commandとPromptfooのadapterはGAHの契約と公開された原典に基づき設計し、追加adapterと区別する。MVPに本番の自動修復は含めない。

外部参照元の一般的な運用の考え方だけを参考にし、コード・Schema・テスト・文書・データの持込みや依存化は行わない。評価runner・指標計測・データ/校正はGAHの要求から独立に設計する。初期運用値の再承認を前提にせず、基準管理AIが許可範囲内で採択する方式を設計する。有限集合の回帰を初期目的とし、母集団推定の有効化と本番性能の保証は別に検証する。

詳細は[要求草案](../docs/requirements.md)、着手時の記録は[Task Seed](../TASK.codex.md)。旧案件のM0/M1/M2定義はGAHへ引き継がない。
