---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 外部参照元からの評価運用基盤の転用

## 転用元の確定

利用者が指定した転用元はRNA4219/private-reference（参照先非掲載）（外部参照元）。2026-09-10にmainのcommit `nonpublic-revision` を取得し、静的に確認した。package versionは0.1.0、campaign Schemaは1.0。今後の変更追跡にはpackage versionだけでなくcommitを用いる。

転用対象は評価運用の骨格とする。この版はManifest・進捗・期限・提出枠の管理を実装している。LLMの検出率/FPR、Mutation評価、比較統計、評価器校正、期待ラベル付きの評価データを提供する実装は確認できない。GAHの評価機能を外部参照元に実装済みとは扱わない。

調査用checkoutはGAHの`.ga/`内に置き、製品コード・依存関係へ組み込んでいない。上流のCLI・テストは実行していない。[取得記録](evidence/reference-confirmation-20260910/source-inspection.json)にcommit・Git blob・確認ファイルのhashと範囲を記録する。

## 転用対応表

「転用」は今後の実装対象の選定を表す。現時点でGAHへのコード移植は行っていない。

| ID | 確認した外部参照元の実装・資料 | GAHで転用する範囲 | 補う条件と受入への接続 |
|---|---|---|---|
| 外部参照元-M01 | build_manifest / validate_manifest（参照先非掲載）、Schema（参照先非掲載） | 入力構築・型/参照/日時・cross-field検査の構成 | Kaggle固有の項目をGAHのControl・評価契約・対象内容へ置換し、予定ケース・版・必須義務も検査する。R01/R02、AC01/AC02 |
| 外部参照元-M02 | checkpoint_manifest（参照先非掲載） | カウンタ・段階・時刻の巻戻し拒否と更新前後の検査 | 単なる件数入力を評価証拠にしない。run/対象/契約/試行の結び付け、重複・取消し・遅延結果の規則を加える。R05/R18/R26、AC05/AC18/AC26 |
| 外部参照元-M03 | compute_status（参照先非掲載） | 時刻を入力できる決定的な判定と理由一覧 | `continue/release_ready/escalate/hold`は外部参照元固有。GAHの五状態・Critical優先・WARNING成功と初期値で再定義する。R04/R12/R13/R19、同番号のAC |
| 外部参照元-M04 | initialize_campaign / checkpoint_manifestの保存（参照先非掲載） | 正本JSON・イベント追記・一時ファイルからの置換という保存構成 | 現状はManifest置換後に別ファイルへイベントを追記する。GAHでは並行更新・途中失敗・再取込みの整合、採択済み証拠の不変性を追加検査する。R18/R22/R26、同番号のAC |
| 外部参照元-M05 | lanes / recovery設定（参照先非掲載）、Blueprintの不変条件（参照先非掲載） | baselineと変更候補の分離、retryと世代変更を区別する考え方 | `identity_manifest`や不変性フラグの存在を強制の証明にしない。内容識別子・権限・採択根拠・セッション分離を実装する。R24〜R26/R30/R31、同番号のAC |
| 外部参照元-M06 | CLI（参照先非掲載）、Schema tests（参照先非掲載）、campaign tests（参照先非掲載） | JSON出力・入力エラー・Schemaとruntimeの対応を検査する構成 | GAHの正常・境界・欠損・矛盾・古い結果のfixtureに置換する。テスト名にatomicとあることだけで並行更新や障害時の整合を検証済みにしない。R01/R02/R19/R23、同番号のAC |

`project_active_slots`、Kaggleの提出枠・締切縮退・6/24/48時間の既定値、releaseだけに厳格Gateを置く運用は今回転用しない。GAHはPR時点でも必須条件を検査し、[初期運用方針](operating-policy.md)を維持する。

## GAHで追加する評価機能

外部参照元の`actual_receipts`と`accepted_results`はcheckpointで与える件数であり、評価結果の内容や由来の検証器ではない。次の機能を外部参照元から取得済みとは扱わず、既存のGAH要求に従って追加する。

- generic command/Promptfooの固定ケース結果の取込み、ControlとMutation結果の正規化（R05〜R08）。
- TP/FP/TN/FN、検出率/FPR、Mutation Score、比較可否、絶対条件と差分の判定（R09〜R13/R29）。
- 合成ケースと期待ラベルの由来、開発/校正/受入の分離、評価器校正、単発・二段階ケース（R28/R30）。
- 予算予約・実行中の境界、AI管理の採択権限、証拠の内容識別・鮮度・失効と再検証（R03/R04/R18/R22/R25〜R27/R31/R32）。

初期は固定した有限集合の回帰を評価する。外部参照元に母集団推定の手法はないため、推定を有効にする場合の手法・信頼水準・標本/停止規則は別の評価設計で選定する。外部参照元由来の正常ケースをLLMガードレールの期待ラベル付きデータに読み替えない。

## 管理と利用条件

基準管理AIが許可範囲内でGAHの契約とbaselineを採択し、決定的な処理が権限・根拠・予算を検査する。外部参照元のcampaign運用規約を理由に、GAHの通常の基準更新へ都度の人間承認を追加しない。

この固定版のpyproject.toml（参照先非掲載）は`Proprietary`表記で、追跡ファイルにLICENSEはない。利用者による転用指定を根拠として計画するが、公開OSSライセンスが確認済みとは記録しない。コードを移植する工程で帰属・転用範囲を記録し、公開・配布前にGAH/外部参照元の利用条件の表記を揃える。今回ライセンス変更や公開は行わない。

## 次工程の完成条件

外部参照元-M01〜M06ごとに、移植する関数/契約、GAH側の変更点、元commitとファイル、期待結果、対応する受入条件を定める。外部参照元のテスト成功や運用実績をGAHの成功へ継承せず、追加した条件を含むGAHの受入で確認する。これで転用元の特定は完了し、残りは実装方式・fixtureの設計となる。
