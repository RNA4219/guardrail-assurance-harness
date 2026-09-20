---
intent_id: INT-GAH-001
owner: RNA4219
status: in_progress
last_reviewed_at: 2026-09-20
next_review_due: 2026-10-20
---

# 保存上限・worker計測・コピー削減の継続検証

LunaがSQLite上限、host保存、worker計測、hotpathを分担し、親がsource/image束縛、計測区間、結合検証、DGXレビュー採否を確認した。[実装状況](../../productization-status.md)と[監督レビュー](../../reviews/productization-continuation-20260919.md)へ対応する。既存MVPの32条件と拡張14条件は別に扱う。

保存DBのwriting接続ごとに256 MiB上限を設定し、既存page size、DELETE journal、rollback時の既存rowを維持する。hostでは1文書1 MiB・直下256 MiB・同directoryのOS lockをCheckpointなどの既定writerへ接続した。親子directoryを含む総量、Docker storage、物理blockの保証とは区別する。setupの容量UNKNOWNは維持している。

固定workerの終了sidecarはrequest/run/operation/image/sourceへ結び、通常runの予定30件を分母に照合する。集計は既存DBをread-onlyで開き、WALと補助journalは接続前に拒否する。clientは回収前に最終snapshotを取る。CPU等の部分観測を全子RSS・group peakやSLO合格へ変換しない。

[固定sourceの回帰試行](regression-attempts-v1.json)は全1,223件・12laneの初回で7lane完走、5laneが1800秒上限に達した。regression-5はログで完了済み185件を照合し、残る15件の再試行が成功した。他の4lane（combined、llm-supervision、finding-revalidation、regression-0）は3600秒の局所検証予算でも完走せず、全回帰は未合格である。元の失敗・再試行・テスト入力だけの補正を保持し、製品SLOの閾値は変更していない。これらの長時間試行はsource-v3/v3-testfixであり、最終保存処理source-v5の全件成功として扱わない。

[実Dockerの3試行](docker-continuation-v1.json)は、初回でsetup60件・gen2採択後に検証driverの重複prepareが失敗し、次に過去clientのinspect例外を検出した。providerを修正したsource-v4で保存状態から通常30件を実行し、fresh CI、report、worker計測30件の束縛、全worker停止・所有containerの回収に成功した。90件の単一連続成功ではない。worker CPU合計は7,891,874,000 ns、I/Oは欠測、RSS/group peakは未取得。memory peakの和をgroup peakとはしない。

同じbrokerでstate/ipcのbyte・inodeを観測した。Docker内filesystemの値であり、hostのVHDX backing空き容量や総書込上界の証明ではない。[容量・doctorの親検査67件](storage-probe-parent-review-v1.json)と[修正後collector](collector-corrected-v2.json)は成功。試験数は重複があるため全件回帰へ加算しない。

コピー削減の結合検査で、`_core_bound`の新しい返却形式にtransition側が追従していない問題を検出して修正した。collector試験の2件ではtest名由来のrun_idが64文字上限を超えていたため、テスト入力だけを短い安定IDへ修正。修正後のcollector 8件は成功した。失敗試行を成功結果で上書きしていない。

[60件setupのコピー計測](copy-profile-comparison-v1.json)は48,748,902→28,048,541呼出し、約42.5%減。候補照会単独の数値ではなく、並行負荷付きwallの改善/SLO合格は主張しない。計測driverの型仮定ミスと再測定を分けて記録した。

[初回通常runを含む容量監査](capacity-first-run-bound-v2.json)で15保存系統・5 host DBを特定した。容量予算案は物理容量の証明ではなく、UNKNOWNを維持する。Docker観測helperの整形は[AST一致と専用11件](authority-storage-format-v1.json)で意味不変を確認した。

固定3imageをsource-v2から再構築し、実image configとsource hashを照合してlockを反映した。fixture/guardrailは既存内容と同一で、authorityは契約遷移修正を含む。base cacheありのoffline構築は24.016秒。ネットワーク初回取得・両OSの導入時間を測ったものではない。

[保存時の走査削減](bounded-writer-cost-audit-v1.json)で、1書込5回→3回、200文書のentry訪問数100,900→60,500を確認した。外部変更検知のため累積O(N²)は残る。Windowsのlink数はfresh statで検査し、以前のDirEntry由来の誤認を修正した。最終保存処理を含めた[3image再構築とlock反映](image-lock-installation-v3.json)はsource-v5-preimageから実施し、既存base cacheあり24.797秒だった。以前のDocker90件はこの最終imageの実行証拠ではない。

[最終差分100件](final-delta-aggregate-v1.json)は99件成功・検証driver起因1エラーの後、標準unittest入口で該当1件を再検査し成功した。Windows spawnがdriverを再importしたもので、製品sourceを変更せず解消した。これは単一実行の全成功ではない。[source/test対応表](source-test-coverage-v1.json)で追加21件が全てこの100件に含まれ、旧1223件を削除していないことを確認した。最終discoveryは1244件。

[最終imageのauthority22項目](authority-smoke-v5.json)は認証・採択・再起動・撤回の反映・所有containerの回収に成功した。[構文/非掲載情報/旧証跡の検査](static-review-v1.json)ではPython318ファイル、旧MVP証跡559ファイルの不変性を確認。非掲載識別子のファイル名・UTF-8本文残存は0件。[差分空白](whitespace-review-v1.json)はWindowsのCRLFを行末として扱って成功した。

[受入記録v8](acceptance-records-v8.json)は固定source、事前の局所計画、[完了済み結果の要約](known-results-v1.json)へ結んだ。記録時点の完了済み結果を示す。後続の回帰試行は4laneが時間上限に達し、全PACの受入は引き続き未完了である。

実案件の対象・独立データ・運用観測、全計数とSLO、導入と運用の全条件は未受入。現在の14件の状態はNOT_RUN4件、INCONCLUSIVE10件、PASS0件を維持する。

[照会SLO・lane判定の親修正](query-slo-parent-review-v1.json)では、少数点照会の全系列SLOへの昇格と、通常laneでのskip/expected failureの成功扱いを禁止した。関連58試験が成功した。これは前段の最終差分100件と重複するため、合計158件の独立成功とは数えない。

[Linux第一段診断](linux-lane-diagnostic-v1.json)は同じsource-v5のLLM laneを900秒で停止した。sourceがWindows共有のままであるため、OS/filesystemの影響を除去した試験ではない。[第二段診断](linux-lane-diagnostic-v2.json)ではsourceもLinux tmpfsへ移したが、900秒でTIMEOUTとなった。sourceの不変と所有container回収は確認済み。


照会系列の内部schedulerを追加し、親が時計異常・cleanup・入力改変・未実行分母・保存receiptを補正した。[関連71試験](query-series-parent-v1.json)は全て成功。固定27 cell・2916観測のfake adapter検査であり、実runtimeの400/800/1600件、cold/warm lifecycle、全CPU/RSS等の計測とSLO受入は含まない。

[lookupの親レビュー](lookup-parent-v1.json)では、保存済みcaseからの選択とfresh source検査を保ち、固定packの文書mapとcase-id索引だけをsource digest付き1件cacheへまとめた。専用7試験は成功。反復6照会でpack全走査は1回となり、同じ先頭5試行のcanonical呼出しは20,578から5,542へ減った。deepcopy 383,091回とsource-read 3,885回は不変。単発profileのwall差はSLO達成や全400/800件の改善の根拠にしない。

[lookup修正後の実image](image-lock-installation-v4.json)を再構築し、実imageのsource/設定とlockを照合した。既存base cacheで21.813秒、authority imageはd2f0476b…へ更新。[新imageのauthority22項目](authority-smoke-v7.json)はすべて成功した。source-v7は1,665ファイル、1,268テスト（source-v6から7追加、削除なし）で固定し、Linux tmpfs上の12lane回帰を開始した。結果確定前のため全回帰成功とは扱わない。

[query cold計測の親修正](query-cold-parent-v2.json)は起動時間をcold wallへ含め、SLOの最大値を実行deadlineへ流用せず遅い値も保持する。warmup/warm取消し後の追加照会を独立反例で検出・修正し、関連17試験が成功。先行[16試験](query-cold-parent-v1.json)で検出できなかった取消し境界を補った。これはsource-v7固定後の差分であり、進行中の1,268件回帰に含まれない。実runtime lifecycle、series全体のdeadline、全counterの実装・実証は残る。

[規模別corpusの親検査](query-scale-parent-v1.json)で400/800/1600件を独立生成する`query_scale_data`を追加し、5試験が成功した。3サイズは同じ単一stage構成で、CaseSetは259,759 / 519,359 / 1,038,560 bytes、既存の1 MiB文書上限を維持する。bool件数・ラベル入替え・不正oracleの親反例を補正済み。既存の混合stage受入400件とは別系列であり、実runtimeへのadmission、履歴規模、実性能・独立oracleの実証は含まない。source-v7固定後の差分として扱う。

[実容量不足の限定試験](capacity-enospc-v1.json)は[固定計画](capacity-enospc-plan-v1.json)に従い、新authority imageの専用32 MiB tmpfsへ実ENOSPCを発生させた。bounded writerの失敗時に既存ファイル不変・新規target不在・一時ファイル回収を、SQLiteのSQLITE_FULL時に既存row保持・未commit row不在を確認。空き回復後は両方の書込・読戻しとDB整合性が成功し、9項目と所有container回収を確認した。最初のdriverがSystemExit(0)を失敗記録した試行も保持する。setup/run全体のreceipt・未精算予約・FailureSinkの耐久性、host backing容量の証拠ではなく、setupのUNKNOWNとPAC状態は維持する。

[確定集計の親検査12件](finalize-parent-v1.json)で、`evidence_finalize`直後の全再集計を、terminalのaggregate digestに結んだ保存行の検証付き読取りへ置換した。初回の集計は2回から1回となり、旧新のreceipt/evidenceが同じ小fixtureで一致し、再配送・権限失効・rollbackも成功。親がdigest反例の空振りを補正し、内容hashが一致する不正時刻も拒否することを確認した。これはsource-v7固定後の局所修正で、通常LLM runの時間上限を解決した証拠ではない。

[規模別保存サイズ診断](query-scale-size-v1.json)では、1,600件×2版のtrial planが1,438,640 bytesとなり、v1の`validate_trial_plan`は`DOCUMENT_SIZE`で拒否した。同じcase/planを既存bundleへ配置した代表envelopeは1,600件×1版でも1,764,371 bytesで、1 MiB artifactには収まらない。代表envelopeは正式にbind/admitしたものではなくサイズ診断に限る。corpus生成だけでは実runtime対応にならず、計画・bundleの参照分割契約が必要と確認した。

[FailureSinkの親検査](failure-sink-parent-v1.json)は18件中17件成功・WindowsのFIFO 1件skip。`read_existing`による別processでの読戻しを追加し、全zero未記録・厳密JSON・header/長さ/padding・regular/single-link・サイズを検査する。親が深いJSONの例外漏れとFIFO openの停止可能性を指摘し、固定エラーへの収束とnonblocking open前後の検査を補正した。

[実ENOSPC中のFailureSink検証](failure-sink-enospc-v1.json)は[固定計画](failure-sink-enospc-plan-v1.json)に従い、source-v7 imageへ新moduleだけをread-onlyで重ねた差分診断である。専用32 MiB tmpfsの空きを128 KiBまで圧迫した状態で、事前確保した2 MiB領域へ書込/fsync/読戻しが成功し、新Python processからも同じ合成envelopeを読めた。Linux上のFIFO拒否・深いJSON拒否を含む16項目、source不変、終了0、所有container回収を確認。合成receipt/未精算予約マーカーの保持であり、実app予約・setup・停電やOS再起動後の耐久性を実証したものではない。`durable_after_enospc=false`と`bound_verified=false`を維持する。

[Checkpoint返却のコピー削減・15試験](checkpoint-copy-parent-v1.json)では、fresh JSON parse後のget返却と、入力から独立decode済みのput返却で重複するdeepcopyを除いた。入力・get/put/再配送の返却値の変更が保存へ波及しないことを、既定bounded writerと明示StorageBudgetの両経路で確認した。不変保存、digest、容量上限、flush/publish失敗の拒否は維持する。全体速度やSLOは未実証であり、source-v7固定後の差分として扱う。

[Linux全回帰source-v7](linux-regression-v1.json)は12laneを完走して結果を回収した。7laneの844件が成功、4laneは2400秒でtimeout、1laneは207件実行中の2 error eventで失敗した。後者はテストが作業source直下へ一時directoryを作るのに、診断がそのrootを読取り専用にした不一致である。source/manifest不変とcontainer回収を確認したが全回帰成功ではない。

[通常runの5試行profile](normal-five-operation-profile-v2.json)でprepareと新規試行を分け、800件planのうち未実行5件・8段階を完了、元DB/checkpoint/source不変を確認した。開始検証で大きなcopyが反復されていたため、[要求内JSON snapshot](normal-start-hotpath-parent-v1.json)を追加した。親はsurrogate pairの型変換を補正し、局所36試験が成功。修正後の同一入力比較は準備失敗と入力の退避漏れにより未完了で、速度改善は主張しない。

[容量障害の製品入口接続](supervised-capacity-parent-v1.json)では、記録先の事前割当・再open、最初の記録保持、容量errnoと一般I/Oの区別、取消し/照会を妨げない境界を追加した。関連102件は98成功・1 error・3skip、Windows spawnのstdin driver問題だった1件は同じsource/testを実ファイルdriverで再試験し成功した。集約99成功・3skipを単一実行の全成功とはしない。実SQLiteで、Checkpoint障害後にも本物のjournal receiptと未精算予約が保持され、新しいterminal receiptやAttemptを作らないことを確認した。実容量枯渇下の製品接続は次の限定診断へ結ぶ。

[DGX Qwenのレビューと採否](dgx-capacity-review-v2.json)も保存した。最初の15秒timeoutを保持し、到達確認後の短い依頼は4.437秒で応答した。指摘は既存の厳密JSON検査、stage/UNKNOWNの意図、固定小envelopeと上限拒否へ照合し、一般論だけで追加変更や受入成功へ変換しない。

[通常runの実ENOSPC検証](supervised-capacity-enospc-v1.json)では、32 MiB tmpfsを実際に満杯にし、終了2の障害記録、実journal receiptと未精算予約の保持、圧迫中の別process読取、空き回復後の通常cancelによる実停止・精算を確認した。15のprobe確認と成果物照合1項目が成功し、その中のLinux FIFO/symlink試験も2件成功した。328 Pythonファイルの不変性と所有containerの回収を確認。247.359秒の局所診断であり、全体容量gateや最終imageの製品受入には昇格しない。初回はsink初期化前の読取りというdriver不備でENOSPCに到達しておらず、失敗記録を保持して修正後の試行と分ける。

[doctorの読取専用修正](doctor-readonly-parent-v1.json)は関連47試験が成功した。容量profileはdoctorではmemory内で生成し、既存workspaceのファイル不変・未存在workspaceの非作成・不正profileの拒否を実filesystemで確認した。環境probeとauthorityは当該回帰のmock範囲であり、Dockerは起動していない。setup applyの保存経路と容量gateは変更しない。

[同一DBでの比較試行](normal-matched-profile-v1.json)はINCOMPLETEである。旧版のop0〜4は39.462秒・7段階、修正版は開始前CONFIG_MISMATCHで0操作だった。旧版DBの版拘束は変更していない。各版で正規生成した別DBの論理入力を照合する次の比較へ進む。

[表示改修に対するDGXレビュー採否](dgx-report-guidance-v1.json)も記録した。600 token上限で途中終了した一般的な設計レビューで、コード検査や独立受入ではない。取得途中の状態変化を反例として採用し、親の表示関連18試験で成果物欠落・healthy gate、参照不一致、未知状態、8状態の案内と実parserを確認した。実際の撤回・停止不明を扱うSQLite経路の検査は別記録へ結ぶ。

[状態別reportの親検査18件](report-guidance-parent-v1.json)は成功した。JSON/Markdownに理由・次操作・必要役割・目的を示し、成果物欠落はfresh gateがHEALTHYでも終了2を維持する。CLI案内は実parserで構文を確認した。[CI理由の保持](ci-reason-preservation-v1.json)では、実DBの撤回・停止不明を確認し、先行する整合性異常を隠さない順序補正を追加した。実DBの2件は順序補正前、現sourceの軽量反例は1件成功として別記する。

[版拘束を保った5操作比較](normal-matched-profile-v2.json)は出力が一致した。各版で正規生成した別DBの要求・800件plan・5操作/7段階を照合し、旧版37.355秒、新版37.744秒。コピーの一部は減ったが速度改善は確認できない。Row混在tupleのfallbackを次の限定改修とし、製品SLOや全runの受入には昇格しない。

[tuple要素別snapshotの親検査38件](tuple-snapshot-parent-v1.json)は成功した。Rowと大きなJSONを含むtupleでも、plain JSONの要素は独立bytesへ保持し、Row/custom/nested tuple等の要素は従来copyへ残す。要求・transaction・source境界は不変。[DGXレビューの採否](dgx-tuple-snapshot-v1.json)では型保持・非finiteの反例を既存試験へ照合し、誤った説明を採用しなかった。この版の全run速度は未測定。

[plan bindingの親検査18件](binding-reuse-parent-v2.json)は成功した。同関数内の検証済み入力を再検証する重複と、trialごとに全entryを再走査するO(N²)を除いた。旧固定版とのpure bindingのcanonical bytesが比較条件あり/なし双方で一致し、意味反例は参照を再結合して正確な拒否codeまで確認した。初回18件のうち既存copy回数assertが1件失敗したのは削減後の4回に対して6回を期待したためで、入力・返却分離の検査を維持して補正し、失敗ログも保持した。

[修正版3imageの構築とlock反映](image-lock-installation-v6.json)はsource-v8-preimage 1,708ファイルを固定してoffline実行し、既存base cacheで完了した。実imageの設定/label/sourceと新lockを検証し、checkoutの全記録source hashも一致した。既存runtimeを移行したものではなく、最終sourceの12lane回帰・製品実行は次の記録へ結ぶ。

[新imageのauthority smoke](authority-smoke-v8.json)は22項目すべて成功した。認証・独立採択・撤回・再起動と所有container回収を確認し、source-v8の1,709ファイルは不変。全回帰は1,334試験（旧1,268を保持・66追加）を12laneで実行中であり、結果確定前に全件成功とは記載しない。

[rollback journal容量の追加調査](capacity-journal-review-v1.json)では、主DB上限からページレコード部分の上界を導出した。header/secondary journalと実filesystemの条件は残り、全体容量gateを合格へ変更していない。WindowsとLinux検証imageのSQLite版は異なるため、source上の上限を別binaryへ無条件に適用しない。

[分割codec設計に対するDGXレビュー](dgx-partition-codec-v1.json)は4.781秒で完了した。3指摘を実際のPython value APIとcanonical生成へ照合し、raw JSON断片操作を前提とした提案は採用しなかった。コード検査・製品受入の代替にはしない。

[分割TrialPlan codecの親検査28件](partition-codec-parent-v1.json)はすべて成功した。3,200 entryのlogical plan 1,437,017 bytesを900,000 bytes以下の2 segmentへ分け、順序・総量・digest・global node上限・再署名した重複拒否と独立返却を確認。親レビューでサイズ計算の厳密照合と不要copy削除を補った。source-v8固定後の差分であり、実行中の1,334件回帰には含めない。authority/DB/admission接続は残る。

[v2 bindingの親検査38件](partition-binding-parent-v1.json)は成功した。1,600 distinct caseとcandidate/baseline計3,200 entriesを、900,000-byte以下のartifactとref-only出力へ束ねる。親がcontract内のCaseSet参照不一致を正規の再署名で再現し、policy/registry/CaseSetの実content照合を追加した。全case/variant欠落の拒否、既存整合入力の14条件、出力field/refを確認した。authorityの認証・現在性・保存・実行への接続は含まない。

[動的3並行回帰実行器の準備](linux-regression-v3-preparation.json)では、空いたslotへ次laneを開始する実装を作成した。親レビューでcopy/ACK待機中の期限監視とwrapperの再帰shadowを補正し、実bootstrap helper・実child停止・ACK結合のWindows診断が成功した。Linux Dockerでのv3実行は未実施で、実行中のv2は変更していない。

### 照会系列の実行予算

[query-budget-parent-v2.json](query-budget-parent-v2.json)は既定2時間・上限4時間の協調的な実行予算に関する28試験の成功を記録する。時刻異常、open/query timeout、最後の保存での予算超過、cleanup/persistの優先順位を検査した。初回の親driverはsrc import pathを欠き失敗したため、その事実を同証跡に残し、製品とテストを変更せずdriverを修正して再検証した。blocking callの強制中断、実transport、製品SLOは未検証。この変更はsource-v8の全回帰には含まれない。

### 明示v5追加前後の既存v4互換

[親の32試験](adoption-v5-compatibility-parent-v1.json)で、schema-v5の明示許可と固定source-v8 validatorの移行互換を追加した後も、既存v4の認証・評価・移行試験が成功した。v5の保存実行やDocker配置の受入を含まない。[DGX保存設計レビュー](dgx-partition-storage-v1.json)は、親が判断した採用範囲を記録する。

### 分割Planの保存・再読取と明示移行

[親の80試験](partition-authority-parent-v2.json)はすべて成功した。既定v4を保つ明示v5拡張で、実AdoptionStoreへの契約採択、upload途中のSQLite再オープンと同一要求再送、commit後の再オープンと読取、権限撤回後の拒否を確認した。3,200 entriesの保存用fixtureを複数segmentへ分け、未完了commitの不可視性と1 MiB以内のrequest/response、同じstatus request IDでのfreshな読取も検査した。

v4→v5は追加3表だけを同一transactionで作り、既存行を保つ。DDL途中障害・source途中変更でrollbackすることを確認。親レビューで現在契約の世代照合、resume/既commit shortcutのpermission/source再検査、確定済みdataの削除防止、容量の二重計上を補正した。先の79試験を包含する80件であり、合算しない。OS peer credentialを実socket経由で検査した記録ではなく、v2 run・全consumer・実runtime・CI成功・SLOの受入は含まない。

### CaseSet reportのコピー削減と比較条件

[親の106試験](corpus-copy-parent-v1.json)が成功した。公開CaseSet validatorの独立deepcopyを保ち、exact list/tupleからreportを作る内部経路だけで不要copyを省略した。2つのCaseSetを扱う局所counterは2回から0回となった。任意iterableが入力を途中変更する場合は旧snapshotを保ち、差分oracleと反例を検証した。80件の分割保存・移行回帰も含むため、別記の80件とは合算しない。通常runの実測高速化・SLOはこの試験から主張しない。

[source-v8正規seed](normal-seed-v8-v1.json)は初期600段階、旧400/新800試行、採択と通常run開始直前のcheckpointまで作成し、source不変とコンテナ回収を確認した。[旧版との比較preflight](normal-matched-profile-v3-preflight.json)はexpected contract ref不一致でINCOMPARABLE。registryとbaselineのdigestが異なるため、同一条件の性能比較として実行しなかった。古いDBの再束縛や比較検査の弱体化は行っていない。

- [source-v8全回帰](linux-regression-v2.json): 1,334予定、1,324成功、3レーン10予定は時間切れ。source不変・全ログhash・container回収確認。全回帰/PAC成功ではない。
- [normal-v8単独profile初回失敗](normal-v8-profile-v1-failure.json): 診断側source-lock一覧の1件漏れを開始前に検出。0操作・元DB不変・回収確認。失敗を保持し、v2診断で一覧を修正する。
- [CaseSet転送DGXレビュー](dgx-case-partition-v1.json): canonical対象・0始まり・byte counterの明文化を反映。token上限で応答途中終了、完全レビュー/受入ではない。

- [normal-v8単独profile再実行](normal-v8-profile-v2.json): 実800-entry normal planの先頭5操作/7attemptを完了、finalize未実行。操作部分29.145秒。source/DB/Checkpoint不変、回収確認。前後比較やSLO達成ではない。
- [CaseSet分割の親検証](case-partition-parent-v1.json): codec13件を含む関連45件PASS。1,600-caseは既存1MiB内で2segmentへ分割し完全復元。byte境界、上限、bool、共有入力変更、再署名不整合を確認。DB/runtime admissionは未接続。

- [分割authorityの実Linux診断v3](partition-authority-linux-v3.json): 4UIDの隔離、実SO_PEERCRED、3200-entry/2segment保存、broker再起動読取、candidate拒否、source不変と回収を確認。storage-only fixtureで、既定入口/v2 run/admissionの受入ではない。[v1設定照合失敗](partition-authority-linux-v1.json)と[v2起動パス失敗](partition-authority-linux-v2.json)も保持。
- [評価入力copy削減の親検証](evaluation-copy-parent-v1.json): 関連53件PASS。内部document mapのdeepcopyを0にし、破棄する中間copyを削減。公開独立性と再署名済み不正入力の拒否を確認。性能SLOは未判定。

- [分割consumerと関連差分の親回帰](partition-consumer-parent-v1.json): 114件PASS。v2 pure集計の8件、既存v1集計、分割保存/移行、CaseSet、copy削減を含む。集計coreの旧sourceとのAST一致も確認。runtime admissionは未接続。

- [分割consumerと開始検証の親140試験](partition-consumer-parent-v2.json): 内部committed-plan reader、corpus分割、同一transaction内の履歴検査重複削減を含む。ソース不変とfreshness条件のAST一致を確認。旧114件を包含し合算しない。
- [corpus分割の実サイズ](scale-corpus-sizes-v1.json): 400/800/1600 corpusを完全復元。1600のlogical 2,210,921 bytesをCaseSet 2・documents 2 segmentsへ分ける。runtime admissionではない。
- [source-v9 seed生成の停止](normal-seed-v9-v1-failure.json): corpus.pyと旧guardrail lockの不一致でfixture準備を拒否。0通常操作・source不変・回収確認。
- [runtime再構築](runtime-refresh-v1.json): 固定baseとnetwork noneでguardrail/authorityを再構築し、fixtureを含む3 lockと実imageを照合。実run受入とは別。
- [DGXの開始検証レビュー](dgx-transition-reuse-v1.json): 応答完了。型・payloadの2指摘は既存経路にも同じ前提があるため、新しい不具合として採用しなかった。

- [ダイジェスト処理の親63試験](bound-digest-parent-v1.json): 完全bundleの実byte上限、warm hitのsource再照合、保存・認可の既存回帰を確認。全suiteではない。
- [局所30回×3反復](bound-digest-micro-v1.json): 同じ400ケースのpure digest計算で中央値1.505秒から0.821秒（約45.4%短縮）。同processの局所値で、全run/SLOではない。
- [Luna独立レビュー](bound-digest-review-v1.json) / [DGX限定レビューと親採否](dgx-bound-digest-v1.json): 動的validator差替は旧warm cacheと共通の未サポート範囲。DGX応答は中断し、提示3件は新規回帰の根拠がなく採用しなかった。
- [authority runtime再構築v2](runtime-refresh-v2.json): digest処理修正を正式にimageへ構築し全lock/source/imageを照合。guardrail/fixture imageはsource-v10と同一。

- [source-v11固定](source-v11-freeze.json): 1,765 files / Python 535 filesのAST成功。v10からの製品差分はrun_evidenceとauthority lockだけ。固定後の証跡はsnapshot自身に含めない。
- [source-v10正規seed](normal-seed-v10-v1.json): 初回600段階、旧400/新800件比較・採択、通常800-entry開始直前。親が5DB/receipt全table件数・12checkpoint/source hashと回収を照合。synthetic fixture観測であり実guardrail worker受入ではない。
- [profile v1の不成立](normal-v10-profile-v1-failure.json): 親の開始と担当の最終準備が重なりdriver/plan不変条件が不成立。source/seedは不変、所有container回収済み。比較には採用しない。
- [source-v10 profile v2](normal-v10-profile-v2.json): 引継ぎ済みdriverで再計測。prepare 0.290秒、5操作27.142秒、7attempt、全5操作精算。全入力・script不変、回収確認。v11との比較と全run/SLOは継続。

- [source-v11正規seed](normal-seed-v11-v1.json) / [5操作profile](normal-v11-profile-v1.json) / [同条件比較](normal-v10-v11-matched-profile-v1.json): v10とのmanifest/plan/request/tag・保存Attempt全列一致。27.142秒→26.921秒の単回局所値、実worker/SLOではない。
- [digest cacheの負荷移動レビュー](bound-cache-matched-profile-review-v1.json): digest処理は軽くなる一方、別のfull bind bodyが4→7回。shared LRUの正確なevictionは未計測。
- [資源JSON cache](resource-unpack-cache-v1.json): immutable markerだけの64件LRU。旧oracle/fresh返値/拒否条件の7試験と局所測定。約1.03MiBは推定保持量。
- [DGX cacheレビュー](dgx-cache-review-v2.json): 完了応答の3提案を親が棄却。canonical比較と型は既存契約どおり、DB変更数は検証後に再取得している。
- [親回帰v3](continuation-parent-v3.json) / [v4](continuation-parent-v4.json): 追加試験の時計参照・再帰spy集計の不備を保持。全回帰成功へ数えない。

- [開始検証の親2試験](start-validation-parent-v2.json): 同一取引の再利用、write/no-tx/gen1 fallback、現在状態変更の拒否。直前の[期待エラー不一致](start-validation-parent-v1-failure.json)も保持。
- [分割Evidenceの親45試験](partition-evidence-parent-v2.json): 既存34 + 新11の部品試験。再送/再開/現在resolver/破損/入口拒否/既存エラー互換性。v6の認証actionと実workerは含まない。

- [v6移行前の実v5 fixture](v6-predecessor-fixture-v1.json): 固定済み旧source pairで採択・15-entry plan commit・再開読取。DBと全table hashを保存。移行自体はまだ未実行。


### 明示v6診断APIと旧DB移行

[固定ソースの関連88試験](v6-parent-v3.json)が27.580秒で成功した。実AdoptionStoreの採択、分割plan、診断runの開始/再送/再開、validatorによる合成Attempt記録、権限失効、source/segment変更、途中失敗の全rollbackを含む。直接dispatchによるrole検査であり、新v6の実OS peer認証/worker受入は含まない。

先行実行の[68件中63成功](v6-parent-v1.json)と[87件中84成功](v6-parent-v2.json)を保持する。移行のmetadata比較、共通validatorのschema4固定、保存JSONのimport漏れとtest fixtureの版不一致を修正した。partial segment間の空き領域と隣接連続性、全segment到着済みuploadの復元も検査する。

[変更前実DBの移行](v6-real-migration-v1.json)では、旧ソースで生成した44表34行・15entryのDBをコピーし、許可したmetadata更新以外の全旧行を保持した。v6再open、旧planのPLAN_STALE、新plan_idによる再登録、診断run開始と再起動後のref receipt一致まで成功した。原本SHAは不変。実worker・通常regression/admission・全consumer接続は別の残件である。

[DGX Qwenの限定レビュー](v6-dgx-review-v1.json)はlength終了で未完了。通信schemaとDB schemaを同一にする提案などは親が却下し、採択した修正はない。レビュー出力をテスト結果や製品受入へ数えない。

[配布依存の15試験](v6-packaging-parent-v1.json)は14成功・Windows symlink未利用の1skip。v5/v6の10moduleをimage収録へ追加し、[authority再構築](runtime-refresh-v3.json)で実imageとsource lockが一致した。guardrail/fixtureのimage identityとlockは維持した。image作成を実動作受入へ数えない。

[source-v12凍結](source-v12-freeze.json)は1,798ファイル・39,477,423 bytes・545 Python ASTを照合した。[新imageの既定authority診断22項目](authority-smoke-v12.json)で4UIDの隔離・実socket認証・採択・再起動・失効・所有container回収が成功した。対象は既定schema-v4であり、新v6 actionの実Linux診断は別に扱う。

- [v6実認証診断](v6-linux-smoke-v1.json): 固定source-v12、実Linux SO_PEERCREDの4役割・23操作、明示移行、15件plan、合成Attemptの保存/再送/再起動を確認。79.093秒、所有container回収。通常worker・全PAC受入は含まない。

- [authority期限の親63試験](deadline-parent-v2.json): create/inspect/start/exec・mutex・時計逆行retryへ残り期限を伝播。既存3種clientと回収の互換性を確認。series製品接続・全体期限保証・SLOは含まない。

- [v12通常run seed再生成](normal-v12-seed-v2.json): 固定sourceから5DBを再生成し、v11のrequest/manifest/plan/tagとの一致を照合。[先行driver失敗](normal-v12-seed-v1-failure.json)は別記録として保持。
- [v12の5操作profileと親比較](normal-v12-profile-v1.json): 5操作・7Attemptを保存し、v11との結果hash一致を確認。26.921→28.496秒で改善なし。各版1回の診断で全800操作やSLO判定は含まない。
- [v6資源接続の親検証](v6-resource-parent-v1.json): 予約・送信・停止観測・精算・closeと開始時の失効拒否。45成功にテスト期待のみ修正した1条件の再成功を合わせた46条件。専用の中断・回収接続は後続差分。

- [資源cache・取消し接続の71試験](resource-cancel-parent-v1.json): 44.837秒、全成功、source不変。二重decode削除とv6取消し/期限切れowner回収の原子性、独立観測後の精算を確認。固定v12全回帰の後続差分。
- [取消し設計のDGXレビュー](v6-cancel-dgx-review-v1.json): 応答を親が評価。roleとleaseの別検査を確認し、欠測を自動精算する変更は採用しない。

- [保存状態readerの60試験](v6-resource-read-parent-v1.json): 全成功・skipなし・source不変。取消/期限切れ/現在契約変更と新規開始を分離し、保存時の結合・破損拒否・遅延beginを検査。
- [oracleの18試験](oracle-copy-parent-v1.json): 読取り専用oracleの不要copyを除き、判定と入力非変更、公開APIの独立返値を保持。速度改善の再計測は含まない。

- [最新authority imageの再構築](runtime-refresh-v4.json): 検証済みの資源取消し・保存照会・cache/oracle修正をoffline構築し、source lockと実imageを照合。guardrail/fixture imageは同一。実OS診断は次の固定sourceで行う。

- [source-v13固定](source-v13-freeze.json)と[配布imageの22項目](authority-smoke-v13.json): source不変、4 UID・採択・再起動・失効・所有container回収を確認。
- [v6資源の実Linux診断](v6-linux-smoke-v3.json): 31操作、97.172秒、取消し後の未精算保持と合成validator観測後のcloseを確認。実workerは未起動。[先行診断失敗](v6-linux-smoke-v2-failure.json)はDB補助読取のUID誤りとして保持し、権限境界を変更せず補正した。

- [1件worker consumerのDGXレビュー](v2-consumer-dgx-review-v1.json): 初回timeoutと再試行完了を区別し、クラッシュ後の段階復元を設計・試験へ採用。既存schema外のfield追加や重複した回収機構は採用しない。

- [LLM admissionの二重復元修正18試験](admission-decode-parent-v1.json): 4.832秒、全成功。fixed source-v13と今回の3ファイルだけで検証し、検証本体・エラー分類のAST一致を確認。

- [固定source-v12の全回帰](linux-regression-v4.json): 12lane/1,498件中、9laneの1,488件成功、3laneの10件はTIMEOUTで未確認。最大3並列・lane上限2,400秒、source不変・全所有container回収。全件成功やSLO達成とはしない。

- [明示v6 runtimeの親32試験](partitioned-runtime-parent-v1.json): 既定v4互換、空v6 DB新規作成、既存DBの不一致拒否、mode保存・再起動・回収・期限伝播。実containerでの確認は後続。

- [v6 broker対応imageの再構築](runtime-refresh-v5.json): image/source lock一致、固定baseでoffline構築。guardrail/fixtureは同一。

- [v6対応imageの既定モード実OS診断22項目](authority-smoke-runtime-v5.json): 46.203秒、全成功。実行入力不変、4 UID・採択・再起動・失効・所有container回収を確認。v6 worker診断は別工程。

- [一件診断consumer/CLIの親81試験](partitioned-diagnostic-parent-v1.json): 69.775秒、全成功、入力ソース不変。実行ID・終了状態・必須fieldの厳密照合を追加。RUNNING/STOPPED回収後の精算とcloseのACK欠落は追加レビューで見つけた後続修正。

- [一件診断consumer/CLIの親86試験](partitioned-diagnostic-parent-v2.json): 87.755秒、全成功、全収録入力のhash不変。RUNNING/STOPPEDからの回収とclose ACK欠落/lease更新を追加し、CLI引数と重複テスト定義を親が補正。実worker受入は別工程。

- [source-v14固定](source-v14-freeze.json): 1,831ファイル・39,756,389 bytes・552 Python ASTとcopy hashを照合。
- [v6一件診断の実worker接続](v6-worker-runtime-v2.json): 一件だけを実Dockerで起動し、FINISHED receipt・停止/回収・精算・v2 Attempt・budget close・再送時の追加起動0を確認。15件planは維持し、診断全体はOPEN/未finalize、全適格性flagはfalse。[先行driver失敗](v6-worker-runtime-v1-failure.json)も保持。

- [corpus provisioningのDGX設計レビュー](partitioned-corpus-design-dgx-v1.json): 契約前commit、原子的確定、規模ごとの系列分離を採用。親レビューで確定segment保持・commit再送・固定producer照合を具体化した。実装・実行受入とは別の設計証跡。


- [v7 corpus保存の親検証](v7-corpus-parent-v1.json): 初回126件中125成功。1件のエラーコード期待値を仕様へ直し、該当moduleの5件を再実行して成功。製品source変更はない。
- [authority image更新](authority-refresh-v7.json) / [固定source-v15](source-v15-freeze.json): 固定base・network noneでbuildし、1,844ファイルを固定。
- [v7 corpusの実コンテナ検証](v7-corpus-runtime-v1.json): 400/800/1600のcommit再送、全artifact復元、再起動後read、candidate拒否とcleanupに成功。契約/run/worker実行、移行、CI/SLO/PAC受入は含まない。

- [reportの世代表示と実DB状態検証](report-generations-parent-v1.json): 契約/baseline世代を参照本文から表示し、取消しを表示失敗へ変えていた不具合を修正。関連34件は33成功・WARNING未実装1skip。世代値の実DB/Markdown照合を追加後、該当1件成功。7状態を確認し、WARNING・配布image・全PAC受入は未完了。

- [予算80% WARNINGの実接続](budget-warning-parent-v1.json): 閉鎖時の5軸根拠を保存し、実DBでWARNING・CI終了0・JSON/Markdown・再open、遅延矛盾後の現在拒否と過去Decision不変を確認。初回75成功とテスト補正後の1成功で関連76件の未解決0。製品sourceは同一で、先行するテスト失敗も保持した。配布image・最終全回帰・全PAC受入は含まない。

- [WARNING対応image](authority-refresh-warning-v1.json) / [固定source-v16](source-v16-freeze.json) / [実DockerのWARNING経路](budget-warning-runtime-v1.json): 固定90件・217項目が1,357.875秒で全成功。旧15件HEALTHY、新30件と通常30件WARNING、保存予算根拠、JSON/Markdown、CI終了0、再起動、根拠撤回後の拒否、全停止・回収とsource不変を確認。全8状態・最終全回帰・全PACの受入は含まない。
- [容量journalとmemoryのレビュー](capacity-journal-memory-review-v1.json): spill停止だけで容量保証を成立させる案は、authorityのmemory上限と両立する証拠がなく不採用。容量上界の未実証を維持する。
- [v7移行のDGXレビュー採否](v7-migration-dgx-v1.json): 既存FK手順を再確認し、SQLite内部schema_versionの手動変更と型の暗黙変換は採用しない。移行本体の検証結果とは区別する。

- [明示v6→v7移行の親47試験](v7-migration-parent-v1.json): 15.407秒、失敗・skipなし。非空run保持、意味破損、未知版/source、rollback、旧plan拒否と既存v5/v6移行・CLI互換を確認。
- [固定旧版DBのCLI移行](v7-real-migration-v1.json): source-v16の45表46行を許可metadata以外不変で移行し、v7再open・旧planのPLAN_STALEを確認。現在版の正規v5→v6→v7連続移行も成功。合成Attemptの保存検証であり実worker受入とは分ける。
- [移行コードを収録したimage](authority-refresh-migration-v1.json) / [実OS診断](migration-image-smoke-v1.json): offline buildと22項目の既定v4 mode診断に成功。移行コードはsource固定と配布依存へ含めた。v7 corpus/runの実worker受入はこの診断に含めない。

- [以前TIMEOUTした3レーンの再実行](remaining-regression-v2.json): source-v15 / Windows、最大2並列、10件すべて成功。combined4件・Finding再検証5件・LLM監督1件、全source/clone不変と6ログhash一致。7,200秒は完走上限でSLOではなく、旧Linux source-v12の1,488成功へ合算しない。

## query-scale入力・worker・保存の接続

[query-scale-flow-v1.json](query-scale-flow-v1.json): 親レビュー後の44試験が全成功（51.228秒、source不変）。400／800／1,600件の全2,800ケースをin-process workerへ通した。実コンテナは各規模の末尾3件と対象2版の差2件が成功（13.944秒）し、保存・再読込・再送・停止・回収を確認した。全件を実コンテナで実行した結果や、authority採択・通常gen2・CI・SLOの受入ではない。

[初回通常authorityへの接続](partitioned-normal-authority-v1.json): 1,600件の採択・開始、末尾1件の固定worker結果の精算・保存・再openを確認。関連73種類の試験が成功し、失敗履歴・修正・再実行を記録した。実コンテナ全件、baseline/gen2全件受入、CLI/CI、速度改善の実測は含まない。

[分割入力の候補・通常監督接続の途中記録](partitioned-transition-progress-v1.json): 最新の保存照合・改変拒否・互換・現在性の33試験が156.449秒で全成功し、source不変。先行sourceでは初回400件の固定in-process worker実行、確定、基準の独立採択まで通過した。全比較・通常CLI800件・fresh CI・baseline更新の一連受入は中断しており、現行sourceの全件受入、Docker、性能SLO、全拡張受入の成功とはしない。親の誤判断による中断も記録している。
