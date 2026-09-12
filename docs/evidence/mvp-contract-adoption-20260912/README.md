---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 契約世代2の採択と現在有効性の検証証跡

[統合検証](verification.json)は507テストと実Docker150項目が成功した。
固定UC-CIの初回15件・旧条件15件・新条件30件を実行し、保存Evidenceから契約generation 2を
採択した。baselineはgeneration 1を保持する。全MVP受入と通常CIは未完了である。

| 検証 | 結果 |
|---|---|
| 全回帰 | 507件成功、前回494件を全保持、新規13件。1,051.945秒 |
| 固定実行 | 60件全て完了。隔離・停止・回収を確認し、operation IDは重複なし |
| 採択 | 専用validate/adoptの主体制限、旧契約・baselineの期待世代、原子的更新 |
| 現在状態 | 採択後と再起動後に有効。根拠撤回後は無効となり、採択・候補receiptは不変 |
| 保存故障 | SQLite triggerによる採択・DB移行のrollbackを検査 |
| 予算 | 別runの未送信予約で24時間合算が変わっても、完了済み候補の採択は成立 |
| 保全 | 試験ソースと、要求・初期方針・設計例7ファイルのhashが一致 |

[unit-check](unit-check.json)、[実Docker check](runtime-check.json)、
[60件の実行receipt](runtime-execution-receipts.json)、[authority応答](runtime-observations.json)、
試験時のソースとcheckerを保存した。応答のelapsed_secondsはclientの起動・回収を含む所要時間である。
製品imageは`sha256:b5f19c381ac4797803d3ae047f153005356d4aa0567c205c03687b58f271c323`。
コンテナの回収と不存在を再確認し、DB volumeは証跡として保持した。

今回の採択用モードは、従来133項目のうち127項目を保持し、6項目を採択後の検査へ置き換えている。
旧候補の未送信予約probeをそのまま実行したとは扱わない。差分の項目名は統合検証へ記録した。
候補だけを確認する従来の`--candidate-runs`モードも残る。

先行の[全回帰](prior-unit-check.json)は507件中1件の再upgradeエラー分類が失敗し、従来の分類へ修正した。
先行の[実Docker](prior-runtime-check.json)は60件を完了した後、validationの応答取得で失敗した。
詳細な輸送エラーが保存されていないため原因を断定しない。要求受信5秒、結果待ち30秒、
外側のclient待機45秒を分け、最終実行のunits-02/runtime-02で上記の成功を確認した。
失敗した先行記録を上書きせず保持する。

DB移行は自作helperから生成した、候補と片側runを含む合成旧v4 DBを検証した。
配置済み旧volumeを移行した実績ではない。予算試験は一時SQLite内の合成予約で、実送信・課金はない。

Lunaの下書きと初期レビュー後に利用上限へ達したため、親が修正・レビュー・検証を引き取った。
DGX Qwenは範囲を絞った2回の照会がいずれも50秒で時間切れとなり、レビュー本文は取得できなかった。
[照会1](qwen-01-receipt.json)・[照会2](qwen-02-receipt.json)と提出稿を保持し、モデルの最終独立レビューは未完了とする。
修正理由と処遇は[監督レビュー](../../reviews/mvp-contract-adoption-20260912.md)に記録した。

非掲載情報の検査は製品テストと別に扱う。旧ビルド用ディレクトリ
`.ga/authority-images/a3117c1630933eb7-xa1d5cpf`は前工程の昇格読取りでも列挙できず、内容を未検査として残す。
読取り可能な範囲の一致0件を全範囲の合格へ読み替えず、列挙エラーを最終照合へ記録する。
ACLや所有者を変更していない。

通常gen2 runの比較context・入力実体化、条件変更を伴う契約・baseline更新、認証されたモデル資源、
Finding/Plan、通常CIと全32要求の受入は継続する。
`full_mvp_accepted=false`、`ci_eligible=false`、`release_gate=no_go`を維持する。
