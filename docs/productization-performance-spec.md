---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-20
next_review_due: 2026-10-20
---

# プロダクト化性能・CI詳細仕様（GAH-PR05〜08/13）

本書は [productization-requirements.md](productization-requirements.md) のGAH-PR05〜08/13を、同じ入力・判定・権限・資源上限で測るための計測とCIの仕様案である。数値は提案SLOであり、達成済みの性能ではない。今回の文書検収、技術受入、性能達成、製品CIでの利用を別の状態として記録する。

計画artifactの外枠（`schema_version`、`kind`、`id`、`requirement_ids`、`source_ref`、`requirements_ref`、`created_at`、`expires_at`、`payload`）と補助操作resultは [共通productization仕様](productization-spec.md#31-計画artifactの共通外枠) に従う。本書のmanifest field表はその `payload` 内だけを定義し、外枠を重複定義しない。大きな観測列は共通仕様のsegment参照で保存する。

MVPの32条件、`release_gate=go`、既存の受入証拠、既存CLIの出力と終了値は変更しない。拡張のGAH-PAC01〜14は初期状態をすべて `NOT_RUN` とし、既存の合成400ケースや907件の分割証拠を拡張受入へ流用しない。

## 1. 対象と測定単位

| 要件 | 測る対象 | 受入の主判定 |
|---|---|---|
| PR05 | 単体、通常run、初回準備、CIを同一条件で再現できるか | PAC05 |
| PR06 | 固定400ケース・2variantの通常800試行/1200段階 | PAC06 |
| PR07 | 候補・現在CI・レポートの点照会と一覧照会 | PAC07 |
| PR08 | 早期結果、全件gate、runner総量、計画整合 | PAC08 |
| PR13 | 最適化前後の判定、権限、予算、Evidenceの意味 | PAC13 |

既存の照会境界は、候補が `contract_candidate_read` と `candidate_outputs/candidate_artifact`、通常runが `run_outputs/run_artifact`、現在CIが毎回freshな `ci_check`、人間向けレポートがこれらの成果物取得後にもう一度 `ci_check` を行う経路である。計測対象はこの呼出しを包む。`ci_check` の応答や保存済み表示をcacheしてCI利用許可の代りにはしない。

性能を測る観測と機能を受け入れる観測を同じ結果へ畳み込まない。機能不成立は性能集計から除外せず故障分類へ残し、性能SLOだけを満たしてもPAC13の機能不合格をPASSへ変更しない。

## 2. 測定manifestと結果

manifestは計画artifactの `payload` として開始前に基準管理AIが作成し、対象・入力・期待結果・環境・反復・資源・計測区間を固定する。manifestと結果は未知キーを拒否し、整数は厳密な整数（`bool`を含めない）とする。時間は `ns`、容量は `bytes`、回数と作業量は `count` を単位にし、未取得値を0にしない。

| field | 型・単位 | 規則 |
|---|---|---|
| `measurement_surface` | enum | `unit` / `normal_run` / `initial_preparation` / `quickstart_total` / `query` / `ci` / `fault_reproduction` |
| `baseline_source_ref`、`candidate_source_ref` | ref object | 共通参照 `{kind,id,digest}`。baselineのcommit idは `c6a575ec0f00ec461a4757aeab62355645654433` に固定 |
| `input_ref`、`expected_ref`、`resource_profile_ref` | ref object | 許可済み入力、独立期待結果、資源profileの完全参照 |
| `contract_ref`、`baseline_ref`、`target_refs` | ref object / ref配列 | 測定対象の契約・基準・対象。内容のdigestは既存のcanonical bytesと `content_ref` を使う |
| `os`、`kernel`、`cpu_model`、`python_version`、`docker_version` | string | 実値を保存し、未確認を成功扱いしない |
| `vcpus_count`、`parallelism_count`、`concurrency_count` | int count | 専有4 vCPU、full並列4、PR並列2等の実値を記録 |
| `ram_bytes`、`storage_free_bytes` | int bytes | 開始前とピークの両方を別fieldで記録 |
| `image_ref`、`storage_profile_ref` | ref object | imageとstorage条件の完全参照 |
| `clock_monotonic_ok`、`clock_wall_ok` | bool | 時計検査を合格/不成立で明示。時計不成立は無効観測 |
| `case_count`、`variant_count`、`planned_trial_count`、`planned_stage_count` | int count | 通常runでは400、2、800、1200を固定して記録 |
| `planned_test_count`、`history_run_count`、`page_size_count` | int count | CI計画、履歴規模、一覧page上限。pageは最大100 |
| `work_amount` | object | 4軸の件数、ID集合ref、各軸の係数、単位定義を持つ。trial+stageの合計だけで比較しない |
| `work_set_refs` | object | `cases`、`variants`、`trials`、`stages`の順序付きID集合完全ref。件数と全体digestを照合 |
| `cold_iterations_count`、`warm_iterations_count`、`warmup_iterations_count` | int count | 通常run/全件CIは各3回、cold 1・warm 2。queryは本文§5を適用 |
| `broker_rss_limit_bytes`、`cache_hard_limit_bytes` | int bytes | 134217728（128 MiB）を維持し、cache提案上限は16777216（16 MiB） |
| `cache_entry_cap_count`、`cache_entry_max_bytes` | int count / int bytes | in-process cacheは最大32 entry、1 entry本文は既存1,048,576 bytes以下。総量hard limitを同時に適用 |
| `source_sha`、`plan_digest`、`lane_set` | string / array | CIで全jobが同一SHA・同一計画digestを再照合する |
| `intervals` | object | 下表の開始/終了イベント名を固定する。時刻値は各結果へ保存 |
| `observation_count`、`observation_segments`、`observation_digest` | int / ref配列 / string | 観測数、順序付きsegment完全ref、全体digest。segmentは既存wireの1,048,576 bytes・深度16以内 |

manifestのref、digest、canonicalizationは既存契約を参照し、別の正規化規則を導入しない。計測結果もmanifestの完全refを持つ。以下はpayloadの記法例であり、実在しないdigestを受入証拠として扱わない。外枠のsource_refは比較対象のsource snapshot、payload内の2つのsource refは基準/候補の両方を指す。segmentの順序、件数、全体digestを照合し、上限超過時に末尾を切り捨てない。

```json
{"measurement_surface":"query","page_size_count":100,"history_run_count":10,
 "cache_hard_limit_bytes":16777216,"broker_rss_limit_bytes":134217728,
 "observation_count":300,"observation_segments":[{"kind":"measurement_segment","id":"<segment-id>","digest":"<existing-digest>"}]}
```

各観測は次のfieldを持つ。`wall_ns`は同一host内の単調時計の区間、`cpu_ns`は対象プロセスと全子処理の合計である。`rss_group_peak_bytes`はサンプリング時刻ごとのプロセス群RSS合計の最大値 `max_t(sum_p rss(p,t))` とし、各processの別時点peakを単純合算しない。brokerも同じ定義で、対象process一覧とsampling間隔を記録する。raw monotonic値を機器間で比較せず、比較するのは同一hostで得た差分である。対象の応答待ち `target_wait_ns` とharness処理 `harness_wall_ns`は重なり得る独立区間として直接計測し、wallから単純減算しない。copy/serialize/hash/DB走査/authority呼出しの各counter、read/write量、保存容量、retry回数は未取得ならnullとし、0に補完しない。全子processのCPU/RSSを取得できない観測は値をnullで保存し、valid_for_slo=falseとしてSLOへ採用しない。

```text
observation はsegmentのpayload内の要素であり、{iteration:int, warmness:cold|warm, wall_ns:int|null, cpu_ns:int|null,
  target_wait_ns:int|null, harness_wall_ns:int|null, rss_group_peak_bytes:int|null,
  io_read_bytes:int|null, io_write_bytes:int|null, copy_count:int|null,
  serialize_bytes:int|null, hash_count:int|null, db_scan_count:int|null,
  authority_call_count:int|null, stored_bytes:int|null, retry_count:int,
  failure_class:enum, operation_status:enum, exit_code:int, valid_for_slo:bool}
```

`operation_status` は共通補助操作の `COMPLETED` / `REJECTED` / `INCOMPLETE` / `CANCELLED`、終了値は0/1/2/3に固定する。性能結果の `PASS` / `FAIL` / `INCONCLUSIVE` はPAC状態であり、操作終了値や既存CI判定の代用ではない。

## 3. 計測区間、計算、故障

区間の境界は次のとおりである。

| 区間 | 開始 | 終了 | 含めるもの |
|---|---|---|---|
| `unit` | 入力検証完了 | 出力のserializeとdigest検証完了 | 対象の処理とharness。process起動は別の準備区間 |
| `initial_preparation` | clean workspaceで準備要求を受けた時点 | image/runtime/broker readyを確認した時点 | 初回準備、採択・初回比較の時間は別runとして記録 |
| `normal_run` | `run_prepare` の開始要求 | fresh `ci_check` 完了、必要なら停止・回収・精算確認完了 | 800試行/1200段階、入力配送、保存、Finding/Plan、現在照会。停止確認時刻と精算時刻は別field |
| `quickstart_total` | PR09の5コマンド以内の開始 | sample評価・現在CI・表示完了 | image取得を含む初回利用の総時間。`initial_preparation`、PR06通常runと混ぜない |
| `query` | 照会入力のdecode完了 | 応答の本文・ref・digest検証完了 | 候補/現在CI/レポート、page単位の一覧と点照会 |
| `ci` | pushからのevent時刻 | 全件gateの確定時刻 | `queue_ns`、runner区間、gate区間、push-to-gateを分離 |

基本式は `wall_ns = end_ns - start_ns`、`median = sort(values)[floor(n/2)]`（奇数は中央、偶数は中央2値の算術平均を整数分子/分母で保持）、`max = max(values)`、`p95 = sort(values)[ceil(0.95*n)-1]` とする。`candidate / baseline` はbaselineが0より大きいときだけ計算し、0を分母にした倍率や改善率を出さない。改善率は `100 * (1 - candidate / baseline)` とする。CPUはprocess/子処理の和、runner時間はjob時間の和、critical pathは並列jobの開始からgateまでの最長経路として別々に保存する。queue待ちはcritical pathとrunner時間から分離し、push-to-gateには含める。

作業量は `cases`、`variants`、`trials`、`stages` の4軸で保存する。各軸に `planned_count`、`observed_count`、`coefficient`（既存の整数分子/分母形式）、順序付きID集合refを持たせ、4係数がすべて1/1で、4つのID集合が重複・欠落なく完全一致した場合だけ同条件SLOへ入れる。通常runはcases=400、variants=2、trials=800、stages=1200を固定し、総単位 `trials+stages` は補助表示にとどめ、軸の入替えを隠す比較に使わない。CIは各版の全test ID集合と対応するlane割当を一致させる。基準/候補間でlane割当が変わること自体は許可し、共通907件のID集合を照合する。追加testは別に識別したうえで全件の絶対目標へ含める。

故障は次の分類で全件保持する。

| `failure_class` | 扱い |
|---|---|
| `NONE` | 正常観測。SLO計算に利用 |
| `INPUT_REJECTED` / `AUTHORITY_REJECTED` | 入力・権限不成立。意図した対照試験以外は性能達成に数えない |
| `STALE_OR_INVALIDATED` / `CI_PLAN_MISMATCH` | 世代、cache、SHA、planの不一致。成功へ変換しない |
| `RESOURCE_LIMIT` / `TIMEOUT` / `IO_OR_STORAGE` / `DB_INTEGRITY` | 実行故障。観測を捨てず、機能または性能FAIL/INCONCLUSIVEへ反映 |
| `TARGET_ERROR` / `HARNESS_ERROR` / `SERIALIZATION_ERROR` | 対象とharnessを分離して原因を記録 |
| `CANCELLED_STOP_CONFIRMED` / `CANCELLED_STOP_UNCONFIRMED` | 停止確認済みの取消し、停止不明の取消しを区別 |
| `MEASUREMENT_ERROR` / `UNSUPPORTED_ENVIRONMENT` | 計測不能。SLOを推測せずINCONCLUSIVE |

開始後の失敗、retry、cancel、未精算、計測負荷を都合よく除外しない。故障再現は `measurement_surface=fault_reproduction` として別系列にするが、原観測と原因は同じ結果へ残す。

## 4. 反復と提案SLO

基準/候補を同じmanifestで各3回実行し、各系列はcold 1回、warm 2回とする。coldは新しいclient/broker process、再オープンした固定DB snapshot、空のin-process cacheとし、DB本文は削除しない。OS file cacheは権限付きflushを行わず `retained` と記録し、imageは既存digestをpreloadedに固定する。image pullやbroker初回準備を含む環境coldは `initial_preparation` で別計測する。warmは同じprocess/DB接続/image/OS状態を再利用し、PR07だけ各surface/sizeで5回のwarmupを先に行う。warmupは100回のp95から除くがwall/CPU/runner予算と観測件数には含める。通常run/CIに合成warmupは置かず `warmup_iterations_count=0` とする。最初のpilot 1回で計時・件数・期待結果・資源制限を確認し、時計不成立や計測故障は無効理由を残して再計画する。フル性能試験は候補版ごとにこの6回と故障再現1回を原則上限とし、原因や修正のない追加反復をしない。

| 要件 | SLO（保存値はns） | 比較条件 |
|---|---|---|
| PR05 | manifestに全環境、入力、権限、資源、計測区間、負荷、cold/warm、失敗、計測負荷を記録 | 基準commitと不変candidate snapshot、同じ期待結果・上限 |
| PR06 | 開始要求からfresh CI/回収まで中央値≤1800秒、最大≤2700秒、中央値≤基準の60% | CPU合計を増やさず、coldも最大内。入力・結果・Finding分類・未精算の意味を一致 |
| PR07 | warm 100照会のp95≤1秒、最大≤2秒。別process初回3回の最大≤3秒 | 候補/現在CI/レポートを別計測。400/800/1600件で倍増系列≤2.5倍 |
| PR08 | 早期結果中央値≤3分、全件gate中央値≤30分・最大≤45分、runner総量中央値≤120分かつ基準の60% | queue待ちを除く値とpush-to-gateを併記。追加試験も絶対目標へ含める |

PAC06の通常runは対象応答時間とharness時間を分離し、初回採択・比較は別区間で報告する。PAC07は履歴run数1/10/100、page上限100で照会し、点照会が履歴本文を全コピーしないことをcounterで確認する。PAC08の3回比較は基準/候補各3回の規則を適用する。これらは母集団のp95推定ではなく、固定反復で得た受入値である。

## 5. cache、page、cursor

read-side cacheを導入する場合、keyは認証済み `principal_id`、対象scopeの完全ref（`scope_ref`）、`surface`、既存のmanifest/contract/baseline/target/evidence/output ref、権限generation、evidence generation、query shape、page size、opaque cursor、実装digestから構成する。principalやscopeが違えば同じ本文でも別entryとする。keyのbyte化は既存の `canonical_bytes` を使い、新しいcanonicalizationや別のdigest規則を作らない。run idだけをkeyにして現在性を省略しない。

次のいずれかで該当keyを同期的に無効化する。

- contract/baseline generationまたはdigest、target/source/evaluator/adapterのrefが変わった。
- permission generation、actorのrevoke、evidenceのgeneration/state、retention、削除/復元が変わった。
- run state、cancel、stop観測、resource settlement、DB migration/restore、実装digestが変わった。
- query shape、sort、page size、cursor scopeが変わった、またはcursor snapshotが期限切れになった。

cacheの提案hard limitは16 MiB（16,777,216 bytes）で、payload・index・管理領域を含む。in-process entryは最大32件、各本文は既存wireの1,048,576 bytes以下で、32件上限と総量上限の小さい方を適用する。brokerの既存128 MiB（134,217,728 bytes）上限とRSSを別に測り、`cache_resident_bytes + その他のbroker resident` が128 MiBを超えないことを確認する。上限到達時はLRU等で破棄して再計算し、再計算不能なら明示的にINCOMPLETEを返す。cache hitでもcurrent authority、失効、契約、Evidence、資源のfresh照合を省略しない。

一覧は `page_size_count` 1〜100、初回cursor=null、次pageはopaque cursorとする。sortは全surfaceで `created_at ASC, id ASC` に固定し、cursorはsort key、認証principal、scope、snapshot refに結び付く。cursorの最大byte数は4096、発行から15分（900,000,000,000 ns）で期限切れとする。クライアントがoffsetや内部IDを解釈してはならない。snapshotが変わったcursor、重複/欠落を検出したcursorは `STALE_OR_INVALIDATED` とし、古いpageを成功へ連結しない。点照会は要求された完全refだけを返し、`run_outputs.read` の全成果物再構築を必要とする既存経路はその処理量として記録する。一覧page/cursor APIは初版実装済みだが性能SLO未実測であり、query cacheは未実装である。

## 6. CI分割、全件gate、取消し

計画生成は既存の `python -m tools.test_matrix --plan` を使う。現在の `--lane` 引数と6専用lane（`llm-supervision`、`llm-revision`、`combined`、`finding-revalidation`、`following-contract`、`mutation-review`）と残り最大6のgeneral lane、最大12並列を維持する。計画の `include`、`total_tests`、`plan_digest` は既存出力をそのまま使い、別のhash入力やcanonicalizationを追加しない。現在のplan_digestのhash対象は既存 `describe` が作るlaneからordered test ID listへのinventoryであり、timing、timestamp、log、runner IDはhash対象に含めない。plan artifactにはそのinventoryの全ID、計画作成時刻、timing履歴ref、履歴の鮮度とfallback件数を別fieldで保存する。

履歴時間を使う拡張planは、同じrunner profile・Python/image・計測方式版の直近3回の成功観測をmoduleごとに取得し中央値nsを推定時間とする。moduleのsetup/teardownと全test実行を含む区間を直接測り、fixture時間が抜けるtest時間の単純合計を代用しない。workflow revisionは記録し、観測の計測方式版が一致するか独立に検査する。履歴artifactは既存ref/digest、観測時刻、source/profileを照合し、digest不一致、欠落、別profileは改竄/条件不一致として使わない。履歴snapshotに固定as_ofがある場合だけ、その時点との比較で期限を判定する。未観測moduleは、検証済みmodule履歴の「module中央値ns/test件数」の中央値を切り上げた値と1,000,000,000 nsの大きい方を、当該moduleのtest件数へ掛けて推定する。履歴ゼロなら1test当り1,000,000,000 nsに固定する。全てnsとして並べ、件数と秒を混ぜない。fallback件数と予測不確かさを記録するが、必要な実測が揃ったSLO判定まで不明にしない。moduleは分割せず、専用laneを先に固定し、general laneは推定時間降順・module名昇順で、現在負荷が最小のlane（同値はlane名順）へ割り当てる。未観測時も全件実行計画を作り、予測時間を実測した改善率として主張しない。履歴の順序・ref・fallback方針は開始前に固定し、実行後の都合のよい除外を許さない。

時間分割の `--timing-history <file>` と `--timing-history-digest <digest>` は `tools.test_matrix` に初版実装済みであり、省略時は現行の件数分割を維持する。digestはcanonical JSONだけを受け付け、履歴を指定した場合は4環境キーを必須とする。plan jobは採択済み履歴snapshotを固定して配布し、全laneが同じfile/digestとalgorithm版を使う。jobごとに最新履歴を取り直さない。履歴は信頼するCI実行主体とrun/SHA/profileの参照に結び、自己申告のJSONやdigest一致だけでは採用しない。検証不能な履歴は不使用理由を記録して固定fallbackへ進み、全件割当・gateの確認を省略しない。

plan lock artifactはsource SHA、algorithm版、timing_history_ref（なければnull）、fallback設定、ordered test inventory、lane割当、既存plan_digestを閉じたpayloadへ保持する。各jobはこのlockの完全refも照合してから同じ割当を再生成する。既存plan_digestが同じでも、別sourceや別lockの結果を混ぜない。

全jobは計画作成時の `source_sha` と `plan_digest` を入力し、`python -m tools.test_matrix --lane <lane> --expected-plan-digest <digest>` で再照合する。job結果には `source_sha`、`plan_digest`、`lane`、`planned_tests_count`、`tests_run_count`、`conclusion`、`queue_ns`、`runner_ns` を保存する。同一SHA・同一digest、重複0、欠落0、割当件数=実行件数を満たさない計画は拒否する。

fail-fastは無効とし、独立jobを最後まで実行する。全件gateは必須jobの全成功、全割当実行、同一SHA、同一plan、期限内完了を要求する。fail、timeout、cancel、skip、未起動、別SHA、期限切れ、収集エラーを一つでも含む場合は成功にしない。早期subsetは情報提供に限り、CI成功権限や `ci_eligible` を発行しない。既存の12ジョブ構成に対するGitHub上の短縮時間は未確認である。

取消しはGitHub側のworkflow/job結論と製品内の停止・未精算を別イベントとして記録する。`ci_cancel_concluded_at`、`stop_confirmed_at`、`settlement_at`、`budget_closure` を別fieldで保存する。停止要求後、所有operationについて停止観測（`stopped=true`）を取得した時点をstop-confirmedとし、精算は後続のsettlement時点として扱う。GitHubのcancelledだけでは停止確認済みとしない。停止未確認は `CANCELLED_STOP_UNCONFIRMED` / `INCOMPLETE` / exit 2、停止確認済みの取消しは `CANCELLED_STOP_CONFIRMED` / `CANCELLED` / exit 3とする。ただしbudget openのまま性能SLOや全件gateをPASSにせず、settlement完了までSLO判定はINCONCLUSIVEのまま保持する。どちらも全件PASSへしない。既存 `gah_ci`、`gah_report`、`gah_run` の終了意味論は変更しない。

## 7. PAC受入ケース（初期状態はすべてNOT_RUN）

| case | 確認内容 | 成功条件 |
|---|---|---|
| PAC05-A | 基準/候補各3回、環境・入力・時計・負荷・全counterをmanifest/resultへ保存 | 欠損を0にせず、同じrefと期待結果を再照合 |
| PAC05-B | timeout、IO、時計不成立、計測counter欠落を注入した対照 | failure分類とINCONCLUSIVE/FAILを保持し、成功回だけを選ばない |
| PAC06-A | 800試行/1200段階、cold 1/warm 2、基準と候補 | 中央値・最大・60%・CPU合計・意味一致を同時に満たす |
| PAC06-B | 対象待ちを遅延させ、harness処理と分離 | target_waitとharnessを別掲し、SLO判定を改ざんしない |
| PAC06-C | 通常runの取消し、stop未確認、遅延精算、再起動 | 停止/未精算/receiptを分離し、既存終了値を保持 |
| PAC07-A | 400/800/1600件、履歴1/10/100、100 warm + 3 cold process | p95/max、2.5倍、page上限、point照会のcounterを満たす |
| PAC07-B | revoke、世代更新、対象変更、削除、移行、再起動をcache前後で実行 | 旧cacheによる成功0件。再計算不能はINCOMPLETE |
| PAC07-C | cursor途中の追加/削除/順序変化 | stale cursorを拒否し、重複/欠落pageを返さない |
| PAC07-D | cacheを16 MiB hard limitまで満たす | broker 128 MiB内、上限到達時の明示失敗、証拠欠落0件 |
| PAC08-A | 計画作成、6専用+最大6 general、各jobの再照合 | 全jobの同一SHA/plan digest、重複/欠落0件 |
| PAC08-B | fail/timeout/cancel/skip/未起動/期限切れjobの各対照 | 全件gateが成功権限を出さない |
| PAC08-C | 早期subsetだけを成功させる対照 | early成功を全件gateや `ci_eligible` に昇格しない |
| PAC08-D | workflow cancelと製品operation cancelを別々に発生 | GitHub結論、stop確認、budget closureを別掲 |
| PAC13-A | 正常、違反、欠損、失効、取消し、資源不足を最適化前後で照合 | 判定・理由・権限・予算・Evidenceが一致し、差異はFAIL |
| PAC13-B | 旧Evidence、receipt、MVP証拠の再読込 | 古いbyteと受入記録を変更しない |
| PAC13-C | 最適化が影響対象外のControlへ波及する対照 | 影響表を拡大し、未評価をPASSへしない |

## 8. 実装境界、状態、残る論点

補助CLIは `python -m tools.gah_benchmark plan`、`measure`、`compare` の名前空間に限定し、manifest検査、固定recipeの指定区間観測、canonical bytes/refを検証した算術比較を初版実装済みである。新JSON Schema、既存CLI wrapper、Docker試験は本書の作業対象に含めない。

I/Oは `plan --input <plan候補> --output <新規artifact>`、`measure --plan <採択済みplan> --iteration-id <採取ラベル> --iteration <1..N> --warmness <cold|warm> --runtime <既存runtime> --request <固定request> --output <新規観測>`、`compare --plan <plan> --observations <segment一覧> --output <新規結果>` に固定する。measureのrecipeは既存AuthorityRuntimeをoperator UIDで照会するcandidate/current_ci/reportのqueryと、既存Supervisor経路でrun・fresh CI・cleanupまでをwall計測するwhole_runに限定し、任意callable、import、shell、外部runnerを受け付けない。`--series baseline|candidate`でplan内source refを選ぶ。iteration_idは採取ごとの任意ラベルで事前列挙せず、計画が固定するのはwarmnessごとの1..Nの連続indexである。`--request-id` は任意指定、変更操作の既定はplan/iterationのIDとし、入力digestを含めて共通の冪等性検査を行う。計測plan、iteration、選択したsource refをartifactへ結び付ける。実runtimeのsource、環境、採択状態の独立照合が未接続なら観測をSLO根拠に採用せず、`valid_for_slo=false`またはcompareの証拠不足として記録する。actorは固定operator入口から適用する。出力先既存は別内容で上書きせず、同一requestの保存結果だけを再参照する。観測保存後はrequest_digest、観測artifactの完全ref、cleanup確認、operation resultを持つ完了receiptを保存し、journalの終了応答が失われた再配送ではreceiptと観測を検証して終了処理だけを回収する。receiptまたは観測が揃わない開始意図は再計測せず、OPERATION_UNKNOWN/INCOMPLETEとして保持する。

whole_run recipeは、固定run requestと固定ci_check requestのrefをplanへ結び付け、各run_idを一度だけfresh実行する。同じrun_idでiterationやrequest_idだけを変えた再測定は、authorityのrun_statusまたは既存Checkpointを検出して拒否する。複数回のwhole_run測定には、各回の事前に固定した新規run requestと対応する新規planを用いる。

補助CLIの0は補助操作完了だけを意味し、製品CI成功を意味しない。性能閾値不成立は `REJECTED` / exit 1、環境・計測不能は `INCOMPLETE` / exit 2、停止確認済み取消しは `CANCELLED` / exit 3とし、常に `ci_eligible=false` とする。未知key、bool整数、欠損ref、別digest、異なるSHAは入力拒否またはINCOMPLETEとし、推測で埋めない。

この文書の重要な設計判断は、(1) 同一仕事量を係数とraw countで同時に残す、(2) cacheは認証・現在性を肩代りしない、(3) CIの計画digestとcommit SHAを全jobで再照合する、(4) workflow取消しと製品停止確認を分離する、(5) 技術受入・性能達成・製品CI利用・実案件有用性を別判定にする、の5点である。補助resultは共通の `extension_operation_result` 外枠を使い、観測のsegment参照だけを `result_ref` から辿る。

`examples/productization/benchmark-*.json` は保存した参照文書に結び付く決定的な合成計算例であり、実測値や製品SLOの受入証拠ではない。

現実装との差はquery cache、GitHub上の性能SLO計測、本番条件の性能SLO実測である。一覧page/cursor、性能manifest/result、`tools.gah_benchmark`の固定recipe実操作は初版実装済みだが、SLO達成を示す実測証拠はまだない。M0で固定するのは、各counter取得手段と計測誤差、CI queue時刻の一次記録、専有runnerの実値である。cacheの置換方式はLRU、clock/digest/主体による失効は本書の条件を優先する。レビューでは16 MiB cache上限がbrokerの他用途と両立するか、work unitの定義がUC-LLMにも適用できるか、cancel後の予算精算待ちをどのCI時間へ帰属するかを確認する。



## 9. 開発残件表（2026-09-15時点）

状態の意味を次のように固定する。「実装済み」は入力検査・保存・計算または接続入口がコードとして存在する状態、「未実装」は必要な実行経路・計数・照合・最適化がまだない状態、「実測待ち」は実行経路があり、製品条件での測定と証拠採取だけが残る状態を指す。固定whole runの初版接続があっても、全子processのCPU/RSS、IO・copy等のcounter、実最適化が未実装なら実測待ちへ分類しない。合成artifactと専用テストの成功は製品SLOの実測証拠に数えない。

| 要件 | 対象 | 状態 | 現時点の根拠・残件 |
|---|---|---|---|
| GAH-PR05 | 閉じたmanifest、plan外枠、canonical ref/digest、未知key・型・boolの拒否 | 実装済み | benchmark moduleのvalidatorとplan保存入口に実装済み。実在するsource・環境・採択状態の独立照合は別項目で未接続。 |
| GAH-PR05 | candidate、current CI、reportの固定query入口、厳密応答・終了値、観測保存、journal/完了receipt再配送 | 実装済み | 3 recipeのquery接続、nullable観測、同一入力digest、観測refとcleanupを含むreceipt回収を初版実装済み。常にci_eligible=false。 |
| GAH-PR05 | source、実host環境、採択receipt、権限の独立検証とSLO採択 | 未実装 | 計測planの値を実runtimeの検証済み事実へ昇格する接続がなく、compareのCLI結果は証拠不足で止まる。 |
| GAH-PR05 | cold/warmのprocess・DB・cache状態、setup/teardownを含む同条件の実行制御 | 未実装 | 固定queryとwhole-runの計測入口はあるが、製品全体のcold/warm制御や初回準備区間の実装ではない。 |
| GAH-PR06 | median、nearest-rank p95、max、厳密Fraction比率、欠測・失敗保持 | 実装済み | 保存された観測列に対する決定的な算術と欠測除外を実装済み。合成入力の計算結果だけではSLO証拠にならない。 |
| GAH-PR06 | 通常run/CIの開始要求からfresh CI・回収までのwhole run計測 | 実装済み | 固定run/ci requestの内容refを照合し、既存AuthorityRuntime・Supervisor/LlmSupervisor・固定Docker runner経路でfresh runを実行してfresh CIとcleanupまでのwallを初版計測する。既存run_status/Checkpointは再利用せず拒否する。全子resource計数と製品SLO受入は別項目で未実装。 |
| GAH-PR06 | 全子process CPU/RSS、IO read/write、copy、serialize/hash、DB走査、authority呼出しの実counter | 一部実装 | 固定scope snapshotの集約、authority cgroup/host CPU providerとwhole-run副証跡を追加。短命worker・全必要scope・真のgroup peak・copy/serialize/hash/DB等の全counter接続は残る。 |
| GAH-PR06 | 純粋pack/bind生成の最適化と製品SLO受入 | 一部実装 | pack生成とUC-CI acceptance 15/30件のbind生成を完全入力・source・実装identityに結ぶ16 MiB/32 entry cacheへ接続済み。独立source/acceptance照合、全資源計数、同条件3回の比較・採択経路は未実装。局所呼出し削減を製品SLO合格へ換算しない。 |
| GAH-PR07 | candidate/current CI/reportの点照会recipe | 実装済み | 既存authorityの固定query adapterへ限定接続済み。normal run・CI・quickstart全体の代用にはしない。 |
| GAH-PR07 | run一覧のpage/cursor初版 | 実装済み | opaque cursor、page上限、stale条件を含む初版入口がある。性能SLOは未測定。 |
| GAH-PR07 | 400/800/1600件、100 warm照会、別process cold 3回を各surfaceで分離する測定器 | 一部実装 | 内部series schedulerは27 cell・2916予定観測、warmup5、cold3/warm100、cell別保存・18隣接比率を実装。実runtimeの入力規模・cold process/DB/cache・warm同一processの接続と証明は未実装。局所fake adapter試験を製品性能の実測にしない。 |
| GAH-PR07 | read-side query cacheと失効・置換の実装 | 未実装 | 現在の認証・撤回・世代を確認するfresh query応答を保存して返すcacheは未実装。実装済みの純粋pack/bind cacheとは分ける。page/cursorも毎回現在状態を照合する。 |
| GAH-PR07 | page/cursor初版の単一API wall計測 | 実測待ち | API初版と基本wall観測の固定入口は存在するため、実環境での値採取だけが残る。ただしこれは提案SLO・資源counter・サイズ系列の受入証拠ではない。 |
| GAH-PR07 | page/cursorとqueryの提案SLOを満たす実環境証拠 | 未実装 | 400/800/1600件、100 warm、別process cold 3回、全counter、証拠保存を一体で駆動する経路がない。欠けた資源counterを補ってPASSにはしない。 |
| GAH-PR08 | timing historyの固定schema、同条件3回選択、canonical digest、module中央値fallback | 実装済み | test_matrix側に履歴検査、固定fallback、同snapshotのplan配布用入力、未知module処理を実装済み。期限判定は固定snapshotのas_ofに限定。 |
| GAH-PR08 | module setup/teardown込みの一回実行、skip/fail/expectedFailure/全件数のgate | 実装済み | 一回のtimed経路で成功観測と失敗詳細を分離し、欠測moduleを成功履歴へ入れない。 |
| GAH-PR08 | GitHubのpush-to-gate、queue、runner総量、全lane集約を用いた提案SLOの実証 | 未実装 | 履歴分割とlane境界はあるが、製品SLOに必要な全時間の実測・同source前後比較・3回受入証拠は未接続。 |
| GAH-PR08 | 実測時間に基づくCI最適化そのもの | 未実装 | 現在は計画・割当・観測保存の入口までで、runner構成や実行方式の最適化は実装していない。 |

この表の「実測待ち」は、実行経路が存在する初版APIの性能値をまだ採取していない項目だけを指す。GAH-PR05〜08の製品受入、SLO達成、実最適化完了を示すPASS記録は現時点で存在しない。

## 局所プロファイルの結果

[固定CI setup60件の比較](evidence/productization-implementation-20260915/profile-comparison-v5.json)はdeepcopy 76,157,597→60,243,193→48,748,902呼出しを記録した。最後の値は初回から約36%減。wallは222.349→208.431→260.625秒で、profiler負荷・並行検証負荷を含む。呼出し回数の削減は観測したが、速度向上と製品SLOは未実証。cacheの98411 bytes/5 entryという保存量をhit回数として扱わない。


9月19日の[同じ60件setupの再計測](evidence/productization-continuation-20260919/copy-profile-comparison-v1.json)ではdeepcopy総呼出しが48,748,902から28,048,541へ約42.5%減った。primitive callsは320,018から262,826。範囲はsetup全体で、単独のcandidate照会1回の値ではない。profile対象は固定source-v3、並行負荷とprofiler負荷を含み、wall 201.065秒を製品SLOや同条件の速度改善へ換算しない。最初の計測driverはreceiptの型をlistと誤認して範囲検査で失敗し、old/new keyed dictを検査するv2で再計測した。製品の返却構造は変更していない。

## 継続実装: 資源snapshotとwhole-run接続

`resource_probe` はplan/source/request digestに結んだsnapshot列から、同一scopeのCPU/IO差分、host harness CPU、観測時のRSS群最大とcgroup memory peakを分離して集計する。親cgroupと子PIDの二重加算、counter reset、identity変更、sample gap、scope不足を検査する。サンプリング値を真の瞬間最大へ変換しない。部品の取得完全性と製品SLOを区別する。

`AuthorityResourceProbeProvider` は既存runtimeが所有するbroker/既知clientだけを前後inspectし、固定cgroup v2ファイルを同じ固定UID・Python literalで読み取る。Docker接続先は既存のUnix/npipeを使い、任意command/path/URLは受けない。memory.currentはRSSではなく別の値として保持する。host process CPUも独立scopeに含める。

whole-runの資源snapshotはoperation開始からfresh CI完了・client回収の直前までを取得し、runのCheckpointに`resource-probe`副証跡を不変保存する。wall時間は後続cleanupも含むため、資源計測区間と分けて記録する。計測途中に増えた既知clientの終了snapshotも回収前に採取する。既に回収された過去clientのinspect失敗は固定理由`CLIENT_UNAVAILABLE`でそのscopeの欠測を示し、brokerやhostを含む他scopeの観測を失わせない。保存失敗でもfinallyでclientを回収し、観測失敗は固定の欠測理由にしてrawエラー内容を出力しない。

固定workerは終了直前にcgroup v1/v2のCPU、memory peak、block I/Oを上限付きstderr footerへ出す。runnerがfooterを取り除き、既存stdout結果を維持したまま、回収前にrequest/run/operation/image/sourceへ束縛したimmutable sidecarを保存する。空・不正・欠測のcounterはnullを保持する。RSSは未取得であり、memory peakの和を真のgroup peakとして使わない。

`collect_worker_observations`は保存済みfull plan、Checkpoint、ExecutionJournal、sidecarを照合し、UC-CI 30件またはUC-LLM 800件の予定operationを分母にする。欠測・別binding・再配送を区別し、必須全件に値があるcounterだけを合計する。DB読取りはrollback-journal形式とsidecar不在を接続前に確認し、read-only SQLite接続で行う。worker/client終了の部分観測だけでは全子の終了後処理、真のgroup peak、copy/serialize/hash/DB計数を満たさない。`valid_for_slo=false`を維持する。

## 単一点照会と系列受入の境界（2026-09-19）

既存の`benchmark_result`は一つの点照会系列の算術結果であり、cold/warmをまとめた少数観測からPR07の受入を判定しない。queryでは`INCONCLUSIVE`・`EVIDENCE_UNAVAILABLE`・`slo_evidence=false`を維持し、保存結果のPASS/FAILやSLO証拠ありの宣言もvalidatorが拒否する。基準/候補の比率や原失敗は保持する。`evidence_confirmed`は厳密なboolだが、それだけでサイズ・履歴・surfaceの全系列、起動条件、全計数の証拠を代用しない。[親の反例検証](evidence/productization-continuation-20260919/query-slo-parent-review-v1.json)で既存の誤合格経路を修正した。

通常`test_matrix --lane`も履歴収集laneと同じく、全予定testの実成功を要求する。skip、expected failure、unexpected success、fixtureエラー、件数不足を成功へ変換せず、通常laneの結果JSONには各件数を保存する。


内部`query_series`は共通計画と固定GAH-PR07を使い、各cellの観測artifactを上限内で保存し、全体は順序付きref・件数・digestを持つ。保存receipt不一致・cleanup失敗・時計異常・取消では後続sessionを開始せず、未実行slotを保持する。cold/warmラベルやadapterの自己申告を実process条件の証明にしない。[親の71試験](evidence/productization-continuation-20260919/query-series-parent-v1.json)で算術・失敗・入力改変の反例を検査したが、実runtime/CLI adapter、全計数、製品SLOは残り、`valid_for_slo=false`を維持する。

照会系列ではcold wallをsession open直前から応答検証完了まで計る。warmは各query区間を計測する。§5の最大3秒/2秒はSLO判定値であり実行deadlineではないため、超過した正常応答もraw observationへ保持する。取消しを受けたwarmup/warm sessionは追加要求を送らず閉じ、残りslotを未実行で残す。内部schedulerには全seriesの実行予算を実装した。既定7,200秒、上限14,400秒とし、session open/queryの前後、cleanup、保存、返却の境界で単調時計を確認する。残予算0では新しい処理を開始せず、正常に返った遅い応答と未実行slotを保持する。呼出し中のblocking処理を中断する機能は含まず、実runtime adapterのtransport timeoutは別途必要である。[親の28試験](evidence/productization-continuation-20260919/query-budget-parent-v2.json)で境界値と停止理由を確認した。

規模別入力の準備には`query_scale_data.build_scale_corpus`と`validate_scale_corpus`を用意する。400/800/1600件は同じ自作合成familyの単一stage構成で、サイズ間のcase/lineage/input識別子を分離し、カテゴリと正負の分母を均等にする。CaseSet・各文書の1 MiB上限、内容ref、oracleと期待labelの対応を検査する。これはquery前の準備用APIであり、queryごとの全corpus検証に使わない。既存の混合stage受入400件とこの系列を性能比較に混ぜない。実admission、実履歴1/10/100、planの保存サイズとruntime bindingは別途接続・検証が必要であり、現時点でruntime利用可能とは宣言しない。[親の5試験](evidence/productization-continuation-20260919/query-scale-parent-v1.json)を記録した。

### 規模系列の保存契約の残件

[サイズ診断](evidence/productization-continuation-20260919/query-scale-size-v1.json)で、既存entry形状を使う1,600件×2版のplanは1,438,640 bytesとなり、v1文書上限を超えることを確認した。代表bundleは800件×2版で1,244,370 bytes、1,600件×1版で1,764,371 bytesとなる。CaseSet単体が1 MiB内でも、planとbundleの保存・応答境界は別に成立させる必要がある。

実runtime系列を追加する前に、v1の1 MiB制限を保持した内容参照による分割形式を定義し、全partの順序・件数・総量・内容digest・contract/sourceとの結合、欠落/重複/別世代の拒否を検査する。現在のMVP経路を暗黙に新形式へ変更せず、復元後の全集合・両variantの意味検査を維持する。これは必要な次の仕様・実装単位であり、分割形式が既に利用できるという宣言ではない。

Checkpointはgetで新規decodeしたpayload、putで入力から独立decodeして不変保存したpayloadをそのまま返す。保存はcanonical bytesとして完了しており、返却objectを内部に保持しないため、二度目のdeepcopyは行わない。毎回のファイル読取・digest・canonical一致検査、入力からの分離decode、writer容量検査を維持する。[親の15試験](evidence/productization-continuation-20260919/checkpoint-copy-parent-v1.json)で既定writer/明示StorageBudget双方の返却改変・再配送・保存障害を確認した。

### 通常試行開始時の検証キャッシュ

source-v7の[5試行限定profile](evidence/productization-continuation-20260919/normal-five-operation-profile-v2.json)では、保存済みの途中状態を専用コピーにし、full plan 800件を保ったprepareと未実行5操作だけを直列計測した。prepareは0.406秒、5操作は41.304秒、8段階のAttempt追加と5件の停止・精算を確認した。確定処理や全runの再開走査は含めず、terminal receiptは発行しない。`read_checks._copy`は6,186,610回、`copy.deepcopy`は5,268,235回で、各呼出しの累積時間は重なり合う。並行負荷とcProfile負荷を含む局所値でありSLOではない。

[要求内snapshotの修正](evidence/productization-continuation-20260919/normal-start-hotpath-parent-v1.json)は、厳密なplain JSONだけを不変UTF-8 bytesとしてcacheに保持し、hitごとに独立したmutable値へ復元する。root tupleは直下要素がplain JSONの場合に型を復元し、Row混在・nested tuple・custom型・非finite値は従来copyへ戻す。UTF-8にできないsurrogate code unitも従来copyを使い、Unicode文字列の値を変えない。要求境界、SQLite transaction、DB identity、書込時失効、sourceの開始前・完了後検査を維持する。

局所36試験と親の関連検証は成功したが、同じ途中DBでの修正後profileは未実行である。診断driverの準備失敗と、回帰container cleanup前の入力退避漏れを記録し、修正後の速度向上とはしていない。再利用する入力はホストへ先に保存し、新しい入力で比較する場合は基準・候補の双方を測り直す。

[同一DBを使った比較の試行](evidence/productization-continuation-20260919/normal-matched-profile-v1.json)では、旧版の通常op0〜4が39.462秒、7段階の保存まで完了した。修正版を旧版DBへ直接適用すると、コードを含むextension fingerprint不一致で`CONFIG_MISMATCH`となり、開始前に拒否された。これは性能改善の証拠ではない。保存された版拘束を付け替えず、各版で同じfixture・clock・要求の初期評価と採択を正規に行い、通常manifest・論理要求・予定件数が等しい別DBから比較する。修正前後のDB bytesが同じとは記載せず、source差分をread_checks.pyだけに限定する。

[版拘束付き比較v2](evidence/productization-continuation-20260919/normal-matched-profile-v2.json)では、各版で正規生成した入力の論理要求・通常manifest・800件planと5操作/7段階の保存内容が一致した。wallは37.355秒→37.744秒、`_copy`呼出しは6,186,610→5,683,670、`deepcopy`は5,266,033で同数だった。局所修正の速度改善は確認できず、Row混在tupleで大きなJSONも従来copyへ戻る箇所を追加修正する。元入力とsourceは不変、所有containerは回収済み。

追加修正ではroot tupleを要素ごとにsnapshot化し、Rowと同居するplain JSON要素にもbytes方式を適用する。nested tuple/custom/非finite/不正Unicode/深い値は従来copyへ戻す。[親の関連38試験](evidence/productization-continuation-20260919/tuple-snapshot-parent-v1.json)でRow identity、返却改変分離、2 hitのcopy回数が2以下となること、開始/書込/sourceの既存境界を確認した。実全run・SLOの改善は引き続き未受入。

[plan bindingの重複削減](evidence/productization-continuation-20260919/binding-reuse-parent-v2.json)では、manifest binding内ですでに検証済みのplan/contract/registry/CaseSetをprivateな意味照合へ渡し、公開plan validatorは維持する。同じtrialを複数caseへ使わない検査はentry走査で集計してから従来位置で判定し、二重走査を除く。局所18試験と旧版pure binderの出力一致を確認した。評価TrialPlan/admissionの参照分割は[別draft](productization-partition-spec.md)でwire・DB・consumerの移行範囲を追跡し、未実装として扱う。

### TrialPlan分割codecの限定検証

[分割仕様](productization-partition-spec.md)に基づくpure codecは、各artifact 900,000 bytes、最大16 segment、logical全体8 MiB/100,000 nodeを維持する。entry追加ごとの全体直列化を避け、entry長とheader/commaを加算し、完成後に実byte数を照合する。[親の28試験](evidence/productization-continuation-20260919/partition-codec-parent-v1.json)で3,200 entriesのroundtripと全体境界を確認した。分割形式だけでは27 cellの実runtime系列・SLOを満たさず、versioned bindingと全consumer接続が残る。

### CaseSet reportの内部copy削減

公開`validate_case_set`は完全検証と独立deepcopyを維持する。`corpus_report`はexact list/tupleのother_setsに限って検証済み入力を読取り、内部copyを省略する。generatorなどが列挙途中にdocumentを変更する場合は、従来の先行snapshotを保つ。[親の106試験](evidence/productization-continuation-20260919/corpus-copy-parent-v1.json)で返却の独立性、同じ入力の重複参照、境界の同値性と局所copy counterを確認した。source-v8の固定回帰には含まれない。

source-v8から正規の通常run seedを作成できたが、[旧版との比較](evidence/productization-continuation-20260919/normal-matched-profile-v3-preflight.json)は契約のregistry/baseline参照が異なり開始前に拒否した。比較条件の一致は未達であり、速度差をSLOへ採用しない。

## source-v8の限定実測

[単独profile](evidence/productization-continuation-20260919/normal-v8-profile-v2.json)で、正規に生成した800-entry normal planの先頭5操作を実行し、7attemptと全5操作のjournal/ledger精算を確認した。prepare 0.294秒、操作部分29.145秒。cProfile中のdeepcopyは4,713,848呼出し、request-cache restore累計0.937秒であった。累計時間は入れ子で重なる。開始時のtransition履歴・採択状態の再検査が主要負荷として残る。これはsource-v8単独の診断で、runtime identityが異なる旧seedとの速度比較には使わない。後続のcorpus/分割保存差分は含まない。

### 同一要求内の履歴検査とruntime lock

`_validate_transition_state`はcurrent historyを一度完全検証し、private freshness helperへ同じvalidation rowを渡す。別世代・別要求、期限、permission generation、役割失効、現在baseline/proofの照合は維持する。共有cacheや認可結果の持越しは追加しない。[親140試験](evidence/productization-continuation-20260919/partition-consumer-parent-v2.json)と変更前freshness条件のAST一致を確認した。改善率は未実測である。

固定snapshotの診断前に、guardrail/fixture/authority lockと実source・実imageの一致を検査する。source-v9の不一致失敗を保持し、[再構築](evidence/productization-continuation-20260919/runtime-refresh-v1.json)後の別snapshotで再試行する。image/target/contract参照が異なる旧seedを同条件比較へ流用しない。

### ダイジェストだけを必要とする束縛照合

`bound_bundle_digest`の400〜415ケースかつplain JSONの入力では、完全入力とbaseline context、現在source digest、束縛・pack・walkerの関数identity、document/integer上限をkeyにする。既存の共有16 MiB/32 entry内に結果のdigest stringだけを保持する。miss時は入力を私有decodeし、従来の束縛と全bundleのdepth/node/1 MiB検査を実行する。失敗は保持せず、非適格入力は従来経路を使う。hitでもsource digestを再取得し、外側のDB・現在権限・期限・証拠照合は毎要求実行する。

[親63試験](evidence/productization-continuation-20260919/bound-digest-parent-v1.json)が成功した。[局所3反復](evidence/productization-continuation-20260919/bound-digest-micro-v1.json)では同一fixture・30 warm callsの中央値が1.505秒から0.821秒になった。[v10/v11の正規seed・同一要求による5操作比較](evidence/productization-continuation-20260919/normal-v10-v11-matched-profile-v1.json)は27.142秒→26.921秒でほぼ同時間だった。copy.deepcopyは3,441,111→3,671,629回となり、別経路のfull bind増加を確認した。単回の局所比較であり、全run/実worker/SLO改善の証明ではない。固定source内のmodule-global validatorを実行中に差し替えるhot-reloadは、旧cache・新cacheともサポートしない。

## 資源JSONの完全byte検証の再利用

4,096 UTF-8 bytes以下のnon-flat JSONは、完全raw/digestとdecoder・packer・canonical encoderの関数identityをkeyとする64-entry LRUへ成功markerだけを保持する。hitでも毎回decodeし、返却treeを共有しない。失敗・対象外入力は旧検査条件を維持する。時刻・権限・DB残高・精算状態は保持しない。[局所測定](evidence/productization-continuation-20260919/resource-unpack-cache-v1.json)は同じ284-byte予約JSONの1,000回×3反復で中央値7.660ms→2.981ms。保持量約1.03MiBはUnicode表現とkey overheadのモデル値で、allocatorを含むpeak上限ではない。

## 開始前の履歴照合

契約generation 2以上では、同じSQLite transaction内で`_validate_state`が採択履歴・候補・live proof・現在期限/permission/役割を完全に検査し、処理前後の`total_changes`が同じ場合だけ、末尾の同一履歴検査を省く。generation 1、transaction外からのentry、検証中のDB書込みは末尾検査を保持する。要求を跨ぐ現在状態のcacheではない。[実DBの追加2試験](evidence/productization-continuation-20260919/start-validation-parent-v2.json)で正常・fallback・失効拒否を確認した。gen1への必要な再帰呼出しを除いたgen2の検査回数を数える。[固定source-v12の5操作計測](evidence/productization-continuation-20260919/normal-v12-profile-v1.json)ではv11の26.921秒から28.496秒となり、全体改善は確認できなかった。保存Attemptと操作内容は一致した。各版1回の診断で、全runのSLO判定は行わない。

### Authority transportへの期限指定

`AuthorityRuntime.client(..., deadline_ns=...)` は `time.monotonic_ns()` と同じ時計の絶対期限を任意で受ける。非boolの整数0〜2^63−1を許し、期限切れでは新しいdispatchを行わない。create/inspect/start/execの各transport待ちとclient mutexの取得時間を残予算以下へ制限し、時計逆行時の再照会にも同じ期限を使う。未指定時は既存挙動を維持する。

失敗後の所有物回収は期限と独立した既存の有限待ちで必ず試みるため、client全体の返却が指定時刻以前である保証ではない。回収失敗時は所有記録を保持する。transportのTIMEOUTはbroker側DB処理やworkerの取消し成功を意味しない。期限超過時にも受信した時計逆行拒否は保存する。

[親の63試験](evidence/productization-continuation-20260919/deadline-parent-v2.json)が成功した。Windowsの最大ロック待ち時間を超える有効なdeadlineは分割待ちにし、残り期限を再計算する。これはclient側の期限部品であり、query seriesの固定製品adapter/CLI接続、全27cellのtransport/保存/cleanup予算、実Dockerの期限検証とSLO受入は未完了である。

### 資源cache miss時の二重decode削除

source-v12の5操作profileでは、小nonflat cache missで同じrawを再decodeしていた。後続修正は最初のdecode値を完全canonical/digest照合へ渡し、成功markerだけを64件LRUに保存する。処理関数をdecode前に固定し、短いlockでmarker表の参照/更新だけを保護する。同時missで重複検証は許すがmutable treeは共有しない。[関連71試験](evidence/productization-continuation-20260919/resource-cancel-parent-v1.json)にmiss/hitのdecode数、拒否条件、関数identity、実LRU eviction、返却分離・同時呼出しを含む。修正後の全体速度は未計測で、局所の呼出し削減をSLO成功へ換算しない。

### oracleの読取り時コピー

`oracle_detection`は入力を読むだけなので、内部validatorの独立copyを省く。公開の入力取得・pack検証の独立返値は維持し、[既存18試験](evidence/productization-continuation-20260919/oracle-copy-parent-v1.json)で正負/不確実判定、不正入力拒否、入力非変更を確認した。固定v12 profileのdeepcopy総再帰回数3,671,629に対し、独立した最上位呼出しは37,845であり、再帰回数を巨大文書のcopy件数と同一視しない。今回省くoracle検証のcopyは同profileで3,090回だった。全体の性能改善は再計測前である。

LLM admissionのdispatcherが同じ保存payloadを二重に復元していた箇所を、private verifierへfreshな復元値を渡す形へ変更した。公開verifyは毎回自ら復元し、時刻・行binding・現在sourceからのfactory再構築との照合と例外分類を維持する。[親18試験](evidence/productization-continuation-20260919/admission-decode-parent-v1.json)が成功し、旧validator本体（重複decode以外）と例外handlerはAST一致を確認した。全runの速度改善率は未測定である。
