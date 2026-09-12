---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
template_version: 1.0.0
---

# Blueprint

## 1. Problem Statement

coding agentの変更による制約・検査系の劣化と、LLMガードレールの変更による検出性能・誤検知の変化を追跡する。GAHは、その制御を今どこまで信用できるかを証拠に基づいて示し、問題があればCI判定と修復計画につなぐ構想である。

## 2. Scope

MVP案はControl Registry、Mutation CI、Decay Detection、Remediation Plannerの4機能。全体構想には証拠管理、権限・影響範囲の制限、段階展開と復旧を含む。既存の評価・red-team・IAM・Policyツールはadapterで利用する方針案であり、再実装を目的としない。

MVPは宣言された合成fixtureでの評価と計画生成まで。本番の自動修復・任意対象への攻撃実行は含めない。絶対安全の保証、法令適合の自動保証、組織のリスク受容判断の代行は対象外。[要求明確化案](docs/requirements.md)と[未決定事項](docs/open-questions.md)を正本とする。

## 3. Constraints / Assumptions

現在は要求段階。最終判定・権限境界を決定的なルールで扱い、Criticalの不明・未検証・期限切れを安全成功にしない。Mutationは隔離環境内で行う要求である。これらの機能は未実装。

初期対象はcoding agentの開発・CI（UC-CI）とLLMガードレール評価（UC-LLM）の両方で、利用者確認済み。証拠と判定の基盤を共通化し、利用場面ごとに指標・比較条件・受入シナリオを持つ。外部参照元から`assurance-core`を抽出する案はMVP着手の前提とせず、技術選定・運用閾値・SLOは今後の検討対象とする。

## 4. I/O Contract

入力案はManifest、Control定義、評価結果、基準となる証拠。出力案はEvidenceEnvelope、指標とAssurance状態、Finding、修復計画。YAML/JSONの例は概念記法であり、機械可読な正式Schemaは未作成。[契約の入口](docs/contracts/README.md)で今後整理する。

## 5. Interfaces

製品CLI/APIとadapterの呼出契約は未定義。現時点で実行できるのは[RUNBOOK](RUNBOOK.md)の文書生成・検査・ワークフロー回帰テストのみ。

## 6. Verification

要求文書の技術検収は[EVALUATION](EVALUATION.md)に従う。将来の製品検証は[32件の受入条件案](docs/acceptance-criteria.md)に対応付ける。敵対的な反例検討で、評価契約の独立性、結果の照合、依存先への影響、推定の不確かさ、状態分離と修復確認を補強した。[拡張案](docs/extension-roadmap.md)は着手条件付きの後期候補。要求案の全面承認・製品実測結果はない。
