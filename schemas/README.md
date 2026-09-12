---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Schema

[component-assessment.v1.schema.json](component-assessment.v1.schema.json)に部品診断入力を定義した。追加fieldを拒否し、版・型・容量・値域を固定する。JSONの整数表記、重複key/ID、分数の整合、fnrのbaseline等は[実装の検査](../src/gah/decision.py)と[wire検査](../src/gah/wire.py)を併用する。汎用Schema評価器によるmetaschema検証は未実施。

[Control Registry](control-registry.v1.schema.json)と[CaseSet](case-set.v1.schema.json)を追加した。[実装契約](../docs/runtime-contract-spec.md)に沿い、横断的なID一意性・依存閉包・段階とlabelの対応・byte上限は必ずruntime validatorで確認する。Schema単独で登録を採択しない。

Manifest、Evidence、修復計画等の全MVP Schemaは引き続き実装中。[データ契約](../docs/contracts/README.md)と[完了監査](../docs/mvp-completion-audit.md)を参照する。設計例を機械検証済みの製品契約として扱わない。
