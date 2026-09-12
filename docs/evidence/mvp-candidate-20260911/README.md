---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 契約候補runの接続証跡

[統合検証](verification.json)は全8条件が成功した。

| 検証 | 結果 |
|---|---|
| 全unittest | 494件成功、前回463件保持、新規31件 |
| 実Docker | 133項目成功、前工程52項目を保持 |
| 実行件数 | 初回15件、旧条件15件、新条件baseline/candidate計30件 |
| 保存 | 新旧の別目的・Evidence・closure、再起動後の不変receipt、freshな保存参照照合 |
| 撤回 | 未開始side/未送信dispatchを拒否、予約取消しと回収、旧receiptを保持した現在利用拒否 |
| 保全 | 試験ソースと要求・初期方針・設計例7ファイルのhash一致 |

製品イメージは`sha256:83add5fedc46abbd0640ac885ce91ee96940637d7aa0ae961659e28ed523ce74`。
runtime-02を最終結果とし、runtime-01の検証コードの比較失敗は[旧check](prior-runtime-check.json)と
[receipt比較](prior-receipt-comparison.json)へ残した。保存本文と応答の封筒fieldを区別して修正した。
コンテナは回収・不存在を再確認し、DB volumeは証跡として保持した。

[unit-check](unit-check.json)、[実Docker check](runtime-check.json)、[実行receipt](runtime-execution-receipts.json)、
[authority応答](runtime-observations.json)と、実行したソース・checkerの写しを保存する。
artifactのhashと現行ソースのhashは統合検証へ記録する。runtime-manifestの15件は初回packの数で、
最終checkと実行receiptが候補を含む60件を記録する。

既知v3の固定ソースは[移行元照合](predecessor-source.json)で確認した。
DB移行試験には合成v3 fixtureを使っており、配置済み旧volumeの移行を実測した証拠ではない。
[Qwen照会](qwen-receipt.json)は補助レビューであり、認証・採択・性能の証明に使わない。
[親・Lunaレビューの処遇](../../reviews/mvp-candidate-20260911.md)を参照する。

[最終照合の再検査](final-check-followup.json)では、非掲載情報の検査に未確認範囲を残す。
初回照合は生成前の結果ファイルへのリンクを検査して失敗し、結果保存後の再検査で文書整合を確認した。
読み取れた本文・pathの一致は0件だが、旧ビルド用ディレクトリ
`.ga/authority-images/a3117c1630933eb7-xa1d5cpf`を列挙できず、全範囲の検査は未合格である。
昇格した読み取りでも同じ制限があり、ACLや所有者を変更していない。
従来のwalk検査はディレクトリ列挙エラーを数えていなかったため、過去の成功記録から
このディレクトリの内容を検査済みとは推定しない。今回の検査はエラー1件を保持する。

generation 2の採択validation・原子的更新、通常run/CI、全MVPの受入は未完了である。
`full_mvp_accepted=false`、`ci_eligible=false`、`release_gate=no_go`を維持する。
