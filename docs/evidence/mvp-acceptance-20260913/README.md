---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-13
next_review_due: 2026-10-13
---

# 後続世代と受入の継続証跡

全32条件のMVP技術検収が完了し、release_gate=goとなった。[最終証跡](mvp-final-20260915-summary.json)と[要件別対応表](acceptance-map.json)を参照する。実Dockerの初回400ケース・600段階、旧400/新800比較・独立採択、通常CLI800試行、freshなCI、JSON/Markdown、再起動後不変、全精算・回収を確認し、時計同期設定を復元した。全898試験と追加CI分割9試験が成功し、907件を12ジョブへ割当済み。GitHubでの実行と短縮時間は未確認。

以下は以前の工程記録であり、当時の「実行中」を最新状態として扱わない。

[回帰結果](regression-summary.json)は、契約3の入力照合・通常CI、baseline 3への更新、時計拒否からの限定再送を含む固定ソースの全660試験の成功を記録する。106・141・206・207件の4分割を独立コピーで実行し、各ログの終了と成功件数を照合した。対象ソースのhash、ログのhash、保護7文書のhashを保存する。

この後に追加した対象限定runの接続は660件の結果に含めない。[対象限定の8試験](targeted-summary.json)は105.098秒で全件成功した。実SQLiteと固定合成runnerで、2件の実行、再開、未実施範囲の表示、全体CIへの流用拒否、baseline昇格拒否を確認した。[実Dockerの93実行・203項目](targeted-runtime-summary.json)は全件成功した。全件の停止・回収、未実施範囲の表示、全体CIへの流用拒否、撤回後のCI拒否を確認した。

[実DBコピーの移行](migration-summary.json)では、後続世代と対象限定の両方について、schema・保存行・validator・時計を保持し、extension digestだけを更新した。元の稼働DBは変更していない。契約3・baseline3の6試験と移行31試験が成功した。資源時計の一時更新も戻す追加修正の7試験も成功した。

後続世代Dockerの長時間試験では期限超過後の停止専用取得が正しく成立し、期限前の拒否だけを期待したdriverが失敗した。失敗結果を保持し、保存済み停止証拠に基づく取消し・精算・再起動後のCI3を別に確認した。全MVP受入は未完了である。

[継続レビュー](../../reviews/mvp-acceptance-20260913.md)と[完了監査](../../mvp-completion-audit.md)で残件を追跡する。


[固定691試験](regression-691-summary.json)は全件成功した。各分割114・149・215・213件で、対象限定、Finding管理、実行profileまでを含む。その後の[LLM接続の追加検証](llm-integration-summary.json)は33試験と実Docker2ケースが成功し、400ケースの認証付き実行は進行中である。


[400ケースの実測](llm-runtime-30-summary.json)はworkerの件数が期待値に一致し、全操作の停止・精算・cleanupが成立した。二段階の時間帯を重複して記録したため、保存AssuranceはHOLDとなった。元の結果を保持し、[段階時刻とoracle・分割候補の修正](llm-corrections-summary.json)を検証中である。

[保持・削除とレポート](retention-report-summary.json)の13試験が成功した。計画と適用の主体分離、保留・期限、rollback、再配送・再起動、削除後CI拒否、再現不能と対象Controlの表示を確認した。外部コピーや物理媒体の消去を確認した証拠ではない。

修正版の固定source36で400ケース・600段階の実Docker評価、HEALTHY、初回baseline採択、再起動後の現在利用、cleanupの16項目が成功した。[公開要約](llm-runtime-36-summary.json)にhashと適用範囲を記録する。

## Finding管理と再束縛の追加検証

[管理と表示の要約](finding-management-summary.json)に固定source44の15試験、source45の19試験、source46の52試験の成功を記録した。重複を含むため固有試験数として合算しない。実SQLiteの処遇・権限・再起動・rollback、CLI、表示、保存Evidence、純粋な再束縛cacheを対象とし、Docker受入ではない。

通常LLM・対象版差はsource39、複合runはsource43、劣化版からの修復確認と再発はsource47で検証中。これらの完了結果をまだ記録していない。

## 初回LLMのDB移行と新版ランタイム

[移行と現在利用の要約](initial-llm-migration-summary.json)に、実400ケースの保存DBコピーの移行、全保存行の保持、未知版・改変・未停止操作・対応外形式・途中障害の7否定試験を記録した。新版Dockerでもbaselineの現在利用、再起動、同一要求に対するreceipt不変性、client回収の5項目が成功した。128MiB・0.5 CPUの制限を維持する。

source39の対象版差800試行の統合試験は6501.583秒で成功した。初回seed400を含む実SQLite検証であり、OS境界は別のDocker証跡へ対応付ける。通常LLM・複合・修復確認は継続中。source50では実Dockerの旧400・新800と採択後の通常800を検証する。現状の処理時間も受入上の課題として測定中。

## 候補照会のコピー重複と実行時間

[性能不具合と検証の要約](copy-performance-summary.json)に同条件の4回計測とprofileを記録した。source50からsource57でdeepcopyの再帰呼出しは1,907,350回から289,982回へ、候補照会のCPU時間中央値は約2.88秒から1.77秒へ減った。不要なコピーを削除し、現在のDB・認可・期限・撤回の検査は維持する。

source56の50試験、source57の既存109試験、試験helperの名前衝突を修正した追加3試験が成功した。開始を同一transactionへまとめたsource58の60試験、固定clientの継続利用に関する19試験、source59の実Docker初回移行と再起動6項目も成功した。異なる範囲の重複試験を固有試験数へ合算しない。

source39の通常LLM統合は11000秒でtimeoutした。完走へ読み替えない。source50に加えsource59で旧400・新800、独立採択、通常CLI800を実行中。複合run・修復確認・再発と全32条件の受入は継続中。

## 完了caseの保存と時計の境界

[全段階保存・時計の検証](completion-clock-summary.json)にsource60の35試験、source61の67試験、source62の実Docker初回移行6項目の成功を記録した。validatorの同一取引内で停止・usage・全段階を保存し、例外時rollbackと矛盾HOLDの保持を区別する。hostとruntimeの時刻を直接比較せず、workerの実時刻はauthorityの配送・停止・現在時刻で検査する。未来観測の拒否と時計逆行の検査は維持する。

source50は既定時間予算でSTART_DENIED、source59はhostとruntimeの時計の比較により停止した。両方の所有コンテナ回収を確認した。過去の時刻や失敗結果を補正しない。source62で実Dockerの旧新比較・通常CLIと、実SQLiteの通常監督全体を検証中。

## 保存結果の再計算の削減

[継続計測と検証](candidate-performance-recheck.json)ではsource65の深いコピーが74,190回、候補照会のCPU中央値が1.367秒となった。source50の1,907,350回・2.875秒から約52%短縮した。全保存行の内容は毎回照合し、現在の権限・失効・期限をcacheしない。source64の69試験とsource65の追加5項目、実Docker移行6項目は成功。source65の全体回帰と実Docker全件実行は継続中。

source63の共有worker変更は固定lockとの不一致を検出して不採用とした。source47の修復確認は評価保存後の確認処理で終了2となり、0試験・setup失敗1件で停止した。確認直前の証拠を残すsource65の再試験を開始し、原因と結果を追跡する。全MVP未受入を維持する。

## ソース照合と最新の試験状態

source65の影響回帰107試験とsource66の境界9試験が成功した。source67は要求内の重複ソース読取りを前後2回にまとめ、16境界試験が成功した。処理中の変更・最終読取り失敗は取引をrollbackし、以後の再利用をプロセス再起動まで拒否する。同条件の候補照会は74,190回・CPU中央値1.297秒。影響回帰は継続中。計測条件とraw証跡hashは[継続計測](candidate-performance-recheck.json)へ記録する。

source62の旧Docker試験は、競合解消のため親が正規取消しを行い、所有コンテナの全回収を確認した。予算超過の結果ではない。source65のDocker全件実行を継続する。source43の複合runは14000秒でtimeoutした。source62の通常監督とsource65の修復確認・再発は未完了で、全MVP未受入を維持する。

source67の影響回帰114試験が895.839秒ですべて成功した。source68では保存本文を検証した後の候補参照を小さい保存rootから作り、同じ参照のための全本文再ハッシュを除いた。4境界試験と同条件測定が成功し、CPU中央値は1.227秒（source50の2.875秒から約57%短縮）。採択・履歴の影響回帰は継続中。

source69はLLM後続契約をCIの30件materializationへ渡す分岐を修正し、旧800・新800を維持する。新しい構造6試験と既存の後続契約10試験が成功。400件の初期化と800件の合成試験のhelperも製品のresource_start/evidence_completeへ揃え、対象数・実入力・主体分離を維持した。固定source69で通常監督、基準2、契約3、基準3の統合を開始し、実行結果はまだ受入へ昇格しない。

[最新の32受入対応表](acceptance-map.json)は、過去の関連証拠と通常LLM・複合run・修復確認・後続世代・実Docker・保持/移行・最終検証の残件を結ぶ。全条件の受入数は0、release_gate=no_goのまま維持する。

source68の候補参照修正は採択・履歴・改変拒否を含む33試験が814.439秒ですべて成功した。source69の通常監督・後続世代と、source65の実Docker・修復確認を継続する。

source70では、4run×2経路の反復で発生するcacheの入替えを、共有16MiB・32件と完全JSONの可逆圧縮で修正した。初回の容量試験15件中1件の失敗を保持し、修正後の16試験が成功。候補照会は深いコピー74,190回・CPU中央値1.086秒で、source50から約62%短縮した。影響全回帰は実行中。

source65の修復確認はSOURCE_NOT_READYで停止し、保存DBコピーで内部の現在条件照合がAUTHORITY_INVALIDとなることを確認した。source71では認証済み呼出元を引き継ぎ、12境界試験が成功した。source65のDocker試験は旧400件完了後、新側720件の最後の進捗を記録してTIMEOUTとなり、所有コンテナは回収済み。source71の空runtime移行・再起動等6項目が成功し、修復確認と実Dockerの全件再検証を開始した。受入条件・予算・全MVP未受入を維持する。

source70の影響回帰55試験が842.455秒ですべて成功した。製品コードがsource71と一致する試験版source72を固定し、追加の複合Evidence削除検査を含む全回帰を開始した。4分割・同時2processで全件を検証し、未完了の分割を成功には数えない。

[adapterと保存判定の接続](adapter-evidence-summary.json)は25試験が成功。固定出力の意味・raw追跡・保存後の再起動・未対応/不正出力・実行失敗を確認した。診断結果のci_eligible=falseを維持し、OS実行や通常CIの証明とは区別する。

[固定版の予定試験対応表](planned-test-coverage.json)で832試験と32要求を照合した。過去の対応表に記載された関連試験IDの欠落は0件。工程別の関連試験を示すが、実行成功や各条件全体の受入を意味しない。

[source62の通常LLM監督](llm-supervision-62-summary.json)は統合1試験が8807.747秒で成功した。初回400・旧400/新800の採択、通常800、CI・表示・再開・取消し・中断回収を確認した。SQLiteと合成runnerの結果であり、OS実行と修正後の固定版は別に検証する。

[非掲載・credentialの再検査](privacy-recheck-summary.json)は公開対象1439ファイルを読み取り、禁止識別子・credential pattern・読取不能・範囲外pathがいずれも0件だった。元の識別子や一致本文は出力しない。

source69の通常LLM監督1試験が6663.097秒で成功した。続く基準2への更新も成功したが、旧800・新800の候補prepareはDOCUMENT_SIZEで失敗した。source73では文書上限1MiBを維持し、大きな節だけを256KiB・最大16断片へ分割する。実800件候補を含む16試験が成功し、修正版で後続世代の全件採択を再検証している。失敗結果を保持し、MVPは未受入のままとする。

[source71の修復確認](finding-revalidation-71-summary.json)は5統合試験が5359.153秒ですべて成功し、新EvidenceによるVERIFIED、再起動、撤回・削除後の現在利用拒否、再発追跡を確認した。[実Docker比較](llm-runtime-71-summary.json)は旧400件と新744件の停止・精算後に期限へ達し、次の開始を拒否してコンテナを回収した。source73の取り込みは検証スクリプトの旧版照合値を修正した後に6項目が成功し、3workerで全件再検証を開始した。

source73の全838試験を開始したため、重複するsource72の試験は実行主体と子2processを確認して中止した。旧版の途中結果を全回帰成功には数えず、ログと中止記録を保持する。修正版は全件を省略せず検証する。CI検証jobの上限は実測に合わせ300分とし、製品のfull5400秒は維持する。

source73の影響回帰70試験が1222.036秒ですべて成功した。候補照会は深いコピー74,190回・CPU中央値1.203秒。約1.93MBの移行情報だけを分割し、約0.77MBの旧・新実行データは従来形式を保つ。全838試験、実Dockerの比較・通常CLI、後続世代と複合runは引き続き実行中。

[修正版の予定試験対応表](planned-test-coverage-73.json)は全838試験を対象とし、後続候補の分割保存も世代更新の関連試験へ含めた。過去の関連試験IDの欠落は0件。全回帰の実行結果は完了後に別途照合する。

[複合CI・件数表示の追加レビュー](combined-ci-counts-review.json)と[最小件数の分類検証](measurement-classification-summary.json)に、再現した失敗・修正・境界試験を記録する。source73全回帰は中止履歴を保持し、固定source74の複合全件を再検証中。根拠付き除外は未実装で、全MVP受入数0・no_goを維持する。

[除外審査の接続結果](mutation-review-summary.json)は、元の失敗や必須欠損を消さない独立審査の証拠である。source75の実SQLite統合4試験が成功した。ERROR/除外の同一runでの併存と最終全回帰は未完了。

[最小LLMシナリオの実測対応](llm-minimum-scenario-summary.json)はsource71の保存aggregate/receiptのhashを照合した。固定400ケース・600段階で、検出率98%→92%、見逃し率2%→8%、FPR2%→1%とDEGRADED判定を確認し、正常版・修復版のHEALTHYとも区別する。現行source76の全865試験は実行中であり、この過去版の結果だけで全MVP受入にはしない。

[複合runの再検証](combined-revalidation-74-summary.json)はsource74で4試験6008.359秒が成功した。[通常監督・配布・回収の補正](runtime-supervisor-recheck.json)では、実Dockerの逐次通常runの期限到達、client回収漏れ、モジュール配布・ソース固定漏れを記録する。修正版の並列800件と影響回帰を実行中。[最新の予定879試験](planned-test-coverage-78.json)は旧865試験を全保持する。全MVPは未受入。

[source78の影響52試験](runtime-supervisor-recheck.json)は成功し、ERRORと承認済み除外の併存も確認した。[JSON解析の再利用45試験](saved-json-reuse-79-summary.json)と[既知validator互換性22試験・実起動](runtime-compatibility-80-summary.json)を追加する。source79は移行後の起動で失敗した。source80で移行・既定factory・実Dockerの現在利用・再起動・receipt不変を確認し、比較と通常CLIを実行中。[予定898試験](planned-test-coverage-80.json)は実行結果とは区別する。全MVPは未受入。
