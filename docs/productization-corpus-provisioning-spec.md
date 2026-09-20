---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-20
next_review_due: 2026-10-20
---

# PAC07 query-scale corpus provisioning（schema-v7）仕様

## 目的と境界

本書は、固定 query-scale corpus を authority の認証付き・再検証可能な保存物として確定する、v7 provisioning 専用APIを規定する。契約、TrialPlan、run、LLM admission、baseline、通常実行を作成・許可する仕様ではない。corpus の commit は、後続の別producerがCaseSet refを契約へ結ぶ前提条件である。未commit upload は一切のread/contract bindingに使えない。

入力corpusの唯一の初期producerは固定実装 `query_scale_data.build_scale_corpus(case_count)` とし、`case_count` は厳密なintの400/800/1600だけを許す。transport APIは型別artifact bodyを受け取るが、target、worker、任意のcountは受け付けない。commitでは `partition_scale_corpus` / `restore_scale_corpus` による既存意味検査後、保存された全artifactのcanonical bytesを同じcountの固定 `build_scale_corpus` から再生成した4 artifact群のbytesと完全一致させる。semantic validationだけではproducerとの同一性にならないため、この完全一致を必須とする。これは単段の独立query-scale familyであり、固定400 LLM acceptance packやnormal LLM 800と同一workloadとは扱わない。codec自身のclaims（runtime admission、semantic oracle independence、real-workload performance）はfalseのまま保持する。

新しい `PartitionedCorpusEvaluationExtension` は明示schema-v7としてv6から派生する。今回はcorpus provisioning/readだけを追加し、v6のgen1・diagnostic・baselineなしrun gateを変えない。v4/v5/v6の既定選択、actions、tables、validator、保存JSONは変更しない。v7からgen2、LLM transition、baseline、通常run、admissionまたはCIを有効化しない。

## 既存codecの保存対象と上限

純粋codecの戻り値は以下の4 artifact群である。

| Artifact | kind / ID | fieldと上限 |
|---|---|---|
| Corpus index | `query_scale_corpus_index` / `corpus_id` | `schema_version, kind, corpus_id, case_count, document_count, claims, stage_counts, case_set_index_ref, ordered_document_segments, reconstructed_bytes, reconstructed_digest`。900,000 bytes以下。 |
| CaseSet index | `case_set_index` / `case_set_id` | 既存 `partitioned_case_set` exact schema。CaseSet全体のref、case count、segment refs、canonical byte数/digestを保持。900,000 bytes以下。 |
| CaseSet segment | `case_set_cases_segment` / codec決定ID | 既存 `schema_version, kind, id, case_set_id, segment_index, first_case_ordinal, case_count, cases`。最大16個、各900,000 bytes以下。 |
| Document segment | `query_scale_documents_segment` / codec決定ID | `schema_version, kind, id, corpus_id, segment_index, first_document_ordinal, document_count, documents`。各document itemは `{ref, document}`。最大10個、各900,000 bytes以下。 |

Corpus indexの `case_set_index_ref` は `case_set_index` ref、`ordered_document_segments` は順序付きdocument segment refを保持する。CaseSet index内のordered refsは全CaseSet segmentを固定順に指す。segment ID、ordinal、count、reference digestの生成規則は既存pure codecの正本であり、store側で再命名・再番号付けしない。

固定semantic上限は400/800/1600 cases、documentsは1〜case_count×4、CaseSet復元後は既存1MiB、各artifact 900,000 bytes、復元corpusはcodec専用8MiB、CaseSet parts最大16、document segments最大10、全体node/depth上限は既存codecどおりとする。boolを整数として受け入れない。8MiBはSQLite/transport/OS容量保証ではない。stored uploadはindex 2個＋CaseSet segment 16個＋document segment 10個の最大28 artifactを含み、個別900,000-byte上限、upload合計64MiB以下とする。committed corpus合計は64MiB、commit数は256以下。上限はpartition-plan storeと同等の独立論理capで、DB全体・rollback journal・host/Docker volumeの物理quotaやENOSPC耐性を保証しない。

成功commitは `corpus_ref = content_ref("query_scale_corpus_index", corpus_id, index)` と、復元したv1 CaseSetから計算した `case_set_ref = content_ref("case_set", case_set_id, case_set)` を返す。contract producerはこのCaseSet refを使えるのはcommit後だけであり、commitはcontractを作成・adoptしない。

## schema-v7 action API

各requestはschema 1の閉じたJSON objectで、common fieldsは厳密に `schema_version, action, request_id`。unknown field、重複・不正ref、bool integer、oversize、custom objectは拒否する。表中で示すfield以外を受け付けない。

| action | common fields以外のrequest fields | role |
|---|---|---|
| `corpus_partition_begin` | `upload_id, policy_series_id, expected_policy_generation, expected_permission_generation, index, case_set_index` | manager |
| `corpus_partition_put_case_set_segment` | `upload_id, segment` | manager |
| `corpus_partition_put_document_segment` | `upload_id, segment` | manager |
| `corpus_partition_status` | `upload_id` | manager |
| `corpus_partition_commit` | `upload_id` | manager |
| `corpus_partition_abort` | `upload_id` | manager |
| `corpus_partition_read` | `policy_series_id, corpus_ref, artifact_kind, segment_index` | manager / validator / operator |

`artifact_kind` は `corpus_index`, `case_set_index`, `case_set_segment`, `document_segment` のいずれか。`segment_index` はheader読み取り時は厳密なnull、segment読み取り時は対応kindの範囲内の厳密な非負int。readは必ず既存commitの `corpus_ref` とcurrent policy seriesに束縛される。別request fieldにpolicy generationを任意指定してhistorical bypassを作らない。

全response envelopeは `schema_version=1, kind="partitioned_corpus_store_result", action, request_id, ci_eligible=false` とする。beginは `upload_id, corpus_id, corpus_ref, expected_case_set_segments, expected_document_segments, stored_bytes, expires_at, resumed, already_committed, committed_at` を返す。putは `upload_id, artifact_kind, segment_index, duplicate`。statusは両segment群のpresent/missing index配列とexpires_at。commitは `corpus_ref, case_set_ref, case_count, document_count, reconstructed_bytes, reconstructed_digest, artifact_bytes, committed_at, already_committed`。readは `corpus_ref, policy_series_id, policy_generation, artifact_kind, artifact` を返す。`artifact` は要求したindexまたはsegment本文一つで、本文フィールド名はこの固定名を使う。応答はcorpus全体を再同梱しない。これらのAPIは `adoption_verified`, `runtime_verified`, `authority_connected`, `baseline_freshness_verified`, `ci_eligible` をtrueにしない。

## Freshness、認可、source pin

7 actionすべてをextensionのfresh action集合へ登録する。exact request replayでも保存済み応答だけを返してhandlerを省略してはならない。AdoptionStoreが認証したOS role/actor/contextに加え、毎回以下を同一SQLite transactionで再検査する。

1. 固定v7 extensionのexact type・source digestをstore pinと照合する。commit/readのcanonical restore前後でもdigestを再照合し、処理中変更を拒否する。
2. `evaluation_authority._policy(store, db, policy_series_id, now)` 相当で現在adopted/current/valid policyを得て、profile ID、expected/current generation、policy digest/refを一致させる。beginではexpected permission generationも厳密比較する。後続各actionはその現在permission generationをrow pinへ照合する。
3. upload操作では同じowner_actorとowner_context、upload_id、未commit状態、TTL、permission generation、extension digestを照合する。別actorのresume/put/status/commit/abortを拒否する。
4. committed readも保存済policy series/generation/ref、現在permission generation、v7 sourceを照合する。policy更新後に旧commitをhistorical refだけで読み出せない。更新後に同じ固定 corpusを再provisionする場合は新しいupload/commit認可記録が必要で、既存pinを書き換えない。

policy/permission/source失効・不一致時はfail closed。期限切れuploadは契約へ使えない。新beginで期限切れの未commit uploadを掃除する場合も、先に現在manager role・current policy/permission・sourceを検証し、expiredかつcommit markerなしの当該staging行だけを同transactionで削除する。committed artifactや別tableのplanを掃除しない。

## Upload/commit/read状態と冪等性

- 一つのdatabaseにつきactive corpus uploadは最大一つ、TTLは作成時刻から3,600秒。beginがupload/policy/count/refをpinする。active uploadがあれば別uploadは `UPLOAD_BUSY`。expected policy/permission/sourceの変化、owner/context不一致、期限切れではresume不可。
- 同一upload_idで同一policy pin・同一canonical index群のbegin再送はfreshness再検査後、staging中なら `resumed=true` でstatusを返し、既にcommit済みなら保存owner/pinと完全restoreを検査して `already_committed=true` を返す。異なる本文・別ownerでの再利用はconflict。
- CaseSet/document segment putは、indexのordered ref、kind、ID、segment index、first ordinal、count、canonical digestと正確に一致させる。既存ordinalへの同一canonical bytes再送だけを `duplicate=true` として許す。異なる内容は `SEGMENT_CONFLICT`。単一segmentの上書きはない。
- statusはDBのsegment keysを順序付きで返し、範囲外・duplicate key・stored_bytes不整合をcorruptionとして拒否する。
- commitは全CaseSet segmentと全document segmentが揃い、indexに記録された順序と完全一致する場合だけ行う。全canonical bytes/ref/digest/byte count/ordinal/countを確認し、既存 `restore_scale_corpus(index, case_set_index, case_set_segments, document_segments)` を呼んで完全なCaseSet/document/corpus意味検証を行う。400/800/1600件、case ID/lineage/input/oracle/initial-state参照、label分布、single-stage counts、document ref uniquenessと全 coverage、claims、全体digestを検証する。index/headerを信頼せず復元値からCaseSet refを計算する。
- commit marker、artifact bytes/countの加算、staging削除は同一transactionで確定する。欠損・不整合・上限・DDL/SQLite障害ならmarkerなしで全rollbackし、既存committed rowsを保持する。同一upload_idからのcommit再送は保存owner/current pinを再検査し、保存済み全artifactを完全restoreして `already_committed=true` を返す。commit markerは `upload_id` をUNIQUEで保持するため、ACK喪失後もupload_idで結果を復元できる。同一policy series/generation/corpus_idに対して異なるupload_idの新commitを作らず `CORPUS_CONFLICT` とする。policy generationが変わった場合のみ新commit bindingを許す。既存commitを再署名・上書きしない。
- abortは同じ現在owner/contextとfreshnessが必要で、未commit stagingだけを原子的に削除する。commit済みcorpusはabort不可。成功後のabort再送でuploadが無い場合は固定 `UPLOAD_MISSING`（状態変化なし）を返す。これは削除済みデータの成功receiptを偽造しない。
- readはcurrent pin検査後にcommit/index/全segmentsをcanonical保存値からloadし、各stored digest/byte_countと `restore_scale_corpus` を再実行してから要求artifact一つを返す。index read、CaseSet index read、型別segment readはすべて同じ完全commitの検査を通る。readはuploadを見ず、partial uploadを返さない。

## schemaと明示migration

v7はv6の後方互換aliasではなく明示extension/runtime modeである。新moduleは `partitioned_corpus_store.py`、v7 extensionは `PartitionedCorpusEvaluationExtension` とし、v6 extensionをbaseとしてsource digestへ新store module bytesを固定する。v7はcorpus action/tableだけをmergeし、v6 run actions/gatesをそのまま継承する。v6→v7 migration module/APIは未実装で、後続の明示移行設計に属する。

追加表の厳密なcolumn set:

| table | fields（他column禁止） |
|---|---|
| `partition_scale_corpus_upload` | `singleton, upload_id, corpus_id, policy_series_id, policy_generation, policy_ref_json, corpus_index_json, corpus_index_digest, case_set_index_json, case_set_index_digest, expected_case_set_segments, expected_document_segments, stored_bytes, owner_actor, owner_context, permission_generation, extension_digest, created_at, expires_at` |
| `partition_scale_corpus_segments` | `upload_id, artifact_kind, segment_index, segment_json, segment_digest, byte_count`。PKはupload/artifact kind/index。artifact_kindはCaseSet/documentの二値。 |
| `partition_scale_corpus_commits` | `corpus_id, policy_series_id, policy_generation, upload_id, policy_ref_json, corpus_index_json, corpus_index_digest, case_set_index_json, case_set_index_digest, case_set_ref_json, artifact_bytes, owner_actor, owner_context, permission_generation, extension_digest, committed_at` |
| `partition_scale_corpus_committed_segments` | `corpus_id, policy_series_id, policy_generation, artifact_kind, segment_index, segment_json, segment_digest, byte_count`。PKはcommit binding/artifact kind/index。composite FKでcommit markerへ結合する。 |

upload singletonは1行制約、upload_id/corpus pinとの一致制約を持つ。`partition_scale_corpus_segments` はstaging uploadにFKする。commit時にはcommit markerを追加し、全ての検査済みsegmentを `partition_scale_corpus_committed_segments` へ同一transactionでコピーした後にstaging行を削除する。従って、readはstagingの有無に依存せずdurable commit segment表だけを使う。commit PKは `(corpus_id, policy_series_id, policy_generation)`、`upload_id` はUNIQUEとし、同じ固定corpus bytesを新しいcurrent policy generationで明示再provisionする余地を残す。異なるpolicy series/generationからreadする場合はrequest内seriesでcurrent rowを一意に選ぶ。committed segmentsは同一commit bindingに結び、policy pinの違うcommit間で暗黙共有しない。

明示移行は `migrate_partitioned_corpus_store_v6_to_v7(path, *, expected_source_digest)` で行う。CLIは `python -m tools.migrate_evaluation_store PATH --partitioned-corpus --expected-source-digest SHA256`。移行flagと元extension digestは両方必須で、既定の旧v2移行経路は維持する。通常openの自動upgrade、v4/v5/未知schemaの直接移行、未知source pairは拒否する。移行成功の終了0は管理操作の完了であり、CI成功ではない。

許可する元pairは、[固定source-v16](evidence/productization-continuation-20260919/source-v16-freeze.json)のv6 extension `267711ce3e4de23c9f5646e7e9bc68f28f92da9409f169a0220163a9efec5b00` / validator `4dd2dbd519231346bb4536655b470684574d559bcefbc389d460c454f20d4494` と、entry時に現在source bytesを再照合したv6 pairである。厳密なtable/column/meta/config、保存JSONとbinding、FKを検査する。v6診断runのEvidenceは保存時の契約・plan・policy・保存時計を使って既存EvidenceBookで意味検証する。現在の失効・期限切れを過去記録の破損と混同しない。既知v5またはentryで照合したcurrent-v5の旧planも、元のextension digestを変更せず保存する。

`BEGIN IMMEDIATE` 内でcorpus用4表だけを追加し、`adoption_meta.schema_version`、`adoption_config.extension_digest`、`PRAGMA user_version` 以外の既存rowを保持する。全rowの前後照合・FK検査・元/先source再照合後にcommitし、途中失敗はrollbackする。SQLite内部の `PRAGMA schema_version` は手動設定しない。移行後の旧plan/runは旧sourceに束縛されたままで、新しい現在利用やCIへ昇格しない。

[関連47試験](evidence/productization-continuation-20260919/v7-migration-parent-v1.json)と[固定旧版DBのCLI移行](evidence/productization-continuation-20260919/v7-real-migration-v1.json)で検証した。固定v6の45表46行を許可metadata以外不変で移行し、v7再openと旧plan拒否を確認。現在checkoutの正規v5→v6→v7連続移行も検証済み。これらは診断runと保存移行の受入であり、大規模LLM admission・gen2通常run・全PAC受入ではない。

## 固定error code

Malformed requestは `INVALID_REQUEST`、role/owner不一致は `PERMISSION_DENIED`、action不明は `INVALID_ACTION`、requestのuint範囲外は `INVALID_REQUEST`、内部時刻の不正値は `CLOCK_INVALID`（authorityの時計逆行は既存 `CLOCK_ROLLBACK`）とする。policy/permission/source不一致は `POLICY_STALE`, `PERMISSION_STALE`, `EXTENSION_INVALID`。stagingは `UPLOAD_BUSY`, `UPLOAD_MISSING`, `UPLOAD_STALE`; segmentは `SEGMENTS_INCOMPLETE`, `SEGMENT_BINDING_MISMATCH`, `SEGMENT_CONFLICT`, `REFERENCE_MISMATCH`; corpus validationは固定 `CORPUS_INVALID` または既存codecの安定したContractError codeへ写像する。容量/保存は `STORAGE_LIMIT`, `STORAGE_CORRUPT`, `TRANSACTION_REQUIRED`。exception text、任意JSON、filesystem/database pathをresponseへ漏らさない。

## 必須検証

実装は少なくとも次をisolated unit/integration testで確認する。一般テストからDockerを起動しない。

1. 400/800/1600それぞれをpure producer→partition→begin/typed puts/status/commit→read全artifact→restoreし、全ref・canonical bytes・CaseSet内容・corpus digestが元の固定builderと完全一致。CaseSet refはcommit前に使えずcommit後に一致する。
2. bool/不正count、unknown field、1 byte oversize、wrong artifact kind/ID/segment index/ordinal、欠損・重複・順序違い・re-signed ref・modified document/case、claims/stage-count改変をcommitで拒否し、同transaction後rollback時に既存commitが保持される。成功したpart duplicateのみno-op。
3. row/total cap、256 commit cap、単一upload slot、TTL、expired cleanup、exact replay、同request id別body、cross-owner/context、changed policy generation/permission/sourceでの各action拒否を確認。拒否後に stale/uncommitted/committed行の範囲が仕様どおり。
4. committed readはmanager/validator/operatorのみ成功し、candidate拒否。policy currentness消失/世代変更/source mismatch/commit marker・segment digest破損を各artifact-kind readで拒否。readはdomain stateを書き換えず、reopen後も同一ref/bytesを返す。
5. 新規v7 DBでprovision/read/reopenを確認する。既存v6 DB対応を別途実装する場合は、そのmigration専用試験でv6のrun/plan/resource/evidence rowsが不変、corpus用4表だけ追加、reopenとv6 run diagnostic継続、v4/v5/unknown pair/DDL注入失敗のrollbackを確認する。migrationが実装・検証されるまでは既存v6 DBを拒否し、default v4/v5/v6 modeは変えない。

## 後続の未実装境界

この仕様完了だけではCaseSetをadopted EvaluationContractにしない。次段階のcontract producerは、committed readerから復元CaseSetを取得し、既存 `evaluation_authority.contract_propose/contract_validate/contract_adopt` のfresh policy/permission/acceptance/calibration検査へ接続する必要がある。query-scale documentsを実runtimeへ渡すref-only materializer、TrialPlan生成・commit、LLM evaluator意味、baseline record、gen2 transition/normal execution、Evidence/operations consumers、CIも未接続である。

`partition_scale_corpus` のsingle-stage synthetic familyを400 fixed LLM acceptanceや800 old/new pairに読み替えない。ここではperformance/semantic independence/runtime admissionの証拠を作らない。gen2/baselineはこのv7 provisioning patchに追加せず、v6 gatesを保つ独立後続工程とする。


## この仕様の実装確認

新規v7 DBについて、[親検証](evidence/productization-continuation-20260919/v7-corpus-parent-v1.json)と[実OS認証・実コンテナの保存診断](evidence/productization-continuation-20260919/v7-corpus-runtime-v1.json)を実施した。400/800/1600件のcommit再送、全artifact復元、再起動後read、candidate拒否、cleanupを確認済み。前節の後続境界とv6移行は未実装のままとする。
