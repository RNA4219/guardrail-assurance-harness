---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 条件比較部品の親レビュー

親が実装・レビュー・検証を担当した。外部モデル・subagentへの委任は行っていない。

比較処理は全体のbindと内容参照を再検査し、実行ID・絶対時刻と条件を分離した。比率の等価表現を約分し、実効時間上限、段階順、trial数、必須性、依存、oracle、baseline側の対象差替えを保持する。対象の内容が変わらない改名を修復候補の根拠にしない。

追加レビューで、公開する後続契約検査から呼出側による比較関数の差替えを除いた。ネストした入力の型不正は固定ContractErrorへ変換する。Critical格下げ、必須Control/obligationの除去、禁止event緩和、世代・policy参照再利用を検査した。

新規30件と既存21件の計51試験が成功した。初稿の試験では依存先を外した後に対象外entryを残してUNKNOWN_REFERENCEとなった。fixtureの対象範囲を両側で揃え、失敗logを保持して再検証した。これは製品側の検査を緩めた修正ではない。

既存authorityが使うファイルのhashとextension digestを維持する。今回の部品はauthorityへ未接続で、契約3採択・対象版の実行・修復確認を完了としていない。全体622件の試験とDockerは再実行していない。[詳細仕様](../semantic-conditions-detail-spec.md) / [証跡](../evidence/mvp-condition-core-20260912/README.md) / [残件](../mvp-completion-audit.md)。
