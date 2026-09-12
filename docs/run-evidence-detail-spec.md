---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# Run Evidence Store v1

これは実行監督と管理authorityの間に置く、保存と決定的な再計算の部品契約である。OS認証、resource closure、Artifact/Evidenceの実体認証、baseline/contractの採択はこの部品の責務外である。未接続状態では全ての返却に`ci_eligible=false`を付け、通常CIの成功へ読み替えない。

## 境界と保存

`RunEvidenceStore`は独立SQLite DBを使い、全公開メソッドの時刻をstoreへ注入されたclock（既定は`int(time.time())`）から取得する。公開メソッドに`now`、再計算関数、承認関数、外部readerを渡すAPIはない。時計は非負整数、`2^53-1`以下、保存値からの巻戻りなしを要求する。

同一SQLite transactionを所有するauthorityからは`create_schema(db)`と`RunEvidenceBook(db, now=..., allowed_bindings=...)`を使う。`create_schema`は呼出側が開始したtransaction内でのみ実行し、独立`PRAGMA user_version`を変更しない。BookはBEGIN、COMMIT、ROLLBACK、接続生成、接続closeを行わず、authorityが渡した固定`now`だけを使う。Bookの失敗時のrollbackとcommitは呼出側transactionが担当する。StoreとBookは同じ保存・再計算実装を共有し、別の判定経路を持たない。

共有authorityが表の衝突検査へ使えるよう、`TABLES`と`COLUMNS`は`table_name -> column_names`のコピーを公開する。既存のrun evidence表が一部だけ存在するDBは自動修復せず、unsupportedまたはcorruptとして拒否する。

保存するJSONはUTF-8、canonical JSON、最大1 MiB、深度16、100000 nodes、整数は`-2^53+1..2^53-1`とする。重複key、BOM、非finite、float、指数表記、末尾JSON、非文字列key、不正Unicode、未知の型を拒否する。入力や保存障害は固定`EvidenceError.code`だけを返し、raw本文をエラーへ含めない。

`bound_runs`は開始時に`bind_run_manifest`を再実行したbundleとdigestを不変保存する。`allowed_bindings`はtrusted設定の`run_id -> canonical bound bundle digest`であり、開始時に一致しなければ保存しない。`execution_profile`もfixture、adapter、isolation digestを再検査して保存する。`baseline_context`は開始時に固定した比較文脈として保持するが、この部品単体では採択済みbaselineとは扱わない。

Attemptは`aggregation`の厳密AttemptRecord検査とrun/contract/policy/target/evaluator/fixture/isolation binding照合を通過したものだけ保存する。同一内容の再配送は元JSON、元digest、元時刻を変えず配送回数だけを別状態として増やす。異なる内容は`attempt_events`へ固定イベントを追加し、元recordを上書きせずrunをHOLDにする。terminal後の遅延attemptもreceiptを変えず、現在状態だけをHOLDへ落とす。

## 集計・Decision・receipt

選択したobligationが全てconstraintなら、率の指標が空でも制約事象・必須欠損・整合性から
Decisionを確定する。率を作り足さず、通常の診断APIで空metricsを許可する変更もしない。
candidateの制約FAILは`constraint_violation`としてDEGRADEDへ反映し、critical違反の
HOLDを優先する。他の率が合格した混在runでも、この制約違反を打ち消さない。

`aggregate(run_id)`は保存済みAttemptだけを読み、自前で`aggregation.aggregate`を再実行する。callerのattempt配列、metrics、summary、baselineを受け付けない。全scopeのmetricsと欠損・違反・依存伝播をcanonical payloadへ保存し、1 MiBを超える場合はtruncateせず固定エラーにする。集計履歴はdigest単位で不変に保持し、現在digestは別状態へ置く。

`finalize(run_id)`はcallerの`decision_input`、`assurance`、`passed`、`execution_status`、`summary`を受け付けない。保存集計を再計算し、metricsを最大100件ずつ`decision.assess`へ渡した後、全component結果と全metricsを一つの不変診断Decisionへまとめる。100件制限を理由にmetricsを丸めたり捨てたりしない。Decisionとfinalization receiptは一度だけ保存し、同じ再配送では同じbytes/digestを返す。

このreceiptは診断最終化であり、resource closureやOS認証が未接続のためauthorityの終端成功ではない。receiptには次を固定する。

```json
{
  "diagnostic_finalized": true,
  "authority_connected": false,
  "resource_closure_verified": false,
  "ci_eligible": false
}
```

## 現在利用状態

`record_evidence_state`は、上位Artifact authorityが取得した状態スナップショットを保存する補助APIである。入力は`evidence_events`へ追記し、同じ内容の再配送は同じeventを返す。`REVOKED`または`DELETED`の後にVALIDへ戻す更新、世代を戻す更新は拒否するため、撤回・削除は過去の状態を上書きしない。ここへ渡された値自体は認証根拠ではなく、このstore単体で利用成功を許可しない。`current_use(run_id, expected_binding)`は毎回clockを読み、binding、診断最終化、HOLD、Evidenceの有効期限を再評価する。

Evidenceが`UNKNOWN`、`REVOKED`、`DELETED`、`EXPIRED`、期限超過、binding不一致、保存台帳不整合のいずれかならreview readyにしない。全てが局所的に成立しても`authority_connected=false`と`resource_closure_verified=false`のため`use=false`であり、CI successを返さない。撤回・削除・遅延矛盾は過去receiptを変更せず現在利用可否へ反映する責務を上位authorityへ渡す。

`record_evidence_state`の`VALID`は接続済みauthorityからの検査済み証明ではない。この独立storeでは、値が妥当でも`current_use`の`ready_for_authority_review`を常にfalseとし、`EVIDENCE_UNVERIFIED`と`AUTHORITY_NOT_CONNECTED`を付与する。上位authorityが接続後に別transactionで実体、世代、資源closureを再照合する。

候補Evidenceの観測freshnessは通常24時間、保存90日を設計値とする。VALID snapshotは`checked_at + 24h`または明示`valid_until`の早い方を超えた時点でcurrentではない（境界時刻は有効）。baseline比較30日は、このstoreがbaseline Evidenceの実体へ接続していないため検証していない。`baseline_freshness_verified=false`と`adoption_verified=false`を常に返し、baseline_contextはbind済みtarget比較情報だけを固定保存する。30日証拠鮮度はArtifactStoreと後続authority接続で検査する。保存部品は期限と状態を保持するが、ArtifactStoreとの実体照合・撤回世代取得・外部課金closureは未接続であり、上位authorityが接続後に再照合する。

`current_use`の返却は診断の利用可否を確定しない。典型的な未接続時の形は次の通りである。

```json
{
  "kind": "run_use_decision",
  "ready_for_authority_review": false,
  "use": false,
  "ci_eligible": false,
  "reasons": ["EVIDENCE_UNVERIFIED", "AUTHORITY_NOT_CONNECTED"],
  "authority_connected": false,
  "resource_closure_verified": false,
  "baseline_freshness_verified": false,
  "adoption_verified": false
}
```

## 受入境界

- 初期bundleのbinding digest不一致、同run別bundle、既知の壊れたDBを拒否する。
- 独立connectionの同時開始・再配送で、不変bundleとreceiptを維持する。
- 同一Attemptの同額・同内容再配送を一件へ畳み、異内容後着を別event/HOLDへする。
- 保存Attemptだけから再計算し、caller summaryや自己申告の合格を採用しない。
- Critical欠損、必須欠損、依存先への不足伝播を集計へ残す。
- 時計巻戻り、DB保存失敗、canonical digest破損、Decision保存失敗を成功へ変換しない。
- 候補Evidenceは24時間freshness、明示期限、撤回/削除/期限切れを現在状態で拒否する。baselineの30日実体照合と90日物理保持運用は、このstoreの未接続範囲として後続authorityの受入で検査する。
- terminal後のreceiptを不変にし、遅延矛盾は現在状態だけをHOLDへ変更する。

本部品の試験合格は、保存・再計算・診断receiptの部品検証を示すだけであり、管理identity、resource budget closure、baseline採択、通常CI利用の完了を示さない。
