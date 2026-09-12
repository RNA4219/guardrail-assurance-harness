---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 評価データの予定領域

UC-CIでは制約・検査系と定義済みMutationの合成fixture、UC-LLMでは要検知/正常の期待ラベル付き合成ケースを今後管理する。共通してケースID、由来・使用条件、版・digest、期待結果とその根拠を保持する。

UC-LLMは期待ラベルを対象ガードレールの今回の回答から作らず、評価前に固定する。カテゴリ、集計単位、反復回数、モデル・評価器設定、校正根拠も比較条件に含める。不明ラベルや応答欠損を正解・正常へ変換しない。

現時点でGAHの評価データと製品実測結果はない。[要求明確化案](../docs/requirements.md)と[受入条件](../docs/acceptance-criteria.md)の数値例は算術確認用であり、本番性能や採択した閾値ではない。
