---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 認証された観測と保存・精算の接続仕様 v1

`assurance_authority.py`は、既存のAdoptionStoreとEvaluationExtensionの同じSQLiteへ
RunEvidenceBookを接続する。固定UC-CIの15entryから初回baseline採択まで実Dockerで検証した。
通常CI・契約更新・実モデル送信を含む全MVP受入は未完了である。前工程の357テストと実測は、
[その時点の証跡](evidence/mvp-evaluation-20260911/README.md)として保持する。

## 1. 操作と所有権

外部要求は既存brokerのSO_PEERCREDで認証し、次の固定操作へ振り分ける。
役割、時刻、合格、精算完了、保存先を要求本文から採用しない。

| 操作 | 主体 | 処理 |
|---|---|---|
| evidence_open | operator | 保存済み契約・Manifest・Planからbundleを再bindし、証跡領域を開始 |
| evidence_record | validator | 停止・精算済みoperationと厳密に結び付くAttemptを保存 |
| evidence_finalize | operator | 保存AttemptからDecisionを再計算し、精算・根拠を不変receiptへ結ぶ |
| evidence_current | manager / validator / operator | 現時点のbinding・撤回・期限・採択条件・保存整合を再照合 |
| evidence_revoke | operator | 過去receiptを変えず、不可逆な撤回eventを追加 |

transactionの開始・commit・rollback・接続終了はAdoptionStoreが所有する。
RunEvidenceBookは開始済みtransactionとauthorityの固定時刻を使う。途中の保存失敗で
Decisionだけ、Evidenceだけ、receiptだけが確定しない。同じDBの資源状態を再検査してから
一つのcommit点で保存する。

## 2. 固定fixtureのbinding

この接続段階は、既存DockerRunnerの固定fixtureに限る。`fixture-runtime.lock.json`の
worker digest、`normalized.py`のdigest、DockerRunnerの固定PROFILEのcanonical digestを
authority側で取得する。Manifestのenvironment_refもこのPROFILE digestを指す。
この参照の形だけをOS隔離の証明にはせず、実行監督・観測・資源operationとの対応を別に検査する。

record時はrun/operation/owner_epoch、Planのobligation/case/trial/variant/段階、対象・評価器、
fixture・adapter・isolationを一致させる。ResourceBookのManifest、PolicyProfile、profile、
deadlineも保存bundleと照合する。別runや別manifestの精算結果を利用しない。

観測はdispatch intent以後に開始し、確認された停止時刻までに終了していることを要求する。
停止不明、usage未確定、解放済み予約、矛盾したoperationは取り込まない。重複・遅延矛盾の
保存方式は[RunEvidenceBook](run-evidence-detail-spec.md)に従い、元のAttemptを上書きしない。
validatorの主体・context・権限世代・観測保存時刻を別の不変origin行へ結び付ける。

## 3. 終了記録と現在の利用

finalizeは、resource runが閉鎖され、未停止・未精算・予算超過・取消しがないことを
確認する。保存Attemptとoriginから診断Decisionを再計算し、Manifest、bundle、Decision、
resource closure、Evidenceの内容参照をauthority内へ保存する。

Evidenceは初回baseline候補ならbaseline_comparison、その他ならnormal用途を作成時に固定する。
元の観測時刻から30日または24時間、保持は90日を上限とし、取得し直した時刻で期限を延長しない。
保存したEvidenceと関連artifactの参照先、本文、digest、run、元のDecisionを再照合する。

過去receiptの再配送は過去時点の事実を返す。現在の撤回や期限切れを理由に本文を書き換えない。
現在の利用可否はevidence_currentで毎回再計算し、撤回・失効・遅延矛盾・破損を反映する。
撤回は固定eventと現在状態の更新を同じtransactionで行い、同じ撤回を再配送しても履歴を増やさない。

## 4. 採択・実入力への残る接続

`authority_connected=true`と`resource_closure_verified=true`は、認証された保存と
精算の接続範囲を表す。輸送fixtureの参照やcallerの合格から、実入力・oracleの実在性を
推定しない。輸送fixtureは`input_materialization_verified=false`である。

`fixture_prepare`は、採択済み方針から[固定pack](fixture-materialization-detail-spec.md)を生成し、
実入力・oracle・初期状態・15個のentryを同DBへ保存する。計画・参照・worker/profileと
36vectorの測定校正を再検査できるrunだけが、`input_materialization_verified=true`を得る。
固定UC-CIのこの経路はLLM評価の400件要件や検出率の校正へ読み替えない。
測定校正は自作の固定関数と正規化器の検査であり、Docker隔離の受入は別に実施する。
sourceは校正の前後で照合し、検証したworker bytesを使う。

receiptの`adoption_verified=false`、`ci_eligible=false`は維持する。初回baselineの採択は
別APIで確定し、候補runの過去receiptを採択後に書き換えない。

固定packの初回採択の接続検証に続き、更新契約、比較文脈の実体解決、Finding/Planの
認証された保存と修復確定、モデル送信、通常CI判定を続けて接続する。比較必須のrunを
元baselineなしで現在Registryへフォールバックさせず、未接続なら明示的に拒否する。

## 4.1 固定packの準備

managerの`fixture_prepare`要求は`schema_version=1`、`request_id`、`run_id`、
`policy_series_id`だけを受け取る。受入集合・校正合格・実行結果・保存先の自己申告は
受け取らない。返却`prepared`に固定Manifest/Plan/Contract/Registry/CaseSetとpackを含み、
後続の契約proposal/validation/adopt、run_beginにその内容を渡す。
準備時刻・方針世代を固定し、後の照会で作り直して本文を変えない。

保存admission・関連objectのいずれかが保存に失敗すれば同じtransactionをrollbackする。
validation/currentは保存内容とprofileを読み直す。requestの再配送による過去結果は
immutable receiptとして返し、現在の利用照会と区別する。

## 5. 検証範囲

統合unit testでは固定した自作の輸送fixtureを使い、実APIの採択・資源予約・精算・
観測保存・最終化・現在状態、主体拒否、binding差替え、保存失敗のrollbackを検査する。
unit testはDockerやモデルを起動しない。固定15entryの実Docker受入で、全10制約と5種の検査系を
正常条件で実行し、初回baselineの採択と再起動後の利用・撤回を確認した。全劣化条件を新しいrunで
比較する通常運用と、UC-LLMの実入力・認証・資源への接続は継続する。
