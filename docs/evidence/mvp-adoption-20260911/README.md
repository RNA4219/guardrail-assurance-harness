---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 固定UC-CI初回baselineの接続証跡

[統合検証](verification.json)は、固定15件の実Docker実行を同DBの精算・Evidence・初回baseline採択へ結んだ工程の記録である。
全MVP、通常CI、契約更新、UC-LLMの認証・モデル資源管理は未完了で、releaseはno_go、ci_eligibleはfalseを維持する。

| 検証 | 結果 | 証跡 |
|---|---|---|
| 全部品テスト | 441成功、前工程357件を保持 | [結果](unit-check.json)、[log](unittest.log) |
| 固定15entryから初回採択・再起動・利用・撤回 | 40項目成功 | [結果](baseline-check.json)、[観測](baseline-observations.json)、[実行receipt](baseline-execution-receipts.json) |
| 参照輸送fixtureと認証・精算・証跡保存 | 41項目成功 | [結果](runtime-check.json)、[観測](runtime-observations.json) |
| OS identity・役割分離・方針採択 | 22項目成功 | [結果](authority-check.json)、[観測](authority-observations.json) |
| 固定測定側の校正 | 36vector成功 | baseline観測内のfixture_prepare応答 |
| DGX Qwenによる設計照会 | 1要求完了、7,013token | [receipt](qwen-review-receipt.json)、[送信内容](qwen-review-submitted.txt) |

最終3経路は同じauthorityイメージと現行ソースで検査した。15entryは10制約と5種の検査系を正常条件で実行したものであり、36vectorは測定側の校正である。LLM性能や全劣化条件の比較をこの件数へ混ぜない。

途中のテスト作成ミス、log集計の失敗、制約のみのDecision最終化不成立、初期Dockerエラーを保持する。
prior-runtime-check-05は起動完了確認を追加する前の成功記録で、最終結果はruntime-check.jsonとする。
コンテナは回収済み。DB volumeは保存したdeploymentに対応する状態として保持する。

指摘と修正は[監督レビュー](../../reviews/mvp-adoption-20260911.md)、残る要件は[完了監査](../../mvp-completion-audit.md)へ結ぶ。
