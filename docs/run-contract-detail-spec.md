---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 評価契約・実行計画・開始境界の詳細仕様

[全MVP Task](tasks/TASK.mvp-completion-09-11-2026.md)の次の接続工程。[評価契約](contracts/evaluation-contract.md)と[LC01〜LC06](contracts/lifecycle-contract.md)を正本とする。以下の構造検査、管理DBの拡張、資源台帳を実装し、管理主体と実行監督へ接続する。未接続の部品を通常CI成功へ使わない。

## 1. 共通

既存の`contracts.py`と同じUTF-8/1 MiB/深さ16/100000 nodes/2^53−1/一意ID/小文字SHA-256の規則を使う。canonical bytesによる内容参照はkind/id/digest。直接Python値もJSON型だけを許し、非文字列key、float、tuple、孤立surrogateを拒否する。未知fieldと未知版は拒否し、コピーを返す。参照一致だけでは認証完了にせず、採択・失効・現在時刻は管理DB内で別に照合する。

## 2. EvaluationContract v1

必須fieldはschema_version=1、kind=evaluation_contract、contract_id、generation、policy_series_id、policy_generation、policy_ref、registry_ref、case_set_ref、calibration_case_set_ref、evaluator_refs、required_categories、use_cases、comparison、required_outputs。

- generation、policy_generationは1以上。policy_ref.kind=policy_profile、registry_ref.kind=control_registry、両case_set参照.kind=case_set、evaluator_refsはkind=evaluatorの一意で空でない参照配列。
- use_casesはUC-CI/UC-LLMから一つ以上を一意に指定する。required_categoriesは空でない一意ID配列、required_outputsはdecision/evidence/findings/plans/run_receiptの5項目を一意に全て含む。
- comparisonはmode、baseline_ref、changed_axes、reasonの4field。mode=requiredではkind=baselineのbaseline_refを必須、reason=null。mode=not_applicableではbaseline_ref=null、reason=initial_baseline_pendingに固定する。changed_axesはtarget/evaluator/policy/corpus/environmentの一意な配列で、条件差がない場合は空配列とする。空配列は比較義務を免除せず、実体の条件差は別途照合する。非適用を通常回帰へ使えない。
- `validate_evaluation_contract(value)`は形状・一意性・固定値・上限を検査する。配列上限はevaluator 1000、category 256、use_cases 2、outputs 5、changed_axes 5。
- `bind_evaluation_contract(contract, policy, registry, case_set, calibration_set)`は全参照のid/digest、方針の初期境界、Registry、CaseSetの厳格検査、acceptance/calibration用途、必須カテゴリの完全一致、Registryで参照する評価器集合の完全一致、義務とUCの対応を検査する。各UCに少なくとも一つのrequired義務を必要とする。constraint/mutationはUC-CI、llm_metricはUC-LLMに属する。受入集合の件数/用途間重複/校正結果は管理採択の検査で別途必要とし、構造検査だけで完了にしない。

## 3. TrialPlan v1

必須fieldはschema_version=1、kind=trial_plan、plan_id、contract_ref（kind=evaluation_contract）、entries。entriesは空でない最大10000件。各entryはobligation_id、case_id、trial_id、variant、stage_ids、required、event_policy、evaluator_ref、target_refの9field。variant=candidate/baseline、stage_idsは順序付き1〜2個の一意ID、requiredはbool、event_policy=forbidden/aggregate/none。evaluator/target参照のkindも固定する。

同じobligation/case/trial/variantを重複させない。baselineとcandidateは別の予定実行とし、必須の段階・義務・評価器はRegistry/CaseSetの内容から照合する。caseやstageの改名で予定外の結果を混入させない。

`validate_trial_plan(value)`は構造検査。`bind_trial_plan(plan, contract, registry, case_set, selected_controls, target_refs)`は契約参照、選択と依存閉包、Controlの対象、義務のrequired/event_policy/evaluator、CaseSetのcaseと順序付き全stageを照合する。選択した必須義務を少なくとも一つのcandidate entryで満たす。llm_metricの必須義務では受入集合の全caseをcandidateで予定し、比較必須なら同じcase/trial/stageにbaselineも必要とする。baseline非適用時はbaseline entryを拒否する。複数caseを同じtrial_idへ集約しない。

## 4. RunManifest v1

必須fieldはschema_version=1、kind=run_manifest、run_id、contract_ref、purpose、use_cases、target_refs、control_ids、baseline_ref、plan_ref、policy_ref、profile、environment_ref、actor_context_ref、created_at、deadline。

purposeはcalibration/contract_validation/baseline_candidate/contract_old_regression/contract_candidate/regression/diagnostic。profileはpr/full。created_atは非負整数、deadlineはそれより後。kindはplan_ref=trial_plan、policy_ref=policy_profile、target_refs=target、environment_ref=environment、actor_context_ref=actor_context。target_refs/control_ids/use_casesは空でない一意配列。

`validate_run_manifest(value)`は構造検査。`bind_run_manifest(manifest, contract, plan, policy, registry, case_set)`は全参照、UCの一致、依存閉包、全targetの一致とTrialPlanを再照合する。regressionでは採択済み評価契約と比較必須のbaseline参照を必要とする。baseline_candidateではbaseline=nullと比較非適用を必要とし、profile=full。calibration/contract_validationはBootstrapContractの専用経路へ渡すため通常契約とのbindでは拒否する。diagnosticは契約の比較状態を明示的に継承する。deadlineはcreated_at+選択profileのelapsed_secondsを超えない。構造bindの返却にci_eligible=trueを付けない。

contract_old_regressionとcontract_candidateは[契約移行](contract-transition-detail-spec.md)の専用入口へ渡す。
両者はfull必須で、旧側は比較非適用/null、新側は比較必須/一致baselineを必要とする。
通常のrun_beginではこれらの目的と候補に予約されたrun IDを拒否する。

## 5. 管理DBへの接続

方針採択と評価契約/校正/baseline/実行開始の可変状態を別DBのキャッシュで確定しない。既存AdoptionStoreへ、信頼するコードで固定した拡張を渡す。拡張は固定table/column、schema作成、config digest、許可操作、要求検査、transaction内の処理を定義する。要求本文から拡張やSQLを選ばない。

拡張なしは既存v1 DB/APIを維持する。拡張ありは新しいv2 DBで動き、既存v1の暗黙変換を拒否する。旧DBの破棄や初期化を自動で行わない。既存のOS認証・actor取消し・request_id衝突検査・時刻の単調性・BEGIN IMMEDIATEを共有し、拡張側が外部I/Oをtransaction中に呼び出さない。

拡張操作の書込み結果は不変の再配送、現在状態の照会は毎回再評価とする。失効したpolicy、別世代、失効した校正、比較根拠欠損で採択/実行開始しない。独立validatorが認証済みの観測から決定的に校正と受入を計算する。passedという自己申告だけのAPIは作らない。

## 6. 全資源台帳の接続

同じ管理DBのtransaction内にrun資源とoperation予約を持ち、開始前のcase/trial実行、model call、input/output token、費用上界、同時実行枠をまとめて予約する。プロファイルは採択したPolicyProfileから固定する。stageごとのcall/tokenと、初段で消費するcase/trialを区別し、baseline/candidate・再試行を各operationとして加算する。

実際のdispatch前に不変のoperation IDと意図を保存し、送信不明を新しいIDで再実行しない。停止確認済みまで同時枠を解放しない。停止と料金/usageの確定は別の状態で保持する。使用量が不明なら予約を残し、budget_closureを成功にしない。実測超過は元の予約を書き換えず記録し、後続開始を拒否する。失敗・取消しの使用量も消さない。

全体費用は確定時刻が(T−86400,T]の確定額と年齢を問わない未精算予約の和。費用は既存のLC04と同じ操作単位の保守的切上げで整数化する。0費用には固定ローカル実行等の信頼する非課金根拠を必要とする。実時間/所有世代/取消し/clock rollbackも開始時に照合する。台帳の引数は認証済みの監督から取得し、candidateの成功/usage文字列を直接使わない。

この仕様の確定後、実装・独立レビュー・契約/競合試験・実行監督への接続結果をTaskと要件別監査へ反映する。

### 資源APIの具体化

`ResourceBook`は呼出側が開始したBEGIN IMMEDIATE内でのみ動く。`create_run`で方針・manifest digest・元deadlineを固定し、`reserve`で全次元と枠をまとめて予約、`dispatch_intent`で再送できない意図を保存する。`observe`は認証済みobserver専用で、旧ownerの処理も同じoperationへ精算する。停止とusageは別順序で届いてよいが、停止未確認では枠、usage未確定では予約を保持する。

`claim`は通常の所有更新、`claim_recovery`は期限後・取消し後・予算違反後に限る回収用所有更新。回収所有者も新規予約/dispatchはできない。`cancel`は未送信と証明できる予約だけを解放する。原子的な`run_begin`は方針/契約/校正の現在状態確認、manifest/plan保存、`create_run`を一つの確定点で実施する。manifest.created_at <= broker now < deadlineを必要とし、準備中に進んだ時刻を理由にdeadlineを延長しない。

費用/usageの矛盾は例外によるrollbackだけで終えず、固定拒否応答とともにbreached/未解消exposureを保存し、旧budget_closure成功を現在利用できないようにする。同一event IDを別operationへ再利用した場合は両方へ反映する。元eventの不変receiptを返しても現在の失効は解除しない。期限後の観測は計上してoverrunを保持し、過去の使用量を捨てない。

### 今回の採択実装範囲

登録・校正・初回EvaluationContract採択・run_beginを接続する。初回用contract_validate/contract_adoptは比較必須の世代更新をPREREQUISITE_UNAVAILABLEで拒否する。[移行仕様](contract-transition-detail-spec.md)の専用APIでは、旧条件15件・新条件30件の根拠を再検査し、baselineをgeneration 1に保ったまま契約をgeneration 2へ採択する。採択後の通常runは比較context・入力実体化が未接続のためBASELINE_CONTEXT_UNAVAILABLEで拒否する。これは全MVPの未完了範囲であり、既存の方針や要求を縮めるものではない。400件の参照を生成した部品fixtureは、製品用の実評価集合と区別する。

## 7. 実装済みの認証APIと返却

`EvaluationExtension`をbrokerが固定ロードする。managerはobject_register（Registry/CaseSet）、contract_propose、contract_adopt、validatorはcalibration_record、contract_validate、operatorはrun_beginを使う。contract_current/run_statusはcandidate以外が読める。方針系列と評価契約系列の世代は別tableで照合し、同名でも混同しない。校正は観測と期待値を決定的に照合し、採択/現在照会/新規開始で24時間の期限とpermission generationを再確認する。保存payload・digest・current/contract/proposalの世代一致も再確認する。

構造bindの返却は入力そのものではなくコピーのbundleである。bind_evaluation_contractはcontract/policy/registry/case_set/calibration_set、bind_trial_planはplan/contract/registry/case_set/selected_controls/target_refs、bind_run_manifestはmanifest/contract/plan/policy/registry/case_set/selected_controlsを返す。いずれもci_eligible=falseを付ける。通常validatorは検証済みdocumentのコピーを返す。

owner_idはOS認証identityとは別の監督インスタンスのlease IDである。初期owner_idはrun_beginのrequest_id、epoch=1、leaseは60秒。後続操作でもOS peerからoperator権限を確認するため、owner_id文字列だけでは操作できない。manifestのenvironment_ref/actor_context_refは記録上の参照であり、その文字列自体をOS認証の証明にしない。

| API | 認証主体と境界 |
|---|---|
| resource_claim | operator。run_id/owner_id/recovery。通常は最新採択を再照合。期限/取消し/予算違反後のrecoveryは回収専用 |
| resource_reserve | operator。run_id/owner_id/owner_epoch/operation_id/entry/scenario。entryはobligation_id/case_id/trial_id/variant。最新契約・planを再照合し、同じ予定試行を新しいoperationへ複製できない |
| resource_dispatch | operator。run_id/owner_id/owner_epoch/operation_id。採択を再照合して送信意図を一度保存。同一request再配送も再評価し、過去の許可を再発行しない |
| resource_observe | validator。run_id/operation_id/event_id/stopped/usage。binding済みoperationだけ。古いowner・閉鎖後も既存費用や矛盾を捨てない |
| resource_cancel / resource_close | operator。run_id/owner_id/owner_epoch。cancelは未送信予約だけ解放。closeは停止・精算済みを必要とし、その後の新規開始を拒否 |

各要求にはschema_version=1/action/request_idも必要。全resource APIは同一requestでもfresh処理し、許可をキャッシュしない。eventの同内容再配送は元観測receiptを返す。run_statusは現在の予算状態を返す。closeは評価の成功確定ではなく新規開始の禁止であり、遅れて判明した矛盾は閉鎖後もbudget_closureを無効にする。

今回のbroker予約は自作30種類のconstraint/mutation fixture、1stage、外部モデルなしに限定する。case_trial=1、model_calls/token/API費用=0を信頼コードが決め、worker digest・scenarioとplanの対象/評価器digestを照合する。usageはnullまたはinput_tokens=0/output_tokens=0/cost_usd="0"のみ。未登録scenario、2stage、外部モデルや課金情報を任意要求から有効にしない。資源台帳coreには有料操作とtoken上界の検査があるが、実providerへの予約・usage認証接続は未実装。

## 8. 接続検証と残る範囲

`python -m tools.verify_evaluation_runtime --output .ga/evaluation-check-new`で、独立したUIDの採択、開始、予約、固定Docker実行、停止とusageの別到着、精算・閉鎖、失効後拒否を明示検証する。400参照と3ラベルの校正観測は輸送・保存の合成fixtureで、実評価器の校正や400件の性能計測ではない。31項目と既存認証22項目の証拠を[統合記録](evidence/mvp-run-contract-20260911/verification.json)に結ぶ。

実行journalと管理資源DBをまたぐ常設orchestrator、dispatch中の権限失効に対する継続停止、モデル送信/外部課金、正式BootstrapContractと実校正データ、baseline採択と旧条件回帰、全試行集計・Evidence/CIは残る。現在のbaseline/candidate構造bindは同一targetに限定しており、異なる旧targetとの比較文脈をまだ実装していない。比較採択を拒否している間の制限であり、通常回帰の要件充足とは扱わない。
