---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Security Acceptance Criteria

現在は文書基盤と判定・永続化・診断コアの初期実装がある。ローカルsecurity posture checkerは必要な開発文書・設定の存在と整合を確認する。[製品のセキュリティ要件](../requirements.md)の機能検証や脆弱性スキャンの代替にはしない。

| 項目 | 現在の状態 | 実装・公開前の条件 |
|---|---|---|
| SAST | Pythonコアのコードレビュー・部品試験を実施。専用scanner未導入 | 配布・外部接続前に実行し指摘を評価 |
| Secrets | 実データ・秘密値をfixtureに含めない。専用scanner未導入 | 履歴を含むsecret scan、漏えい時は失効 |
| 依存 | Python第三者依存なし。ActionsのDependabot定義あり | 追加依存のlicense・脆弱性確認とlock |
| Container | image未作成、scan対象なし | image導入時にdigest固定とscan |
| CI権限 | contents: read、Action SHA固定 | GitHub設定と実行結果を確認 |
| branch protection | desired設定のみ | 必須checkとreview設定の実exportを検証 |

[レビュー表](Security_Review_Checklist.md) と [Security方針](../../SECURITY.md) を使用する。
