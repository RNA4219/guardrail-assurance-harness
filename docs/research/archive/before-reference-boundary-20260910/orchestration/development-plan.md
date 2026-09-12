---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 開発順序案

現在は要求整理の段階。以下は着手順の案であり、製品開発の完了記録ではない。

| 段階 | 成果物 | 次へ進む際に確認すること |
|---|---|---|
| 要求整理 | 現行原稿、入口、来歴、未決定事項 | コピー元の記録とGAHの要求を分離できる |
| 要求の明確化 | [32要求](../docs/requirements.md)、[受入条件](../docs/acceptance-criteria.md)、[原稿との対応](../docs/requirements-traceability.md)、[初期運用方針](../docs/operating-policy.md) | WARNINGのCI成功・数値委任・OSS転用・生成AI管理を反映し、残る具体化を設計へ渡せる |
| 契約・評価設計 | 評価契約と管理AIの権限、Schema、状態と判定、外部参照元移植設計、adapter、評価ケース | [転用対応表](../docs/reference-boundary.md)の6範囲を具体化し、初期値の境界、個別事象/集合指標、再試行、依存先、推定の不明、実行境界と再検証を検査できる |
| MVP実装 | Registry、Mutation CI、Decay、Plan | 合意した最小フローを端から端まで実行できる |
| MVP検証 | 実測指標、Evidence、技術検収 | 制御と検査系の意図的な劣化を区別して検知できる |
| 後期拡張 | [拡張案](../docs/extension-roadmap.md)、adapter拡充、署名証拠、段階展開・復旧 | 各候補の着手条件を満たし、別Taskで範囲と権限・承認条件を確定する |

外部参照元からの共通core抽出は[未決定事項](../docs/open-questions.md)として境界を検討するが、抽出完了をMVP着手の前提にしない。MVPで想定するgeneric commandとPromptfooのadapterは契約・MVP実装段階で扱い、追加adapterと区別する。MVPに本番の自動修復は含めない。

評価運用の転用元は利用者指定の外部参照元で確定し、commitと6範囲を確認した。外部参照元にない評価runner・指標計測・データ/校正をGAH側に追加する。初期運用値の再承認を前提にせず、基準管理AIが許可範囲内で採択する方式を設計する。有限集合の回帰を初期目的とし、母集団推定の有効化と本番性能の保証は別に検証する。

詳細は[要求草案](../docs/requirements.md)、着手時の記録は[Task Seed](../TASK.codex.md)。旧案件のM0/M1/M2定義はGAHへ引き継がない。
