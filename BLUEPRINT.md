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

現在は契約・評価設計から判定・永続化・診断コアの初期実装へ進んだ。最終判定・権限境界を決定的なルールで扱い、Criticalの不明・未検証・期限切れを安全成功にしない。Mutationの隔離実行、管理AI認証、外部runner等は未接続である。

初期対象はcoding agentの開発・CI（UC-CI）とLLMガードレール評価（UC-LLM）の両方で、利用者確認済み。証拠と判定の基盤を共通化し、利用場面ごとに指標・比較条件・受入シナリオを持つ。[初期運用方針](docs/operating-policy.md)にWARNINGのCI成功、生成AIによる基準管理、初期閾値・予算を定めた。外部参照元はクローズド資産であり、共通core抽出やコード等の移植は行わない。[参照境界](docs/reference-boundary.md)の下で一般的な運用の考え方を参考に、GAHの要求から独立に設計する。指標計測・評価データ・校正、技術選定・本番SLOは次工程で具体化する。

## 4. I/O Contract

全MVPの入力案はManifest、Control定義、評価結果、基準となる証拠。出力案はEvidence、指標とAssurance状態、Finding、修復計画、CI用receipt。[契約](docs/contracts/README.md)に情報と参照関係、初期値JSONと期待例を記載する。初期実装は[部品入力Schema](schemas/component-assessment.v1.schema.json)と診断出力に限定し、全MVPの受入を示さない。

## 5. Interfaces

[詳細仕様](docs/detail-spec.md)に部品API・SQLite・診断CLI、[adapter仕様](docs/adapter-spec.md)に外部接続境界を定める。[RUNBOOK](RUNBOOK.md)から診断と回帰試験を実行できる。診断完了でもci_eligible=false、終了1を固定する。通常CIの合格発行機能と外部runnerは未接続。

## 6. Verification

文書と部品の技術検収は[EVALUATION](EVALUATION.md)に従う。全MVPの製品検証は[32件の受入条件](docs/acceptance-criteria.md)に対応付ける。敵対的な反例検討で、評価契約の独立性、結果の照合、依存先への影響、推定の不確かさ、状態分離と修復確認を補強した。[拡張案](docs/extension-roadmap.md)は着手条件付きの後期候補。部品の実行証跡はあるが、全MVP受入と実モデル性能の測定は未実施。
