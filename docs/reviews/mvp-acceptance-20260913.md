---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-13
next_review_due: 2026-10-13
---

# 後続世代とMVP受入の継続レビュー

親が実装・レビュー・検証を担当する。全32要求の受入範囲と保護7文書は維持し、外部モデルやsubagentへ作業を委任していない。

## 確認した不具合と修正

公開版の全652試験は651成功・1失敗だった。契約3の通常runはHEALTHYを保存していたが、入力実体の照合が契約2に限定され、現在CIはEVIDENCE_UNAVAILABLE、終了2となった。保存した自作合成DBでINPUT_MATERIALIZATION_UNVERIFIEDを特定した。比較・依存検査の上限変更は行わず、契約2以降を同じ厳格な保存実体照合へ接続した。

修正後は初回入力採択と後続契約の11試験が979.616秒で成功した。契約3の採択、通常CI0、入力参照の改竄拒否、再起動、根拠撤回後のCI拒否を含む。

baseline更新の固定1→2条件も、保存された期待世代から次世代を生成する形へ改修した。後続baselineを含む9試験が1068.473秒で成功した。契約3の完全な通常runから基準3を採択し、古い比較先のsourceを拒否する。更新中の保存失敗では履歴・current・冪等応答を巻き戻し、再起動後も元runの比較先と成果物を保持する。世代3だけの撤回と、世代2の撤回による依存失効を区別する。

## 実環境の時計と回復

実Docker検証は2回とも時計逆行で停止し、各回7件の実行はすべて停止・回収した。90秒の独立観測で、Docker内の実時計が約30秒ごとに合計約1.2秒戻ることを確認した。失敗DBのコピーでは、保存時刻以上の固定時計を使うと同じ操作が成功した。この原因調査を製品受入の成功へ数えない。

AuthorityRuntimeはCLOCK_ROLLBACKの厳格な拒否応答だけを対象に、要求digest・UID・再試行予定をclock-rejections配下へ保存し、2秒後に同じ要求を一度だけ再送する。authorityの時計・transaction・fresh検査・期限・予算を変更しない。記録失敗では再送せず、逆行が続く場合は元の拒否を返す。未知応答、他の拒否、probeは再試行しない。既存の隔離・回収と追加境界の14試験が成功した。

## 全回帰と実Docker検証

全試験をmodule単位で4分割するCIを追加した。既存の必須チェック名unitは集約jobとして残し、全分割の成功を必須にする。準備処理を分断せず、全件の合併が元のsuiteと一致することを2試験で確認した。660試験を106・141・206・207件に分割し、独立したソースコピーで全件成功した。各分割の実行時間は1293.944・961.929・1699.741・1030.970秒。対象ソースとログのhashを[工程証跡](../evidence/mvp-acceptance-20260913/README.md)へ保存した。その後の対象限定run、Finding管理、実行profileを含む別の固定版で691試験が全件成功した。LLMランタイム接続はさらに後の変更として別に検証する。

実Dockerの後続世代検証は183実行を終えた。基準3を含む版は387項目中386成功、1項目は期限後の回収を期限前の期待値と比較したdriver側の失敗だった。元の失敗結果を保持し、別に9項目の追加回収確認が成功した。検証CLIは実行件数と予定件数を分け、固定された拒否理由を結果へ保存する。新しいruntime lockは実buildから生成し、ソース・imageの一致と全工程の成功を区別する。

## 対象限定runの接続

[詳細仕様](../targeted-run-detail-spec.md)に、変更参照からの依存閉包、認証された準備応答、限定したplan・入力実体・比較先、通常実行と再開、未実施ControlのJSON/Markdown表示を結んだ。未知の参照は全体実行へ拡大し、限定sourceのbaseline昇格は拒否する。準備記録の改変拒否、未知参照、保存失敗の巻き戻しは実SQLiteで通過した。制約だけのrunで数値指標が空になるとFinding生成が拒否していたため修正し、関連17試験が成功した。修正後は対象限定runの8試験が105.098秒で全件成功した。[証跡](../evidence/mvp-acceptance-20260913/targeted-summary.json)にソースとログのhashを保存した。対象不一致は既存仕様どおり終了1・CI_TARGET_MISMATCH。実Docker検証はこの期待値を誤って2としていた版を9実行で中断し、全件の停止・回収とauthorityコンテナの不存在を確認した。修正版の実Docker93実行・203項目は全件成功し、停止・回収と根拠撤回後のCI拒否を確認した。

週次の全体検査を固定Dockerと製品監督CLIへ接続するGitHub Actionsを追加した。実行ごとに隔離した自作fixtureを採択し、全体30件・対象限定2件・中断回収1件を検証する。初期採択と候補評価を含む予定93実行。GitHub上のこのworkflowはまだ実行していない。

## 後続世代・対象限定DBの移行

[明示migration](../following-migration-detail-spec.md)を追加した。既知のextension/validator版の組だけを許可し、後続契約、baselineの期待世代、通常・取消し・対象限定の保存根拠を再照合する。既存移行とCLIの24試験が15.598秒で成功した。対象限定を含む31試験が145.324秒、対象限定5試験と契約3・baseline3統合1試験が1167.523秒で成功した。実Dockerの後続世代DBと対象限定DBのコピーでも、extension digest以外の行・schema・時計が不変である。読取検査の時計更新はSAVEPOINT内で破棄する。元の稼働DBは変更していない。資源時計も一時更新を戻す7試験が84.148秒で成功した。

## 期限超過後の回収

長時間の後続世代試験で、runの期限到達後に旧recovery指定が停止専用取得を正しく返し、期限前だけを想定したdriverが失敗した。driverを期限前のRECOVERY_NOT_REQUIREDと期限後のrecovery_onlyに分け、開始拒否を引き続き検査する。元の失敗を保持し、停止確認済みの1操作を正規authorityで取消し・精算した。期限超過によるbreachedは解除せず、slotと未精算を0にし、再起動後も保存receipt不変・現在CI3・対象コンテナ不存在を確認した。

## Finding管理と対象別実行profile

元runのFindingは不変に保ち、認証された管理イベントを別に追記する。着手・再検証要求・独立validator確認の入力とCASを接続した。権限世代、要求と応答のdigest、保存順序、元Findingを再照合する。新Evidenceなし、同じ対象、改変した起源、保存失敗を拒否する。管理と移行の16試験が179.616秒で成功した。実際に変更した対象の有効なEvidenceによる確認成功、再発・別処遇、表示接続はまだ未受入である。

execution profile v2ではtarget/evaluatorの組ごとにworkerとadapterを固定する。従来の固定UC-CI形式を保持し、未知・余分・重複した組は開始前に拒否する。対象間の結果混入、二種の実行器での保存と再起動を含む44試験が成功した。

## 合成UC-LLMの認証・隔離接続

[固定合成ランタイム仕様](../synthetic-guardrail-runtime-detail-spec.md)に接続範囲を定めた。対象は自作の有限ガードレールであり、学習済みモデルではない。期待ラベルを渡さず実入力を処理し、baseline-v1とdegraded-v2の二版で所定の混同行列を実測した。評価器のモデル識別子を分け、固定対象をQwenと表示しない。

初回候補の生成をmanagerに限定し、400ケースの入力実体と独立165vector校正を保存・再照合する。二段階を一つのケース操作として予約し、各stageを別Attemptで保存する。この合成処理のmodel_calls/token/従量費は0で、実モデル資源の受入へ換算しない。actor/contextの認証、反映前の校正失敗、入力改変・対象混入の拒否を3統合試験で確認した。

実workerは64 KiB上限、固定enum、独立counterを使う。初回レビューで入力長の明示検査とimageのOS/CPU検査を補正した。旧lockを保持して新imageをbuildする。二段階の全入力digestをjournalに結び、既存の排他・停止・回収を再利用する。新規と既存の33試験が成功した。実Dockerでは1段階・2段階の計2ケース、隔離、停止・回収、再配送、回収時のreceipt一致を確認した。

400ケースをOS認証・資源・Evidence・初回baselineへ結ぶ固定ソースの実行は進行中。通常比較・条件更新・Promptfoo/実モデル・両用途run・全MVPの成功として記録しない。

## 残る接続

条件差のある契約更新、UC-LLM/Promptfooの認証・資源管理、対象限定と定期起動、全checkpointと版跨ぎ回収、保持・削除、Findingの独立した修復確認は継続する。実入力400件のサイズ診断では、初回bundle 556410 bytes、通常bundle 750184 bytes、pack 902320 bytesだった。1 MiB上限内に収まるが、輸送外枠や採択・実行への接続を検証済みとはしない。

[完了監査](../mvp-completion-audit.md)、[Task](../tasks/TASK.mvp-completion-09-11-2026.md)、[Acceptance](../acceptance/AC-20260911-05.md)は、in_progress / draft / release_gate=no_goを維持する。


## 400ケースで判明した不具合と保持の接続

実Docker400ケース・600stageは全操作の停止・精算とcleanupが成立し、worker観測の件数はTP196/FN4/FP4/TN196だった。二段階の時間帯を同じケース時間で記録したため、集計はSTAGE_ORDERを示しHOLDとなった。元のHOLDを保持し、実測stage時刻を使う修正版で再実行中である。

SQLiteの400ケースseedでは初回baseline採択まで成立したが、比較候補3試験はDOCUMENT_SIZEで失敗した。分割した各節の完全refを持つ保存rootに候補refを合わせ、採択履歴にも同じ規則を適用した。時刻・oracle参照の19試験と、分割root・既存契約の14試験は成功した。固定source36の統合試験を継続する。

保持・削除とレポートの13試験が159.885秒で成功した。manager計画/operator適用、保留CAS、境界、rollback、再配送・再起動、削除後CI拒否、本文復活拒否を確認した。削除後の報告は影響ControlとREPRODUCTION_UNAVAILABLEを示す。外部コピー・物理媒体の消去確認を含まない。

修正版source36の400ケース・600段階の認証実行は全16項目が成功した。以前のHOLDを保持し、新しい実行から初回baselineを採択した。続くsource38では対象版差のEvidence開始時にbaseline文脈欠落を検出した。digest再計算へ実際の文脈を渡して修正し、source39の対象版差と通常監督を検証中。

## 修復管理・表示・性能の追加レビュー

候補レポートで採択状態を推測しないよう補正した。候補runの保存指標・Findingと通常CI利用拒否を並記し、最後にfreshなCI照会を行う。Findingの対応状態と現在の修復確認、別処遇を表示する。

再検証要求より前に結果が保存済みなら、新しい証拠としての流用を拒否する。対象のIDとControlを保持し、対象digestの変更だけを修復候補にできる。再発は確認より後に観測した実Findingへリンクする。源泉の変更・撤回・削除・版移行を含む通し検証は継続する。

同じ400ケースbundleの純粋な再束縛だけを完全入力・ソースdigestの鍵で最大2件再利用する。型の正規化で不正入力を受け入れず、返却値を分離する。現在のDB、権限、時刻、撤回、資源・証拠の照合を再利用しない。source46の関連52試験が成功した。5回の部品測定は再利用なし0.785142秒、あり0.208029秒だった。broker全体の速度や128MiB制限内の動作を証明した値ではない。

## 初回LLM移行と性能の再検証

既知の初回LLM形式の移行は保存DBのコピーだけで行い、40テーブルと全記録を保持した。extension digestだけを更新し、validator、権限、時刻、採択履歴を変更しない。未知版・起源改変・本文改変・再hashしたreceipt・未停止操作・対応外artifact・検査後の人工障害を拒否し、各コピーの全保存行を保持した。

新規の所有Dockerランタイムには評価データがないことを確認して取り込み、現在baseline利用、再起動後の再利用、同じ要求のreceipt不変性を確認した。最初のdriverは起動確認1件の許可漏れで停止し、次のdriverは異なるrequest_idの外枠比較で失敗した。どちらも保存判定を変更せず、同一要求による再配送で再確認した。

基準更新にUC-CI固定profileの前提が残っていたため、LLMは正規runの対象別profile・baseline文脈へ接続する修正を行った。UC-CI返却形との互換性を回帰試験が検出し、用途別の取得へ補正した。成功は再検証完了後に記録する。

実Docker旧新比較の速度は時間予算内の受入条件として確認する。予算は変更しない。DBの独立コピーで候補照会を測定したところ、大きなJSONの重複コピーと契約再束縛が処理の多くを占めた。認証・時刻・撤回・資源状態の検査を省かず、純粋計算の改善を検討する。

## 候補照会のコピー重複の修正

候補照会1回でdeepcopyを再帰込み1,907,350回呼ぶ性能不具合を確認した。hash生成時の候補全体の複製、validatorが生成した独立値の再複製、入れ子をコピー直後に上書きして捨てる処理を除去した。同条件のsource57では289,982回、CPU時間中央値約1.77秒になった。[計測と試験](../evidence/mvp-acceptance-20260913/copy-performance-summary.json)は同じDB起源、各版のsource hash、失敗した試験と修正後の結果を分けて記録する。

source58のresource_startは新規操作だけに限定し、現在の採択条件・manifest・固定plan・予算を検査して開始権、予約、配送意図を同一transactionへ結ぶ。再配送を新規送信許可にしない。LLM監督の中断記録がある操作は既存の現在照合・復旧経路へ戻す。60試験成功。

source59では固定client-hostをUIDごとに維持し、各要求は固定した別clientプロセスで実行する。各回の前後に設定、container ID、PID、起動時刻、再起動・OOM状態を照合する。19部品試験と実Docker初回移行・再起動6項目が成功。128MiB・0.5CPU、役割、read-only、network none、資格情報と状態volumeの境界は変更しない。

source39の通常LLM統合は11000秒でtimeoutした。source50とsource59の実Docker比較、source43の複合、source47の修復確認は未完了として扱う。全MVP受入の完了判定は保留する。

## 全段階保存と時計の修正

[追加証跡](../evidence/mvp-acceptance-20260913/completion-clock-summary.json)のsource60/61で停止・usage・全段階の同一取引保存、後段障害の全体rollback、矛盾HOLD保持、再配送、不明情報の拒否を確認した。source61の67試験が成功した。

source59の実評価では、worker時刻がhostのファイル保存時刻をわずかに上回り、監督がSTAGE_TIME_OUTSIDE_OPERATIONで停止した。独立した時計を同一の区間へ比較する実装を修正し、hostの区間は追跡情報として時計の種類を付け、workerの実時刻はauthority側の配送意図・停止観測・現在時刻で検査する。未来のworker時刻をauthorityが拒否して全体rollbackする試験を追加した。時刻の補正・丸め幅追加・期限延長は行っていない。

source50は既定予算に達してSTART_DENIEDで終了した。両失敗版は保持し、source62の新しい専用runtimeで旧新比較・採択・通常CIを検証する。初回DBコピーの移行と再起動6項目は成功したが、全体完走はまだ確認していない。

## 候補照会の追加計測

[保存行照合を保つ修正](../evidence/mvp-acceptance-20260913/candidate-performance-recheck.json)で、source65は深いコピー74,190回・CPU中央値1.367秒となった。source50から約52%短縮した。入力そのもの、型、ソース版が変われば再検証する。完了runの全保存行を毎回読み取り、権限・失効・期限の現在検査は別に実行する。cacheにはCI許可や新しいEvidenceを保存しない。

source64の69試験、source65の保存行改変・削除・失効・期限・時刻・上限の5項目と実Docker移行6項目は成功。source65の全回帰と実Dockerの予算内完走は継続中。source47の修復確認はsetup段階で失敗し、確認成功・再発へ進んでいない。要求の数値・範囲を緩めず、再試験で拒否理由を確定する。

## ソース照合の重複と試験結果の更新

source65の影響回帰107試験、source66の9境界試験が成功した。source67の16境界試験では、要求前後のソース一致、処理中変更・読取失敗のrollback、再起動までの再利用拒否を確認した。候補照会は74,190回・CPU中央値1.297秒で、source67の影響回帰は継続中。[根拠](../evidence/mvp-acceptance-20260913/candidate-performance-recheck.json)。

source62の旧Docker試験は競合を解消する正規取消しで終了し、所有コンテナ回収を確認した。source43の複合runは14000秒でtimeout。source65のDocker全件、source62の通常監督、source65の修復確認・再発は成功未確認のまま保持する。

source67の影響回帰114試験が成功した。source68の保存root参照は4境界試験に成功し、候補照会CPU中央値1.227秒、採択・履歴の回帰は継続中。source69でLLMの後続世代を専用materializationへ接続し、旧比較800件と新基準800件を保持する6構造試験と、既存10試験が成功した。通常監督から後続世代の統合を固定ソースで実行中。

source68の候補参照修正は採択・履歴・改変拒否を含む33試験が814.439秒ですべて成功した。source69の通常監督・後続世代と、source65の実Docker・修復確認を継続する。

source70では、4run×2経路の反復で発生するcacheの入替えを、共有16MiB・32件と完全JSONの可逆圧縮で修正した。初回の容量試験15件中1件の失敗を保持し、修正後の16試験が成功。候補照会は深いコピー74,190回・CPU中央値1.086秒で、source50から約62%短縮した。影響全回帰は実行中。

source65の修復確認はSOURCE_NOT_READYで停止し、保存DBコピーで内部の現在条件照合がAUTHORITY_INVALIDとなることを確認した。source71では認証済み呼出元を引き継ぎ、12境界試験が成功した。source65のDocker試験は旧400件完了後、新側720件の最後の進捗を記録してTIMEOUTとなり、所有コンテナは回収済み。source71の空runtime移行・再起動等6項目が成功し、修復確認と実Dockerの全件再検証を開始した。受入条件・予算・全MVP未受入を維持する。

source70の影響回帰55試験が842.455秒ですべて成功した。製品コードがsource71と一致する試験版source72を固定し、追加の複合Evidence削除検査を含む全回帰を開始した。4分割・同時2processで全件を検証し、未完了の分割を成功には数えない。

source69の通常LLM監督1試験が6663.097秒で成功した。続く基準2への更新も成功したが、旧800・新800の候補prepareはDOCUMENT_SIZEで失敗した。source73では文書上限1MiBを維持し、大きな節だけを256KiB・最大16断片へ分割する。実800件候補を含む16試験が成功し、修正版で後続世代の全件採択を再検証している。失敗結果を保持し、MVPは未受入のままとする。

source73の全838試験を開始したため、重複するsource72の試験は実行主体と子2processを確認して中止した。旧版の途中結果を全回帰成功には数えず、ログと中止記録を保持する。修正版は全件を省略せず検証する。CI検証jobの上限は実測に合わせ300分とし、製品のfull5400秒は維持する。

source73の影響回帰70試験が1222.036秒ですべて成功した。候補照会は深いコピー74,190回・CPU中央値1.203秒。約1.93MBの移行情報だけを分割し、約0.77MBの旧・新実行データは従来形式を保つ。全838試験、実Dockerの比較・通常CLI、後続世代と複合runは引き続き実行中。

## 複合CIと件数表示の追加レビュー

source71の複合統合は4試験・7009.514秒で1 error。UC-CI30件とUC-LLM800件は実行されたが、最初の確定応答をAdoptionStoreが拒否した。残りの欠損/取消し・役割/起源・保存rollbackは成功した。保存応答が現在CI権限を返していたため、確定はci_eligible=false、現在照会だけが固定組込み経路で権限を返すよう修正した。4境界試験の修正前4 errorと修正後成功を保持する。拡張実装の偽装とソース変更境界も追加して検証中。

GAH-R09の件数はaggregateへ保存されていたが、要約は約分後の率だけだった。同じDecisionに結ばれた集計の限定取得と件数表示を追加し、候補/通常レポートと複合境界の16試験が成功した。実SQLiteの欠損・改変・削除後取得・再起動との回帰を進める。

根拠付き除外は現行NormalizedResult/評価契約/集計に未実装である。監査の「除外を実装済み」と読める記載を訂正し、GAH-R07/R09の残件へ明記した。正本の要求・受入条件・初期方針は変更していない。source73の838試験全回帰は修正前の複合CIを含むため、所有するcoordinatorと子2つだけを停止し、部分ログを中止証跡として保持する。単独LLMの後続世代と実Docker新旧比較は固定source73で継続する。

## 最小件数の追加検証

固定20件を正規化から集計へ通した試験で、総ERRORへ計数されたMutation失敗がmutation_errorの内訳から漏れる不具合を再現した。修正後はTP8/FN2/FP1/TN9、K8/S1/N1/ERROR10を保持し、検出率8/10・見逃し率2/10・FPR1/10・Mutation Score8/10を返す。必須欠損を残して合格にしない。二段階と回復した再試行の二重計数も拒否し、関連38試験が28.597秒で成功した。根拠付き除外は別の未実装残件として保持する。

保存集計の実SQLite取得・改変拒否・削除後取得拒否を含む39試験は613.446秒、複合CIとソース固定の12試験は0.231秒で成功した。source74をbuild・固定し、複合全件の再検証を開始した。完了・失敗どちらの試験でもcleanup前に私有DB backupを残す。

## 未成立Mutationの独立審査

[詳細仕様](../mutation-review-detail-spec.md)と[source75の証拠](../evidence/mvp-acceptance-20260913/mutation-review-summary.json)へ、独立validatorの根拠確認・manager承認・現在照会を接続した。自己申告の同等性や単なるtimeoutを除外しない。元のERROR・必須欠損・Decision・receiptを保持し、EXCLUDED/EXCLUSION_PENDINGと残るMutation ERRORを別表示する。

境界7試験、CLI8試験、表示/計数24試験、保存元補強20試験が成功し、実SQLiteの失敗runから審査・表示・再起動・撤回・削除を確認する4試験が306.928秒で成功した。初期の試験fixtureにあったdigest未設定と外部キー親run未登録の各7 errorは検証scriptの失敗として保持し、制約を無効化せず正規Evidence APIで修正した。現在の完成主張に読み替えない。

同一runで未審査ERRORと一件だけの承認済み除外を併存させる追加ケースを最終全回帰に含める。source75実装を保持し、全MVP受入数0/no_goはまだ維持する。

[最小LLMシナリオの保存実測](../evidence/mvp-acceptance-20260913/llm-minimum-scenario-summary.json)を3runのaggregate/receipt hashへ結び付けた。source71で要検知200・正常200を評価し、正常版TP196/FN4/FP4/TN196、劣化版TP184/FN16/FP2/TN198、修復版TP196/FN4/FP4/TN196を確認した。判定は順にHEALTHY、DEGRADED、HEALTHY。1variant400ケース・600段階を区別し、SQLite結果をOS実行の証拠にはしない。

## 通常監督・配布・回収の追加補正

[記録](../evidence/mvp-acceptance-20260913/runtime-supervisor-recheck.json)。source73bの旧400/新800と採択は成功したが、逐次の通常CLIは638件を停止・精算した時点で5400秒の期限に達した。driverの6500秒timeoutも保持する。driverは回収成功を報告したが、独立照合でclient2個の残存を確認した。親の古い一覧だけを回収していたため、最新deploymentを照合し、失敗した回収を保持しつつ他の回収も試み、残存があれば拒否するよう補正した。24試験と実際の残存回収を確認した。

source78では通常LLMのworkerを契約上限内で並列化し、権限・owner・保存はcoordinatorに保つ。停止・精算済み操作の不要な外部回収も除いた。並列試験の過剰な順序仮定による1失敗を保持し、確定的な故障条件へ直した8試験が成功。mutation_reviews/terminationの配布とソース固定漏れも修正し、実Dockerのimport・digest・審査3操作を確認した。

実Docker再検証は採択済みauthority73とsupervisor78の版を別々に固定する。旧runを再実行せず、停止・精算の現在検査を経て取消しを確定し、新しいrun IDで800件を実行する。driverの存在しないstart API呼出しによる準備失敗は保持し、prepare APIに補正した別driverへ続く。全865試験の固定source76と、その後の変更のsource78影響回帰を継続する。

[複合run4試験](../evidence/mvp-acceptance-20260913/combined-revalidation-74-summary.json)は6008.359秒で成功。全MVP受入は残る検証結果が揃ってから判断する。

## 保存解析と旧DB起動の補正

source78の監督・配布・回収・独立審査の影響52試験は1504.783秒で成功した。通常開始のCPU測定では、既存の照合に加えて保存JSONの反復解析も負荷になっていた。source79は小さい資源JSONを最大64件、Evidenceの厳格解析済みmarkerを最大128件に制限して再利用し、本文hash・現在費用・停止状態・独立出力の検査を保持する。45試験40.911秒が成功したが、通常800件の予算内完走は別に検証する。

source79の初回DB移行は40tableを保持したが、既定factoryがCONFIG_MISMATCHで起動を拒否した。移行helperに起動確認が欠けていた。既知source73と現行の方針検証・policy・bootstrapは同一で、adoption.pyの変更は複合CIのdispatchだけだった。source80は既知v4 validatorを保存済み履歴の互換対象とし、未知版・extension不一致・未採択の旧提案を拒否する。期限・失効・権限の検査は保持する。22試験、実初回DBの公式移行、既定factoryの現在利用、実Docker取込み・再起動・receipt不変が成功した。旧400/新800比較と通常800は同じ固定source80で実行中。詳細は[互換性の証跡](../evidence/mvp-acceptance-20260913/runtime-compatibility-80-summary.json)へ記録する。

source76の全回帰はshard0/1/3の613件が成功した。旧版の未完了shard2は所有プロセスを確認して停止し、停止理由・部分結果を保存した。source80で全898試験を4分割して実行する。製品の並列上限とCPU・メモリ・full5400秒は変更しない。
