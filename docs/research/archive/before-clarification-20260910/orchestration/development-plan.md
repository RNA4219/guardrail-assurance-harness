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
| 要件の具体化 | MVP範囲、優先度、受入条件 | 全体構想との対応、未採択事項、根拠が明確 |
| 契約・評価設計 | Schema、状態と判定、adapter、評価ケース | 正常・失敗・不明・未実行を検査できる |
| MVP実装 | Registry、Mutation CI、Decay、Plan | 合意した最小フローを端から端まで実行できる |
| MVP検証 | 実測指標、Evidence、技術検収 | 制御と検査系の意図的な劣化を区別して検知できる |
| 後期拡張 | adapter拡充、署名証拠、段階展開・復旧 | 別Taskで範囲と権限・承認条件を確定する |

外部参照元からの共通core抽出は[未決定事項](../docs/open-questions.md)として先に境界を検討する。MVPで想定するgeneric commandとPromptfooのadapterは契約・MVP実装段階で扱い、追加adapterと区別する。MVPに本番の自動修復は含めない。

詳細は[要求草案](../docs/requirements.md)、着手時の記録は[Task Seed](../TASK.codex.md)。旧案件のM0/M1/M2定義はGAHへ引き継がない。
