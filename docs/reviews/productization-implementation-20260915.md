---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-16
next_review_due: 2026-10-16
---

# 拡張実装の監督レビュー

[実装Task](../tasks/TASK.productization-implementation-09-15-2026.md)のレビュー記録。作業は継続中であり、本文の局所検査は全14製品受入を意味しない。

## 分担

Lunaはgpt-5.6-lunaで性能/CI、導入運用、実案件評価を分担する。親が所有pathを指定し、共有ファイルの他者変更を戻さないこと、製品実行と合成テストを区別することを指示した。親は共通wire・journal・Schema・結合・コードレビューと検証を担当。DGX Qwenはユーザー指定の既存接続を用いた局所レビューのみ。

## DGX仕様レビューの処遇

モデルqwen3.8-flash-next、入力4,341/出力1,279 token、finish=stop。入力は共通拡張仕様だけ。応答全体を根拠なしに採用せず以下を判断した。

| ID | 提案 | 親の判断 |
|---|---|---|
| D01 | 補助JSONへ製品gateを併記/ネストする | 不採用。既存10 field契約を崩す。結果はresult_refから参照し現在CIは従来経路だけ |
| D02 | 未知版/fieldとpayloadを閉じたvalidatorで拒否する | 既定要件として実装。ただし応答にpayloadがあるという指摘は誤読 |
| D03 | request_id=nullをcommand+digestで再配送する | 不採用。未認証・ID欠損の操作は開始しない。principalを失った冪等性へ緩めない |
| D04 | journalのdigestと元ファイルraw digestを比較する | 不採用。canonical artifactと元ファイルは異なる参照。snapshot内で別々に照合する |
| D05 | 固定reason一覧を明記する | 採用。Python定数とSchemaに閉じた集合を定義。INCOMPLETEは必ずresult_ref=nullという提案は不採用、保存済み根拠は保持する |

## DGX共通コードレビューの処遇

入力はproductization.pyとproductization_journal.pyだけ。入力4,324/出力1,800 token、finish=lengthで応答は途中まで。完全な独立レビューや最終版のレビュー証明にはしない。

- `_now`のNone未検査という指摘は不採用。照会対象コードに既に`last is None or ...`があり、短絡評価で参照を防いでいる。
- docstring直後のfuture importへの懸念は不採用。現コードは正しい位置にある。
- Path.is_relative_toを文字列startswithへ替える提案は不採用。対象Pythonは3.11以上で、文字列prefixはpath境界の比較にならない。
- decode_documentがlist/Noneを返すという仮定は既存実装と不一致。wireによりobjectを要求し、直接メモリ入力にも共通検査を適用する。

## DGX履歴一覧レビューの処遇

履歴一覧の局所レビューは、最初の要求で本文が返らず（finish=length、1,500 reasoning token）、保存処理も本文Noneを受けて失敗した。これをレビュー完了には数えない。既存の非思考モード設定で再依頼し、入力2,756/出力727 token、finish=stopの本文を取得した。

DGXはvalidate_responseにschema_versionがないと指摘したが、入力コードのfieldsには先頭から存在していたため不採用。実SQLite応答を通すCLI試験も成功している。戻り値の辞書展開とstore属性への懸念は具体的な不具合ではなく、既存AdoptionStoreの属性と関数の戻り型を親が確認した。反復した誤指摘を実装修正や受入根拠にしない。

## 親レビュー

| ID | 実装中の指摘 | 処遇 |
|---|---|---|
| I01 | 運用部が共通journalを再実装していた | 共通OperationJournalへ統合済み。各変更操作の外部commit回収は接続ごとに検収 |
| I02 | journal主体にgetpass.getuser由来の名前を使用 | OS token UID/SIDかbrokerの認証結果へ変更を指示。unknown主体で更新を開始しない |
| I03 | authority応答のaction/request_id欠損を許容 | action/request_id/kind/ci_eligibleの欠落拒否を確認 |
| I04 | 確定した権限拒否を通信不明と同じ終了2へ変換 | 確定拒否は終了1、未観測/通信不明は終了2へ分離するよう指示 |
| I05 | 既存pilot専用採択APIがない | validator検証とmanager採択を別actionで追加し、metadata採択を製品run開始権限にしない方針で実装中 |

## 追加の結合レビュー

| ID | 指摘・対照例 | 処遇 |
|---|---|---|
| I06 | 偶数観測の中央値を整数切捨てで評価 | pilotをFractionへ修正、親の反例試験を保持 |
| I07 | summaryの自己申告だけで工数改善・LLM受入へ進める | 個別観測、集合照合、全資源指標を必須化。欠測Criticalを0にしない |
| I08 | CI fallbackが各moduleの比率の中央値だった | module時間中央値の中央値 / test件数中央値の中央値へ修正。100秒/1件・2秒/3件・9秒/100件の対照試験で固定 |
| I09 | 計測のために同じtestを再実行し得る | 一度のmodule実行から履歴と結果を取得し、skip/expectedFailure/準備失敗も成功履歴へ入れない |
| I10 | CI観測のsource/run/profile混同 | 計画profileと実測profileを分離し、全laneのsource・run/attempt・module・件数を集約時に照合 |
| I11 | Dockerコマンドの存在だけで稼働を判定、容量は256MiBだけ | 実daemon観測と残書込上界・予約を要求するようLunaへ補正指示、運用部の最終検収待ち |
| I12 | DB版の正式read APIがなくreadyが不明のまま | authority_diagnosticsを追加し、版の一致・失効・fresh readを実SQLiteで検証 |
| I13 | 履歴一覧の上限・cursor未接続 | 固定run_catalog_listとgah_run listを追加。状態変更、別主体、期限、改変、本文非展開を実SQLiteで検証 |
| I14 | 権限拒否reasonが共通enumになくIO_ERRORへ落ちる | AUTHORITY_DENIED/REVOKEDとSTALE_OR_INVALIDATEDをPythonと両Schemaへ追加 |

## 追加の親反例と修正

| ID | 具体的な問題 | 処遇 |
|---|---|---|
| I15 | query recipeを通常runやquickstart全体の計測として扱う | candidate/current_ci/reportをqueryに限定し、架空run_report actionを既存build_reportへ接続 |
| I16 | bool/floatをFractionへ暗黙変換、CPU/RSS欠測を保存不能 | 厳密整数と欠測nullに変更。親の反例4件を保持し、未観測の全子CPUをlocal CPUで代用しない |
| I17 | CLI measureが実測せず一律停止 | 既存AuthorityRuntimeの固定照会へ接続するよう差戻し。wall観測を保存し、全子CPU/RSS未観測はINCOMPLETEとして保持 |
| I18 | Windows PATH/SystemRoot由来の実行fileを信頼、WSLアプリ版でWSL2と判定 | OS API由来の固定system directoryと実engine情報へ補正指示。日本語/UTF-16出力を対照にする |
| I19 | setup参照保存先がkind/idだけで別source版と衝突 | digestを含む保存pathと旧参照保持へ変更指示 |
| I20 | 保持applyのcommit後応答消失で再操作のおそれ | 正規run/実SQLiteによる親試験3件成功。同request回収・tombstone一件・元receipt保持を確認 |
| I21 | 長い結合試験中にsourceが変更されEXTENSION_INVALID | 検査を無効化せず、hash固定コピーで試験を再実行して成功。ライブ版の失敗ログも保持 |
| I22 | pilot statusがjournalの過去結果を使いfresh応答を捨てる | currentを毎回照会して状態artifactを保存するよう差戻し、最終検収待ち |
| I23 | 個別観測の出所未確認でもCLIがpac_status=PASSを生成 | 算術結果を保持しても未検証の受入をINCONCLUSIVEへ留めるよう差戻し、最終検収待ち |
| I24 | pilotの不明操作を永久確定、入力digestからruntimeやreport metadataが漏れる | 同request回収、全入力binding、不正IDの暗黙置換禁止、client lock/closeを追加指示 |
| I25 | bundle取得中のrun状態変更、出力途中の失敗 | 前後再照合と不完全出力保持を実装。親8件でmetadata除外・容量・権限・再配送等を確認 |

## DGX bundleレビューの処遇

入力はoperations_bundle.py/run_diagnostics.pyのみ、4,550入力/1,500出力token、finish=lengthで途中出力。レビュー完成として数えない。entry_countがデータentry数かmanifest込みか曖昧との指摘は、file_countへの改名と仕様追記に採用した。manifestを自身のentriesへ含める提案は自己digest参照になるため不採用。固定2ファイルに対する上限比較は正しく、manifest分を除外する提案は不採用。必須checked_atや削除時tombstone_refのNoneを許容すべきとの懸念も不採用、型不成立時は拒否する。

## setup・CLI接続の追加レビュー

| ID | 具体的な問題 | 処遇 |
|---|---|---|
| I26 | 各CLIが異なるoperation_lockキーを使い同じdeploymentを排他しない | 全入口をdeployment/supervisorへ統一。CI/reportもcleanup後にだけ結果を公開する |
| I27 | setupがgen1 baselineまでしか作らず、次の通常runを開始できない | 固定gen2候補の旧/新評価・独立検証・採択を導入へ追加。実SQLiteで通常30件とfresh CIまで接続確認 |
| I28 | baseline確定応答の消失、開始後の終刻欠損 | 初回15/400件の全範囲と同operation回収を試験。終刻の補完・別operation再送を禁止 |
| I29 | bundle再配送の入力digestがruntime identityを含まない | pathと固定prefix/imageへ結合、別runtime再利用拒否の親試験を追加 |
| I30 | benchmarkの不明intentをNOT_STARTEDと断定、journal確定前のartifact回収がない | OPERATION_UNKNOWN保持と完了receipt照合をLunaへ差戻し。測定を繰り返して埋めない |
| I31 | setup管理plan自体の採択と、製品authorityの採択を同一視 | 固定計画の管理保存とpolicy/contract/baseline/candidateの実認証による採択を仕様で分離 |
| I32 | ready doctorのbaseline field集合比較がsetのsetとなり常にTypeError | 親が検出し、実baseline応答を通す対照試験とともにLunaへ修正指示 |

## DGX setupレビューの処遇

tools/setup_apply.py/setup_baseline.pyの9,030入力/936出力token、33.266秒、finish=stopのレビューを取得した。fresh currentをcheckpointの過去応答に置き換える提案は失効検知を弱めるため不採用。candidate_refを自分のdigest対象へ追加する提案も自己参照になるため不採用。evidence_finalizeが非同期にbaselineを採択するとした指摘は実APIと異なり、実装ではEvidence確定後のbaseline_propose/validate/adoptを別途完了してからcurrentを読むため不採用。親の実SQLite試験で正常経路と同setup再配送を確認した。モデルレビューを独立認証や受入の証拠にしない。

## 現時点の検証

親の共通契約16件、journal7件、pilot反例4件、CI履歴結合13件の計40件が成功。診断authority3件・既存認証/配布17件の20件、履歴一覧5件の各試験も成功。journalのWindows試験では作成用SQLite接続をcontext managerだけで閉じたつもりになっていた試験コードを明示closeへ修正した。製品のSQLite接続とは別の試験資源の問題であり、修正後は全7件成功。

局所レビューの入力hash・モデルreceiptは作業領域に保存し、実装が安定した後の最終検査は別の対象hashで記録する。実案件・性能SLO・両OSの導入受入は未実施。

## 監督側の実接続・移行再レビュー

| ID | 問題 | 処遇と実検証 |
|---|---|---|
| I33 | 初回実行の回収でreceiptのbinding照合前に停止を認定 | journal/epoch/entry/imageと停止/cleanup/隔離を先に照合。別runのreceiptと終刻欠損の反例3件が成功 |
| I34 | gen2契約はbaseline系列名を含まずready照会が不成立 | runtime metadata v2へbaseline_series_id保存。実baseline_currentとの一致/別系列/別refを含むdoctor31件で確認 |
| I35 | setupが容量profileをdoctorへ渡さず必ずUNKNOWN | 固定sample用予算refを保存して渡したが、推定を上界としてPASSにする欠陥を再レビューで検出。予算と検証済み上界を分離し、未実証はUNKNOWNへ修正 |
| I36 | 移行previewが唯一のsnapshotをdry-runで更新 | 元形式の不変backupとdry-runを分離し、apply後の元byte保持を反例で確認 |
| I37 | 移行後DBの状態一致だけで当該requestの完了を合成 | 保存済みcommit receipt限定回収へ変更。receipt前中断はUNKNOWN、同request再実行0回を確認 |
| I38 | 移行journal入力がplan pathだけ、要求refが空 | plan内容ref・実装digest・要求本文snapshotを追加。計画入替えと実装変更を拒否 |
| I39 | 実CLIのtime.time小数を厳密整数clockへ渡し開始前停止 | setup入口でUTC整数秒へ正規化。小数clockの境界試験を追加し、7件が成功 |

修正後の移行/診断/既存移行/配布依存は71件成功。固定sourceのsetup統合45件は504.004秒、LLM初回400・候補旧400・新800の計2400段階は2011.213秒で成功した。後者は実SQLiteと合成runnerの試験であり、実Dockerの30分導入目標を証明しない。目標未達の測定を隠さず保持する。最新のCLI関連51件とsetup境界7件は別時点で重複を含むため総件数として加算しない。

実Docker初回試行のIO_ERRORは監督用PowerShellの単一文字列へのindex指定によるpath誤りであり、製品開始前の呼出失敗として区別する。続くCLOCK_UNAVAILABLEは上記I39の製品不具合で、元source/planを保持して修正版を別source snapshot上で再検証している。

## DGX移行レビューの処遇

移行の5関数を2,551入力/732出力token、22.028秒、finish=stopでレビューした。os未importという指摘は、抜粋外のmodule冒頭にimportがあるため不採用。result_ref=Noneという指摘は当該回収処理でNone参照のTypeErrorを捕捉してUNKNOWNを返すため、完了合成には到達しない。共通result契約自体はCOMPLETEDのnull参照を禁止していないため不採用。journal=Noneでfinallyへ到達するという指摘は、初期化失敗時に外側tryからreturnしており後続try/finallyへ入らないため不採用。抜粋レビューの誤指摘として記録し、機械的な修正を加えない。

## 性能と時計の追加実測

固定CI setup60件のcProfileでは222.349秒、deepcopy約76,157,597呼出しを観測した。純粋fixture再構築とbaseline/candidate検証が重複しており、権限・世代・撤回をfreshに照合する条件を維持した最適化を検討する。profiler負荷と実Docker試験の同時負荷を含むため、製品SLOの測定には使わない。

実Dockerの初回operationはbrokerのintended_at=1789467053、stopped_at/settled_at=1789467054に対しhostのfinished_at=1789467055だった。既存の厳密な順序検査がOPERATION_TIME_MISMATCHを返したことを確認した。Windows hostとLinux brokerの整数秒を同一精度として扱わず、開始/終了を実authorityのfresh診断clockで観測する接続へ修正済み。元時刻の補完・停止時刻への丸め・許容値の拡大は行わない。

容量算定のLuna再レビューでは、32文書/段階・SQLite係数2・固定余裕は保守的な予算仮定であり、保存総量の強制上限を証明していないと確認した。この算定と診断結果をPR10の容量保証や性能受入のPASS根拠にしない。文書種別ごとの最大量の積算とDB/Docker保存量の検証を開発残件として維持する。

## 時計と計測の親検収

I40: authority時計の9field厳密応答・request ID・非CI・逆行を検査し、setupと通常runのSupervisorへ注入した。時計・whole-run・setup境界・CLI cleanupの35件が成功。I41: whole-run計測は既存run/status/checkpointを拒否し、同じrunの再表示を新規反復へ数えない。cleanupまで同じdeployment lockを保持する。全子資源計数は未実装であり、wallだけの観測はSLO証拠にしない。

DGX時計レビューは初回1,098入力/1,200出力tokenで打切り。実際のrequire_uint契約を補足した再レビューは1,011入力/129出力、finish=stopで確定欠陥なしだった。型の仮定に基づく初回指摘と、完了していない応答を検収成功として扱わない。最終的な時計順序と認証は既存authorityが検証する。

I42: 固定packの純粋生成だけをsource・固定関数・全入力へ結ぶ可逆cacheへ移した。返り値は毎回新しく復元し、manifest/calibration/contextと認証検査を省略しない。専用反例を含む32件成功。固定60件の比較ではdeepcopy 76,157,597→60,243,193（約20.9%減）、wall 222.349→208.431秒（約6.3%減）。profilerと並行負荷を含む局所比較であり、PR06の性能SLO受入ではない。

## 最終差分の反例検査

I43: 同一(warmness, iteration)を別iteration_idへ変えると性能比較の予定件数を満たせた。各series内で組を一意にし、cold/warmそれぞれ1..Nの完全な番号集合を要求する。measureでも型と上限を実行前に検査する。23件成功。iteration_idは採取ラベルで、独立した採取・全履歴の証明そのものではない。

I44: 実Dockerでsetup60件・通常30件・停止/cleanupが成立したが、setup生成のCI要求はCI_TARGET_MISMATCH、reportはREPORT_BINDING_MISMATCHとなった。契約・baseline・対象・use_casesは一致し、manifest refだけが異なる。setupと通常Supervisorでprepareのrequest IDが違い、別時刻に再生成していたためだった。通常run requestのcanonical hashから同一のprepare IDを作り、同一operator/本文/contextに結ぶ既存authorityの保存responseを再利用する。新しいwire key、時刻補完、元のCI要求の書換えは導入しない。時刻を進める反例を含む最終差分93件が成功。clientを閉じて2秒後に別clientで通常runする実Docker90件も現在CI・レポートまで成功した。

DGXのprepare共有レビューは1,978入力/176出力token、finish=stop。Pythonの`not in`が構文エラーという指摘は、標準構文であり実行済みのため不採用。fresh actionのNone応答混入という指摘も、直前の`replay is not None`と`STORAGE_CORRUPT`検査があるため不採用。抜粋からの仮定を実行証拠にせず、原コードと反例で親が採否を判定した。

I45: setupのsource manifestへ通常CLI・CI/report・両Supervisor・Checkpointを加え、prepare IDの計算や実行境界を変更した古いplanを継続させない。1,110件の回帰はsource-v4に固定し、後続の小集合cache・反復番号・prepare共有はsource-v5の93件と実Dockerで区別して検査した。

I46: 全件回帰用コピーに既存の入力fixture3ファイルが欠け、12件がFileNotFoundErrorとなった。元の失敗を保持し、同一source bytesへ不足fixtureだけを補った専用コピーで12件すべて成功。初回の再試験selector生成にも誤りがあり、製品未実行のloaderエラーとして別に保持した。

I47: 性能分冊の「実最適化未実装」はpure pack/bind cache実装後の状態と一致しなかった。Lunaの最終scopeレビューを親が確認し、純粋生成cacheの実装済み、fresh query cacheの未実装、全資源計数・製品SLO採択の未実装を分けた。固定60件の最終deepcopyは48,748,902呼出し、wall260.625秒。呼出し削減を観測し、wallの速度向上は未実証として保持する。

I48: 容量強制の次案も親が差戻した。通常runのwriterが別経路、SQLite rollback journalと一時領域がDB上限の外、Docker daemon側metadataが未観測、ENOSPC後の停止証拠用reserveがないため、現案では残り書込みの総量上界を証明できない。Lunaのコード根拠付き再レビューで確認した。DGXの詳細照会は50秒で時間切れ、論点を絞った再照会は136入力/81出力token・finish=stopで同じ不足を指摘した。採用したのは不足の保持であり、モデル回答からbound_verified=trueを作らない。

I49: 資源計測の次案ではDocker statsの時系列maxを真の瞬間RSS最大と同一視せず、cgroup memory.peakとも分離するよう親が修正指示した。親cgroupと子PIDのCPU/IOの二重計数を禁止し、Windows host側harnessも計数対象から落とさない。Lunaは設計メモを修正した。probe・全子資源計数は未実装であり、設計レビューを性能受入へ換算しない。

I50: 合成LLM runnerの12失敗event（11 test method）は固定copyにdatasetsの自作packがないことによるものだった。元source bytesに不足入力だけを補った専用copyで、外部通信をmockした19件すべて成功。I46と合わせて不足は4ファイル。原本の失敗結果を保持し、2組31試行で23 methodの失敗を解消した。

I51: 固定copyを手書きのファイル種別リストからGitの管理対象・未ignoreファイル全件の収録へ改めた。Luna実装を親がレビューし、path逸脱・既存先の上書き拒否・必須4入力・原本/コピーのhash照合後だけmanifestを保存する境界を確認。1,583ファイルの実copyと既存先への再実行拒否も成功した。これは検証準備の改善であり、製品の容量保証ではない。

全12laneの完走後、元計画digest・予定/実行1,110件・source不変・ログhash・補完のtest IDを親が機械照合し、未解決0件の局所集約を保存した。最終版のdiscoveryは1,117件。追加差分93件、固定実Docker90件、単一source全件実行、全14PAC受入を区別する。


## 継続実装の監督記録

- I52: 新規配布先へ固定base取得・3image構築をまとめた。既存checkoutのlockを変更しない。DGX指摘のリンク検査とコピー境界の明示を採用し、CLI実行ファイルdigestをimage層と混同した指摘、Pythonパッケージ解決を仮定した指摘は、固定builderのコードと一致せず不採用とした。
- I53: Lunaの容量部品へPOSIX directory fsyncと単一process共有instance前提を追記させた。毎回の総量確認は全ファイル走査を伴うため、既存通常runへ無条件で組み込まず、Checkpointの明示予算接続だけを追加した。論理bytes制限をDocker/物理容量の保証へ昇格しない。
- I54: 資源部品がsampled RSSを持つだけでSLO有効を返せる条件を差し戻した。取得完全性とSLOを分離し、memory.current、RSS、cgroup memory peakを混同しない。whole-run副証跡にはcleanup後の欠測とworker未接続を記録する。
- I55: offline importを共通CLIとjournalへ接続。Lunaの独立レビューから、明示空/不正request ID、workspace外出力、image lockの未知schema版の3件を修正した。root/invokeは信頼された呼出側のDIで、CLIに任意source rootやbuilderを指定する経路はない。preflightで出力先を確保できない場合はstdout理由のみ、確保後の失敗はincomplete記録を保存する。

検査準備の失敗も保持する。imageテスト作成時のstdin指定ミスとCheckpoint単独テストのimport準備不足は、製品実行前のloader errorだった。pilot CLI試験では偽のCLI時刻と実journal時刻の不一致が生じ、両テスト時計を揃えた。製品側の時刻照合は緩めていない。providerの開発中検査で2件のdict参照誤りが出たため、snapshotのmapping契約に合わせて修正を要求した。後続の検証記録と混ぜずに扱う。


I56（2026-09-16）: 実Dockerでclientの空io.statが全payloadの拒否へ波及した。I/Oだけをnullへ限定し、CPU/メモリを保存するよう親が修正。最終固定版116件は114成功・2skip。実Docker再観測でCGROUP_PAYLOAD_INVALIDがなく、STATS_UNAVAILABLEとして欠測を保持することを確認した。[継続証跡](../evidence/productization-continuation-20260916/README.md)へ結ぶ。
