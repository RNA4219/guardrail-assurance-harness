---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# 回帰検証

文書生成の冪等性、古いsource、壊れた世代、参照欠落、検収欠落・ID重複を検出する。テストは一時ディレクトリの小さなfixtureを変更し、現行要件の内容を正解として固定しない。

`python -m unittest discover -s tests -v`。製品機能の試験は実装時に追加する。
