---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# データ契約の入口

[拡張仕様の設計ケース](productization-spec-cases.v1.json)は36件の入力断片と期待値で、正式Schemaや実行済み製品証拠ではない。[統合仕様](../productization-spec.md)と合わせて読む。

要求v0.4から独立に作成した基本設計を置く。判定・保存・診断CLIは[コア詳細仕様](../detail-spec.md)へ具体化し、部品入力Schemaと実装を追加した。全MVPの正式Schema・実行監督・外部adapterは未完成。

| 資料 | 内容 |
|---|---|
| [評価契約 v0.3](evaluation-contract.md) | 情報と参照、結果照合、予算、時刻、CI返却、再検証 |
| [初回・採択・再開・終了の契約 v0.3](lifecycle-contract.md) | 信頼起点、確定時再検査、所有世代、精度、保存前判定、同時障害 |
| [初期運用値JSON](initial-policy.v1.json) | 運用方針v1の機械可読な設計表現。runtime設定ではない |
| [算術例JSON](decision-examples.v1.json) | 初版の43例。製品試験はNOT_RUN |
| [状態と境界の追加例](review-examples.v2.json) | v0.2の25例。製品試験はNOT_RUN |
| [状態例](state-examples.v3.json) | v0.3の32例。製品試験はNOT_RUN |
| [評価設計 v0.3](evaluation-design.md) | ケース、校正、Control、11シナリオ群、32要求との対応 |

[要求](../requirements.md)のControl、run、Evidence、Mutation、Assurance、Finding、計画と、評価契約・採択記録・試行照合・依存・校正・状態分離・再検証を扱う。意味と参照関係は初版に記述し、型・互換性・物理的な保存方式は次工程で固定する。

[初期運用方針](../operating-policy.md)のCI成功状態、閾値、予算とrun間合算、管理AIのidentity・context・許可範囲を具体化した。通常の管理更新に都度の人間承認を置かず、認証された構造化要求を決定的に検査する。

[独立設計と非掲載方針](../reference-boundary.md)に従い、外部参照元のコード・Schema・テスト・文書・データを持ち込まない。正式Schemaは[schemas](../../schemas/README.md)、製品fixtureは[fixtures](../../fixtures/README.md)へ実装準備Taskで追加する。[受入条件](../acceptance-criteria.md)と[判断事項](../open-questions.md)を合わせて確認する。
