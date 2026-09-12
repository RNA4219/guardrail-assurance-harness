---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 比較契約への移行前検査の証跡

保存済み初回baselineを保持した契約移行の事前検査を、固定15件の実Docker経路へ接続した。
[検証manifest](verification.json)は全463テスト、既存441件の保持、実Docker52項目、
要求・受入条件・方針・設計例7ファイルの不変、実行時ソースとimageの一致を確認する。

| 証拠 | 範囲 |
|---|---|
| [全回帰](unit-check.json) / [log](unittest.log) | 463件成功。追加22件は構造検査12件、DB統合9件、readinessの不正JSON1件 |
| [実Docker](runtime-check.json) / [応答](runtime-observations.json) | 既存40項目を保持した52項目。移行前検査・主体分離・再起動・撤回後の同一要求の拒否 |
| [15実行のreceipt](execution-receipts.json) / [配置](runtime-deployment.json) | 固定fixtureの隔離、停止、回収。検証用DB volumeは保持 |
| [モデル照会](qwen-review-receipt.json) / [提出](qwen-submitted.txt) / [返答](qwen-response.txt) | 自作仕様だけの1回の設計レビュー。4,487 token。採択・性能の証拠にはしない |
| [監督レビュー](../../reviews/mvp-transition-20260911.md) | Lunaへの分担、親の修正、再レビュー2件の処遇 |

途中版の[462テスト](prior-unit-check.json)と[52項目](prior-runtime-check.json)も保存した。
応答契約の厳格化とreadiness修正後に同じ固定authority imageで再検証し、最終結果を分けている。
一部の変更ソース・検証ツール・テスト本体もこのpacketへ保存し、hashをmanifestへ結んだ。

`preflight_ready=true`は現在の前提照合であり、後続runや採択の許可証ではない。
現行契約はgeneration 1を維持し、generation 2の採択validationは拒否する。
候補専用run・旧条件回帰・世代更新・UC-LLMの認証/資源・Finding/Plan・通常CIは未完了。
`full_mvp_accepted=false`、`release_gate=no_go`、`ci_eligible=false`を維持する。
