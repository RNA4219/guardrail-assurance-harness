---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-20
next_review_due: 2026-10-20
---

# 拡張実装と製品受入の対応

2026-09-20。対象は[拡張14要件](productization-requirements.md)と[実装Task](tasks/TASK.productization-implementation-09-15-2026.md)。文書検収・技術受入・実案件での有用性・GitHub公開を別に扱う。下表の局所試験は全PACの合格を意味しない。変更中のsourceに対する長時間試験は不変コピーを使用し、試験時sourceと最終sourceの差を記録する。

## 14要件の対応

| PR / PAC | 実装と局所検証 | 製品受入に残るもの | 状態 |
|---|---|---|---|
| 01 | pilot登録・固定計画・独立metadata採択、offline結果importの厳密検査と算術正規化 | UC-CI 2repoとUC-LLM 1対象の選定、独立出所確認・実targetへの実行経路 | NOT_RUN |
| 02 | 校正/観測の算術検査、欠損・Critical・不明ラベルの判定 | 実履歴20組、正常100件、実劣化から修復後の再検査 | NOT_RUN |
| 03 | TP/FN/TN/FP・分母・カテゴリ・二重配送・版照合 | 利用許可済みの独立した実利用集合、対象2版の実測 | NOT_RUN |
| 04 | 対応観測・資源/介入回数の比較、順序と待ち時間の記録形状 | 両UCの20観測、原則10営業日の運用、実効果 | NOT_RUN |
| 05 | 厳密plan・query/whole-run壁時計計測、authority/host snapshot、回収前client計測、固定workerの終了sidecarと予定全件への束縛 | 全子CPU/RSS/IO/copy等の計数、基準/候補各3回 | INCONCLUSIVE |
| 06 | 固定full範囲・資源条件を維持。純粋pack/lookupの再利用、確定時の重複集計・Checkpointの重複コピーと要求内snapshotを修正し、出力・返却分離を検査 | 同条件の意味・CPU・wall比較と性能SLO | INCONCLUSIVE |
| 07 | HMAC cursor・metadata一覧、分割入力・全件plan生成、固定worker/journal、初回通常admission・1600件の採択/開始と末尾1件の保存 | 先行sourceで初回400件・baseline採択まで確認。現行sourceの比較全件・通常gen2/CLI・fresh CIの一連受入、実履歴系列、実cold/warm lifecycle・全計数・失効対照のSLO | INCONCLUSIVE |
| 08 | 12lane・全件discovery・履歴結合・SHA/plan/全job集約 | GitHubの各3回、実待ち時間/費用、追加テスト込みのSLO | INCONCLUSIVE |
| 09 | 新規配布先で固定base取得・3image構築、setup preview/apply・全件gen2採択・planからrun/report | 容量上界未実証のためCLIは開始前停止。公開image配信、両OS各2回、LLMを含む5command/30分の実証 | INCONCLUSIVE |
| 10 | Docker/WSL2・時計・identity・authority診断、全writing DBの256 MiB上限、既定writer直下256 MiB上限、実ENOSPC/SQLITE_FULLの既存内容保持・復旧、通常runの容量障害記録 | 保存先別総量・Docker backing容量、実setup/runの全故障接続。開始UNKNOWNは継続 | INCONCLUSIVE |
| 11 | report/CI接続、8状態のJSON/Markdown案内、成果物欠落時の終了2、撤回/停止不明理由の保持、18表示試験・実DBの局所確認 | 最終source/imageで全8状態と実操作導線の受入 | INCONCLUSIVE |
| 12 | 保持・bundle・journal・offline移行。実ENOSPC下の通常runでreceipt/未精算予約と第一記録を保持し、別process読取と空き回復後の実取消し・精算を確認 | 全故障/再起動/遅延精算、10回運用と移行復旧、最終imageでの受入 | INCONCLUSIVE |
| 13 | 下記32条件の影響表、局所反例・互換試験、固定source回帰 | 最終sourceの全件回帰と正常/違反/欠損/失効/取消/資源不足の実接続 | INCONCLUSIVE |
| 14 | 共通受入Schema、計画/結果/根拠の参照、管理と候補の権限分離、指摘の採否記録 | 14件の機械可読記録は保存済み。独立検査と採択の全受入 | INCONCLUSIVE |

INCONCLUSIVEは対象の製品受入証拠が不足する状態。実装されていない機能を実測待ちだけとは記載しない。query計測・合成ケース・metadata採択を実案件の評価完了へ変換しない。現在の性能開発残件は[性能分冊](productization-performance-spec.md)、導入の前提は[quickstart](productization-quickstart.md)で追跡する。

## 既存32条件への影響

以下は再検査すべき既存不変条件と対応moduleの一覧であり、表の存在を試験実行の証拠にしない。全件回帰の結果は別のsource付き実行記録へ結ぶ。受入済みの旧MVP証跡は保持する。

| 既存要求 | 維持する条件と拡張の影響 | 主な回帰module |
|---|---|---|
| R01 | Control/依存/必須範囲。固定sample factoryは変更しない | test_contract_preflight |
| R02 | 開始前固定、初回と通常比較の区別。setupのgen2遷移が追加 | test_setup_apply, test_run_contracts |
| R03 | 固定fixture隔離。新入口も既存runnerとreceiptを使用 | test_setup_baseline, test_execution_profiles |
| R04 | 予約/停止/未精算。回収と容量診断に直接影響 | test_ledger_lifecycle, test_setup_baseline |
| R05 | 実証拠必須。終了コードだけの成功を採用しない | test_productization_review, test_run_evidence |
| R06 | 変更前の成立条件。candidate旧/新を全件実行 | test_fixture_calibration, test_setup_apply |
| R07 | Mutationの区別。固定分類の意味は維持 | test_semantic_conditions |
| R08 | 固定adapter。任意runnerを追加しない | test_adapter_evidence_integration |
| R09 | 分子分母/不明。pilot算術と移行で保存を維持 | test_pilot, test_measurement_calibration |
| R10 | 義務分母と欠損。診断完了をcoverageにしない | test_assurance_authority, test_operations |
| R11 | 比較可能条件。gen2 baseline/currentをfresh照合 | test_baselines, test_setup_apply |
| R12 | 最終状態の優先規則。補助operation状態を分離 | test_assurance_authority, test_productization |
| R13 | 鮮度/撤回。保存receiptを現在CIに使わない | test_regression_gate, test_pilot_cli |
| R14 | HOLD時の権限。診断/listにCI成功権限を付けない | test_run_catalog, test_cli_lifecycle |
| R15 | 原因不明を保持。metadata一覧とbundleの説明に影響 | test_run_report, test_operations_bundle |
| R16 | 計画と実修復の分離。pilot計画は対象修復をしない | test_finding_lifecycle, test_pilot |
| R17 | 任意LLM不在時の決定的判定。DGXはreviewだけ | test_finding_conditions, test_pilot |
| R18 | 不変保存/世代。共通journal・setup回収・移行に影響 | test_productization_journal, test_operations_migration |
| R19 | JSON/Markdownの同根拠。CLIの出力時点をcleanup後へ | test_run_report, test_candidate_report, test_cli_lifecycle |
| R20 | 部分/全体CI。全12laneとfresh製品CIの意味を分離 | test_test_matrix, test_ci_history |
| R21 | 非掲載/秘密/raw入力の除外。bundle既定項目を固定 | test_operations_bundle, test_productization_review |
| R22 | 保持/撤回とcurrent依存。既存authorityへ委譲 | test_operations_retention, test_evidence_retention |
| R23 | 自身の障害を表面化。cleanup/未知応答を成功にしない | test_operations, test_benchmark_review |
| R24 | 再計算と実測の区別。欠測nullと厳密整数 | test_benchmark, test_pilot |
| R25 | 独立validator/manager。metadataと製品採択の分離 | test_pilot_authority, test_adoption, test_setup_apply |
| R26 | 全予定/二重通知/取消。同request回収とreceipt binding | test_setup_baseline, test_supervised_run |
| R27 | 対象/世代変更と影響。HMAC cursor・plan/source失効 | test_run_catalog, test_setup_request |
| R28 | 独立集合/校正。実案件未照合を受入へ数えない | test_pilot, test_measurement_calibration |
| R29 | 有限集合と母集団の分離。効果/SLOの未実証を保持 | test_pilot, test_benchmark |
| R30 | 段階/状態隔離。固定LLMの初回/旧/新全件に接続 | test_setup_apply, test_llm_supervised_run |
| R31 | 非信頼データから権限を作らない。strict JSON/固定権限 | test_productization, test_pilot_authority, test_operations_bundle |
| R32 | 修復と基準改訂の分離。移行・metadata採択でFindingを閉じない | test_finding_dispositions, test_finding_revalidation_integration |

## 現在の検証と残件

2026-09-20、採択済み候補checkpointから通常run 800件を固定in-process workerで完了し、再開/status/出力ref、current CI照会、baseline世代2の採択・現世代照合を確認した（[証跡](evidence/productization-continuation-20260920/README.md)）。製品CLI別processと実Docker workerの800件実行ではなく、全counter・cold/warm lifecycle・SLOの証拠でもないため、PAC07/13を含む拡張受入状態は変更しない。

拡張14件は[受入記録v8](evidence/productization-continuation-20260919/acceptance-records-v8.json)のNOT_RUN 4件・INCONCLUSIVE 10件を維持し、PASSは0件。既存固定版MVPの32条件完了とは分ける。[source-v7全回帰](evidence/productization-continuation-20260919/linux-regression-v1.json)は1,268件を12lane・最大3並行で実施し、7laneの844件が成功、4laneがtimeout、1laneが診断のreadonly rootとテスト一時directoryの不一致で失敗した。全回帰成功ではない。後続の修正を旧snapshotの成功件数へ混ぜない。

source-v7固定後に親が検証した差分は、[cold/warm取消しの17件](evidence/productization-continuation-20260919/query-cold-parent-v2.json)、[規模corpusの5件](evidence/productization-continuation-20260919/query-scale-parent-v1.json)、[確定集計の12件](evidence/productization-continuation-20260919/finalize-parent-v1.json)、[Checkpointの15件](evidence/productization-continuation-20260919/checkpoint-copy-parent-v1.json)。各範囲は異なるため合算して単一全回帰とはしない。

保存系は[実ENOSPC/SQLITE_FULLの9項目](evidence/productization-continuation-20260919/capacity-enospc-v1.json)、FailureSinkの[18件中17成功・1skip](evidence/productization-continuation-20260919/failure-sink-parent-v1.json)と[Linux実ENOSPCの16項目](evidence/productization-continuation-20260919/failure-sink-enospc-v1.json)を確認。後者は新moduleを旧固定imageへread-onlyで重ねた差分診断であり、実アプリの予約保持や新image全体の受入ではない。setupは必要な容量証拠が揃うまでUNKNOWNで停止する。

新しいlookupを含む[実imageのauthority22項目](evidence/productization-continuation-20260919/authority-smoke-v7.json)は成功した。固定CIのsetup/採択/通常run・fresh CI・計測・回収の[実Docker90件](evidence/productization-continuation-20260919/docker-continuation-v1.json)は途中失敗と再開を保持した結果であり、単一の90件連続成功ではない。実Docker workerによる最終sourceのLLM全800試行、全資源counter、同条件の性能SLOは未受入である（in-process統合試験の800件とは区別する）。

[通常5試行profile](evidence/productization-continuation-20260919/normal-five-operation-profile-v2.json)から開始検証のcopy反復を特定した。[各版の正規入力による比較](evidence/productization-continuation-20260919/normal-matched-profile-v2.json)は保存内容が一致したが37.355秒→37.744秒で速度改善なし。残るRow混在tupleへ要素別snapshotを追加し、[親の関連38試験](evidence/productization-continuation-20260919/tuple-snapshot-parent-v1.json)は成功した。追加版の全run比較は未完了。[容量障害記録の通常run接続](evidence/productization-continuation-20260919/supervised-capacity-parent-v1.json)は関連102件中99成功・3skip、実SQLiteでreceipt/未精算予約を保持した。[実ENOSPC下の通常run接続](evidence/productization-continuation-20260919/supervised-capacity-enospc-v1.json)は障害記録・別process読取・空き回復後の実取消しと精算まで確認した。全故障接続と規模系列の保存・admissionは継続する。[サイズ診断](evidence/productization-continuation-20260919/query-scale-size-v1.json)で1,600件×2版のplanは既存1 MiB上限を超えると確認したため、corpus生成のみをruntime対応とはしない。実案件の対象・独立データ・運用期間、両OSの導入受入も残る。

過去の固定source・失敗・再試行・レビュー採否は[継続検証一覧](evidence/productization-continuation-20260919/README.md)と[監督レビュー](reviews/productization-continuation-20260919.md)へ集約する。source-v4/v5の当時のdiscovery・局所集約を、現在の全件数や全件成功と読み替えない。

### 分割保存の実装差分

[分割仕様](productization-partition-spec.md)のpure TrialPlan codecを追加し、[親の関連28試験](evidence/productization-continuation-20260919/partition-codec-parent-v1.json)が成功した。既存v1の1 MiB上限は維持し、大きいlogical planは別のv2 index/segmentsで検査する。pure codec/bindingに続き、明示v5のauthority保存・読取・migrationを追加した（後段の80試験参照）。v2 run開始と全consumer/admissionは未接続である。これらはsource-v8固定後の追加分である。

### 照会系列の予算境界

[親の28試験](evidence/productization-continuation-20260919/query-budget-parent-v2.json)で、全seriesの協調的実行予算（既定7,200秒、上限14,400秒）と未実行slot保存を確認した。blocking callの強制中断と実transport timeoutは未接続であり、PAC07のSLO判定はINCONCLUSIVEを維持する。この変更はsource-v8固定後の追加分である。

[分割保存・移行の80試験](evidence/productization-continuation-20260919/partition-authority-parent-v2.json)が成功した。明示v5のAdoptionStore保存/再開/読取まで接続し、既定v4を維持している。[限定実Linux診断](evidence/productization-continuation-20260919/partition-authority-linux-v3.json)で実socket/OS peerと分割保存・再起動読取を確認した。既定製品入口、v2 runと全consumer、runtime admissionへの接続が残る。PAC07はINCONCLUSIVEのままとする。

### source-v8全回帰の結果

[source-v8全回帰](evidence/productization-continuation-20260919/linux-regression-v2.json)は1,334件を12レーン・最大3並行で実行し、9レーンの1,324件が成功した。残る10件を含むLLM supervision・combined・finding revalidationの3レーンは各2,400秒で時間切れとなった。全sourceと12ログのhash照合、所有containerの削除を確認した。全回帰成功とはしない。後続の分割保存・CaseSet・corpus・照会予算の差分はこの固定sourceに含まれない。

### 分割consumerとcopy削減の差分

CaseSetのpure分割・復元とv2 pure集計を実装し、[関連114試験](evidence/productization-continuation-20260919/partition-consumer-parent-v1.json)が成功した。評価入力内部の重複copyも削減し、公開返値の独立性と拒否条件を維持した。新集計は1,600 distinct cases/3,200 entriesの予定分母を扱えるが、証拠DB・authorityと全consumerのv2接続は未完了である。source-v8全回帰へこの成功を混ぜず、PAC07/13はINCONCLUSIVEを維持する。

### 内部reader・corpus分割とruntime整合

[関連140試験](evidence/productization-continuation-20260919/partition-consumer-parent-v2.json)が成功した。committed planを同一transaction内で現在契約・permission・ソース・全segmentsと照合して復元する内部readerを追加し、400/800/1600 corpusの分割・完全復元を実装した。保存JSONの例外分類、DB不変、期限切れ・現在契約欠落時の拒否、byte境界と検査後のcaller変更を確認した。v2の実run開始・証拠DB・全consumer・runtime admissionは継続中である。

開始検証では同じ要求・transaction内の履歴検査だけを共通化し、期限・権限世代・役割失効の条件はAST一致を確認した。[source-v9 seed](evidence/productization-continuation-20260919/normal-seed-v9-v1-failure.json)は旧guardrail lockとの不一致で停止した。[正規のruntime再構築](evidence/productization-continuation-20260919/runtime-refresh-v1.json)後、別の固定sourceで再検証する。新imageはv8とidentityが異なるため、その速度差を同条件SLOへ採用しない。PAC状態は維持する。

### ダイジェスト経路のcopy・再走査削減

大きな束縛済bundleのdigest再照合で、hitごとのJSON復元・全体再packを削減した。[親63試験](evidence/productization-continuation-20260919/bound-digest-parent-v1.json)が成功し、[局所計測](evidence/productization-continuation-20260919/bound-digest-micro-v1.json)は約45.4%短縮した。全runの改善率ではない。authorityを[正式に再構築](evidence/productization-continuation-20260919/runtime-refresh-v2.json)し、guardrail/fixtureは同じidentityを維持した。各固定版の正規seed生成、通常run計測と全回帰は継続する。PAC判定は変更しない。

source-v10の[正規seed生成](evidence/productization-continuation-20260919/normal-seed-v10-v1.json)が完了した。親が初回・比較・採択・通常開始直前の5DBと12checkpointを再検査した。[固定driverによる5操作profile](evidence/productization-continuation-20260919/normal-v10-profile-v2.json)は27.142秒、7attemptと5操作の停止・精算を確認した。準備中に起動したv1はdriver不変条件不成立として残す。修正後source-v11の[正規生成](evidence/productization-continuation-20260919/normal-seed-v11-v1.json)も完了し、request/contract/manifest/plan/tagと保存Attempt全列が一致した。[同条件5操作比較](evidence/productization-continuation-20260919/normal-v10-v11-matched-profile-v1.json)は27.142秒→26.921秒でほぼ同時間。局所digest改善を全runへ外挿しない。実worker/全run/SLO/PACの成功とはしない。

### 分割Evidenceと開始検証の追加差分

[分割Evidenceの親45試験](evidence/productization-continuation-20260919/partition-evidence-parent-v2.json)が成功した。ref-only receipt保存、v1/v2の診断集計・判定同値、同一取引resolver、再開・再送・失効・破損・専用開始入口と既存エラー互換性を確認した。外側のv6認証action・移行・全consumerはこの時点で未接続である。

[開始検証の追加2試験](evidence/productization-continuation-20260919/start-validation-parent-v2.json)が成功した。gen2の実検証を終えた同一取引・DB変更なしの場合だけ末尾の重複履歴検証を再利用し、gen1・書込み・取引外entryは従来検証を保持する。失効・permission世代・役割取消し・保存破損・次要求の再読込も確認した。前段の79件実行には試験実装の失敗が残っており、異なる差分時点の45件/2件を現在の全suite成功へ合算しない。

明示schema-v6の採択済みgen1診断runを実AdoptionStoreへ接続し、[関連88試験](evidence/productization-continuation-20260919/v6-parent-v3.json)が成功した。[変更前の実v5 DB](evidence/productization-continuation-20260919/v6-real-migration-v1.json)でも44表34行を許可metadata以外不変で移行し、旧plan失効、新plan登録、診断開始と再起動を確認した。既定v4/v5、通常regression/実worker/admission/CI成功への接続は残る。[新v6の実OS peer認証診断](evidence/productization-continuation-20260919/v6-linux-smoke-v1.json)も79.093秒で成功した。4 UIDの権限分離、23操作、15件plan、合成Attemptの再送と再起動後の保存を確認し、所有containerを回収した。固定source-v12の[全1,498件回帰](evidence/productization-continuation-20260919/linux-regression-v4.json)は終了し、9レーン1,488件が成功した。combined・LLM監督・Finding再検証の3レーン10件は各2,400秒でTIMEOUTとなり未確認である。ソース不変と所有container回収を確認したが、全成功とはしない。[同版の5操作計測](evidence/productization-continuation-20260919/normal-v12-profile-v1.json)は28.496秒で、v11の26.921秒に対する改善は確認できなかった。保存Attemptは一致した。後続の[資源接続46条件](evidence/productization-continuation-20260919/v6-resource-parent-v1.json)と[期限指定63試験](evidence/productization-continuation-20260919/deadline-parent-v2.json)は別差分の検証として扱う。さらに[資源cache・取消し接続の71試験](evidence/productization-continuation-20260919/resource-cancel-parent-v1.json)で送信済み操作の未精算保持と期限切れowner回収を確認した。[保存状態readerの60試験](evidence/productization-continuation-20260919/v6-resource-read-parent-v1.json)では失効後の回収用照会と新規開始の拒否を分けた。PAC状態は維持する。

[source-v13の配布image診断22項目](evidence/productization-continuation-20260919/authority-smoke-v13.json)と[v6実認証・資源31操作](evidence/productization-continuation-20260919/v6-linux-smoke-v3.json)が成功した。source不変と所有container回収を確認した。v6の停止観測は合成のため、固定fixtureの実worker consumer接続とPAC受入は継続する。

### 明示v6の一件worker接続

[親の関連86試験](evidence/productization-continuation-20260919/partitioned-diagnostic-parent-v2.json)が成功。RUNNING/STOPPEDからの回収、closeの応答欠落とlease更新、CLIの厳密入力・結果検査を確認した。[固定source-v14の実worker診断](evidence/productization-continuation-20260919/v6-worker-runtime-v2.json)では、配布authority imageのv6 modeと既存固定fixture imageを用い、一件の実行・停止・精算・v2 Attempt・budget closeが成立した。再送は同じ結果を返し、実runner呼出しは合計1回、追加起動は0件。全15件planのうち一件だけで、runはOPEN・未finalize、全適格性flagはfalseを維持する。大規模LLM producer、通常gen2/baseline/admission、全consumer・SLO・PACは継続中である。


## 分割corpus保存のv7接続

[契約前provisioning仕様](productization-corpus-provisioning-spec.md)を実装した。400/800/1600件をcurrent policy・permission・sourceへ固定し、全segmentと固定生成値を照合してcommitする。確定segmentを専用表へ保持し、commit ACKが失われても同じupload IDでfreshに再照合できる。

[親検証](evidence/productization-continuation-20260919/v7-corpus-parent-v1.json)は126件中125件成功後、誤った期待エラーコードを直したmoduleの5件が成功。[実コンテナ](evidence/productization-continuation-20260919/v7-corpus-runtime-v1.json)では3規模の保存・同一commit再送・再起動後の全復元、candidate拒否とcleanupを確認した。当時の実コンテナ検証は新規v7 DBだけを対象とする。続く[明示v6→v7移行の47試験](evidence/productization-continuation-20260919/v7-migration-parent-v1.json)と[固定旧版DBのCLI移行](evidence/productization-continuation-20260919/v7-real-migration-v1.json)で、45表46行の保持・再open・旧plan拒否、現在版のv5→v6→v7連続移行を確認した。LLM materializer/admission、baseline/gen2通常runと全PAC受入は残る。


## reportの世代表示・取消し表示

[追加検証](evidence/productization-continuation-20260919/report-generations-parent-v1.json)で保存契約/baselineの世代表示を追加し、取消しrunの除外審査未取得を表示失敗へ変えていた不具合を修正した。実AdoptionStoreでは正常・違反・証拠不足・取消し・停止不明・未精算・撤回の7状態を確認した。関連34試験の33成功・1skipと、世代値照合追加後の限定1成功を区別する。続く[予算WARNING接続の検証](evidence/productization-continuation-20260919/budget-warning-parent-v1.json)で80%警告の欠落を修正した。閉鎖時の根拠を保存し、実AdoptionStoreの30件からWARNING・fresh CI終了0・JSON/Markdown表示・再openを確認。遅延する資源矛盾は現在CIだけを拒否し、過去Decisionを変えない。初回75成功とテスト補正後1成功で関連76件の未解決は0。更新imageでの8状態、全PAC受入は未完了。

## WARNINGの配布image検証

[固定source-v16の実Docker検証](evidence/productization-continuation-20260919/budget-warning-runtime-v1.json)で、初回15・旧15・新30・通常30の90件と217確認項目が全成功した。policy上限37に対する30件の使用でWARNINGが生成され、保存根拠・CI終了0・JSON/Markdown・再起動・根拠撤回後の拒否・回収を確認。sourceは実行前後不変である。PAC11のWARNING接続に関する実image確認は完了し、同一最終版の全8状態と全PAC受入は残る。後続のv7移行差分をこの実行へ混ぜない。

## TIMEOUTレーンの完走確認

[固定source-v15の残り3レーン](evidence/productization-continuation-20260919/remaining-regression-v2.json)はWindows・最大2並列で10件すべて成功した。combined4件、Finding再検証5件、LLM監督1件を確認し、全source/cloneと6ログhashも照合した。旧source-v12/LinuxのTIMEOUT記録は保持する。版とOSが異なるため、旧1,488成功との合算や現在版の全回帰・性能SLO達成とはしない。
