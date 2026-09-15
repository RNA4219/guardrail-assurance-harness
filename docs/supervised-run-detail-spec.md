---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-13
next_review_due: 2026-10-13
---

# 製品の実行監督・再開・対象限定の仕様

固定UC-CIの製品入口tools.gah_runと操作状態照会を接続した。[工程証跡](evidence/mvp-supervisor-20260912/README.md)で実Dockerとレビュー補正後の試験を区別する。以下で現在の接続範囲と未完了の拡張を区別する。全MVP受入は未完了である。

MVPの定期評価はCIスケジュールから製品CLIを呼ぶ接続とする。常駐サービスの新設や即時検知の時間保証は完了条件へ追加しない。詳細は[要求の証拠有効期間](requirements.md#8-証拠の有効期間と計画の成立条件)を正本とする。

## 製品入口と表示

CLIはpython -m tools.gah_runで、run、resume、cancel、statusの各subcommandを持つ。--runtimeに既存deployment、--requestにschema_version=1、run_id、contract_series_id、expected_contract_ref、triggerのJSONを指定する。triggerはmanual / change / scheduled_fullに限定し、未知field・role自己申告・任意shell文を拒否する。現時点は固定contract 2の全30件だけを実行する。changeも影響が未確定のため全体実行へ拡大し、UNKNOWN_IMPACT_FULL_FALLBACKと表示する。scheduler自体や対象限定実行の完成とはしない。

CLIは既存brokerを認証したoperatorとして操作する。managerの提案・採択やvalidatorの観測は固定された独立主体へ分け、任意のUIDを入力から選ばせない。期待契約と異なるcurrentを黙って使わない。

最終表示はrun ID、実施範囲、未実施範囲、時刻、資源状態、保存時Assurance、現在CIと根拠参照を示す。終了コードは既存CIの0/1/2/3を保持し、保存JSONだけで0にしない。

## 操作状態のfreshな照会

resource_operationはoperator/validatorがrun_id、operation_id、expected_manifest_refを指定して照会する。予約のdigest、元owner epoch、送信・停止・精算・解放・競合の状態、既知usageまたはnull、完全な保存entry/scenarioを返す。現在の開始許可や再送許可は返さない。

同じrequest_idでも状態を再読取りし、現在の契約根拠が失効していても保存planとの結合を照合する。unknown usageを0へ変換しない。保存予約・usageのdigest、型、時刻順序、費用、元planとの一致が壊れていれば拒否する。

既知のbaseline更新版からの明示migrationは世代1・2の履歴、元通常runの実体、validation/current/撤回を照合する。移行でEvidenceの寿命や撤回状態を変更しない。以前の版に世代2形式を混入したDBは引き続き拒否する。

## durable checkpointと二重実行の防止

run専用のcanonical directoryはdeployment/supervised/SHA256(run_id)に固定する。requestとsource lock、送信意図、応答、開始・終了、receiptを個別の不変記録へ保存する。OS lockで同一runを排他し、CLI同士の共有deployment transportも直列化する。brokerのowner_id/epochも別に検査し、ローカルlockだけでauthorityの所有権を得ない。symlink/reparse pointを保存先にしない。

checkpointにはschema版、run/manifest/plan/契約の完全参照、source lock、開始要求ID、現在owner/epoch、予定entry、operation ID、段階、実行receipt参照を保存する。ネットワーク送信・Docker開始前に意図をfsyncとatomic replaceで記録する。本文checksumは破損検知であり、authority認証の代わりではない。

各操作のrequest_idと本文を固定する。通信結果が曖昧なら同じ要求を照会し、異なるoperation IDで再送しない。baseline/契約/current/現在CIはfreshな問い合わせを行い、冪等な過去応答と混同しない。

## 再開と取消しの状態表

| 観測できた状態 | 再開処理 |
|---|---|
| 準備要求のみ・開始登録前 | resumeは同じ契約・run IDで準備とrun_beginを再照合し、開始登録を完了する |
| 未予約の予定entry | 現在開始条件とownerを検査して予約できる |
| 予約済み・送信前 | 保存要求とbrokerを照合。取消し時は予約を解放 |
| dispatch済み・実行中 | 同じ実行journalと子処理を照合し、新規実行せず停止/回収へ進める |
| 実行完了・停止確認済み | 同じreceiptから観測・精算を続行。結果を再取得するための再実行は行わない |
| 実行時刻・停止・usageの根拠欠落 | 不足を保持し、正常完了や0費用を作らない |
| terminal保存済み | 不変成果物を読み、現在CIを照会。履歴を書き換えない |
| owner期限切れかつ開始根拠失効 | resource_cancel_claimで取消し取得へ進める |

再開前にbrokerのrun_statusと保存manifest/planを照合する。正常な継続では現在の採択根拠も確認する。根拠失効で新規dispatchができなくても、停止・精算・保存読取りへの経路は維持する。

Ctrl+C、プロセス強制終了、ディスク障害、曖昧な送信結果を中断原因として扱う。既知の違反は取消しDecisionへ残す。停止未確認では終了2、停止済み取消しでは3とし、後日の精算で0へ変えない。共有brokerや他runの子処理は削除しない。

## 変更影響と定期全体実行の残る接続

changeでは、変更対象の完全参照とRegistry依存閉包からControl集合を決定する。入力が未知・欠落・不整合なら影響なしとせず、fullへ拡大するか不足で止める。依存先を取り除いた小さな集合へ縮めない。

manifestへ要求された範囲、導出した範囲、実施範囲、除外理由、未知範囲、trigger参照を固定する。範囲限定の結果はその範囲だけのCI利用に限る。consumerの期待対象・用途と一致しなければ終了0を返さない。

scheduled_fullは全Control・全必須caseを対象とする。schedulerは決まった時刻に製品入口を呼ぶtransportであり、採択・成功の権限を持たない。重複起動はrun IDとOS lock・owner/epochで検査し、前runのreceiptを今回の成功に使わない。

## 時計逆行で拒否された要求の再照会

実行clientは、厳格なCLOCK_ROLLBACK応答でauthorityのtransactionが取り消された場合だけ、同一UID・同一要求を2秒後に一度再送できる。拒否ごとに要求digest・UID・再試行予定をruntime内のclock-rejectionsへ保存する。記録できなければ再送せず、二度目も拒否ならその拒否を返す。未知応答や別の拒否を再試行へ分類しない。

authorityの時計値、元の期限・予算、fresh検査、既存の操作状態を補正しない。モデル呼出やfixture実行自体を再実行する仕組みではない。拒否記録は監督用の記録であり、採択やCI成功を証明するEvidenceではない。[継続レビュー](reviews/mvp-acceptance-20260913.md)に実時計の観測と境界試験を記録する。

## 接続完了の証拠

通常終了と否定結果、全checkpoint境界での中断、二重監督、通信再試行、owner期限、根拠撤回、停止不明・遅延精算、既知違反保持、範囲限定・未知影響・定期全体を実行する。source lock・実operation数・停止/精算・現在CIと再起動を照合する。合成runnerの部品試験と実Docker/providerの受入を分ける。

現行CLIではsource lockやdeployment/fixture版が変わったcheckpointをそのまま再開しない。版を跨ぐ自動回収は未接続で、既存authorityの停止・精算APIと該当版の証跡を用いる。raw出力やcredentialをcheckpointへ保存しない。

## 固定LLMの新規操作の原子的開始

resource_startはoperatorだけが呼び出せるfresh操作で、run_id、owner_id、operation_id、計画上のentry識別4項目、固定scenario、expected_manifest_refを受け取る。現在条件とmanifestを照合し、計画との束縛、複合予算、開始権、資源予約、配送意図を同一transactionで検査・保存する。既存operation_idはDISPATCH_NOT_PROVABLY_NEWで拒否し、再配送から過去の開始権を復活させない。後段失敗時は取引全体をrollbackする。返却値はCI許可ではない。

LLM監督は新規の未開始操作にこのAPIを使用する。開始要求、従来の予約、開始・終了checkpointまたはrunner journalがある操作では、既存の現在照合と復旧手順を使う。停止とusageの観測、段階ごとのEvidence保存はvalidatorが担当し、完了caseは下記のevidence_completeへまとめる。

製品CLIはUID別のclient-hostを維持し、その上で絶対pathと引数を固定したclientプロセスを毎要求起動する。任意コマンドをAPI入力にしない。要求の前後でUID・image・mount・network・resource設定と、container ID・PID・起動時刻・再起動数・OOM状態を照合する。処理終了時は所有するclientだけを回収し、失敗した回収対象は再試行できるよう保持する。

## validatorによる完了caseの一括保存

evidence_completeはvalidatorだけが呼び出せる不変保存操作で、run_id、operation_id、event_id、測定済みusage、attemptsを受け取る。固定UC-LLMの同一case・同一操作の全段階（1または2）を要求し、planの順番・完全な段階集合、binding、完了状態と時刻順序を検査する。欠けた段階、不明usage、停止未確認、未完了は補完しない。既存の段階保存APIも復旧・段階的観測用に維持する。

停止観測・精算と全段階の保存を同一transactionで処理し、束縛とbookを共有して各段階の既存検査を通す。SQL等の例外は全体rollback。既存の観測やAttemptとの矛盾を検出しHOLDを保存した場合は、その事実を保持しaccepted=falseを返す。再配送が新しい観測時刻・開始許可・CI許可になることはない。

LLMのcheckpointにはhostの開始・終了をsupervisor_host_utc、workerの時計をauthority_runtime_utcとして記録する。異なるOSのUTCの同期精度を仮定して両者の区間を直接比較しない。workerの観測値を変更せず、authorityの同じruntime時計による配送意図以前・停止観測以後・現在時刻より未来を拒否する。host側の時計逆行も拒否する。期限・保持期間・鮮度の計算、元のEvidence時刻を補正・延長しない。

## 完了済み保存結果の照合コスト

管理側は、同じ契約・集合・実装ソースに対する純粋な構造検査を再利用する。現在のPolicy/契約採択、認証、校正、失効、鮮度の判定は各要求で行う。契約・集合の入力は全資料で、512KiB以下の固定400〜415ケースを2件まで保持し、返却値はJSONから独立に生成する。

完了runの構造検査は、同一transactionでbound_runs・run_stateとattempts・attempt_events・evidence_events・aggregates・decisions・terminalsの全保存行を読み直し、全列・SQLite型・本文を含むfingerprintと実装ソースを照合する。成功markerは32件までとし、4MiBまたは10000行を超える場合、未完了run、より過去の時刻、行の変更時は元の構造検査を行う。保存digestだけでは再利用しない。現在のbinding・権限・撤回・期限照合は毎回実行し、markerを許可証やEvidenceへ変換しない。

ソースdigestの再利用は一つの管理要求内に限定する。最外の要求開始時と終了時に対象ソース全体を読み直し、一致を確認する。処理中の変更または最後のソース読取り失敗はEXTENSION_INVALIDとして取引全体をrollbackする。途中で生成した計算cacheの再利用も、そのプロセスを再起動するまで拒否する。通常の要求失敗でソースが一致する場合は領域を解放し、次の要求で再度照合する。DBの現在状態・権限・失効・期限の検査はこの領域に保存しない。

候補の全節を保存artifactと再生成結果へ照合した後、候補参照は正規化済みの保存rootから生成する。展開した全本文を再度分割・ハッシュする処理を避けるが、保存本文の改変・参照の差替え・不正IDを許可しない。保存rootの本文とdigestも再確認し、単に保存digestだけを返すものではない。

契約bundleとmanifestの純粋な再束縛は、二つの利用箇所で保持量16MiB・32件の上限を共有する。長いJSONは可逆圧縮し、鍵には完全な入力・ソースdigest・実装identityを含める。結果も不変の圧縮JSONとして保持し、呼出元ごとに独立した値へ戻す。保持量には鍵・結果・entry・辞書のPythonサイズを含め、計算中の一時メモリや固定module関数の参照先は含めない。上限超過は保持せず、権限やEvidenceの検査結果をcacheへ昇格しない。

## 通常LLMの並列監督

採択済みPolicyProfileのconcurrent_evaluationsに従い、1〜4件のworkerを同時実行する。authority要求、owner更新、checkpointとAttemptの保存は呼出スレッドで順に処理し、workerの停止・結果確認と精算が済んだ枠を再利用する。各操作のowner epochとhost終了時刻を個別に保持する。既存checkpoint/journalがある操作は復旧経路へ戻し、同じ実行を再送しない。処理失敗を観測した後は新規dispatchを止め、実行中workerの終了を待って従来の取消し・回収へ進む。

取消し時、authorityが停止と精算の両方を確認済みの操作は外部実行器へ再回収しない。保存済みendがあればjournalと束縛を照合し、Attemptの再配送は保持する。停止・usage未確認を成功として補完しない。

runtimeのcleanupは、別CLIによる追加を含む最新deploymentのprefix・image・各所有名を照合する。失敗した対象を記録に保持して残る対象の回収を試み、最後に所有ラベルで残存を確認する。残存時は成功を返さず、状態volumeの削除へ進まない。
