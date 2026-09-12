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

モデル、Policy、IAM、ツール、評価器の変更によって、以前は有効だったガードレールと検査系が劣化し得る。GAHは、その制御を今どこまで信用できるかを証拠に基づいて追跡し、問題があれば停止判断と修復計画につなぐ構想である。

## 2. Scope

MVP案はControl Registry、Mutation CI、Decay Detection、Remediation Plannerの4機能。全体構想には証拠管理、権限・影響範囲の制限、段階展開と復旧を含む。既存の評価・red-team・IAM・Policyツールはadapterで利用する方針案であり、再実装を目的としない。

MVPには本番の自動修復を含めない。絶対安全の保証、法令適合の自動保証、組織のリスク受容判断の代行は対象外。[要求草案](docs/requirements.md)と[未決定事項](docs/open-questions.md)を正本とする。

## 3. Constraints / Assumptions

現在は要求段階。最終判定・権限境界を決定的なルールで扱い、Criticalの不明・未検証・期限切れを安全成功にしない。Mutationは隔離環境内で行う要求である。これらの機能は未実装。

外部参照元から`assurance-core`を抽出する案、技術候補、数値閾値、SLO、MVP受入条件は今後の検討対象であり、今回確定しない。

## 4. I/O Contract

入力案はManifest、Control定義、評価結果、基準となる証拠。出力案はEvidenceEnvelope、指標とAssurance状態、Finding、修復計画。YAML/JSONの例は概念記法であり、機械可読な正式Schemaは未作成。[契約の入口](docs/contracts/README.md)で今後整理する。

## 5. Interfaces

製品CLI/APIとadapterの呼出契約は未定義。現時点で実行できるのは[RUNBOOK](RUNBOOK.md)の文書生成・検査・ワークフロー回帰テストのみ。

## 6. Verification

今回の文書整理は[EVALUATION](EVALUATION.md)に従って検収する。製品の評価指標・MVP受入条件は要求草案中の案であり、実測結果はない。
