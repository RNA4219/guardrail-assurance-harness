---
intent_id: INT-GAH-001
owner: RNA4219
status: in_progress
last_reviewed_at: 2026-09-20
next_review_due: 2026-10-20
---

# 継続実装の監督レビュー

対象は[実装Task](../tasks/TASK.productization-implementation-09-15-2026.md)。Lunaの担当を保存、worker計測、hotpathへ分け、親が依存接続と全体検証を確認した。コード変更と受入判定の根拠は[継続証跡](../evidence/productization-continuation-20260919/README.md)へ保存する。

| 指摘 | 処置と根拠 |
|---|---|
| host writerが同じrootを1書込で5回全走査 | 初期のtype/entry/byte検査とbudget初期値、最後の検査とaccountingを統合し3回へ。外部変更precheckは保持。全体のO(N²)は残る |
| WindowsのDirEntry metadataを実link数と誤認 | fresh os.statへ変更し実hardlink拒否を検査。外部追加のテストから拒否結果のmockを外し、実scannerで確認 |
| SQLite capはconnection localで、既存DBの再openで維持されない | 全writing接続に共通helperを接続。page size保持、上限readback、WAL拒否、実SQLITE_FULL後のrollback/row保持を検査 |
| host書込みの既定経路に上限がない | Checkpoint、productization文書、setup要求、worker sidecar、runtime metadataへ直下quotaとOS lockを接続 |
| lock初期化の競合でquota計数が揺れる | 固定1 byteのlockを排他的に初期化。別processの同root競合を検査 |
| 短命workerがresource snapshotから消える | worker終了footerを回収前に保存し、planned operation全件とsourceへ束縛 |
| cgroup v1のSync/Async/Total行を不正扱いする | Read/Writeだけを加算し補助分類の二重計上を防止。空・不明はnull |
| 過去に回収されたclientのinspect例外で全scope観測が失敗 | 固定の欠測理由へ変換し、broker/host/他clientの観測を保持。実Dockerの失敗と修正版再開を保存 |
| Docker容量観測で不正status型、inode欠測、返却aliasが残る | strict validator、ゼロ/欠測の区別、nested値分離を修正。関連67件で検査 |
| client回収後では終了snapshotを読めない | fresh CIの後・回収直前へ採取を移し、保存失敗でもfinallyで回収 |
| collectorがreading時にDB initializerを呼ぶ | read-only接続へ変更。WAL/header/補助journalを接続前に検査し副作用なしを確認 |
| collectorの成功/失敗でmemory合計キーが異なる | `cgroup_memory_peak_sum_bytes`へ統一。RSS/group peakは未取得のまま |
| コピー削減で内部関数の返り値契約が不一致 | transition呼出元を修正。全2callerとmutation独立性を再検査 |
| Docker daemon停止がbase cache不足に見える | 固定endpointの10秒preflightを追加しENGINE_UNAVAILABLEを区別 |
| collectorテスト用IDが64文字を超過する | test名の短いhash IDへ変更。修正後8件成功。製品側の入力制約は維持 |

DGX Qwenの限定レビュー3指摘を親が評価した。quota到達を直ちに既存データ消失とした指摘はrollback/保存row検査と一致せず不採用、disk capをmemory capと解した指摘も不採用とした。他の書込みで実filesystemが先に枯渇し得る点は残る条件として採用した。モデル回答を容量証明や採択権限に使わない。

容量監査は個別保存上限とsetup一操作の上界を分ける。任意のdeveloper toolや利用者が無期限に作る別workspaceを、固定setupの書込みgraphへ根拠なく混ぜない。全root個数、Docker内のwriter、空き容量観測と枯渇時の失敗処理を具体的に追跡する。

最終source-v5の差分100件は、driver起因1エラーを標準unittest入口で局所再検査して解消した。最終imageのauthority22項目が成功し、追加21テストはすべて差分100件に含まれる。旧MVP証跡559ファイルは不変。全回帰は4laneが3600秒上限でも未完了となり、合格扱いにしない。最終sourceのLinux単一laneで環境差の限定診断へ進む。実Dockerはsetup60件の保存状態を継続し、修正版の通常30件・fresh CI・30件の計測束縛・回収が成功した。途中の2失敗と修正対象を記録した。過去失敗と現在の修正版を別source/試行として保持し、文書更新だけでPAC状態を上げない。

## 照会評価・lane完了判定の追加レビュー

親は点照会の少数cold/warm結果からPR07全体のSLOをPASSにできる経路と、通常laneだけがskip/expected failureを成功にできる不一致を修正した。関連58試験が成功し、[修正とDGX採否](../evidence/productization-continuation-20260919/query-slo-parent-review-v1.json)へsource hashを記録した。最初の試験起動はsrcのimport条件不足で失敗し、標準unittestへ明示的なsrc bootstrapを加えて再実行した。製品コードの失敗ではないが初回試行も保持する。

DGX初回は回答前に出力上限へ達したため、短い依頼で1回再試行した。返答の3観点はcellごとの原観測・保存/cleanup失敗・全27系列の整合性であり、検証事項として採用した。曖昧な「p95除外」は採用せず、除外するのはwarmupだけとした。

[Linux第一段診断](../evidence/productization-continuation-20260919/linux-lane-diagnostic-v1.json)は900秒で停止し、source hash一致と所有container回収を確認した。書込先がtmpfsでもsourceはWindows共有のままで、反復source読取りの影響は残る。終了137はtimeout回収の結果で、OOMを示す証拠ではない。sourceもtmpfsへ移した第二段の結果は下記へ記録した。


照会系列の内部schedulerを追加し、親が時計異常・cleanup・入力改変・未実行分母・保存receiptを補正した。[関連71試験](../evidence/productization-continuation-20260919/query-series-parent-v1.json)は全て成功。固定27 cell・2916観測のfake adapter検査であり、実runtimeの400/800/1600件、cold/warm lifecycle、全CPU/RSS等の計測とSLO受入は含まない。

[Linux第二段診断](../evidence/productization-continuation-20260919/linux-lane-diagnostic-v2.json)はsourceもtmpfsへコピーしたが900秒でTIMEOUT。新条件400件の保存まで進んだがlane完了ではない。source不変と所有container回収は確認した。[5試行profile](../evidence/productization-continuation-20260919/llm-five-trial-profile-v1.json)から、固定packの検索表を繰り返し作る処理を特定した。

Lunaのlookup案を親がレビューし、preparedのcaseを固定corpusへ黙って置換する変更を差し戻した。元のcase選択・改変拒否とfresh source検査を維持した最終版で[親の7試験](../evidence/productization-continuation-20260919/lookup-parent-v1.json)が成功。source digest付き1件cacheは純粋な文書map/case-id索引に限定し、authority状態を保持しない。全回帰と実runtime SLOは別に検証する。

親はcold起動時間の欠落を修正させた後、追加された3秒/2秒打切りを差し戻した。これは性能合否の最大値であり実行deadlineではない。遅い観測を失わず全件保持することを確認した。さらにwarmup/warm取消しの停止条件が削除された差分を検出し、4回目/9回目の取消しが108回まで進む反例を再現。元の即時停止を復元し[最終17試験](../evidence/productization-continuation-20260919/query-cold-parent-v2.json)が成功した。DGXへの追加設計レビューは接続エラーで回答未取得であり、実施済みレビューへ算入しない。

規模別corpusではCaseSetの1 MiB上限を守るため3サイズを単一stage familyへ統一し、既存混合stageの受入集合とは比較を分離した。親がbool件数、正負の均等入替え、不正なoracle required要素を反例にして補正し、5試験の成功を確認した。実admissionと保存planサイズを未確認のままruntime対応とは扱わない。

容量診断では実ENOSPC/SQLITE_FULL、既存内容・transaction rollback・空き回復後の再書込・owned cleanupの9部品項目を確認した。最初のdriverのSystemExit扱い誤りと修正後成功を別記録に保持。物理保存上界と開始preflightの契約上の区別をレビューしたが、現行gateは変更していない。FailureSink・receipt・未精算予約の故障耐久性は追加検証へ進める。

確定集計の保存行再利用は、既存のorigin・operation・resource closure・replay/current-use検査を維持した。Lunaの反例でDB key不一致により時刻検証へ到達していない箇所を親が補正し、同じhashの不正時刻と別runを検査。旧経路と新経路の同一fixture出力比較、既存authority回帰を含む12試験は成功した。時間切れの原因をこの重複集計に確定せず、停止後DBの実到達点から通常監督runの少数試行profileへ絞る。

FailureSinkのreopen読取を親レビューし、深いJSONによる例外漏れとFIFOのblocking openを補正した。親の18件中17成功・1skipと、skipしたFIFOを含むLinux実ENOSPCの16項目を別に記録。実行driverも固定endpoint、planのimage/source照合、終了0・例外なし・overlay不変を成功条件へ追加した。専用tmpfsと所有containerを回収し、部分source overlayの診断を新image全体の受入とは扱わない。

## 9月20日の容量接続と通常試行profile

親が容量障害境界を`gah_run.execute`へ実装し、Lunaが再open writer・エラー分類を担当、別途接続を読取りレビューした。親は初回lock/mkdir/readbackでENOSPCが一般IOへ失われる点、記録済instanceのread_recordが同inodeの内容変更を返す点を補正した。102件は98成功・1 error・3skipで、1 errorはstdin driverをWindows spawnが開けない検証起動の問題だった。実ファイルdriverでその1件を再試験し成功、製品コードと20秒のテスト上限は変更しない。最初の新規統合テストは列名の誤記で失敗し、実スキーマのintended_atへ修正して成功した。[親検証](../evidence/productization-continuation-20260919/supervised-capacity-parent-v1.json)へ経緯を保持する。

通常5試行の実測は開始検証とコピーが支配的だった。LunaのJSON snapshot案を親が点検し、surrogate pairをJSONで変形させる問題をUTF-8 strict/fallbackで補正した。関連36件は成功。同一入力の追加profileはdriver準備の失敗が続き、元DBを回帰container回収前にホストへ保存していなかったため未実行となった。親の監督・入力保存の不足として保持し、新しい入力は作成時からホストへ保存する。旧新で異なる入力を速度比較に使わない。

source-v7全回帰の4timeoutとreadonly rootに由来する1失敗laneは、そのまま失敗として記録した。新sourceの全件成功へ振り替えない。DGXは15秒timeout後にモデル一覧の到達を確認し、短いレビュー依頼には応答した。3指摘を実装・テストへ照合し、既存対策に合わない一般論を追加要件へ採用していない。

Lunaが担当した[通常runの実ENOSPC診断](../evidence/productization-continuation-20260919/supervised-capacity-enospc-v1.json)を親が照合した。最初のend checkpoint書込みで実容量不足を起こし、元のauthority receipt、実runner journal、未精算operationを保持した。FailureSinkのUNKNOWNを精算根拠にせず、別process読取後に空きを回復して通常cancelから停止・精算する順序も確認した。初回のdriverはsink初期化前にファイルを読んで失敗したため、製品不具合や容量試験成功として扱わない。修正後の15 probe確認・成果物照合1項目、Linux 2試験、source不変、所有container回収を確認した。現行checkoutの局所診断と固定authority imageでの全受入は区別する。

親がdoctorのread-only契約とCLIの容量profile生成を照合し、診断だけで保存directoryとJSONを作る副作用を発見した。LunaがCLIをmemory-only生成へ直し、親が既存テストの呼出し期待を更新した。[関連47試験](../evidence/productization-continuation-20260919/doctor-readonly-parent-v1.json)は全成功。実filesystemの不変性を確認した範囲と、mockした環境probeを分けて記録した。

親は表示の次操作欠落、停止不明の一般エラー化、撤回のSOURCE_NOT_READYへの平坦化をLunaと修正した。案内の初案にあった同じCI照会への循環と固定sample setupへの過剰誘導を退け、新run・status・既存診断への実装済み導線へ整理した。TestCase公開importによる重複収集、固定日本語のUnicode escape表示、--helpで必須引数検査を迂回するテストを補正。親の表示関連18試験が成功した。DGXの途中応答は[採否](../evidence/productization-continuation-20260919/dgx-report-guidance-v1.json)に制限付きで記録した。

性能比較の初回は、修正後コードを旧版のDBへ適用したため開始時CONFIG_MISMATCHで拒否された。[失敗記録](../evidence/productization-continuation-20260919/normal-matched-profile-v1.json)を保持し、版拘束や認証検査を緩めない。各版で同じfixtureを正規生成する比較へ修正した。Lunaのsource-lock追加要求は現行checkoutと固定sourceの混同による誤指摘と確認し不採用にした。成果物の意味一致をhost成功条件へ含める指摘とcreate応答消失時の所有container回収は採用した。

### 状態表示と版拘束付き比較の追記（2026-09-20）

表示関連18試験を親が実行し成功。`--help`ではなく実parserへ案内引数を通し、撤回のfresh gate終了1と成果物のないreport終了2を区別する反例を確認した。CI側はSOURCE_NOT_READY以外を先に再throwして、未検証sourceの形状を読まないよう補正した。実DBの先行2試験を最新sourceの成功へ読み替えない。

normal-matched-profile-v2は各sourceの正規初期評価/採択から生成した入力を使用し、設定の版拘束を書き換えず5操作の保存内容hash一致を確認した。37.355秒→37.744秒で速度改善なし。`_copy`は6,186,610→5,683,670、`deepcopy`は5,266,033で同数。Row混在tupleの大きなJSONもfallbackしているため、要素別snapshotを次の狭い改修とする。呼出しcategory合計には補助関数/再帰の重複があるため、比較には個別関数のtotal_callsを使用する。

### binderと分割仕様の監督補正

Lunaのbinder案を採用し、同一関数内で検証済みの4入力だけをprivate helperへ渡す。親はtrialごとの全entry走査も指摘し、既存entry loopの集計へ置換させた。エラー順を変えず、case間のtrial共有を拒否する。負例はmanifest.plan_refを再結合し、正確な意味エラーまで確認した。親18試験はcopy回数の旧assertを補正して成功、旧版のpure binderとの返却bytesも一致した。

[分割仕様案](../productization-partition-spec.md)の初稿はbenchmark plan/corpusを対象としており、実際の評価TrialPlan上限を解決しないため差し戻した。訂正版はevaluation authorityのrun_begin・DB plan_json・run_status・bound_run本文・admission・wire frameまで追跡する。writer単独では実装可能とせず、v2 wire/DB/action/consumer移行の未決をdraftで明示した。今回の性能修正へ未完成v2を混ぜない。

## 分割契約と回帰実行器の追加レビュー

pure分割codecは28件、v2 bindingを含む最終範囲は38件が親の再検証で成功した（包含関係があるため66件へ合算しない）。全体node/再署名重複に加え、contractの内側参照を誤らせても外側refが整合していれば通った欠落を修正。binding成功とauthority採択・freshnessは別境界として仕様を更新した。

動的3並行runnerは期限監視を独立watchdogへ移し、15秒ACK期限を設定した。helperだけの試験が見逃した同名wrapperの再帰解決を親が指摘し、実record_laneの結合検査を追加した。prepared段階でDocker未実行。既存CRLFを持つworking treeはdefault `git diff --check`が848件の警告を出すが、該当sampleは固定source-v8と同hash。CRLFを考慮した `git -c core.whitespace=cr-at-eol diff --check` は成功し、既存ファイルの改行を一括変更していない。

### 照会予算の親レビュー

[28試験の記録](../evidence/productization-continuation-20260919/query-budget-parent-v2.json)に基づき、残予算0ちょうどの開始禁止、全27 cell保存後の超過、未取得sessionのcleanup非捏造、停止理由の優先順位を確認した。協調的な境界検査までを採用する。実transportのhard timeoutとSLO受入は継続する。Windows作業差分の空白検査は `git -c core.whitespace=cr-at-eol diff --check` が成功した。既存CRLFを含むdefault `git diff --check` は848件の警告で終了2となるため、両者を区別する。

### 分割保存の実装レビュー中の補正

初期実装の履歴契約優先lookupは現在契約の照合にならないため、eval_currentの世代一致を必須にした。segment追加の容量計算でcommitted bytesを二重計上しないこと、abort/期限切れ掃除でcommitted markerと衝突する破損行を消さないことを修正要求した。v4互換は[32試験](../evidence/productization-continuation-20260919/adoption-v5-compatibility-parent-v1.json)が成功し、v5統合検証は別に記録する。

上記の保存・移行修正を固定し、[80試験](../evidence/productization-continuation-20260919/partition-authority-parent-v2.json)が成功した。実DB再起動と全体構造・messageサイズ・freshnessを検証した。OS socket輸送とruntimeへの接続は後続の検証として残す。

### reportのcopy省略レビュー

公開validatorの返却契約を残したprivate検証経路を採用した。親が任意iterableによる入力書換えの意味差を指摘し、exact list/tuple以外では旧deepcopyを保つよう補正。新旧で同じ関数を呼ぶだけの自己比較をテストから除き、旧copy経路を使うreport oracleとの比較を維持した。[106試験](../evidence/productization-continuation-20260919/corpus-copy-parent-v1.json)が成功した。

## source-v8全回帰と診断側の修正

固定source-v8の全1,334予定は9レーン1,324件成功、3レーン10予定が2,400秒で時間切れ。全12ログと不変sourceを照合してcontainerを回収した。新しいsourceへ成功を移し替えない。normal-v8 profile初回はdiagnostic inventoryにsupervised_capacity.pyが欠けてsource-lock不一致となり、0操作で停止した。元DB/source/Checkpointは変更されず、診断v2で実在する14ファイルへ一覧を訂正する。入力の再署名・lock上書きは行わない。

## 分割輸送・CaseSet・copy削減の監督

Lunaの実Linux診断案は、別UIDのdirectory通過、CAP_KILLを追加しない終了処理、create応答喪失時の回収、socket再起動判定、実fixtureのサイズを親レビューで修正した。Dockerの固定capability表記とrootパス処理の失敗を保持し、v3で実SO_PEERCRED/3200-entry保存/再起動読取/候補拒否/source不変/回収を確認した。CaseSet codecは検証後の共有入力変更を私有canonical snapshotで分離し、親45試験が成功。評価入力内部copy削減は、型不正documentのdigestも再計算する拒否試験と実copy回数検査を補い、親53試験が成功した。いずれもv2 run/admissionや全PACの成功へ読み替えない。

分割集計の親レビューでは、v1 validatorのimport済みaliasにもspyを当て、共有attempt listの増加時も20,000件で停止し、最終出力1MiBを超えたら拒否する試験を追加した。共通coreのASTがsource-v8の旧bodyと一致し、関連114件が成功した。新規v2入口だけattempt canonical総量64MiBを適用し、既存v1の挙動を変更しない。

## 内部reader・corpus codecと開始検証

Lunaの内部readerを別Lunaと親がレビューし、保存JSON decodeのRecursionErrorを3境界で固定エラーへ変換した。mockのdecode障害、実SQLiteのwrite拒否・total_changes/meta不変、期限切れ・現在契約欠落と過去履歴へのfallback拒否を確認した。helperのcaller認可済み前提は明示し、helperを認証入口にしない。

corpus codecは既存CaseSet codecとquery-scale意味validatorを再利用。親がちょうどbyte上限/1byte差とcanonical化直後のcaller変更を追加検証した。開始履歴の局所重複削減はfreshness条件のAST一致を確認し、DGXの2指摘は新しい回帰を示さないため採用しなかった。親がソース不変で140試験成功を記録した。Lunaの重い統合試験は最終出力を回収できず停止したものを含み、成功件数へ加算しない。

source-v9 seedはguardrail lock内のcorpus.py hashが旧版だったため停止した。元snapshotを保ち、失敗・回収を記録。固定baseからguardrail/authorityを正規に再構築し、3 runtimeのlockとimageを照合した。v8とはruntime identityが異なり同条件速度比較は成立しない。

## ダイジェスト照合の監督

Lunaのdigest-only cacheを別Lunaと親がレビューした。source-v10との差分はrun_evidenceの専用memoizerとdigest入口だけで、miss時の完全binding/全bundle packを保持する。親は実bundleに対する1byte小さい上限でwarm cacheも拒否する試験と、hitでもsourceを毎回取得する試験を追加し、保存・認可を含む63件が成功した。通常source更新はkeyで失効する。実行中のtransitive validator差替は旧warm cacheにも同じ制限があり、新規回帰と扱わない。

DGXの回答はtoken上限で中断した。decoratorが全引数をkeyへ含める点、plainはserializerではない点、既存depth/nodeコードが未変更である点から、提示された懸念を採用しなかった。補助モデル出力を合格根拠にせず、親の実テストと局所計測を記録した。30回×3反復の中央値約45.4%短縮はpure関数の結果で、全run/SLOやPAC受入へ昇格しない。

## 固定入力によるprofileの引継ぎ

v10 profile v1では親が担当の最終完了通知より先に実行し、host script/planの最終調整と競合した。5操作は実行されたがscripts_unchanged=falseのため比較に使わない。入力とソース不変・所有container回収を確認し、引継ぎ後に別v2stageへ固定して再測定した。以後は担当の編集終了と実hashの引継ぎ後に親が実行する。v2の全script/plan/seed/sourceは不変で、7attempt・5操作精算・0terminal/receiptを確認した。

## 同条件比較と分割Evidenceの親レビュー

v10/v11の正規seed・全5DB・source・checkpointを親が再検査し、request/contract/manifest/plan/tagと5操作の保存Attempt全列一致を確認した。5操作27.142秒→26.921秒はほぼ同時間で、deepcopyは3,441,111→3,671,629回。digest経路は軽くなったがfull bind bodyが4→7回となり、以前のcache warmingの喪失と整合する。正確なevictionは未観測で、全run/SLO成功を宣言しない。

新Evidence部品は既存親子表とcoreを共有する。親がgeneration制限、segmentのcopy前上限、継承v1 startの書込み副作用を指摘し、専用入口での拒否へ修正した。外側の認証/currentness接続はまだない。親回帰で新テストの時計fixture階層・再帰呼出し数・保存破損エラー期待値の不備も修正し、失敗ログを残した。

DGXのcacheレビューは完了したが3提案は採用しなかった。保存rawの事前正規化はcanonical一致契約を弱め、str/digestの型不一致は実際にはなく、DB変更数は検証後に既に読み直している。レビュー結果を実装・テスト成功の代用にはしない。


## 明示v6の親レビューと接続確認

Lunaが実行・保存と移行を分担し、親が実AdoptionStore結合試験を作成した。親レビューでmanifest producer欠落を小さい本文保存へ修正し、same transactionのdeferred FK、旧run準備/開始・candidate/combined child・admission予約との衝突、Evidence例外のcode保持を接続した。旧v1 bundleはv2衝突と誤判定しない。

移行ではmetadataを更新後も旧snapshotと比べる誤り、schema4固定の共通validator、JSON import漏れを修正。試験側のv4/v5開き違いも区別して記録した。独立レビューを受け、旧partition bytes/metadata/参照/segment集合をreadonly検査し、partialのprefix/suffix/gap・隣接連続性と全到着時の復元を追加した。旧行を新しいsourceで再署名しない。

[関連88件](../evidence/productization-continuation-20260919/v6-parent-v3.json)と[変更前実DB](../evidence/productization-continuation-20260919/v6-real-migration-v1.json)で移行・再登録・診断run開始/再開を確認。前段の失敗は保持する。DGXの今回の限定レビューは反復してlength終了し、schema混同などの提案を却下した。実OS認証やworker/SLO/全拡張受入の成功へ昇格しない。
