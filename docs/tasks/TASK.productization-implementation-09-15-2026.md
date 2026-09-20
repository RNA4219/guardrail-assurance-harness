---
task_id: 20260915-03
intent_id: INT-GAH-001
owner: RNA4219
status: in_progress
last_reviewed_at: 2026-09-21
next_review_due: 2026-10-21
---

# Task: Luna・DGXを監督した拡張実装

## Objective

拡張14要件と4仕様を実装へ接続し、実際の検証範囲を追跡できる状態にする。

## Scope

In: 実装時の仕様補正、共通契約/管理journal、性能測定・CI分割、導入運用、実案件計画・判定、専用権限接続、Luna分担とDGX局所レビュー、親レビューと関連試験。

Out: 任意shell/任意攻撃実行、非掲載指定資産の複製、無許可の第三者対象、GitHub push・公開設定変更。実案件の対象と観測を架空値で補完しない。

## Requirements

- Behavior: 補助操作の完了と製品CIの現在判定を分離し、実装済みと未接続を明示する。
- I/O Contract: [共通実装仕様](../productization-implementation-spec.md)と各分冊に従い厳密JSON、内容参照、固定reasonと状態を使う。
- Constraints: 管理/候補/validatorの認証を維持。他者差分と既存MVP証拠を保持。Lunaは所有外変更を戻さない。
- Acceptance Criteria: 各実装経路の境界・再配送・不明/失敗・互換性を実検証し、要件との対応・残件・検査の実績を記録する。全14製品受入は必須実測が揃うまで閉じない。

## Affected Paths

- `src/gah/productization.py`、`src/gah/productization_journal.py`、専用Schemaとテスト
- `src/gah/benchmark.py`、`tools/gah_benchmark.py`、`tools/test_matrix.py`と関連テスト/CI
- `src/gah/operations.py`、`tools/gah_ops.py`と関連テスト、`tools/setup_apply.py`、`tools/setup_baseline.py`、保持/bundle/移行専用module
- `src/gah/pilot.py`、`src/gah/pilot_authority.py`、`tools/gah_pilot.py`と関連テスト/authority接続
- 拡張仕様分冊、[実装仕様](../productization-implementation-spec.md)、Task・レビュー・証跡と索引

## Local Commands

[RUNBOOK](../../RUNBOOK.md)の文書生成・検査と、変更範囲のunittestを使用する。

## Deliverables

実装コード、仕様補正、専用テスト、担当/指摘の採否と実行結果、未実施条件が分かる証跡。

## Plan

1. 既存仕様とAPIを照合し、所有ファイルを分割する。
2. Lunaの実装とDGXの局所レビューを親が監督する。
3. 共通境界とauthority/CLI/CIを接続し、故障・互換性を検証する。
4. 専用受入と既存回帰の結果を要件に対応付け、実案件・実測が必要な条件を区別する。

## Tests

固定source-v5の関連93件が228.349秒で成功。setup時刻経過、authority時計、通常prepare共有、CLI cleanup、純粋cache、性能反復番号を含む。実Dockerは初回15・旧15/新30・通常30の計90件、gen2採択、別clientで2秒後の再開、fresh CI/report、全worker停止/回収まで成功した。容量doctorの成功と全PAC受入は含まない。詳細は[今回の証跡](../evidence/productization-implementation-20260915/README.md)。

source-v4の全1,110件を12lane・最大6並列で実行し完走。入力4ファイルの収録漏れで2laneに24失敗event（23 test method）が出たため、同一source bytesへ不足入力を補い、12件・19件の追加試行で解消した。元の失敗結果は保持し、局所集約の未解決エラーは0件。最終版のdiscoveryは1,117件であり、旧固定版回帰と最終差分93件を単一の全件成功として扱わない。固定コピー補助をGit inventory方式へ修正し、1,583ファイル・必須4入力の実コピー、hash一致、既存先拒否を確認した。

移行の元backup保持、原子commit、保存receipt限定回収の反例は検査済み。性能は固定60件のdeepcopy 76,157,597から48,748,902呼出しへの削減を観測したが、並行負荷/profiler込みwallの改善は未実証。容量強制、全子資源計数、実案件入力/対象経路、image配布取得は開発残件。実案件の対象/観測、両OSの導入、同条件SLO実測は未受入。14件の機械可読PAC記録はNOT_RUN4件、INCONCLUSIVE10件、PASS0件。

## Commands

関連unittestは親の統合実行と完成単位で検査し、長い試験にはsource hash固定コピーを使用する。DGXへ仕様、共通コード、履歴一覧、bundle、setupの局所レビューを依頼し、部分応答/誤指摘を区別して採否と入力hashを作業領域に保存。

## Notes

本Taskは継続中。実装完成・全PAC受入・GitHub反映を同じ完了として扱わない。実案件の対象選定は利用者へ確認し、依存しない実装を並行する。


## 2026-09-16 継続実装

容量保存部品と明示Checkpoint予算、offline import CLI・journal、資源snapshot/providerとwhole-run副証跡、固定base取得・3image構築CLIを追加した。Lunaの分担と独立レビュー、DGXの限定レビューを親が監督。実I/Oの欠測処理、ID/path/schema拒否を修正した。最終固定版の関連116件は114件成功・2件スキップ、実image構築と2snapshotの実Docker確認に成功。[継続証跡](../evidence/productization-continuation-20260916/README.md)にsourceを分けて保存した。全writer容量、全子計数、実target実行、公開image、実案件/両OS受入は残り、Taskはin_progressを維持する。


## 2026-09-19 継続実装

全writing DB接続のSQLite上限、既定host writerの容量制限、終了worker sidecarと予定全件への束縛、回収前のresource snapshot、Docker保存先の補助観測を接続した。Lunaの実装を親がレビューし、DGXの容量指摘を検証して採否を記録。内部返却形式の呼出元不一致、過去clientのinspect失敗、Windowsのlink metadataを修正した。

同じ60件setupのdeepcopyは48,748,902→28,048,541回。host書込のroot走査は5→3回へ減らしたが、累積O(N²)と全体SLOは残る。最終source-v5の関連100件はdriver由来1件の局所再検査で未解決0件となり、新規21件を全て含む。最終imageのauthority22項目、前段sourceの保存済みsetup60件→通常30件・fresh CI・計測30件・回収を確認した。全1223件の旧固定source回帰は4laneが3600秒上限でも未完了で、合格扱いにしない。残る1laneのtimeoutは185件の完了ログと15件の追加成功で局所回収した。最終sourceのLinux単一laneで環境差の限定診断を進める。

[継続証跡](../evidence/productization-continuation-20260919/README.md)と[親レビュー](../reviews/productization-continuation-20260919.md)へsource/失敗/再試行を対応付ける。初回通常runまでの容量監査は15保存系統・5 host DBを特定したが、journal/物理容量の上界は未証明。簡易setupのUNKNOWNと、14受入のNOT_RUN4件・INCONCLUSIVE10件を維持する。

親の追加レビューで点照会SLOの誤合格と通常laneのskip成功扱いを修正し、benchmark/CI履歴を含む58試験が成功した。DGXの系列観測・保存/cleanup・全件整合の指摘を試験観点へ反映する。Linux第一段の900秒診断は未完了、source Windows共有の影響が残ったためsource tmpfsの第二段へ進む。各試行の不変source、元の失敗、検証条件を別証跡に保持する。


照会系列の内部schedulerを追加し、親が時計異常・cleanup・入力改変・未実行分母・保存receiptを補正した。[関連71試験](../evidence/productization-continuation-20260919/query-series-parent-v1.json)は全て成功。固定27 cell・2916観測のfake adapter検査であり、実runtimeの400/800/1600件、cold/warm lifecycle、全CPU/RSS等の計測とSLO受入は含まない。

lookup最適化はLuna実装を親が修正レビューし、元のprepared検証を保持した専用7試験が成功した。[計測と指摘採否](../evidence/productization-continuation-20260919/lookup-parent-v1.json)を保存。Linux source-v5のtmpfs診断は900秒でTIMEOUT、source不変と回収を確認。最終実装のimage再構築と同一source全回帰は継続する。

source-v7を1,665ファイル・1,268testsで固定し、Linux tmpfsで12lane/最大3並行の全回帰を開始。新実imageのauthority22項目は成功。固定後にcold起動時間とwarm取消しを修正し、親の17試験が成功した。詳細は[query coldの親検査](../evidence/productization-continuation-20260919/query-cold-parent-v2.json)へ記録。全回帰の完了、実runtime照会系列、規模corpusの実admission、容量故障と導入gateの条件は継続中。

同じ単一stage構成の400/800/1600件corpusを新設し、親の5試験が成功。source-v7後の差分で、実admissionは未接続。専用32 MiB tmpfsへ実ENOSPC/SQLITE_FULLを起こした9項目が成功し、既存データ保持・空き回復後の再書込・所有container回収を確認した。次に確定時の重複集計削減とFailureSinkの再起動/容量不足時の保持をLunaへ分担し、親が契約・出力互換性をレビューする。全回帰の時間上限到達は原結果として保持する。

確定時の重複集計削減は親の既存authority込み12試験、Checkpointのfresh decode後の重複コピー削減は15試験が成功した。FailureSinkの再起動後読取は18件中17成功・Windows FIFO 1件skipで、別のLinux実ENOSPC診断でFIFOを含む16項目が成功した。いずれもsource-v7固定後の差分であり、実app予約保持・全回帰・全PAC受入は継続する。

## 2026-09-20 継続

通常5試行で開始検証のcopy反復を測定し、要求内JSON snapshotを追加した。Luna実装のUnicode互換性を親が補正し関連36件が成功。修正後の同一入力profileは検証driverと入力退避の不備で未実行、ホスト永続入力を作る新しい比較を継続する。

容量障害の固定code保持、FailureSink再open/第一記録保持、通常runの終了2と記録、cancel/statusの継続を接続した。関連102件は再試験を含め99成功・3 OS依存skip。実SQLiteでrunner receiptと未精算予約の保持を確認した。さらに[実ENOSPC診断](../evidence/productization-continuation-20260919/supervised-capacity-enospc-v1.json)で別process読取、空き回復後の通常取消し・精算が成功した。15 probe確認と成果物照合1項目、Linux 2試験を確認し、最初のdriver不備と修正後試行を別に保存した。source-v7回帰は7lane/844件成功、4timeout、1laneがreadonly sourceとfixtureの不一致で失敗し、全件成功ではない。詳細は[継続証跡](../evidence/productization-continuation-20260919/README.md)。DGX短文レビューの応答と採否も保存した。

doctorが容量profileを保存する契約違反を修正し、関連47試験が成功した。LunaのCLI修正とFS不変性試験を親がレビューし、[証跡](../evidence/productization-continuation-20260919/doctor-readonly-parent-v1.json)へ保存した。通常runの表示については、停止不明・撤回理由と次操作の欠落を修正中。性能比較は保存済みseedから旧/新評価を経た同一入力を作成中で、未実行の比較を成功扱いしない。

- 2026-09-20: 8状態のreport案内と成果物欠落時のJSON/Markdownを親18試験で確認。CIの撤回/停止未確認のreasonを保持し、先行整合性異常を隠さない順序補正を追加。
- 2026-09-20: 各版で正規生成した通常run入力から5操作を比較し、保存内容一致を確認。37.355秒→37.744秒で速度改善なし。Row混在tupleのfallbackへ要素別snapshotを追加し関連38試験成功。追加版の全run比較・最終全回帰は継続する。

- 2026-09-20: 同一binding内の4入力再検証とtrial単位の全entry再走査を除去。親18試験成功、旧版と返却bytes一致。失敗した旧copy回数assertは分離検査を維持して補正。次はsource-v8のimage再構築と12lane回帰。
- 2026-09-20: 評価TrialPlan/admission bundleの分割案を別draftへ追加。v2 wire/API/DB/consumer移行の未決があり、保存writerだけを完成扱いしない。

- 2026-09-20: 分割保存/診断v6 API、明示移行、取消し/保存状態読取を追加。source-v13の実Linux認証31操作は合成停止観測までで実workerは未確認。最新配布imageの既定mode22項目と、v6 runtime親32試験が成功。固定source-v12全回帰は1,488件成功・10件TIMEOUTで未確認。admission二重decode修正18試験と、一件consumer/CLIの親81試験も成功し、close応答欠落と回収前journal状態の追加レビュー修正を継続する。[範囲別証跡](../evidence/productization-continuation-20260919/README.md)を正本とし、全PAC未受入を維持する。

- 2026-09-20: 一件診断consumerの回収・close ACK欠落を修正し、親の関連86試験が全成功。実worker診断のdriverをLunaと親でレビューし、固定source-v14で実行する準備を完了。まだ実worker受入の成功とは記録しない。

- 2026-09-20: source-v14（1,831ファイル/552 AST）を固定し、明示v6配布imageと固定fixtureの実一件診断が成功。停止・精算・Attempt保存・closeと再送追加起動0を確認。初回driverのfolder前提不一致を別失敗証跡として保持した。全15件・大規模LLM・gen2/baseline/admission・PAC/SLO受入は継続。


## 2026-09-20 corpus保存の接続

Lunaへ仕様・保存処理・起動mode・診断driverを分担し、DGXの契約前commit案と親レビューを反映した。親は確定segment保持、ACK再送、固定producer照合を仕様へ補い、codec kindの取り違えとdriverの復元比較・エラー応答処理を修正した。初回126試験は125成功、1件のテスト期待値修正後に該当5件成功。固定source-v15での実コンテナ診断は400/800/1600の保存・再送・再起動後復元とcleanupに成功した。[証跡](../evidence/productization-continuation-20260919/v7-corpus-runtime-v1.json)を参照。

利用者から所要時間の指摘を受け、新規設計を増やさず保存・再送・再起動の完成へ範囲を絞った。この保存経路の動作確認は完了。新規v7以外の移行、契約producer・大規模LLM通常run、全PAC受入は完了扱いにしていない。commit/pushは行っていない。


## 2026-09-20 reportの実接続確認

[世代表示と実DBの検証](../evidence/productization-continuation-20260919/report-generations-parent-v1.json)で、契約/baseline世代の出力不足と取消し終了3が表示失敗2になる不具合を修正した。関連34件は33成功・WARNING未実装1skip、親とLunaのレビュー後に実DBの世代値/Markdown照合1件も成功。形式的skipは最終testから除き、WARNINGを未完了条件として残す。保存成果物・DB版は変更していない。今回sourceでのimage再構築・実OS broker受入は未実施。

この時点ではWARNINGの80%注意がrunのDecision生成へ未接続だった。続く実装で閉鎖時の予算根拠を保存し、live snapshotから過去Decisionを再計算しない形で接続した。旧MVP検収記録ではなく下記の追加検証を根拠とする。

既存のTIMEOUT 10件は固定source-v15でWindowsの最大2並列として再実行を開始した。初回driverのmodule起動不備は試験本体開始前の失敗として保持し、修正版`remaining-regression-v2`の完走結果は末尾の追記に記録した。lane上限7,200秒は完走のための上限で、既存SLOの変更・達成ではない。旧Linux source-v12の1,488成功と合算して最終sourceの全回帰成功にしない。

## 2026-09-20 予算WARNING接続の完了

[予算警告の親検証](../evidence/productization-continuation-20260919/budget-warning-parent-v1.json)で閉鎖済み5軸の80%判定、保存根拠による再検証、authority/report接続を確認した。Lunaの分担・独立レビューとDGXの限定レビューを親が監督し、具体的根拠のない指摘は不採用とした。
関連76件は初回75成功・テスト由来1エラー。その1件の観測入力と再送envelope比較を補正し、最終1件が成功した。製品sourceは3試行で同一。WARNINGの実DB生成・CI終了0・両形式表示・再open、遅延した資源矛盾後の現在拒否と過去判定不変を確認。DB版・actionは追加していない。配布image更新、最終source全回帰、全PAC受入は別の残件で、Taskはin_progressを維持する。

## 2026-09-20 WARNING実コンテナ検証

Lunaの検証flag実装を親がレビューし、既定経路の互換性を保ったまま固定90件へ接続した。[source-v16の実行](../evidence/productization-continuation-20260919/budget-warning-runtime-v1.json)は90件・217項目すべて成功、1,357.875秒。実policyの80%判定、保存根拠、CI終了0、両表示形式、再起動後保持、撤回後拒否、停止・回収、source不変を確認した。後続の保存移行は別差分として検証し、全PAC未完了とTaskのin_progressを維持する。

## 2026-09-20 明示v6→v7保存移行

Lunaが移行本体と試験を実装し、親が既存helper・CLI・source固定・配布を接続した。親レビューで旧source値の一時書換え、歴史resolverの余剰field、最大整数時計による検査を除き、保存値と保存時計をそのまま検証する方式へ修正した。DGXの提案は既存FK手順の再確認に使い、内部schema_version操作と不要な型変換は採用しなかった。

[関連47試験](../evidence/productization-continuation-20260919/v7-migration-parent-v1.json)が全成功。[固定旧版DBのCLI移行](../evidence/productization-continuation-20260919/v7-real-migration-v1.json)で45表46行の保持・v7再open・旧plan拒否を確認し、現在版の正規v5→v6→v7も成功した。[更新imageの実OS診断](../evidence/productization-continuation-20260919/migration-image-smoke-v1.json)は22項目成功。これは保存移行の完成であり、大規模LLM producer/admission・通常gen2・全PACの未完了を維持する。commit/pushは行っていない。

## 2026-09-20 TIMEOUTレーンの完走

[remaining-regression-v2](../evidence/productization-continuation-20260919/remaining-regression-v2.json)は固定source-v15の3レーン10件すべて成功。最大2並列・レーン上限7,200秒で完走し、source/clone不変と6ログhashを確認した。以前のLinux source-v12/2,400秒のTIMEOUTとは別の検証条件であり、現在の全回帰・SLO・全PACの成功へは読み替えない。先行driverの開始前失敗も保持した。

## 2026-09-20 通常利用経路を基準とする進行

利用者の指摘を受け、部品数・仕様数・試験数の増加を製品完成の代わりにしない。既存14要件の範囲を維持し、次の完了単位を「採択した同一条件で対象2版を実行し、比較結果を保存・再読込し、通常CLIのreport/CIへ届ける一連の経路」とする。新しいDB版や診断専用経路は、既存経路への接続に不可欠と確認できた場合に限って追加する。局所変更ごとの全回帰反復ではなく、影響する経路を検査してから統合点で全回帰を行う。

Lunaが固定query-scale builderとテストを担当し、親がworker・journal接続を実装した。親レビューで未定義変数、runごとの契約ID変更、不要な全件再コピー、結果保存が旧pack専用だった接続漏れを修正した。[44試験と実コンテナ5件](../evidence/productization-continuation-20260919/query-scale-flow-v1.json)は成功。計2,800ケースの入力生成・worker結果照合、各規模の末尾ケース、対象2版の差、保存・再読込・二重配送・回収を確認した。

初回の固定LLM admissionを既存authorityの採択・開始・資源・Evidence保存へ接続した。[関連検証](../evidence/productization-continuation-20260919/partitioned-normal-authority-v1.json)では1,600件の採択/開始、末尾1件の固定worker結果保存と再open、既存経路の互換性を確認し、73種類の試験が成功した。Lunaはbaseline・保存adapter・cache・統合試験を分担し、親が版衝突、workerへの接続漏れ、旧baseline更新への影響と試験の誤った前提を修正した。通常経路の既存表を使い、新しいDB版・状態機械は追加していない。

次の必須接続は全件完了からbaseline採択、次世代契約の採択と通常gen2、CLI/report/CIである。新規baselineの世代更新は未接続を明示して拒否する。入力検証cacheの機能は確認済みだが、速度改善や全規模SLOは未実測。この成果を全MVP・全PAC完了とは扱わず、配布authority imageの再構築は通常gen2統合時にまとめる。Taskはin_progress、commit/pushなし。

## 2026-09-20 通常run 800件の継続試験

採択済み候補checkpoint（SHA-256 fab43c56d8a47a1e3ee2f82fc54d17a716ea771661ed08157cd7ddf2a45bd6ba）から通常run 800件を実行し、再開/status/出力refの一致、重複実行なし、current CI照会、baseline世代2の採択と現世代照合を確認した。1 test passed、failure/error/skip 0、7,359.23秒。試験sourceは実行前manifest一致・実行中不変。

この試験は固定in-process workerを用いた統合経路であり、実Docker workerや製品CLI subprocessの検証ではない。全resource counter、cold/warm lifecycle、同条件SLO、実案件データも未確認。従ってGAH-PAC07と拡張14件の状態は変更せず、Taskはin_progressのまま。[証跡](../evidence/productization-continuation-20260920/README.md)。

## 2026-09-20 focused verification summary

現行working treeで次のfocused testを実行した。productization 28件、pilot 39件（38成功・Windowsでsymlink作成不能の1 skip）、benchmark/worker metrics 37件、operations/retention/migration 64件、setup request・capacity・storage budget・非heavy adoption境界28件。合計196件を実行し、195成功・1 skip・失敗0。benchmarkは初回にsrc import path不足でmodule load errorとなったため、PYTHONPATH=srcを設定して再実行し37件すべて成功した。operationsでSQLite ResourceWarningが1件出たが、テスト自体はすべて成功した。

広いsetup test patternは、初回/旧候補/新候補の400/800件全評価を再実行する長時間testを含むと判明したため中断した。setup applyの先行2件とsetup baselineの先行4件は成功表示を確認したが、各suiteを最後まで実行していないので集計や受入へ加算しない。重複する大規模実行は既存の800件証跡を使い、実Docker/実案件の欠けた証拠を補ったとは扱わない。

文書生成・整合確認はtools.workflow generate/checkともpass。workflow checkのremote_validation/product_acceptanceはnot_runで、拡張14要件の受入は未完了。

## 2026-09-21 GitHub CIのfixture初期化修正

利用者の赤CI修正依頼に基づき、Lunaが修正を担当し、親が製品コードとの整合とLinuxでの動作をレビューした。[mainのregression-2](https://github.com/RNA4219/guardrail-assurance-harness/actions/runs/35527448876/job/106122009544)は277件を実行し、失敗moduleはtest_authority_cleanup_refreshだけだった。AuthorityRuntime.__new__で生成したfixtureに、通常constructorが設定するdatabase_modeがなく、7件のerrorが発生していた。

fixtureへdatabase_mode='default'を1行追加した。製品側の検査、所有権・不正state拒否のassert、テストの割当と成功条件は変更していない。LunaによるWindowsの対象5件と、親によるLinux Python 3.12の対象5件・既存モード検証13件がすべて成功した。後者は固定Python image内でネットワークなし・repo読取専用で実行したunittestであり、製品Docker workerの実行受入ではない。

この修正のPR・push・マージは利用者の継続依頼に含む。全レーンの結果はGitHubの該当commitのChecksで確認し、局所試験を全件CI成功へ読み替えない。実データ依存の拡張受入は利用者の指示どおり保留する。
## 2026-09-21 大規模統合テストのCI時間枠

fixture修正commit 130c879の[PR側CI](https://github.com/RNA4219/guardrail-assurance-harness/actions/runs/35529155066)は全1,682件と集約unitが成功した。test_partitioned_transition_integrationの2件は9,871.202秒、所属するregression-3の276件は10,696.711秒を要した。[同一commitのpush側](https://github.com/RNA4219/guardrail-assurance-harness/actions/runs/35529124803)では同moduleの完了前に180分で打ち切られ、unitも成功へ進まなかった。fixture初期化漏れの解消と、CI時間枠による失敗を区別する。

Lunaが分割保存の移行統合moduleをpartitioned-transition専用レーンへ移し、同レーンだけ240分、ほかは180分を維持した。専用7＋一般6の13レーンを並行実行する。約2,400 entryの処理と400/800件のassert、全件成功を要求する条件を維持し、製品の検査・評価予算・source lockは変更していない。

LunaのWindowsでの既存分割・履歴テスト20件と、親のLinux Python 3.12での分割・履歴結合・旧shardテスト35件が成功した。実行しない全件計画と独立inventory照合はdiscovered=planned=unique=1,682、対象moduleの2件は専用レーンだけに割り当てられた。新構成の全件CI結果は当該commitのChecksで確認し、旧構成での成功と混同しない。
