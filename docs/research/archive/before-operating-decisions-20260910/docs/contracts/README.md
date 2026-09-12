---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# データ契約の入口（未作成）

[要求明確化案](../requirements.md)のControl、run入力、Evidence、Mutation結果、Assurance状態、Finding、修復計画を今後の契約対象とする。必要な情報と振る舞いは要求案で明確化したが、正式Schemaはまだない。[受入条件](../acceptance-criteria.md)へ接続して具体化する。

要件化の後に[schemas](../../schemas/README.md)へSchema、[fixtures](../../fixtures/README.md)へ正常・境界・異常例を追加する。識別子、状態、失敗、互換性、署名対象、鮮度の判断は[未決定事項](../open-questions.md)から追跡する。

v0.3で追加した評価契約と採択記録、予定ケース/試行と結果の照合、依存する根拠、校正・データ用途、有限集合/推定の判断規則、セッション状態、実行境界、Findingの再検証も契約対象とする。R25〜R32の受入条件に対応させるが、フィールド名や保存技術はここでは確定しない。
