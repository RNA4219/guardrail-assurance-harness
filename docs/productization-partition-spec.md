---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-20
next_review_due: 2026-10-20
---

# 評価TrialPlan・admission bundleの内容参照分割仕様案

## 対象と実在する境界

対象はbenchmark query planではなく、評価系のTrialPlanと、それをRunManifest・CaseSet・document等へ束ねるadmission/prepared bundleである。サイズ診断の1,600-case、baseline/candidate両variantでは3,200 entriesのTrialPlanがcanonical 1,438,640 bytesとなり、validate_trial_planのDOCUMENT_SIZEで拒否された。代表admission bundleは2,482,771 bytesだが、診断上fully boundではなく、実保存・transport・runtime成功を示さない。CaseSet単体1,038,560 bytesは1 MiB未満だが、authority frameへの外側request分の余白がほぼない。

現行検査・保存経路:

- run_contracts._canonicalは型、depth/node、整数等の条件とMAX_DOCUMENT_BYTESを検査する。validate_trial_planは閉じたv1形 {schema_version, kind, plan_id, contract_ref, entries} を検証し、全entryの後に1 MiBを適用する。
- bind_trial_planはentry identity全域重複、case/obligation/stage/evaluator/target/variant、required obligation、required LLM全case coverage、baseline/candidate対とcandidate target集合を全件検査する。bind_run_manifestはv1 Plan全体のcontent_refをRunManifestへ照合し、bound resultにもPlan全文を返す。
- evaluation_authorityのrun_beginはv1 validate_trial_planを要求し、eval_runs.plan_jsonへ全文を保存する。run_statusもPlan全文を返す。authority _packed/_load_jsonは1 MiB制限下で保存・再検査する。
- adoption request digestとauthority.py socket frameの上限は各1 MiB。case_set等をwrapper付きで送る要求、Plan全文を返すstatusはframeを超え得る。
- llm_materialization.buildは固定 evaluation_data.build_pack() を使ってbound_runへplan/case_setを含める。llm_admission.expectedとfixture_admission.prepare/_verifyは生成値を保存し、worker/runtime/calibration等も再照合する。query_scale_dataを受けるAPIはない。resources._packedのDB本文に一律1 MiB制限がないことはwire/artifact上限を迂回する根拠にならない。
- evaluation_data.validate_packは固定evaluation packを検査し、pack全体にMAX_DOCUMENT_BYTESを適用する。query-scale合成corpusは別validatorであり、固定pack検査だけでquery-scaleのcase/document意味は成立しない。

分割TrialPlanだけではruntime admissionは成立しない。pure in-memoryのv2 index/segment codecは実装し、[親の関連28試験](evidence/productization-continuation-20260919/partition-codec-parent-v1.json)で確認した。明示schema-v5のauthority保存・読取・migrationは実装済みで、[親の80試験](evidence/productization-continuation-20260919/partition-authority-parent-v2.json)で確認した。実socket、v2 run、全consumer/admissionは後続検証・実装の範囲である。

## v2 wire契約

既存v1 trial_plan/run_manifestのwire bytes・validator・DB/status意味は変えない。別の閉じたversioned kindを使う。

### trial_plan_entries_segment / trial_plan_index

trial_plan_entries_segmentの閉じた必須fieldは {schema_version, kind, id, plan_id, contract_ref, segment_index, first_entry_ordinal, entry_count, entries}。schema_versionは厳密整数2、kindは固定文字列trial_plan_entries_segment。entriesは既存_ENTRY_FIELDS/_validate_entryが扱うv1 entryを順序どおり保持する。segment idは "tp-" + plan_idのUTF-8 bytesに対するSHA-256先頭32小文字hex + "-" + 4桁zero-padded index。短縮IDだけでbindingを認めず、plan_id/contract_refとsegment全体content digestも照合する。

trial_plan_indexの閉じた必須fieldは {schema_version, kind, plan_id, contract_ref, entry_count, segment_count, ordered_segments, reconstructed_bytes, reconstructed_digest}。index自身のstorage refはkind trial_plan_index、id plan_idとして通常のcontent_refを計算する。ordered_segmentsは固定kind trial_plan_entries_segmentの完全ref列。ref配列index i、segment_index i、first_entry_ordinal、先行entry_count合計、entry_count == len(entries)を一致させ、重複・欠落・並替えを拒否する。

reconstructed_bytes/digestは既存順に復元したv1形logical TrialPlanのcanonical UTF-8 bytesに対する長さとSHA-256。logical planが1 MiBを超える場合、全体にはv1 content_refを作らない。各segment/index個別のcanonical bytesは900,000以下とし、segment content_refは既存content_refで計算する。in-memory restoreは入力のindexとref解決済みordered segment列だけを受け、path、通信、source/ref discoveryをしない。このpure部品はauthority source digest、requirements/admission/current contract/source lock結合を行わず、それらは未接続のまま。 実装はentryごとのcanonical bytes長を一度ずつ計り、header・区切り・件数のbytesを加算する。完成segmentの実測値と予測値が一致しなければ拒否する。決定的な3,200-entry回帰fixtureはlogical 1,437,017 bytes、segments 899,689 + 537,798 bytes（繰返しheader込み1,437,487 bytes）となる。先の規模診断の1,438,640-byte入力とは別fixtureである。

### run_manifest v2

schema_version 2のRunManifestはv1相当の契約・target・相対期限・policy条件を維持し、plan_ref kindをtrial_plan_indexに固定する。v1 validatorは変更しない。`validate_partitioned_run_manifest` と `bind_partitioned_run_manifest` のpure境界を実装し、閉field、index/segments全体、manifestの参照と目的、contract内のpolicy/registry/CaseSet参照、全case coverageと両variantを照合する。[親の38試験](evidence/productization-continuation-20260919/partition-binding-parent-v1.json)で1,600 distinct case・3,200 entriesと再署名した不整合を確認した。

bindingの返却は {schema_version, kind, manifest_ref, contract_ref, plan_index_ref, policy_ref, registry_ref, case_set_ref, selected_controls, ci_eligible} の閉形式。schema_version=2、kind=bound_partitioned_run、ci_eligible=falseとし、実content由来のrefだけを保持する。出力も900,000 bytes以下で、plan/segment本文を再同梱しない。

pure境界は現在時刻、校正採択、fresh current contract、撤回、permission generation、authority source lockを証明しない。後続authority接続で、各開始/read時にこれらを現在状態へ照合する。segment保存やpure bindingからadoption/freshnessを推定しない。

### CaseSetの転送表現

CaseSetの意味と通常のcontent refはv1を維持する。新しいpure転送codecを `partition_case_set(document)` と `restore_case_set(index, segments)` で実装する。DB保存、authority認証、source/current contract照合、runtime admissionはこのcodecに含めない。

`case_set_cases_segment` の閉fieldは `{schema_version, kind, id, case_set_id, segment_index, first_case_ordinal, case_count, cases}`、`case_set_index` は `{schema_version, kind, case_set_id, purpose, required_categories, case_set_ref, case_count, segment_count, ordered_segments, reconstructed_bytes, reconstructed_digest}` とする。どちらもschema_versionは厳密整数2。segment IDは `cs-`、CaseSet IDのUTF-8 SHA-256先頭32桁、`-`、4桁のsegment indexを連結する。

segment indexとcase ordinalはともに0始まりで、配列位置と先行case数合計へ厳密に一致させる。順序を並べ直して補正しない。index/segment各900,000 bytes以下、最大16 segments、全case数1〜1,600とする。復元後のv1 CaseSetは既存validatorと1 MiB制限を必ず通す。TrialPlan用の8 MiB例外をCaseSetへ適用しない。

`reconstructed_digest` は復元v1 CaseSet全文のcanonical UTF-8 bytesをhashした値である。`case_set_ref` はそのv1全文の通常content refと完全一致させる。各segment refは当該segment全体のcanonical bytesに結び、ordinal/count/digestと全case重複を再検査する。受け取るsegmentsはlist/tupleだけとし、入力を変更せず独立treeを返す。分割は各caseのbytesを一度ずつ計り、header・件数の桁・comma・bracketを加算し、完成segmentの実測bytesとの一致を確認する。

[DGXの限定レビュー](evidence/productization-continuation-20260919/dgx-case-partition-v1.json)からhash対象、0始まり、byte counterの明文化を反映した。応答はtoken上限で途中終了しており、完全レビューまたは実装受入とは扱わない。

### admission bundle index

trial_admission_index v2は小さいref-only文書とする。固定fieldでRunManifest ref、EvaluationContract ref、TrialPlan index ref、CaseSet ref、document segment refs、target/evaluator/runtime-lock/calibration refs、用途・順序・件数・digestを結ぶ。大きい本文をbundleへ再同梱しない。各refの現在性、source lock、permission/contract generationは既存fresh authority検査で別々に確認する。

1,038,560-byte CaseSetは単体上限内でもauthority frame envelopeを含む単発uploadに使わない。固定case-set segmentsで転送し、receiverで復元したCaseSetに既存validate_case_setと全体検査を適用し、既存1 MiB上限内を再確認して通常case_set content refへ保存する。復元後CaseSetが1 MiB超なら別versioned semantic contractなしには拒否する。

llm_materialization.buildが生成する固定packとquery-scale合成corpusを混同しない。query-scale入力用の新しい固定builder/admission pathがCaseSet・document validatorを通し、target/evaluator/source/contractへの結合を確立するまでは既存llm_admissionへ入力注入しない。evaluation_data.validate_packの1 MiB gateを拡大・迂回しない。

## 上限とcanonical partition

- index/segment artifactそれぞれのcanonical bytesは900,000以下。
- segment数は1〜16、entries総数は1〜10,000、復元logical planのcanonical bytesは8 MiB以下。どの上限も超過時に拒否し、切り捨てない。
- 既存run_contractsのplain JSON制約（最大depth 16、global nodes 100,000、整数±(2**53-1)、全float拒否、strict UTF-8）をlogical全体へ適用する。segment単位だけでnode上限を判定しない。Python dict APIはraw JSONのduplicate-key検査を行わない。wire decode前の境界は別途必要。
- Partitionerは各entryのcanonical bytes長を一度ずつ計り、固定header、entry_countの桁、array bracket/commaを足してcontiguous segment上限を線形に計算する。完成segmentを最後にcanonical serializeし、byte counterと一致することを確認して公開する。順序は変更しない。
- このpure moduleにはDB保存、transport frame、source/requirements/admission binding、認証・freshnessは含めない。上限を実transportへ適用する際のwrapper余白は別の固定API層で検証する。
## 生成・保存・読取・admission

1. 固定query-scale builderはCaseSet/document意味validatorを実行し、全Plan entryを生成する。v1と同じentry意味規則でlogical order/coverageを検査してから決定的partition、index/ref、byte count/digestを作る。
2. receiverは固定authority actionから一度に1 segmentまたはcase chunkを受ける。kind/ID/plan/contract/index/ordinal/content refを確認し、許可kind専用validatorを通してimmutable eval_objects相当へ保存する。任意kind/path/command/importやbulk bytesを受けない。
3. 最後にtrial_plan_indexをcommitする。全ref存在、digest、順序、重複/欠落、contract/source binding、全count/bytes/digest、全entry意味検査が通るまでrun/admission rowを作らない。先行part保存だけは未admittedのままにする。
4. run_begin_v2はinline planを受けずRunManifest v2とindex refを受ける。eval_runs.plan_jsonには小さいindexを保存し、segmentsをrefで保持する。run_status v2はindex/ref/countを返し、固定read-segmentは1件ずつ返す。v1 run_begin/statusは不変。
5. admissionはindex/ref-only bound representationを保存する。entry実行時に必要segmentを取得し、run id/plan index/contract/source lockを再確認して対象trialへの独立treeを作る。cache・共有mutable返値でowner/currentnessを省略しない。
6. read-sideはfreshness・期限・adopted contract・worker/runtime lock・calibrationを必要時に既存authorityで再確認する。segment refは認証/採択/permissionを代替しない。

## validatorとconsumer移行

v1 validate_trial_planは文書サイズも検査するため、復元後の1 MiB超Planへそのまま呼べば必ず拒否する。size checkをv1から外すのではなくv2用全件iterator validatorを追加する。各segmentはstrict decoderと通常1 MiBを通し、entry shapeを現行helperで確認する。全segmentの走査でidentity重複、unknown/missing case、stage/evaluator/target、variant pair、required obligations、全case coverage、candidate target集合を既存bind_trial_planと同じ規則で検査する。logical v1 canonical bytes/digestを全体上限内で照合する。

bind_run_manifestは現在Plan全文をbound outputに返し、後続callerもentriesを読む。v2のreference-only化には少なくともevaluation_authority run_begin/run_status、run_evidence、operations_bundle、fixture_materialization、llm_materialization/admission、aggregation、runtime entry lookupのversion dispatchが要る。どれかが未対応なら実run利用とは宣言しない。呼出元の必要範囲、status応答、再起動後読取を調べずに実装可能と断定しない。

llm_materialization/llm_admissionは固定packを再生成し、fixture_admissionは保存値と比較する。query-scale用に再利用しない。別の固定builderはevaluation_data.validate_packと別にquery-scale semanticsとsource/contract bindingを定義する。DB admission本文にも大きな入力を重複同梱しない。現在のbundle診断値は実admission証明ではない。

## 失敗・互換・検証

- v1 TrialPlan/RunManifest/fixed evaluation packのwire、validator、DB statusを維持。v1 readerは単一documentのまま。
- v2 readerはkind/versionで厳密dispatch。unknown key、duplicate JSON key、wrong ref kind/digest、bool-as-int、segment reorder/duplicate/missing、ordinal gap/overlap、source/contract mismatch、expired/revoked、count/size超過を拒否する。v2からv1 fallbackしない。
- upload/readback/semantic/index commit failureはINCOMPLETE/REJECTEDで、successful run/admission/observationを作らない。孤児immutable partは自動削除・成功扱いしない。
- 1,600×baseline/candidateで3,200 entriesとCaseSetを復元し、wire/frame bytes、segment数、全ref、logical digest、全件coverageを検査する。candidate-only、両variant、境界値を含める。
- segment reorder/duplicate/missing、case/target/stage/evaluator/ref/source/contract不一致、receipt不一致、restart read、permission撤回/世代変更後のread拒否を検証する。
- 実admission保存・再検査・worker/runtime受渡しは固定実環境で別検証する。unit mockを実受入証拠にしない。

## 初期案と未決設計の台帳

TrialPlan wire契約は下表でも実装済みの最大16 segments・logical 8 MiBに揃える。CaseSetとquery-scale corpusのpure in-memory分割codecも実装済みで、CaseSet最大16 segments/logical 1 MiB、corpus document最大10 segments/logical 8 MiBである。下表のadmission-index、run/status、全consumer接続は未実装の設計案であり、codecやschema-v5保存/読取の存在を実run/admission利用可能性へ昇格させない。

| 対象 | 有限上限案 | 厳密field/振舞い案 | 未決・影響先 |
|---|---|---|---|
| TrialPlan entries segment | 実装済み: artifact各900,000 bytes以下、最大16 segments、entry合計10,000以下、復元logical plan最大8 MiB | 実装済み閉field: schema_version/kind/id/plan_id/contract_ref/segment_index/first_entry_ordinal/entry_count/entries。indexはschema_version/kind/plan_id/contract_ref/entry_count/segment_count/ordered_segments/reconstructed_bytes/reconstructed_digest。既存entry semanticsとglobal depth/node/integer制約を維持 | pure codec、schema-v5 committed storage/reader、pure binder/aggregateは存在する。v2 run/statusと全evidence/admission/runtime consumerへの接続は未実装 |
| CaseSet case segment | 実装済みpure codec: artifact各900,000 bytes以下、最大16 segments、総case最大1,600、復元CaseSetは既存1 MiB以下 | 実装済み閉field: segmentはschema_version/kind/id/case_set_id/segment_index/first_case_ordinal/case_count/cases。indexはschema_version/kind/case_set_id/purpose/required_categories/case_set_ref/case_count/segment_count/ordered_segments/reconstructed_bytes/reconstructed_digest。復元後に既存validate_case_setを適用 | DB保存、authority source/contract binding、transport action、runtime admissionは未接続。新admission refにするかreceiverでv1 CaseSetを保存するかは別途未決 |
| Query-scale document/corpus segment | 実装済みpure codec: artifact各900,000 bytes以下、document segments最大10、documentsはcase_count×4以下、logical corpus最大8 MiB、global nodes/depth制限を維持 | 実装済みcorpus indexはschema_version/kind/corpus_id/case_count/document_count/claims/stage_counts/case_set_index_ref/ordered_document_segments/reconstructed_bytes/reconstructed_digest。document segmentはschema_version/kind/id/corpus_id/segment_index/first_document_ordinal/document_count/documents[{ref,document}]。固定synthetic input/state/oracle kindsのみ | DB保存、transport action、source/contract/target/evaluator binding、admission、runtime callerは未接続。query-scale codecは固定evaluation packと相互代用しない |
| trial_admission_index | 未実装案: 1 MiB以下のref-only文書と各既存partition codecの有限segment上限を維持 | 提案shape: schema_version/kind/id/run_id/manifest_ref/contract_ref/plan_index_ref/case_set_ref/ordered_document_segment_refs/target_refs/evaluator_refs/runtime_lock_ref/worker_source_digest/calibration_ref/permission_generation/aggregate counts+digest。固定kindのみ、source digestはserver-attested | 通常fixture_admission payloadへ含めるfield、fresh generation値、replay/idempotency、runtime image/worker binding、ref用kindと全保存locationの決定。pure codec/保存済みTrialPlanから実装済みと推定しない |
| authority transport | request/response各1 MiB frame/request-digest上限 | segment/case chunkごとの固定action、1 response最大1segment。case set単体(1,038,560 bytes)をwrapper付きで送らない | action名、role allowlist、run作成前のupload権限、readページの権限、失敗/孤児partの保持期間を決定。汎用bulk uploadは禁止 |
| DB/read consumers | individual stored artifactは既存1 MiB検査以下 | eval_objectsへtyped immutable segmentを保存し、eval_runs.plan_jsonはv1 planまたはv2 indexの一方を格納、status responseもversion dispatch | 既存DB column再利用かmigrationか、source lock digest更新時の既存run読取、idempotency replay応答形、DB migration/rollback戦略を決める |

旧64 MiB/64-document-segment/32-case-segment案は採用せず、上のTrialPlan・CaseSet・query-scale codec行にある実装済み上限が各pure codecの契約である。これらはDB/transport/admission容量や全体性能の受入を証明しない。trial_admission_index行は未実装案であり、実装済み扱いしない。すべてのmaxは型厳密（boolを整数扱いしない）、超過時は拒否する。

## 実在module・API接続表

| 現行module/API | 現在の責務 | v2で必要な変更／未調査境界 |
|---|---|---|
| [run_contracts.py](../src/gah/run_contracts.py) validate_trial_plan / bind_trial_plan / bind_run_manifest | v1 Planと全entry bindingを検査し、Plan全文を返す | size-aware v1は維持。shared entry semanticsを抽出しv2 ordered segmentsを全域検査。bound outputをindex refへversion dispatch |
| [evaluation_authority.py](../src/gah/evaluation_authority.py) run_begin / run_status / _store_object / _object | request planをv1 validator後DB保存、statusで全文Plan返却、typed objectsをrefで取得 | 別moduleの明示v5で固定segment upload/commit/readとrole/currentness/atomic commit/replayを実装済み。run_manifest v2の実開始、plan_jsonのv1/v2 discriminator、status pagingは未接続 |
| [authority.py](../src/gah/authority.py) receive_frame / send_frame | 1 MiB socket frame上限 | 上限は維持し、900,000-byte payloadでenvelope/responseも収まることを検証。上限拡大はしない |
| [query_scale_data.py](../src/gah/query_scale_data.py) build_scale_corpus / validate_scale_corpus | query-scale専用合成corpus構造を全体検証 | pure split/restore codecは [partitioned_scale_corpus.py](../src/gah/partitioned_scale_corpus.py) に実装済み。DB保存、authority transport、source/contract binding、runtime consumerは未接続 |
| [partitioned_trial_plan.py](../src/gah/partitioned_trial_plan.py), [partitioned_case_set.py](../src/gah/partitioned_case_set.py), [partitioned_scale_corpus.py](../src/gah/partitioned_scale_corpus.py) | 有限artifactと復元logical上限のpure partition/restore、参照・順序・digest・全体意味検査 | codec実装済み。これらpure APIは認証/currentness、DB/transport、admission/run利用を成立させない |
| [partitioned_plan_store.py](../src/gah/partitioned_plan_store.py) load_verified_committed_plan | 認証済み呼出元向けに同一transaction内でcurrent contract/permission/sourceとcommitted index/segmentsを検査してprivate planを復元 | schema-v5保存・読取境界は実装済み。既定v4 run_begin/status、evidence、operations bundle、admission、CIへは未接続 |
| [partitioned_run_contracts.py](../src/gah/partitioned_run_contracts.py), [partitioned_aggregation.py](../src/gah/partitioned_aggregation.py) bind_partitioned_run_manifest / aggregate_partitioned | pure v2 manifest/segments意味結合、既存aggregate coreのbounded v2入口 | pure binder/aggregateは実装済み。呼出元がfresh authority/source/permissionを毎回検査し、保存境界でreference-only形を使う実接続は未実装 |
| [evaluation_data.py](../src/gah/evaluation_data.py) build_pack / validate_pack | 現行固定packとcase/doc semantics、pack 1 MiB gate | 変更しない。query-scale分割をこの固定packへ混ぜない |
| [llm_materialization.py](../src/gah/llm_materialization.py) build / prepared_response | 固定packでfull bound run/planを生成し、responseへpackだけ省く | reference-only v2 bound representation、新query-scale builderへの分岐。現行400 packの意味を維持 |
| [llm_admission.py](../src/gah/llm_admission.py) expected / verify / prepare | source/lock固定factoryからexpected payloadを再生成し、保存値と比較 | v2 index参照を再生成・fresh checkする専用path。full plan/corpusを別形式のまま信頼しない |
| [fixture_admission.py](../src/gah/fixture_admission.py) _expected_fixture_pack / _verify / prepare | fixture materializationとadmissionを保存/再検証 | v2 typed refs/read-after-restart/permission generationを検査。既存fixture payloadを変更しない |
| [fixture_materialization.py](../src/gah/fixture_materialization.py) manifest validate/materialize | v1 bound runからruntime fixture bundleを作成 | plan entries参照読取、per-trial payload上限、workerへの完全必要入力が未調査 |
| [run_evidence.py](../src/gah/run_evidence.py), [operations_bundle.py](../src/gah/operations_bundle.py), [aggregation.py](../src/gah/aggregation.py) | 保存runからevidence/diagnostics/aggregateを再束縛。operations bundleはstatusのPlan全文を読む | v1/v2 status dispatch、index/partの同一run bindingと全件検査、出力サイズ上限 |
| [resources.py](../src/gah/resources.py) _packed / _unpack | DB文字列をcanonical digest付きで保管。一般payload serializerにTrialPlanの1 MiB validatorはない | admission indexごとの明示1 MiB/frame上限、個別part immutable refs、全DB write/read/error経路 |
| [adoption.py](../src/gah/adoption.py) request digest / idempotency | request/responseをdigest付きで再生 | segmented actionの同一request replay、segment確定前中断、immutable conflictとread retryを固定 |

上表のAPI名は現コードから確認した接続点である。明示v5の保存actionと専用migrationは実装済み。v2 run/admissionと全consumerのAPIは設計段階で、既定製品入口から利用できることを意味しない。

## 実装可否と親判断

本書は設計案であり、現在のv1経路へ直接差し込める完成APIではない。特にbound outputの全文Plan依存とauthority status responseの変更が必要で、partition writer単独では完了しない。最小の実装群:

1. run_contracts/evaluation_authorityへv2 index/segment validator、固定upload/commit/read actions、RunManifest v2、immutable ref保存とpaged statusを追加する。
2. reference-only bound/admissionを作り、query-scale用builderを固定LLM packから分離する。CaseSetもframe-safeに転送して復元後再検査する。
3. run_evidence/operations_bundle/fixture_materialization/llm_admission/aggregation/runtime entry lookupを全てv1/v2 dispatchへ対応し、fresh authority/sourceとmutation isolationを固定環境で検証する。

要求された規模系列を実runtimeへ接続するため、全consumer migrationまでを残りscopeとする。実装はpure codec/binding、不変保存/再読取、authority開始/状態、全consumer/admissionの順で接続する。通常runとadmissionの全consumerが未対応の間は、その実worker経路を開始可能とは宣言しない。後述の明示gen1・1件診断は固定無害fixtureだけの別入口であり、保存部品や限定診断を通常runのruntime対応へ昇格しない。後述の明示schema-v6診断APIは、worker/admission/CIを接続しない別の限定入口とする。サイズ診断・pure bindingは実admissionや全要求/SLO達成の証拠ではない。

## authority保存の実装方針

親レビューにより、既存の`EvaluationExtension`と既定schema-v4を維持し、明示的に選択する`PartitionedEvaluationExtension`のschema-v5で保存・読取を追加する。新しい6 actionはmanagerのbegin/put/status/commit/abortと、manager/validator/operatorのread。OS peer認証と既存AdoptionStore transactionを使い、candidateは使用できない。現在契約のseries/generation/ref、permission generation、実source digestを照合する。履歴上有効な旧契約だけでは現在のupload/commit/readを許可しない。

未完了uploadは1つ、TTL 3,600秒、indexと最大16segmentの合計は15,300,000 bytes以下。committed artifactsは64 MiB/256plan以下とする。commit markerがあるplanだけを読取可能にし、indexまたは1segmentを返す。期限切れ未完了slotとownerによるabortだけが掃除対象で、committed markerと衝突する破損uploadは削除せず拒否する。この論理上限はfilesystem容量予約やrollback journalの実容量保証を意味しない。

v4→v5は専用の明示migrationで行う。既知のsource/validator pairと厳密なschema・保存行の意味を確認し、追加3表とmetadata更新を同一transactionで確定する。旧行の内容・digestを再署名しない。sourceを移行前後に照合し、不明source・途中変更・DDL障害ではrollbackする。

この段階のv5は保存・読取の接続までで、現在の実Docker入口、v2 run開始、全consumer、CI成功への接続は残る。[DGX限定レビュー](evidence/productization-continuation-20260919/dgx-partition-storage-v1.json)は再送と期限切れ掃除の反例へ反映し、schemaの新しさで旧認証を優先・置換する解釈は採用しない。

この保存・読取・明示migration方針を実装し、[親の80試験](evidence/productization-continuation-20260919/partition-authority-parent-v2.json)で確認した。現在契約と履歴契約を区別し、committed readでもindex/全segments/byte総量/復元digestを検査する。3,200-entry保存fixtureはrun意味・admissionの受入証拠ではない。

## CaseSet codecの実装検証

pure転送codecを実装し、[親の関連45試験](evidence/productization-continuation-20260919/case-partition-parent-v1.json)が成功した。1,600件のv1全文は1,038,560 bytes、2segmentは899,393/139,419 bytesである。復元処理はcanonical化した私有snapshotを検証・使用し、digest照合後の共有入力変更が返値へ混ざる問題を修正した。通信外側のenvelope、保存action、CaseSet/source/contract binding、runtime admissionは後続の接続範囲である。

## 分割planの純粋集計入口

`aggregate_partitioned(manifest, contract, index, segments, policy, registry, case_set, attempts, *, execution_profile, baseline_context=None)` を別入口として追加する。渡された入力の私有snapshotへ完全なv2 bindingを行い、復元したentriesだけを既存の集計coreへ内部で渡す。v1の公開入口・cache・validator・出力形式は維持する。coreのprofile適合、attempt/retry、欠損、重複と矛盾、義務分母、禁止違反の計算を共通化する。

新入口の返値は既存集計fieldに `schema_version=2`、`kind=partitioned_aggregation`、`manifest_ref`、`plan_index_ref` を明示し、`ci_eligible=false` とする。全文plan/segments/CaseSetは返さない。返値は既存のstrict canonical規則と1 MiB上限を通す。attemptsはlistのみ・最大20,000件、個別1 MiB以下を維持し、この新入口だけcanonical bytes総量64 MiBを上限とする。個別に検査・加算して超過時点で拒否し、一括の無制限copyや切捨てを行わない。

この入口は純粋計算用で、採択・現在性・認証・run開始・証拠DBへの保存を成立させない。authorityの各actionでcurrent contract/source/permission/run bindingを再確認し、全consumerが対応するまでv2 run admissionを有効化しない。

## 実Linuxの保存・輸送診断

[明示v5の限定診断](evidence/productization-continuation-20260919/partition-authority-linux-v3.json)では、実authority clientとSO_PEERCREDを使い、manager/validator/operator/candidateの4UIDで隔離条件を確認した。採択済みfixture templateから作るstorage-only 3,200 entriesはlogical 1,574,614 bytes、2segments 899,751/675,375 bytesで保存・commit・読取でき、broker再起動後もindexとsegmentが一致した。candidateのuploadは拒否された。凍結87ファイルの不変と所有container回収を確認した。

この値はCaseSet coverageやruntime admissionを証明するplanではない。診断専用adapterでschema-v5を選択しており、既定製品入口とv2 run開始は変更していない。先行診断のDocker capability/tmpfs表記照合とcontainer内パス処理の失敗は修正前証跡として保持する。

分割集計入口を実装し、[関連114試験](evidence/productization-continuation-20260919/partition-consumer-parent-v1.json)が成功した。1,600 distinct cases/3,200 entriesで全予定分母を集計し、大きなplanにv1 validatorを適用しないこと、small planの共通出力一致、実行profile/attempt束縛、attempt総量・iteration件数・最終出力上限を確認した。旧集計coreはASTで完全一致する。証拠DB・authority freshness・operations bundleへのv2接続は未完了である。

## 内部committed-plan読取

`load_verified_committed_plan(extension, store, db, plan_ref, *, contract_series_id, expected_generation, now)`は認可済みauthority caller専用の内部APIとする。同一storeのDB・extensionと開始済みtransactionを必須にし、DB変更・commit・cache・認証を行わない。現在契約/履歴、permission、実sourceの前後pin、commitされたindexと全segmentsを照合し、一度復元した私有planを返す。返値はindex/segments/plan、contract_series_id/contract_generation/contract_ref、permission_generation/extension_digest。期限切れ・現在契約欠落時の履歴fallbackはない。

## Query-scale corpus codec

`partition_scale_corpus(value)`は `(index, case_set_index, case_set_segments, document_segments)` を返し、`restore_scale_corpus`は同じ4要素から私有v1 corpusを復元する。400/800/1600の既存意味validatorを最後に完全適用する。DB保存、transport action、source/contract binding、admissionの権限は持たない。

corpus indexの閉fieldは `schema_version=2, kind=query_scale_corpus_index, corpus_id, case_count, document_count, claims, stage_counts, case_set_index_ref, ordered_document_segments, reconstructed_bytes, reconstructed_digest`。document segmentは `schema_version=2, kind=query_scale_documents_segment, id, corpus_id, segment_index, first_document_ordinal, document_count, documents[{ref,document}]`。segment IDは `qsd-<corpus_idのSHA256先頭32桁>-<0始まり4桁segment_index>` とする。document kindは既存synthetic input/state/oracleだけを許す。

各index/segmentは900,000 bytes、CaseSetは既存最大16partsと復元1MiB、document segmentsは最大10、documentsはcase_count×4以下。logical corpusはこのcodec専用8MiB上限、global 100,000 nodes/depth16を守り、整数にboolを許さない。全ref・位置・count・digest・byte数・意味を検査し、検査したcanonical bytesからのsnapshotだけを使う。8MiBはDB/transport上限の変更ではない。[親140試験](evidence/productization-continuation-20260919/partition-consumer-parent-v2.json)と[実サイズ](evidence/productization-continuation-20260919/scale-corpus-sizes-v1.json)を根拠とする。

## 分割Run Evidence部品

`PartitionedRunEvidenceBook` は既存のRunEvidence表・FK・状態遷移を共有し、`start_partitioned_run(receipt, execution_profile)` だけを開始入口とする。継承されたv1開始APIは書込み前に `PARTITIONED_ENTRY_REQUIRED` で拒否する。`bound_runs.bundle_json` へは閉じたschema 2の `bound_partitioned_run` receiptを保存し、full planやcorpusを保存しない。v1 bundleにはkind/schema_version欄がなく、既定v1 Store/Bookからv2 rowを利用すると拒否する。SQLite列やRunEvidence schema metadataは変更しない。

各操作で同一DB・開始済みtransactionのprivate `resolve_context(db, now, receipt_copy, stored_profile_copy)` を必須とする。返値のexact fieldsはmanifest, contract, index, segments, policy, registry, case_set, execution_profile, baseline_context。部品内でpartitioned binder・復元・profile一致を再検査し、全文はその操作の内部値だけにする。segmentsは復元前から既存MAX_SEGMENTS以下に制限する。保存/表示するbinding summaryはmanifest/contract/index/profileのdigestだけである。旧aggregate/attempt/finalize coreをprivate hook経由で共有し、集計は専用partitioned集計へ渡す。

当面の対象は契約generation 1、purpose diagnostic、comparison not_applicable、baselineなし。失効・context変更・profile不一致・resolver欠落・transactionなしは拒否する。部品自体にはOS認証・現在採択・permission/sourceの証明能力がないため、将来の外側authorityが既存committed reader等で毎回検査する。この部品検証時点では外側authorityへ未接続だった。schema-v6の認証actionと明示移行は後述の追加実装であり、assurance artifact/全consumer/runtime admissionへの接続は残る。CI利用は常にfalseである。小fixtureの部品成功を1,600件の契約採択・実run・性能受入へ数えない。

[親45試験](evidence/productization-continuation-20260919/partition-evidence-parent-v2.json)で旧Evidence 34件と新分割Evidence 11件を確認した。実SQLite部品試験であり、外側resolverは試験専用。OS認証・実workerの受入証跡ではない。


## schema-v6の診断run接続

明示選択する `PartitionedRunEvaluationExtension` を追加する。既定schema-v4と既存v5の選択は維持する。対象は採択済み契約generation 1、purpose diagnostic、comparison not_applicable、baselineなし。現在は実装と結合検証の工程で、実workerや拡張14条件の受入完了を示さない。

全要求は閉じたschema 1のenvelopeで、`schema_version, action, request_id` が共通必須fieldとなる。

| action | 追加field | 認可role |
|---|---|---|
| run_begin_v2 | manifest, contract_series_id, expected_generation, execution_profile | operator |
| run_status_v2 | run_id | manager / validator / operator |
| evidence_record_v2 | run_id, attempt | validator |
| evidence_attempt_v2 | attempt_id | manager / validator / operator |
| evidence_finalize_v2 | run_id | operator |
| evidence_terminal_v2 | run_id | manager / validator / operator |
| evidence_current_v2 | run_id, expected_bundle_digest | manager / validator / operator |

beginはv2 RunManifest本文を受ける。manifestを登録・解決する別producerはないため、manifest_refだけを受ける設計にはしない。plan参照は `manifest.plan_ref` の一つに固定し、重複したplan_ref引数を設けない。manifestは900,000 bytes、transport frameは従来1 MiB以下。execution_profileは既存strict validatorと復元planへの適合検査を通す。profile本文の提出は実worker実行の証明にはならない。

`eval_runs_v2` はrun_id、manifest_json、manifest_digest、bundle_digest、contract_series_id、contract_generation、permission_generation、extension_digest、created_atを保存する。contract_generationは1、permission_generationとcreated_atは非負整数に制限する。run_idは `bound_runs(run_id)` への `DEFERRABLE INITIALLY DEFERRED` 外部キーとし、同じAdoptionStore transactionでmanifest行、ResourceBookのrun、ref-only bound receipt、run_stateを作成する。resourceのmanifest digest・policy/profile・deadline・作成時刻を照合し、再送時に欠損したresource行を自動補充しない。途中の既知型例外は理由codeを保持して返し、全書込みとidempotency更新をrollbackする。RunEvidence既存表・列・metadataは変えない。

全7 actionはfreshとする。同request再送でも、OS identityに対応するrole、actor取消し、permission世代、現在契約と採択履歴、policy/source、committed planと全segments、manifest/receipt/profile bindingを毎回照合する。古い応答を使った現在性検査の省略は行わない。通常run・candidateの旧新run・fixture/guardrail admission・combined childの予約IDを具体的な保存場所から照合し、旧APIの準備/開始もv2との衝突を拒否する。candidate_idや任意のrequest_id文字列はrun IDと比較しない。

返値envelopeはschema 1の `partitioned_run_authority_result`、run/evidence viewの外枠はschema 2とする。内部Decision・保存terminalなど既存正本のschemaとdigestは書き換えない。全応答のci_eligible、resource_closure_verified、admission_verifiedはfalseで、authority_connected/adoption_verifiedもAssurance判定を証明しないことを示すfalseのままとする。呼出元の認証が行われたことと、資源・採択・証跡を含む製品Assurance成立は別の条件である。

## schema-v5からv6への明示移行

`migrate_partitioned_run_store_v5_to_v6(path, *, expected_source_digest)` を使用する。暗黙移行やdry-run APIは追加しない。既知の固定v5 extension/validator pair、または移行entryで照合した現在v5 pairだけを受理する。path、厳密な表/列、設定/metadata、既存JSON・履歴、partition index/segmentの保存整合を検査し、新しいeval_runs_v2表だけを追加する。移行前後でsourceが変化した場合やDDL/内容検証に失敗した場合はrollbackする。

意図した変更はadoption_meta.schema_version、adoption_config.extension_digest、PRAGMA user_versionだけである。metadata/configは許可された変更後の値と照合し、それ以外の既存全行は値まで保持する。旧plan/evidenceのdigestは再署名しない。移行後の旧planは旧sourceに束縛されたままPLAN_STALEとなり、再登録には新しいplan_idを使う。旧v5実DBを保存して移行・再openを比較する試験は、現行ソースで作った空DBの試験と区別して証跡へ記録する。

[親の関連88試験](evidence/productization-continuation-20260919/v6-parent-v3.json)と[変更前実v5 DBの移行](evidence/productization-continuation-20260919/v6-real-migration-v1.json)で上記診断接続を確認した。旧DBの全44表34行を許可metadata以外保持し、新plan登録・診断開始・再起動まで検証した。実OS peer/worker/通常regression/全consumer/SLOの受入は含まない。

### 診断runの資源予約と取消し

v6 gen1診断runは既存ResourceBookへ接続する。開始・reserve・dispatchは現在採択/permission/source、保存plan/profile、期限、retirement、run状態を検査し、FINALIZEDやHOLD後の新規開始を拒否する。取消しはoperatorの現在roleとactor失効、保存manifestとresource行の結合、owner/epoch/leaseを検査する。開始時の契約やpermission世代が古くても、現在認可されたoperatorが既存資源を回収できる。

`resource_cancel`は未送信予約だけをreleaseする。`resource_cancel_claim`は期限切れownerの取得と取消しを同じtransactionで確定し、失敗時は双方rollbackする。送信済み操作の停止・usageを推測せず、validatorの観測まで未精算を保持する。未確認ならcloseを拒否する。actor自体の失効は取消し権限も失わせるため、別の現在認可されたoperatorが回収する。v1のterminal/Evidence経路は引き続き未接続である。後続の`resource_operation`読取は保存時のmanifest/receipt/profile、採択履歴とpolicy pin、分割plan全segment、resource entry/usageの結合を検証する。現在認可されたoperator/validatorに限り、取消し・期限切れ・現在契約変更後も保存状態を確認できる。これは開始/再送の許可ではなく、domain状態を変えない。clock/idempotencyの記録は許可する。manifest作成後に開始が遅れた場合も正当な時刻範囲を受け入れる。[関連60試験](evidence/productization-continuation-20260919/v6-resource-read-parent-v1.json)が成功した。

[親の関連71試験](evidence/productization-continuation-20260919/resource-cancel-parent-v1.json)でcache修正と合わせて検証した。実worker/新v6資源経路のOS認証/全consumer/SLO受入は含まず、全応答の適格性flagsはfalseを維持する。

[固定source-v13の実Linux診断](evidence/productization-continuation-20260919/v6-linux-smoke-v3.json)でSO_PEERCREDの4役割・31操作を確認した。candidateによる資源開始拒否、取消し後のslots/unsettled各1とstopped/settled各false、同ownerの取消しclaim、validatorの合成停止/zero-usage観測後の精算・closeまでを含む。期限切れowner取得はこの診断では実証せず、別の部品試験に限る。実workerは起動せず、全適格性flagとSLO/実worker受入はfalseである。

### 明示v6 runtimeと固定fixture一件診断（実装・検証中）

`AuthorityRuntime(..., database_mode="partitioned-v6")` は固定 `broker-v6` をUID 12000で起動し、schema 6をreadinessで照合する。空DBだけを新規作成し、既存DBの版・extension digest不一致は拒否する。旧3-key deploymentはdefault専用、v6 deploymentは `database_mode: partitioned-v6` を追加した閉じた形とし、再接続・再起動・回収で保存modeを照合する。既存DBの暗黙移行や任意extension指定は設けない。同deploymentの保存操作はcallerの排他を前提とし、read/merge/writeをprocess間CASとは扱わない。

既存 `python -m tools.gah_run diagnostic --runtime <v6-runtime-dir> --request <request.json>` へ限定consumerを接続する。既存の採択済みv6 runtimeと15-entryのcommitted planを前提とし、このコマンドは契約やplanを採択しない。runnerは固定fixtureのみである。

入力はschema 1、kind `partitioned_diagnostic_request` の閉じたobjectとし、追加fieldは `begin_request` と `selector` だけとする。begin_requestは既存 `run_begin_v2` の厳密schemaに従い、gen1・diagnostic・UC-CI・baselineなしのmanifest/profileを受ける。selectorは `obligation_id, case_id, trial_id, variant` の完全一致で一件を指し、variantはcandidateに限定する。15件plan自体は変更しない。新CLIは任意target、worker本文、image、権限を受け取らない。

CLIは開始要求と検査済み応答を独立したCheckpointへ保存する。同じ要求の再開では保存応答を利用して既存操作の回収に進めるが、新しい実行の認可には現在のauthority検査を必要とする。異なる要求で同じ保存先を使う場合は拒否する。consumerはplan全segment、manifest/profile、固定 `constraint:C01:good` とworker/adapter/isolationの束縛を照合し、既存DockerRunnerとExecutionJournalで一件だけを起動する。

資源のdispatch意図、実workerの停止、validatorによる精算、Attempt保存、budget closeは別段階として検査する。通常完了時の所有leaseと、停止回収用のcancel claimを区別する。クラッシュ後に確認できるのが停止・回収だけなら成功Attemptを作らず、既知の非モデルAPI費用だけを精算し、診断は中断として返す。停止不明・journal不整合は未精算を維持する。authority読取拒否があっても、完全に結合したlocal journalの所有worker回収は試み、元の拒否理由を保持する。

close要求の送信前後に応答を失った場合も、新しいworkerを配送せず再開する。元の厳密なbegin要求をconsumerへ渡し、同じ要求のfresh再送で資源snapshotを照合する。既知の成功receiptと保存Attemptに加えてclosed/budget_closureを確認できる場合だけ閉鎖済みの結果を復元する。openの場合は現在の正当なleaseを取得してcloseする。claim応答の時刻変化を固定Checkpointの衝突へ変換せず、古いleaseで現在の権限を代用しない。

成功の終了0は一件診断の完了を表し、全15件の実行・finalize・admission・現在CI・SLOの成功を意味しない。正常返値も適格性の6flagをすべてfalseとする。失敗は診断errorと終了2を返し、未知のCPU/RSS/IOを0で補完しない。実装候補の単体試験・親レビューと、固定imageを使う実OS受入は別に記録する。

[親の関連86試験](evidence/productization-continuation-20260919/partitioned-diagnostic-parent-v2.json)と、[source-v14の実worker一件診断](evidence/productization-continuation-20260919/v6-worker-runtime-v2.json)が成功した。実診断は固定15-entry planの一件のみで、停止・回収・精算・Attempt保存・budget close、再送の追加起動0を確認した。gen1 UC-CI以外のproducer、全件consumer、通常gen2・LLM・baseline/admissionの接続は未完了である。


## 契約前のcorpus provisioning

400/800/1600件の分割CaseSetとdocumentを契約より先に確定するため、[schema-v7の保存仕様](productization-corpus-provisioning-spec.md)を追加した。managerのcurrent policy・permission・source pinを検査し、commit後だけrefを読み出す。新規v7 DBの保存・同一commit再送・再起動後復元は[実コンテナで確認済み](evidence/productization-continuation-20260919/v7-corpus-runtime-v1.json)。v6→v7移行は検証済み。初回の固定LLM admissionは既存authorityへ接続し、baseline/gen2全件実行の受入を継続する。

## 固定query-scale入力からworker保存への接続

`partitioned_llm_materialization.build` は400／800／1,600件の単段階corpusから全件plan、manifest v2、分割artifact、profileとref-only bindingを生成する。初回はcandidateのみ、次世代の候補はbaseline/candidateの両方を生成する。契約とregistryのIDは評価条件から決め、runを変えただけで契約を変えない。返値はprivateなartifact集合であり、集合全体を単一wire文書として送らない。

比較入力は `{baseline_ref, targets, contract}`。以前の契約本文を既存validatorへ通し、CaseSet・評価器・校正集合・policyの一致と、同じimageの固定対象2版への限定を確認する。共通binderへは従来の2-field baseline contextを渡す。baseline参照と以前の契約の採択関係・現在性はauthorityでの照合が必要であり、このbuilderから証明しない。

`partitioned_guardrail_results.PreparedCases` は生成物を最初に一括検査してindex化し、各要求では登録済みentryの完全一致を確認して1ケースだけを独立コピーする。`PartitionedGuardrailRunner` は既存の隔離・journal・再送・回収を再利用する。journalは固定query-scaleのcase IDでfamilyを選択し、そのfamilyのvalidatorが入力・評価器・source・worker・対象・隔離条件を再照合する。従来の固定pack用公開validatorはquery-scale入力を受理しない。

[接続検証](evidence/productization-continuation-20260919/query-scale-flow-v1.json)では関連44試験と計2,800ケースのin-process worker検査が成功した。実コンテナでは各規模の末尾3件と対象2版の比較2件、保存・再読込・再送・回収を確認した。採択済みの通常gen2実行、freshなCI、全規模の実runtime履歴・SLOは未接続・未受入である。

## 分割入力の初回通常admission

`guardrail_prepare_partitioned` はmanagerが現在のpolicyを指定し、400／800／1,600件の固定入力を準備する。契約の提案・独立検証・採択は既存APIを利用する。`run_begin_partitioned` はoperatorがrun ID・契約series・期待manifest参照を渡し、現在の採択・policy・permission・対象失効・期限を照合して開始する。新しいDB版は追加せず、既存admission/object/run/resource/Evidence表を利用する。

保存する計画はindex、Evidence bundleは小さい内容参照である。全計画は同じartifactから内部で復元し、各操作の状態照合に使う。`evidence_open` の応答は既存authorityのschema 1を保ち、schema 2のEvidence本文を`evidence`フィールドへ格納する。純粋な復元・意味検証だけを既存の容量制限付き共有cacheで再利用し、DBの状態や採択の現在性を継続的なcache結果で代用しない。

[親の接続検証](evidence/productization-continuation-20260919/partitioned-normal-authority-v1.json)では、1,600件の採択・開始と末尾1件の固定worker結果の精算・保存・再open、既存経路の互換性を確認した。関連73種類の試験が成功し、初回失敗と再実行を分けて記録した。全件実行からbaseline採択、次世代契約・通常gen2・CLI・fresh CI、速度改善の実測は未完了である。

## 候補保存との直接照合

分割入力のgen1→gen2候補と通常regressionを既存authorityへ接続する。外側のschema 1応答は維持し、準備本文はschema 2の小さいrootとartifact参照で配送する。`run_input_artifact`はrun ID・期待manifest参照・rootに登録されたartifact参照を照合する。歴史データの読取と開始許可は分離し、開始とCI判定で現在の採択・失効を検査する。

保存候補はcompact rootを先に読む。固定factoryから得た期待入力との構造照合、保存root確認、全artifactのkind/id/digestとcanonical bytesの現在DB照合を行ってから、その生成済み入力を内部処理へ渡す。保存本文を全文復元し、同じ計画を再構築してから比較する往復を避ける。これは権限・DB状態の継続cacheではない。

pure build/rebindは既存16 MiB/32 entryの共有cacheを使用し、完全入力・source・実装identityをkeyとする。失敗は保存せず、返却treeは独立する。既存入力node上限に触れる1,600件は通常計算へ戻る。個別本文上限と内部保持の仕様整合・128 MiB内の実測は未受入とする。[途中記録](evidence/productization-continuation-20260919/partitioned-transition-progress-v1.json)の33局所試験と、先行sourceの初回400件・基準採択を区別する。現行sourceの全件比較・通常CLI・fresh CI・SLOは未受入である。
