---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# baseline採択と更新の詳細仕様 v1

[初回baselineから比較契約への移行](contract-transition-detail-spec.md)で、
旧条件を保つ事前検査と、採択前の候補専用run・原子的採択の接続順序を定める。
事前検査はgeneration 2の採択または通常回帰CIの成功を意味しない。

本仕様は、採択済みのbaselineとEvaluationContractを更新し、旧runの解釈を
変えずに新しいrunへ現行版を渡すための実装中の仕様である。
現行の評価authorityでは、不変の採択履歴、同DBのEvidence・資源closure照合と、
baselineの構造照合を追加した。固定15entryの実Docker実行に基づく初回generation=1の採択・
再起動後の利用・撤回を確認した。さらに旧条件15件・新条件30件の根拠から、baselineをgeneration 1に
保ったまま契約generation 2へ採択する専用経路を接続した。baseline自体の更新、採択後の通常runと
条件変更を伴う契約更新・旧条件回帰は継続中である。本仕様の記述だけで
採択、比較可能性、CI成功または性能を主張してはならない。

## 1. 適用範囲と不変条件

baselineは比較用の採択済み参照であり、candidateの今回の結果や、単なるファイルパス・
モデル名・自己申告の`passed`ではない。baseline、EvaluationContract、PolicyProfile、
Registry、CaseSet、evaluator、oracle、実行環境、反復設定、Evidence、Decision、
budget/resource closureは、それぞれ内容digest付き参照で識別する。

次の二つを分ける。

1. **採択履歴**: どの世代を、どのproposalとvalidation、権限世代、時刻で確定したかを
   不変に記録する。
2. **現在の利用可否**: 最新の失効状態、Evidenceの鮮度、権限世代、比較条件を現在時刻で
   再評価する。利用不可になっても履歴や旧runの結果を書き換えない。

採択の世代は単調増加し、同じ世代の内容を上書きしない。旧runは開始時に固定した
`RunManifest`、`TrialPlan`、契約・方針・対象・環境・baseline参照、operationと資源
closureへ結び付ける。新runだけが採択時点の現行世代を使う。過去のrunを現在のpointerへ
再解釈して合格にしない。

部品の返却は常に`ci_eligible=false`とする。採択が成功しても、評価のAssurance、
実行完了、CI利用可否は別の照合結果である。

## 2. 現行実装との境界

`authority.py`のbrokerはAF_UNIX接続の`SO_PEERCRED`からUID/GIDを取得し、固定された
主体だけをAdoptionStoreへ渡す。要求本文のactor、context、roleは認証に使わない。
manager、validator、operatorの操作を分離し、同じモデルや同じ要求本文を二つ渡しただけで
独立性が成立したとは扱わない。

`evaluation_authority.py`には契約proposal、validation、adopt、current、run開始の
初期経路があり、保存先はAdoptionStoreの拡張SQLiteである。ただし現在の前提は初回の
`generation=1`かつ比較非適用の契約である。不変の採択履歴は追加済みだが、
baseline更新を受け入れる操作の全体接続は実装中である。

`run_contracts.py`はManifestとTrialPlanを参照・digest・世代へ結び、構造bindの結果を
CI成功にしない。baseline比較には比較必須、対象差分の宣言、旧条件との整合を追加する。
ArtifactStoreはEvidenceの鮮度、保持、削除、撤回を検査するが、現在はauthorityの採択DB
とは別のSQLite接続である。二つのDBを読めたことだけでは採択の原子的な確定点にならない。

## 3. 提案・検証・採択のAPI

外部の通常clientはbrokerへ固定schemaの要求を送り、認証済みUID/GIDに応じて次の操作を
受ける。入力のunknown field、重複ID、digest不一致、未対応版、期限外時刻は固定理由で
拒否し、入力本文をログや例外へ出さない。

| 操作 | 主体 | 役割 |
|---|---|---|
| `baseline_propose` | manager | 新baselineまたは新契約候補を不変proposalとして登録 |
| `baseline_validate` | validator | 旧・新の保存済み実体を読み、旧条件回帰と新条件を独立検証 |
| `baseline_adopt` | manager | 同一transactionでproposalとvalidationを再検査し、current pointerを一度だけ進める |
| `baseline_current` | manager/validator/operator | 最新pointerと現在の利用可否を毎回再評価 |
| `baseline_revoke` | operator | baseline、validation、Evidenceの撤回を世代付きで記録 |
| `baseline_use` | operatorまたはCI境界 | 呼出側のbinding、時刻、最新失効世代、closureを照合して利用可否を作る |

各書込み要求は`request_id`とcanonicalなrequest digestを持つ。同じID・同じ内容の再配送は
保存済みの不変receiptを返し、異なる内容は拒否する。`baseline_current`と`baseline_use`は
結果をidempotency cacheへ固定せず、毎回現在時刻と最新失効世代を再計算する。

### 3.0 初回candidateからbaseline採択まで

現在実装中の初回APIは、各要求に共通の`schema_version=1`、`action`、`request_id`に
加えて次の項目だけを受け付ける。後述の更新proposalモデル全体をcallerから受け取るAPIではない。

| action | 追加項目 |
|---|---|
| baseline_propose | proposal_id、series_id、run_id、expected_generation（初回0） |
| baseline_validate | proposal_id、validation_id |
| baseline_adopt | proposal_id、validation_id、expected_generation（初回0） |
| baseline_current | series_id |
| baseline_use | series_id、expected_baseline_ref、expected_contract_ref |
| baseline_revoke | series_id |

source runの保存実体から候補・比較文脈・反復条件を構築する。未完の候補は提案へ保存できるが、
validation/adopt/useでは実入力・校正・精算・Evidenceと採択履歴を現在時刻で再検査する。
候補のcreated_atは提案時に固定し、validatorやmanagerの後続操作時刻で作り直さない。
更新世代は現在の初回経路へ通さず、更新契約と旧条件回帰の実装を要する。

request再配送・transaction・時計・主体認証は既存AdoptionStoreの責務であり、baseline専用の
第二の時計や冪等台帳を設けない。currentはcurrent→不変adoption→proposal→validationを
列・本文・内容digestで照合し、期待するbaseline/contract参照が違うuseを許可しない。

初回は更新処理と混ぜず、次の順序を固定する。初回candidate runのManifestは
`purpose=baseline_candidate`、`baseline_ref=null`、契約の`comparison.mode=not_applicable`
かつ`comparison.reason=initial_baseline_pending`とする。このrunに旧baseline runや
`previous_*_ref`を循環参照させない。runが完了しても、それは候補作成の完了であり、
baseline採択や通常回帰CIの成功ではない。

1. 完了したbaseline_candidate runのManifest、TrialPlan、Decision、Evidence、全operationの
   resource closureを保存行から再検査する。
2. その実体参照から`kind=baseline`の候補payloadを生成する。候補recordには
   `source_run_ref`、Decision/Evidence/closure参照、契約・方針・対象・evaluator・oracle・
   CaseSet・反復条件を含め、candidate本文からbaselineの合格を作らない。
3. validatorが候補recordのdigestと保存済み実体を読み、初回の固定受入・校正・必要証拠・
   closureを検証する。初回は`previous_baseline_ref=null`、`baseline_run_ref=null`、
   `expected_contract_generation=1`（`previous_contract_ref`と`new_contract_ref`は
   同じ既存の初回contract refを保持）、
   `expected_baseline_generation=0`とし、旧baseline runを要求しない。
4. managerがvalidatorの不変validationを同一transactionで再検査し、baseline seriesの
   generation=1として採択する。以後の通常regressionだけが、このbaseline_refと比較必須
   モードを使う。

初回candidateの未完了、Evidence不足、closure未確定、校正失敗、期限切れは候補recordを
作れても採択へ進めない。初回candidateを後から通常regressionへ名前変更して比較条件を
補うことは禁止する。

### 3.1 proposal

proposalには少なくとも次を含める。

```text
schema_version=1, kind=baseline_proposal
proposal_id, contract_series_id, baseline_series_id
expected_contract_generation, expected_baseline_generation
previous_baseline_ref, new_baseline_ref
previous_contract_ref, new_contract_ref
comparison_context_ref, changed_axes
candidate_run_ref, baseline_run_ref, decision_ref, evidence_refs, resource_closure_ref
created_at, actor_ref, context_ref
```

`expected_contract_generation`と`expected_baseline_generation`は別のseries IDに対するCAS値で
あり、PolicyProfileのgeneration、AdoptionStoreのpermission generation、失効generationと
混同しない。更新では各seriesの変更対象だけが一つ進み、変更しないseriesは現行generation
とrefをそのまま指定する。初回baselineだけは`previous_baseline_ref=null`かつ
`baseline_run_ref=null`、`expected_baseline_generation=0`を許可し、既存の初回contractと
同じ`new_contract_ref`を`expected_contract_generation=1`で指定する。`previous_contract_ref`
も旧baseline runへ向けない。更新時だけ`baseline_run_ref`を旧採択recordの
`source_run_ref`へ結び付け、旧baseline/旧runの検査対象とする。
候補本文からbaselineや旧条件を作らず、
両run・Decision・Evidence・resource closureは保存済みの参照として指定する。対象、評価器、
oracle、CaseSet、PolicyProfile等に差がある場合は`changed_axes`へ開始前に固定し、未宣言の
差を「同じ条件」と扱わない。

proposalに含める参照は固定kindを使う。`new_baseline_ref`と`previous_baseline_ref`は
`kind=baseline`、`comparison_context_ref`は`kind=comparison_context`、run/Decision/
Evidence/closureはそれぞれの保存オブジェクトkindであり、IDだけの参照やpayload内のpathを
許可しない。

`kind=baseline`のpayloadは次の必須情報を持つ厳密なv1構造とする。

```text
schema_version=1, kind=baseline
baseline_id, baseline_series_id, generation
contract_ref, policy_ref, registry_ref, case_set_ref
target_refs, evaluator_refs, oracle_refs, repeat_config_ref
source_run_ref, trial_plan_ref, decision_ref, evidence_refs, resource_closure_ref
comparison_context_ref, created_at, valid_until
```

各参照はcanonical bytesのdigestまで保存行と一致し、`source_run_ref`のManifest/TrialPlanの
bindingとrecordの契約・対象・評価器・oracle・反復条件が一致することを要求する。payloadの
`valid`、`passed`、`safe`等の自己申告は採択判定に使わない。

反復条件の`repeat_config`は保存済みTrialPlanの内容参照から一意に生成する。
Planに固定されたtrial以外の自己申告の反復数を併記して比較可能と扱わない。
EvidenceはIDに加え、各payloadのcanonical digestもrecordの参照と完全一致させる。

`kind=comparison_context`は`comparison_id`、`mode`、`baseline_ref`、`reason`、
`changed_axes`、`expected_contract_generation`、`expected_baseline_generation`、
`contract_ref`、`policy_ref`、`target_refs`、`evaluator_refs`、`case_set_ref`、
`oracle_refs`、`repeat_config_ref`を必須とする。初回は`mode=not_applicable`、
`baseline_ref=null`、`reason=initial_baseline_pending`、`changed_axes=[]`。更新は
`mode=required`、current baseline_ref、変更軸とその理由を固定する。初回contextに旧baseline
の参照を後付けして循環させない。

### 3.2 validation

validatorはproposalのdigestを読み直し、本文の`passed`やcandidateが作った成功文字列を
採用しない。初回は§3.0の`candidate_run_ref`をsource runとして検査し、旧baseline runを
参照しない。更新時だけ`baseline_run_ref`から旧採択recordの`source_run_ref`を解決して、
新candidateとの比較に使う。validationは次の実体を同一transaction内で再検査する。

- previous/currentのbaselineとcontractのcanonical bytes、ID、digest、generation
- （更新時のみ）旧runのManifest、TrialPlan、binding、対象、環境、PolicyProfile、Registry、
  CaseSet、oracle、evaluator、反復設定。初回は§3.0のcandidate source runについて同じ実体を
  検査する。
- baseline/candidateのDecision、NormalizedResult、Evidenceのsubject/conditions/producer、
  observed/collected/valid-until、撤回・削除状態
- すべてのchild operationの終了、停止確認、usage、料金または非課金根拠、
  `budget_closure` / `resource_closure`のcanonical bytesとdigest
- 旧条件を固定した回帰結果の決定的再計算と、変更された軸だけを除外した比較条件

validationの結果は、検査時刻、validation digest、検査したpermission generation、
baseline/normal Evidenceの期限、失効世代、照合した実体参照を含む。`passed=true`だけ、
receiptのstatusだけ、表示済みの終了コードだけでは有効なvalidationにならない。旧条件の
回帰を実行できない、旧Evidenceが失効している、必要closureがない、料金が未確定、または
対象・evaluator・oracle・反復条件が比較不能なら、採択せず不足またはHOLD材料を残す。

validationの有効期限はbaseline比較Evidenceの最小期限以下で、通常Evidenceは24時間、
baseline比較Evidenceは30日を上限とする。境界時刻は`now <= valid_until`だけを有効とし、
未来観測、保持期限超過、撤回、削除、条件変更は利用不可である。30日という比較用鮮度は
保存保持期間の代用ではない。

Evidenceは記録時に用途を`normal`または`baseline_comparison`のいずれかへ固定する。
用途は後からbaseline採択のために変更できず、同じEvidenceを用途変更して鮮度を延長しない。
各Evidenceの利用期限は、`retention_until`、`observed_at + 86400`（normal）、または
`observed_at + 30*86400`（baseline_comparison）の最小値である。採択時には参照する全Evidence
の最小期限をrecord/validationへ保存し、現在時刻がその期限を超えれば利用不可とする。
削除・撤回・条件変更は期限内でも無効化する。

### 3.3 atomic adopt

`baseline_adopt`は`BEGIN IMMEDIATE`で次を一つの確定点として検査する。

1. brokerが渡した現在のUID/GIDに、managerとしての採択権限があり、actor取消しがない。
2. proposal、validation、旧currentのID/digest/seriesがrequestと一致する。
3. `expected_contract_generation` は契約系列の current と、`expected_baseline_generation` は
   baseline 系列の current とそれぞれ一致し、proposal/validation の世代整合も確認したうえで、
   変更対象の系列だけ新世代を一つ進め、変更しない系列は同じrefと世代を維持する。policy/permission/revocation の世代はこの二系列と別物で
   あり、採択CASの期待値へ代用しない。
4. validationの作成時刻・期限・permission generation・失効世代が現在も有効である。
5. 更新時の旧条件回帰、新条件の受入、必要Evidence、Decision、resource closureの実体digestが
   保存行と一致する。初回は§3.0のsource runを検査し、存在しない旧baselineを要求しない。
6. 検査開始からこの確定点までに可変状態が変わっていない。変わった場合は再検査し、
   再検査できなければ拒否する。

確定時にはcurrent pointerを更新し、不変`adoption_history`へ旧ref、新ref、proposal/validation
ref、actor/context、permission generation、revocation generation、時刻、理由をINSERTする。
既存履歴のUPDATE/DELETE、同じ世代への別内容上書き、validationを使った世代飛越しは拒否する。
保存障害はrollbackして採択成功を返さない。

## 4. 同一SQLiteへの接続方式

採択、run binding、Artifact/Evidence、失効、resource closureを同じSQLite DBの一つの
接続境界へ置く。実装方法は次のどちらかへ固定し、別DBの読み取りを原子性の根拠にしない。

- AdoptionStoreがArtifactStoreの厳格なartifact/evidence表と操作を同じ接続へ持つ。
- 既存Storeを再利用する場合は、信頼済みの単一connectionを渡す明示APIを作り、Storeが
  独自connectionを開く経路と混在させない。

`ATTACH`や別プロセスの二つのcommitを採択の確定点に使わない。外部runner、OS認証、
network、model、filesystemのI/Oはtransaction中に行わず、I/O前にoperation意図を保存し、
戻ったcanonical bytesを同じtransactionで検査する。

最小の追加表は次のとおりである。実際のSQLite型は既存のstrict schema検査に従い、未知表・
未知列・部分migration・重複primary keyはopen時に拒否する。

| 表 | 不変情報 |
|---|---|
| `baseline_proposals` | proposal、series、旧/新ref、期待世代、変更軸、actor/context、digest、時刻 |
| `baseline_validations` | proposal digest、旧条件回帰、新条件、Evidence/Decision/closure refs、期限、permission世代、digest |
| `baseline_records` | baseline payload、契約/方針/対象/CaseSet/evaluator/oracle/反復、source run、Decision、Evidence、closure、digest |
| `comparison_contexts` | 比較mode、baseline ref、変更軸、契約/baseline各seriesと期待世代、比較条件digest |
| `baseline_current` | baseline series、current generation、baseline/contract ref、adoption history ref、失効世代 |
| `adoption_history` | 旧/新current、proposal/validation、権限・失効世代、actor/context、確定時刻、理由 |
| `authority_evidence` | Evidence payload、用途（`normal`/`baseline_comparison`）、観測/収集/期限、artifact/conditions/producer、撤回世代 |
| `authority_runs` | run/operation、Manifest/Plan/contract/baseline各digest、owner epoch、作成時世代、終了/Decision/closure refs |
| `resource_closures` | run/operation、usage、費用上界、停止・解放、確定時刻、digest、closure状態 |

既存Artifact/Evidence表を同じDBへ移す場合も、移行は明示的な版付き処理とし、旧diagnostic DBを自動
破棄・暗黙変換しない。削除はartifact bytesを復元せず、撤回・削除世代と影響するbaseline、
Decision、Finding、UseDecisionの参照だけを残す。Evidenceのdigestは完全性検査であり、
producerやactorの認証の代用ではない。

### 4.1 既知v2/v3からv4への明示migration

移行入口は`migrate_evaluation_store(path)`だけとする。呼出し側から`old_digest`、拡張実体、
任意schemaを受け取らず、リリースへ固定したv2 predecessorのextension/bootstrap/validator digest、
`user_version=2`、全表・全列、保存JSONのcanonical bytes・digest・参照整合を`BEGIN IMMEDIATE`内で
再検査する。未知表、未知列、欠損表、重複key、破損行、別bootstrapは拒否し、通常のStore openは
自動移行を行わない。

v2では従来のv3履歴作成用`migrate_schema(db)`を同じtransactionで呼び、新表作成後に
`policy_adoptions`が宣言されていれば既存`current_profiles`の各行を同内容で一度だけseedする。
`adoption_meta.schema_version`、`PRAGMA user_version`、`adoption_config.extension_digest`の更新と
commitは一括で行い、途中失敗はrollbackして移行成功を返さない。旧行のJSON、digest、時刻、世代、
旧run条件は書き換えない。

既知v3では保存済みの列・payload/hash・current/history・proposal/validation・receiptのartifact参照を
検査する。続いて候補保存用の2表を追加し、v4と現行extension digestを確定する。
v2の二段階処理も同じtransaction内で行い、一部だけをcommitしない。

移行後も旧validator digestは旧行と移行結果に識別可能な履歴として保持する。新v4 Storeはその固定
predecessor digestの既存currentを読み取れるが、旧proposal/validationを新validatorで再検証・再採択
することは許可しない。新しい提案・検証・採択は現行validator digestだけを使用する。移行結果の
`ci_eligible`は常に`false`であり、移行の成功はEvidence、baseline、実運用の採択を意味しない。

## 5. 旧runと新runの分離

旧runの`RunManifest`は開始時のcontract generation、baseline ref、policy、対象、CaseSet、
evaluator、environment、plan、actor contextを固定し、run statusはその固定bindingと終了・
closure履歴を読む。current baselineが更新された後も、旧runのDecisionやreceiptを新世代へ
書き換えない。

新runの開始時は、呼出側のexpected bindingと現行baseline/contract generationを同一transaction
で照合する。現行pointerのEvidenceが期限切れ・撤回・削除済み、validationが失効、権限世代が
変更、resource closureが欠損、または比較条件が不足なら開始またはCI利用を拒否する。

baseline更新時にtarget、evaluator、oracle、CaseSet、policy、environmentを変える場合は、
その軸をproposalへ宣言し、比較で許される軸と固定すべき軸を明示する。旧条件回帰を行わずに
閾値を緩める、検査を削除する、Evidenceを差し替える、baseline pointerだけを更新することは
修復・安全性・劣化なしの証拠にならない。

## 6. 失効、競合、再配送

- 同じrequest IDで同じcanonical内容なら、元の不変採択receiptまたは履歴参照だけを返す。
  現在の利用可否は別照会で再評価する。
- 同じproposal/validation IDで別digestなら、既存行を維持して`REQUEST_CONFLICT`とする。
- 期待世代、permission generation、validation期限、baseline/contract digest、失効世代の
  いずれかが確定中に変われば`GENERATION_CONFLICT`または`STALE_VALIDATION`とする。
- Evidence撤回・artifact削除・actor取消しは世代を増やし、currentを成功へ再解釈しない。
  current照会は`valid=false`と理由を返し、履歴は残す。
- operatorの取消しや撤回が採択後に起きても、過去receiptの当時の事実は不変である。
  CI境界は操作直前に最新世代を照合し、旧成功を現在成功へ再利用しない。

すべての失敗は入力拒否、権限不足、stale/競合、Evidence失効、closure不足、保存障害、
内部不整合を固定codeで区別する。raw request、payload、秘密、外部本文を理由へ含めない。

## 7. 実装受入シナリオ

実装時は固定された無害fixtureと保存済み合成artifactだけで、少なくとも次を独立に検査する。

1. 初回採択、同一内容再配送、異内容再配送を行い、履歴一件・世代不変・conflict拒否を確認する。
2. baseline30日の直前、境界、直後と通常Evidence24時間の直前、境界、直後を照会する。
   期限値を後から延長していないことを確認する。
3. 旧runを固定した後に新世代を採択し、旧runのManifest/Decision/receiptが旧digestのままで、
   新runだけが新currentを要求することを確認する。
4. 旧条件回帰の`passed`自己申告、旧Evidenceの差替え、閾値緩和、検査削除、target/evaluator/
   oracle差の未宣言を与え、validatorが採択しないことを確認する。
5. validator検査後のEvidence撤回、actor取消し、permission世代変更、current更新、closure
   欠損を競合させ、adoptがrollbackして履歴とcurrentを部分更新しないことを確認する。
6. 同時に二つのconnectionから同一expected generationをadoptし、片方だけが成功し、もう片方が
   世代競合になることを確認する。
7. 採択後のartifact削除、Evidence撤回、遅延結果、料金未確定を与え、旧receiptを成功へ変更せず、
   最新UseDecisionを利用不可または不足として返すことを確認する。

これらの試験、実Docker接続、全対象のbaseline採択、通常CIへの接続が揃うまでは、baseline更新
機能を完成済みと記録しない。現時点では本仕様と既存の初回部品の存在だけで、GAH-R11/R18/R22/
R24/R32の全体受入を満たしたとは扱わない。
