---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# 製品化運用詳細仕様（GAH-PR09〜12/14）

本書は [拡張要件](productization-requirements.md) の初回導入、診断、判定説明、
中断・保持・移行、管理AIの採択を操作仕様へ落とす設計稿である。実装・性能測定・
実案件受入はまだ開始しておらず、GAH-PR09〜12/14 と対応する受入条件は
NOT_RUN とする。拡張14条件（GAH-PAC01〜14）はすべて初期NOT_RUNであり、既存MVPの要求7文書、受入証拠、32条件の判定は変更しない。
共通の型・reason code・参照・終了値は [共通契約](productization-spec.md) に集約し、
本書では現行のcanonical digestを再利用する。非掲載指定の資産は取得・記載しない。

## 対象と不変条件

製品入口は現行の `python -m tools.gah_run`、`python -m tools.gah_ci`、
`python -m tools.gah_report` を維持する。新しい管理操作は計画上の
`python -m tools.gah_ops` 配下だけに置き、現時点では未実装と明記する。

| 現在実在するCLI | 引数と出力 | 既存終了値 |
|---|---|---|
| `gah_run` | `run/resume/cancel/status --runtime <既存deployment> --request <JSON> [--runner fixture\|guardrail]`。監督結果JSONを出力 | 結果の0/1/2/3を返す。例外・不完全は2 |
| `gah_ci` | `--runtime <既存authority> --request <ci_check JSON>`。毎回brokerへfresh照会 | 0=現在CI利用可、1=完了だが利用不可、2=照会/入力/出力障害、3=取消し |
| `gah_report` | `--runtime <既存authority> --request <ci_check JSON> [--format markdown\|json] [--candidate]`。成果物取得後にfresh CIを再照会 | reportの既存gate値を返す。表示・取得障害は2 |

上表の意味を新CLIや文書表示で上書きしない。`gah_report` の成功は表示とその時点の
CI照会の結果であり、管理操作の成功やMVP全体の受入ではない。`gah_ops` の補助応答は
共通契約の `kind=extension_operation_result` とし、
`operation_status=COMPLETED/REJECTED/INCOMPLETE/CANCELLED`、`ci_eligible=false`、
`exit_code=0/1/2/3`を持つ。0は補助操作が完了したことだけを表し、製品CI成功を表さない。
JSONの整数欄はboolを受け付けず、時刻は単調なnsとUTC epoch秒を別欄にする。
参照値は必ず `{kind,id,digest}` とし、idだけで本文や権限を解決しない。

## 初回導入の一本道（GAH-PR09）

前提ツールが導入済みの新規workspaceでは、bootstrap診断からsample準備・初回評価・
結果表示までを最初の操作5本以内にする。bootstrap診断はdeployment、authority採択、
baselineを前提にしない。以下は実装済みの入口であり、実測による製品受入は別に記録する。`setup apply` は単なるrequest生成ではなく、固定imageの
lock確認（現実装は取得済みimageを要求）、authority準備、validator検査、manager採択、初回baseline採択を
順に行い、各外部状態をjournalへ記録する。外部DB・Docker・authorityを一つのtransaction
で巻き戻せないため、部分完了は状態として残し、同じrequest_idでauthorityを照会して
回収する。曖昧なcommitを再採択や新規実行へ変換しない。

|順|コマンド|I/Oと完了条件|
|---:|---|---|
|1|`python -m tools.gah_ops doctor --phase bootstrap --workspace <新規workspace> --profile sample-ci --json`|OS/Python/Docker/WSL2/時計/容量/identityを読み取り。deployment・採択・baselineは要求しない|
|2|`python -m tools.gah_ops setup preview --workspace <workspace> --profile sample-ci --output <新規plan>`|managerが設定差分、image/authority準備、validator検査、初回baselineの計画とrefを表示|
|3|`python -m tools.gah_ops setup apply --workspace <workspace> --plan <plan>`|operatorが実行を開始し、分離されたvalidator/manager identityで準備・検査・採択を行う。`run-request.json` と `ci-request.json` を別々に出力|
|4|`python -m tools.gah_run run --setup-plan <plan>`|固定UC-CI sampleを評価し、監督の既存run結果を保存。UC-LLMは`--runner guardrail`|
|5|`python -m tools.gah_report --setup-plan <plan>`|別の`ci_check` requestで成果物とfreshな現在CIを照合し、日本語結果を表示。JSONなら`--format json`|

UC-LLMを選ぶ場合は1・2本目のprofileを`sample-llm`にする。後続runnerは完了済みplanから固定する。同じ
5本をLinux/WindowsでUC-CI/UC-LLM各1回、計4回記録する。`gah_run`のrequestと
`gah_report`の`ci_check` requestを同じJSONへ混在させない。`gah_ci`の単独照会は
この一本道の追加操作として扱わず、常に現行引数・終了値を保つ。再実行時の冪等キーは
共通契約の`(認証principal, command, request_id)`であり、`setup_id`・plan digest・
authority refは入力digestのbindingに使う。同じ入力は保存済みreceipt/request参照へ戻し、
内容違いの同じrequest_idは拒否する。既存workspace、DB、volume、証拠を暗黙に上書きしない。
## Linux/Windowsと開始前診断（GAH-PR10）

受入対象は Linux x86_64 + Docker Linux Engine と Windows 11 + WSL2 + Docker Desktop
Linux Engine。未知のOSは合格にしない。doctorには`bootstrap`と`ready`の二段階を設ける。
`bootstrap`は新規workspaceのOS/Python/engine/時計/容量/identityと入力configだけを確認し、
deployment、authority採択、baselineを要求しない。`ready`は`setup apply`後にdeployment、
image lock、authority準備、validator receipt、manager採択、初回baseline、DB版を再確認する。
setup applyはready検査が終わるまでrun-request/ci-requestを出力しない。

doctorは読み取り専用を原則とし、時計観測の一時作業領域を作る場合も専用prefix・上限・
回収結果を記録する。時刻同期、権限拡大、image取得、volume削除、DB初期化、証拠失効解除、
既存run停止はdoctorに含めない。

|phase/field/check|観測とtiming|非破壊性・理由|
|---|---|---|
|phase|`bootstrap`はworkspace、`ready`は既存runtimeとauthority|phaseを省略・混同しない。未知phaseは`INVALID_INPUT`|
|runtime/Python|bootstrapはworkspace/config、readyはdeployment存在・Python 3.11以上・source lock|開かずに読む。`RUNTIME_MISSING`/`PYTHON_UNSUPPORTED`|
|Docker/WSL/image|bootstrapはdaemon/WSL2/engine能力、readyは必要image lock/digest|起動・pullしない。`DOCKER_UNAVAILABLE`/`IMAGE_MISSING`|
|identity/permission|bootstrapは現在のOSユーザーとDocker接続権限・固定role構成の準備可否。readyは実際のOS peer credentialとmanager/operator/validatorの現在世代|未作成brokerのpeer照会をbootstrapに要求しない。role文字列を権限にしない。`AUTHORITY_DENIED`|
|capacity|free bytes、cache/evidence/bundle上限、CPU/RAM・並列上限|予約・削除しない。`CAPACITY_EXCEEDED`/`RESOURCE_LIMIT`|
|clock|wall UTC秒、monotonic ns、逆行/step、lease・期限の許容範囲|一時観測のみ。既知の逆行は`CLOCK_ROLLBACK`、観測不能は`CHECK_UNAVAILABLE`|
|binding|bootstrapはconfig/plan入力ref、readyはcontract、baseline、target、用途、採択、撤回、DB版の完全ref|保存参照を成功へ昇格しない。`BINDING_STALE`/`DB_UNSUPPORTED`|

補助resultの外枠は共通契約の10 field（`schema_version`、`kind`、`command`、`request_id`、
`operation_status`、`checked_at`、`result_ref`、`reasons`、`ci_eligible`、`exit_code`）に固定し、
doctorの各checkは`result_ref`先のpayloadへ保存する。checkは`check_id`、`required`、
`observed_elapsed_ns`、`observed_at_utc_s`、`status`、`reason_code`、`affected_scope`、`remediation_ref`を持つ。elapsedはdoctor開始から同じ単調時計で測った差分ns、UTCは整数秒。check_idは上表の固定ID、requiredはbool、statusはPASS/FAIL/UNKNOWN/NOT_APPLICABLE、reason_codeは固定codeまたは正常時null、affected_scopeは固定scopeの配列、remediation_refは実装済み手順の参照またはnull。phase上の対象外だけをNOT_APPLICABLEとし、必須checkのunknownを対象外へ落とさない。
text出力も同じreason
codeと次の操作を1行ずつ示す。既知の必須不成立は `REJECTED`/終了1、検査不能または
出力障害は `INCOMPLETE`/終了2、全check完了は補助操作の`COMPLETED`/終了0とする。
時計逆行を検知できたdoctorは既知不成立としてREJECTED/1、時計が観測できないdoctorは
INCOMPLETE/2とする。既存製品CLIの時計理由・終了値は変更しない。合格結果は開始権・
CI権限・採択の証拠にならない。
## setup preview/applyと管理AIの権限

管理metadata（setup、plan、staging、bundle、容量・保持計画）と、authorityが採択した
contract/baseline、現在の権限・leaseを別rootへ保存する。setupの成功参照をauthority
採択へ自動変換しない。

|主体|許可する計画操作|許可しない操作|
|---|---|---|
|manager AI|対象・契約・baseline・oracle・測定条件をpreviewし、独立validator receipt後にplanを採択|dispatch、停止、usage確定、未検証候補の直接採択、閾値引下げ|
|operator|doctor、planのapply、既存`gah_run`のrun/resume/cancel、保持apply|oracle・閾値・採択履歴の書換え、別ownerの資源回収|
|validator|source/binding/予定・実測usage・停止を独立照合し結果を保存|候補作成、dispatch、manager計画の承認|
|候補AI|説明・差分・修正案の下書き|権限、期待label、budget、CI利用可否の自己申告|

`setup preview` は対象環境を読み取り、指定された新規plan fileだけを出力する。入力config、現在root、差分、plan digest、期限、
必要role、容量見積りを出す。`setup apply` はoperatorが開始し、固定imageとauthorityの通信経路を準備してから、validator検査、manager採択、初回baselineの処理を各認証principalへ順に要求する。既存APIを再利用できる部分と、新kindの専用validator/操作を追加する部分を区別する。共通契約の冪等キーは
`(認証principal, command, request_id)`であり、`setup_id`・plan digest・authority refは
入力digestのbindingにする。同じ入力は保存済みreceiptを返し、内容違いの同じrequest_idは拒否する。
外部DB・Docker・authorityと管理journalを一つのtransactionで巻き戻せないため、各操作の
開始/commit/失敗をjournalへ記録する。authority commit直後にjournal保存が失敗したら、
同じrequest_idでauthorityを再照会して既存commitをjournalへ回収し、再採択・新規実行をしない。
照会結果も不明なら`INCOMPLETE`で停止し、部分完了と未解消資源を保持する。差分・権限・digestが
変わったら`PLAN_STALE`でcommitせず、適用済み外部状態を推測でrollbackしない。

開始時はdoctor結果に依存せず、`gah_run`/authorityがfreshにcontract、baseline、source
lock、採択・撤回、owner epoch、clock、資源を再確認する。reserve直後、dispatch直前にも
同じ参照と権限を再確認し、変化時は `STALE_PLAN`/終了1、未送信・未精算を残して実行を
始めない。曖昧な送信は同じoperation_idを照会し、別operationとして再送しない。
`gah_ci` は毎回fresh判定し、`gah_report` は成果物読取り後にfresh CIを再照会する。

previewが保存するplanは共通外枠（`schema_version`、固定`kind`、`id`、
`requirement_ids`、`source_ref`、`requirements_ref`、`created_at`、`expires_at`、
`payload`）を使う。payloadの参照も現行の `{kind,id,digest}` とし、plan自身のdigestを
payloadへ埋め込まない。setup planは管理側の固定操作計画として保持し、製品authorityの採択とは区別する。planが参照するpolicy・契約・baseline・比較候補は、それぞれ独立validator receipt後のmanager採択を要する。setup applyは
staging metadataの保存と、許可されたauthority準備・採択APIの完了を別々のresult/refで示す。
その補助resultは現在CI許可を兼ねず、現在の利用可否は既存のfresh照会で決める。
管理journalはruntime専用の別SQLite（`schema_version=1`）へ保存し、authority DBの
採択・Evidence・予算の正本にしない。principal/command/request_id、入力digest、状態、
`result_ref`、作成/更新時刻をtransactionと一意制約で保持し、秘密値を保存しない。

## 計画payloadと診断の実行限度

`setup_plan` は共通外枠を持ち、payloadは次の閉じたfield集合とする。

| field | 型・値 |
|---|---|
| setup_id / profile | 既存ID形式 / sample-ciまたはsample-llm |
| workspace_path | 明示workspace内の解決済みpath。symlink/reparse後にも書込範囲を検査 |
| platform | linux-x86_64またはwindows-wsl2-x86_64 |
| existing_runtime_ref | bootstrapはnull、既存環境は完全ref |
| image_lock_refs | 固定digestの参照配列1〜8件。任意tag/latestを採用しない |
| config_ref / role_recipe_ref / resource_profile_ref | 入力設定・固定role配置・予算上限の完全ref |
| sample_contract_ref / sample_case_set_ref / evaluator_ref | 選択UCの固定sample契約・全集合・校正版の完全ref |
| output_paths | runtime、run_request、ci_requestの3path。全てworkspace内の新規先 |
| actions | 固定順序 image_prepare、authority_prepare、contract_validate、contract_adopt、baseline_run、baseline_adopt、contract_transition、ready_check、requests_write。任意command文字列は受け付けない |

contract_transitionは初回baseline採択後に同条件の比較gen2を作成し、旧/新候補の全件実行・validator検証・manager採択までを含む。gen1を通常runへ読み替えず、gen2採択が終わるまで通常run/ci要求を発行しない。

setup planの既定有効期間は24時間、参照する許可/契約の期限より後へ延ばさない。sample-llmでは診断専用の少数caseへ間引かず、採択した正負200件以上と必須カテゴリの全義務を実行する。secretや既存DB内容をplan本文へ置かない。

新補助CLIは `--request-id <id>` を任意で受け、変更操作では未指定時にplanのidを使う。読取り操作の未指定IDは新規生成して応答へ返す。構文不成立時にIDを取得できなければnull。profileやpath等が同じIDで変われば共通のIDEMPOTENCY_CONFLICTとする。

clock観測は同じhost/必要な隔離環境ごとに120秒、全doctorは300秒を上限とする。bootstrapは利用可能な環境だけで前提を検査し、readyで実worker/brokerの時計を再検査する。clockのstep/逆行の許容条件は既存契約を参照し、新しい緩和値を作らない。時刻の実値と経過時間のsample件数・欠測を記録する。容量は採択profileの残り書込上界・進行中予約と256 MiBの空き余裕を要求し、上界が不明ならCHECK_UNAVAILABLEとする。これらの前提検査はPR09の総時間へ含める。

strict flat-root writerの重複走査は5回から3回へ統合した。今回lock下で取得した初期計測だけをprivate budgetへ渡し、書込直前の外部変更確認と書込後の再計測は維持する。書込を跨ぐ値のcacheは使わない。link数はWindowsの`DirEntry.stat`の値で判定せず、freshな`os.stat(..., follow_symlinks=False)`で取得する（[Pythonの仕様](https://docs.python.org/3/library/os.html#os.DirEntry.stat)）。不明なlink数をsingle-linkと推定しない。

### Docker保存先の補助観測

ready doctorは同じauthority runtimeが所有する稼働中brokerとstate/ipc volumeを確認し、固定した`statvfs`で空きbyte・総byte・空きinodeを取得する。containerのidentity/restartとvolumeのidentityを前後照合し、変化・欠測・不正応答はUNKNOWNとする。ゼロは実測値として保持し、欠測へ変換しない。補助文書は`observations.docker_storage`へ保存し、任意path/commandや外部JSONから計測事実を作らない。

この値はDocker内filesystemの観測であり、Docker Desktopの疎な仮想ディスクを保持するhostの実空き容量とは別である。観測成功だけではsetup全writerの上界や`bound_verified`を成立させず、容量判定のUNKNOWNを解消しない。

固定setupと最初の通常runの[保存先監査](evidence/productization-continuation-20260919/capacity-first-run-bound-v2.json)では、直下quota対象15系統、host DB5個、Docker state/ipc2volumeを追跡した。setupだけの13系統・4DBでは通常run分が不足する。6.5 GiBのworkspace予算案とDocker側768 MiBは、journal/物理割当の証明前の設計値であり、開始許可に使わない。

## reason code、text/JSON、状態

共通契約で固定する主なcodeは `DOCKER_UNAVAILABLE`、`IMAGE_MISSING`、
`AUTHORITY_DENIED`、`CAPACITY_EXCEEDED`、`RESOURCE_LIMIT`、`CLOCK_UNAVAILABLE`、`CLOCK_ROLLBACK`、
`BINDING_STALE`、`PLAN_STALE`、`STOP_UNCONFIRMED`、`BUDGET_OPEN`、
`EVIDENCE_REVOKED`、`BUNDLE_REDACTION_REQUIRED`、`RETENTION_HOLD`、
`MIGRATION_UNSUPPORTED`、`MIGRATION_ROLLED_BACK`、`CHECK_UNAVAILABLE`、
`OUTPUT_WRITE_FAILED`とする。並び順は決定的にし、生成AIの文章で置換しない。

|状態|補助exit|判断|
|---|---:|---|
|COMPLETED|0|指定操作のI/O・保存・検査が完了。CI許可ではない|
|REJECTED|1|既知の前提不成立、権限不足、期限切れ、stale、保持保留|
|INCOMPLETE|2|検査不能、出力障害、停止・usage・移行結果が不明|
|CANCELLED|3|取消しを受け、停止確認済み。未精算なら`BUDGET_OPEN`を残す|

JSONは共通契約の `schema_version`、`kind`、`command`、`request_id`、`operation_status`、`checked_at`、
`result_ref`、`reasons`、`ci_eligible=false`、`exit_code`を含む。操作時間、単調ns、
入力・source・planの参照はresult artifactのpayloadへ保存する。textは同じ順序で
「状態 / 終了値 / code / 次の操作」を表示する。参照欠損、整数型違い、未知キー、
digest不一致は成功へ補完しない。

## 取消し・停止・精算の運用（GAH-PR12）

既存の `gah_run cancel --runtime <runtime> --request <cancel JSON>` と
`run-cancellation-detail-spec` / `run-recovery-detail-spec` の契約を再利用する。
新CLIで別のcancel APIや終了値を作らない。

```text
PREPARED -> RESERVED -> DISPATCHED -> STOPPING -> STOP_CONFIRMED -> CANCELLED
                                      └-> STOP_UNKNOWN -> INCOMPLETE
CANCELLED + usage不明 -> recovery claim -> SETTLED（receiptは不変）
```

cancelは未送信予約を解放し、送信済みoperationへ停止を要求する。停止観測がないまま
terminalを作らず、現在CIは終了2のままにする。停止済み・usage未確定は取消しterminalを
終了3で保存し、`resource_stop_verified=true` と `budget_closure=false`、`BUDGET_OPEN`
を分ける。後日validatorがusageを一度だけ通知し、recovery ownerが精算してもreceipt、
Decision、終了3、現在CI不可を0へ変更しない。owner期限切れは `resource_cancel_claim`
へ移り、新規reserve/dispatchはしない。別runや所有外資源を停止・削除しない。

## 診断bundleと容量・保持

`python -m tools.gah_ops bundle create --runtime <runtime> --run-id <id> --output <新規dir>`
は、doctor結果、plan/run/reportの参照、reason code、timing、資源上限、permission世代、
移行・保持状態だけを収集する。既定はmetadataと `{kind,id,digest}` で、秘密、credential、
raw input、rawモデル出力、未許可ログを含めない。redaction不能・出力先既存・上限超過は
部分成功にせず `BUNDLE_REDACTION_REQUIRED` または `OUTPUT_WRITE_FAILED` とする。

cacheは[性能仕様](productization-performance-spec.md)の16 MiB/32 entry上限（payload・index・cursor等の管理領域を含む）に従い、Evidence容量とは別に計上する。bundleは本仕様v1で64 MiB/256 entryを上限とする。1 MiB以下のchunkでstream出力し、bundle全体をbrokerのmemoryへ展開しない。上限を0や無制限
として扱わず、空き容量・予測サイズ・実サイズを記録する。保持計画は`valid_until`、
`retention_until`、revocation、未解消run、現在判定への依存、manager holdを照合する。
本文削除と不変tombstoneを同一transactionにし、削除後はREPRODUCTION_UNAVAILABLEを返す。
外部保存先の削除確認がない場合は完了にしない。キャッシュ削除でEvidence・receipt・Finding
を消さず、所有外rootを再帰削除しない。

## 移行と復旧

対象は明示されたworkspace内のoffline SQLite DBであり、Docker volumeを自動mountして更新しない。

```text
python -m tools.gah_ops migrate preview --workspace <workspace> --database <relative.sqlite> --output <plan.json> --request-id <id>
python -m tools.gah_ops migrate apply --workspace <workspace> --database <relative.sqlite> --plan <plan.json> --request-id <id>
```

previewは既知schema v2/v3/旧v4だけを検査する。元DBのbyteと論理状態を固定した復旧用snapshotを保持し、別のdry-runコピー上で既存migrationを実行する。計画は両コピーのpath/digest、前後schema・table件数・全行参照digest・未解消run・permission_generation・source実装digestと要求本文のsnapshotを含む。未知版・不足・変化を拒否し、元DBを更新しない。

applyはOS principal、plan内容ref、DB path、実装・要求・元DB・snapshot・dry-runの各digestを再照合する。既存migrationのBEGIN IMMEDIATE内に固定expected_state検査を置き、移行前のconfig/meta/全行digestと、commit前の想定移行後状態を照合する。動的callbackや任意SQLを計画から受け付けない。行/参照/未解消予約の不一致はcommit前にrollbackし、BINDING_MISMATCHを返す。未知版はSCHEMA_UNSUPPORTED、保存失敗・結果不明はINCOMPLETEに保つ。

成功時は補助resultと専用commit receiptを保存してからjournalを確定する。journal確定応答が失われた場合は同request/principal/inputdigest/plan_refの保存receiptだけを回収する。DBが期待移行後の状態と等しいだけでは、当該requestのcommitを証明できない。commitからreceipt保存までの中断はOPERATION_UNKNOWNを保持し、再migrationや完了receipt合成を行わない。保存済み応答の再取得は過去の操作記録であり、現在CIの利用許可ではない。

元形式のsnapshotはapply後も保持する。手動復旧では書込元を停止し、snapshotのdigest/元schemaを照合して、対応する旧reader専用の新しい復旧先へ戻す。現用DBの上書きや新形式を旧readerへ渡す処理をこのCLIへ組み込まない。実運用での停止区間・復旧先確認はPAC12の別証拠を必要とする。

## 受入ケース（GAH-PAC09〜12/14）

手動black-boxでは正常・単一fault・境界3点・権限×状態・再起動を分け、各ケースのoracleを
この仕様、共通契約、参照先のいずれかへ結ぶ。PAC01〜08を再実行したことに置き換えず、
今回の14条件はすべて初期NOT_RUNから記録する。

|ID / 対象|操作・I/O・状態遷移|受入oracleと証拠|
|---|---|---|
|PAC09-L/W|Linux/Windowsで新規contextを作り、上の5本を各OSでUC-CI/UC-LLM各1回、計4回。applyの冪等性は同じplanの追加対照で検証|30分以内（image取得を含む）の操作時間、追加手入力0、重複setup/run 0、JSON/text参照一致|
|PAC10-M|daemon停止、image欠落、権限不一致、DB版不一致、容量不足、資源不足、clock逆行を1要因ずつdoctorへ入力|必須不成立はREJECTED/1、検査不能はINCOMPLETE/2、理由・影響・実装済み復旧手順、無変更を記録|
|PAC10-T|doctor合格後にcontract/baseline/permission/clockを変更し、run開始・reserve直後・dispatch直前で再検査|staleは送信0、同一operation照会、未知を0費用にしない。開始前合格だけで成功にしない|
|PAC11-P|HEALTHY、WARNING、違反、証拠不足、撤回をgah_ci/reportで照会し、Markdown/JSONを比較|対象・世代・時刻・reason・次操作・ci_eligibleが同じ。古いreportで現在成功にしない|
|PAC12-C|実行中cancel、停止未確認、停止確認、usage遅延、owner期限切れ、broker再起動を順に行う|`STOP_UNCONFIRMED`は2、停止済みは3、後日精算でreceipt/exitを変更しない。別owner横取り0|
|PAC12-R|cache/bundle上限、retention hold、部分削除、外部削除未確認、移行の未知版/途中失敗を試す|Evidenceとcacheを分離、tombstone/REPRODUCTION_UNAVAILABLE、rollback後の元DB不変、所有外削除0|
|PAC14-A|manager plan、候補AI提案、validator確認、operator applyを役割別identityで実行し、閾値/labelを後変更|候補自己採択・権限自己申告は拒否。変更は新plan/version、元失敗保持、全PAC状態はNOT_RUN/PASS/FAIL/INCONCLUSIVEを区別|

## 実装境界と親レビュー論点

- 設計判断: 5本の導入経路は `gah_report` のfresh照会を最終表示に兼ね、管理metadataとauthority採択を分離し、開始・適用・移行の直前再確認を必須にした。
- 実装状況: 共通契約、doctor、setup preview、保持、metadata bundleのCLIを追加。setup applyは初回baseline/比較gen2採択から通常runへつなぐ実SQLite試験が成功し、CLI/実Dockerの検収を継続中。移行は固定offline入口と不変backup/receipt限定回収を実装し、運用・既存移行・配布依存の71件を検査した。image配布・新規OS・性能・実案件の受入証拠はない。現行の詳細は[実装仕様](productization-implementation-spec.md)と[実装Task](tasks/TASK.productization-implementation-09-15-2026.md)へ従う。
- 親レビューで固定: bundle 64 MiB/256 entry、plan/authorityの別参照、setup内の初回baselineと別request生成、bootstrap/ready診断を採用。新仕様を索引へ追加し、既存MVP7文書・受入証拠のbyteは変更しない。

## 導入実装の補足

完了setupはruntime metadata v2にcontract_series_idとbaseline_series_idを保存する。readyは明示selector、次にmetadataから系列を取り、実authorityのbaseline_currentとcontract.comparison.baseline_refを照合する。系列名・current参照を推測せず、旧metadata v1から必要な系列が得られない場合は未成立にする。

`--setup-plan`は完了済みsetup_result、plan/source、runtime固定prefix/image、run/CI要求の参照を照合する読取り入口である。完了済み導入のplan期限を再採択期限として使わず、実run開始・現在CIの期限/権限/世代は既存authorityが毎回確認する。setup applyの期限検査はこれと別である。

bootstrapの`--profile`は新規固定sample向けの推定容量予算を選ぶ。CIは初回15・旧15・新30・通常30の90段階、LLMは600・600・1200・1200の3600段階を計上し、1段階32文書×最大1MiB×SQLite一時書込係数2、管理余裕1GiB、予約余裕4段階を確保する。doctorの256MiB余裕を別加算する。setup applyは根拠resource_profile本文を`.ga/operations/capacity/`に保存する。読取専用のdoctorは同じ本文からmemory内でrefを構築し、workspaceやprofileファイルを作成しない。image取得・他のruntimeの予約を含む観測値ではなく、一般ready診断の残予約をこの値で推測しない。固定sample以外の上界が不明な診断はUNKNOWNを維持する。この式は強制された書込み上限ではない。固定helperはbound_verified=falseを返し、予算以上の空きがあってもcapacityはUNKNOWN/OBSERVATION_MISSINGとする。観測できた予算不足はFAIL/CAPACITY_EXCEEDED。実証済み上界を受け取る内部adapterのbound_verified=trueと、SLO実測の有無を混同しない。現在CLIに利用者の自由JSONからtrueを設定する入口はなく、固定setupは未実証容量のため開始前に停止する。計算式の十分性・書込み量の強制・Docker保存先の実容量の実装と検証が残る。

初回・candidate実行の中断回収では、当該operationのjournal/binding/owner epoch/entry/imageとreceiptを照合してから停止・cleanup・隔離確認を採用する。終刻欠損を現在時刻で補完せず、別runのreceiptを停止証明にしない。部分不明の情報を残して新しいoperationによる再試行へ戻さない。

### authority clockの接続

開始/終了はoperatorのfresh authority_diagnosticsからchecked_atを観測する。応答9field、request_id、非CI、整数秒、同runtime内の逆行を厳密検査し、Supervisor/LlmSupervisorへ注入する。hostとbrokerの時計差を終了時刻の書換えで補正しない。元の不成立試行は保存し、新しいsource/実行で修正を検証する。

### 通常runの準備参照の共有

setupのnormal prepareは、出力するrun requestのcanonical bytesから通常Supervisorと同じrequest IDを算出し、同一operator・request bodyでauthorityへ送る。後続の通常CLIは同じ保存応答を再取得するため、別時刻・別clientでもmanifest refを維持する。ID/body/contextの差は既存REQUEST_CONFLICTで拒否する。run_beginは現在の権限・契約・期限を検査し、CI/reportはfreshに照会する。保存prepareを現在CIの成功とみなさない。


## 継続実装: 固定image準備と容量保存

`python -m tools.gah_images prepare --destination <新規配布先>` は、このcheckoutの配布対象を固定コピーし、公式Pythonの既存固定digestを取得、fixture・guardrail・authorityの順に構築する。`--offline` はbase不足時に停止する。取得先やbuilderを自由入力する引数はない。元checkoutのlock・runtime・volumeは変更せず、新規配布先のlockを生成してimage config/source digestを照合する。配布先へ移動して既存の製品CLIを実行する。公開registryへのimage発行、5コマンド/30分の全導入受入は別途必要である。

コピー上限は5,000ファイル・合計64 MiB・1ファイル8 MiB。対象はsource/tools/config/固定fixture・dataset/schema/example/文書/第三者ライセンスの配布ディレクトリとREADME/LICENSEであり、`.ga`、`.git`、Python cacheを除外する。実案件workspaceの収集や秘密値の検出を行うツールではない。symlink/reparse/hardlink、既存出力先、sourceの途中変更を拒否する。出力先確保後の失敗はincomplete記録を残し、確保前の拒否はstdoutだけに固定理由を返す。

`StorageBudget` は指定根の論理file bytes、rollback reserve、並行予約、atomic一時領域を管理する。既定のrecursive計数は明示root全体、`write_bounded`は子directoryを除く直下のregular fileだけを対象とする。後者は1文書1 MiB、直下合計256 MiB、entry 65,536件を上限とし、同じdirectoryのwriterをOS lockで直列化する。原子一時領域を含めて予約し、crash後の未公開tempを勝手に削除せず容量へ数える。immutable再配送は同一byteに限り、異なる内容を拒否する。

`Checkpoint.put`の既定保存、productization文書、setup request、worker metrics、runtime deployment metadata/時計拒否記録を`write_bounded`へ接続した。明示`Checkpoint(..., storage_budget=...)`の契約も維持する。保存DBのwriting接続は毎回`connect_sqlite`を使い、既存page sizeを保って256 MiB相当の`max_page_count`を設定・readbackする。DELETE journal、FULL synchronous、MEMORY tempを必須とし、既存上限超過DBとWALを変換せず拒否する。上限到達時のSQLite rollbackと既存row保持を検査する。

`StorageBudget`単独の並行予約は単一process内の共有instanceを前提とする。`write_bounded`のOS lockは同一directoryを保護するが、親子directoryや別rootの総量を集約しない。setupの1操作が作る有限root集合、未接続のmigration snapshot/bundle、Docker state領域、物理block・ENOSPC後の独立した永続化を照合するまでは容量UNKNOWNを維持する。FailureSinkの事前割当/readback成功を、容量枯渇後の永続性へ読み替えない。

image準備は固定Docker endpointへ10秒上限のServer version照会を行い、無到達・不正応答を`ENGINE_UNAVAILABLE`として出力先作成前に拒否する。到達後のoffline base不足だけを`BASE_IMAGE_UNAVAILABLE`とする。

実容量不足の[限定検証](evidence/productization-continuation-20260919/capacity-enospc-v1.json)では、専用32 MiB tmpfs上の実ENOSPC/SQLITE_FULLでbounded writerとSQLiteの既存データ保持・失敗閉鎖・空き回復後の再書込を確認した。この部品証拠だけではsetup/run receiptと未精算予約、FailureSinkの再起動耐久性、hostとDockerの保存先を合算した容量条件を満たさない。`bound_verified=false`と開始前UNKNOWNは維持する。物理quota保証と、強制した論理上限・保存先別空き・故障耐性に基づく開始可能性は別の主張であり、契約と全必須証拠が整う前に同一boolで代用しない。

`FailureSink.read_existing(path, capacity_bytes, root=...)`はプロセス内のrecord flagに依存せず、保存済みenvelopeを読取り専用で復元する。指定容量のregular/single-link file、header/payload長、canonical JSON object、残余zero paddingを検査し、全zeroは未記録としてNoneを返す。FIFO等はnonblocking open前後に拒否し、深すぎるJSONも固定エラーへ収束させる。root指定は既存preallocateの保存先分類と同じ意味で、任意パスへのアクセス権限を追加するものではない。

[限定ENOSPC検証](evidence/productization-continuation-20260919/failure-sink-enospc-v1.json)で、事前確保した領域の書込/fsyncと新processからの読戻しが容量圧迫中にも成功した。実appのreceipt/予約との接続、アプリ再起動・OS障害・host backing容量の証明は別に必要である。部品はこの実験結果を将来の一般的な耐久保証へ変換せず、scopeの保証flagをfalseのまま返す。

### 通常runの容量障害記録

`gah_run.execute`はrunの排他lockを取得した後、Checkpoint作成前にrun-localの`capacity-failure.bin`を64 KiB事前割当する。既存ファイルは`FailureSink.open_existing`でregular/single-link、inode、長さ、canonical JSONとpaddingを照合する。blankは一度だけ記録でき、記録済みは保持する。再open直後の`fsync_verified`はfalseで、そのinstanceによる書込/fsync成功後にtrueとする。外部の同run lockが必要であり、部品だけで無排他の並行書込を保証しない。

run/resumeは記録先の初期化失敗時にSupervisorを作らず終了2を返す。statusは記録先を読取りだけで調べ、cancel/statusは記録先の障害だけで通常のauthority照合を省略・停止しない。最初のlock取得より前、run folder作成、全setup経路をこの境界で保護したとは主張しない。

Checkpointの初期化・source lock・Supervisor生成・実行で容量不足を検出した場合、固定reasonとstage、run ID、終了2を保存する。`receipt_state`と未精算operationの状態は`UNKNOWN`であり、記録作成を停止・精算・receipt発行の根拠にしない。例外の自由文からENOSPCを推測せず、型・固定code・errno/SQLite数値codeを使う。raw要求、receipt、例外本文、pathはenvelopeへ含めない。一般I/O障害は従来のエラー経路を保つ。

保存成功は`failure_record=RECORDED`、以前の記録保持は`PRIOR_RECORD_PRESERVED`、保存不能は`UNAVAILABLE`とする。再開や取消しは既存authority/journalを再照合し、最初の記録を消さない。`supervised_capacity.py`もsource lockへ含める。容量障害の[親検証](evidence/productization-continuation-20260919/supervised-capacity-parent-v1.json)は実SQLiteのreceipt/予約保持とerrno注入を含み、関連102件のうち99成功・3 OS依存skip。1件はstdinによるWindows spawn失敗を記録した上で、同じsource/testの実ファイルdriver再試験が成功した。全保存先の総量、全故障・OS障害後の耐久性は別の検証を要する。

[実ENOSPCの接続検証](evidence/productization-continuation-20260919/supervised-capacity-enospc-v1.json)では、専用32 MiB tmpfs上で最初の操作のreceipt保存後に空きを使い切り、通常runが終了2と`RECORDED`を返すことを確認した。journalの実receiptと未停止・未精算operationは保持され、容量圧迫中の別Python processから第一記録を読めた。空き回復後の通常cancelで実停止・精算と終了3を確認し、第一記録も不変だった。現行checkoutの限定診断であり、最終authority imageの製品受入は別に検証する。開始前の容量UNKNOWNは維持する。

[doctorの読取専用検証](evidence/productization-continuation-20260919/doctor-readonly-parent-v1.json)では、CLIの容量profile生成に混入していた保存処理を除去した。既存workspaceのfile tree/hash不変、未存在workspaceの非作成、不正profileの拒否を実filesystemで確認し、関連47試験が成功した。環境probeはmockで、Docker実診断の受入ではない。setup applyの証跡保存と容量UNKNOWNは維持する。

### 状態別の表示と次操作

通常reportのJSONは、現在のCI reasonから固定的に選んだ`reason`、`next_operation`、`required_role`、`operation_effect`を持つ。Markdownも同じ値を表示し、日本語の案内を可読な文として、実在CLIのtemplateをinline codeで示す。HEALTHYは追加操作不要、停止不明・未精算はstatus確認、期限切れ・撤回は採択済み契約と新run IDによる再評価へ案内する。旧証拠の復活や同じCI照会の反復を修復として案内しない。role表示は既存権限の説明であり、権限を付与しない。

成果物の取得に失敗した場合は`kind=run_report_failure`、`artifacts_available=false`、`ci_eligible=false`、終了2を返し、要求したJSON/Markdown形式を保つ。`requested_context`は検査済み要求の期待参照・対象・用途であり、実評価済みの成果物ではない。取得できたfresh gateは既存のrequest/run/manifest/outputs参照検査を通した時だけ使用する。照会不能は時刻null・UNKNOWN、`CI_GATE_UNAVAILABLE`とする。fresh gateが終了0でも、成果物欠落をreport成功へ昇格しない。`gate_exit_code`とreport自身の終了2を分けて記録し、自由文例外を出さない。

`evaluation_versions`に`contract_ref`/`contract_generation`と`baseline_ref`/`baseline_generation`を出す。既存の`run_artifact`（候補は`candidate_artifact`）で、そのrunのmanifestが参照する契約と採択済みbaselineだけを読み、本文digestと世代の厳密な正整数を照合する。baseline行のseries/generationも本文と一致させ、欠損・重複・別参照を拒否する。IDの文字列や契約世代からbaseline世代を推測しない。成果物未取得時の世代はnullとし、人向け出力では不明を明示する。世代artifactの取得後も最後のfresh CI照会を維持する。保存済みoutputs/receiptやDB版は変更しない。

取消しreceiptでは通常完了run向けのMutation除外審査を要求しない。JSONの`measurements.mutation_reviews`と`reviewed_classification`はnull、Markdownは未取得と表示する。観測済み集計・取消し理由・未精算は保持し、最後のfresh gateの終了3をそのまま返す。通常receiptでの審査照会失敗をこの例外へ含めない。


CIは、元の整合性検査が`SOURCE_NOT_READY`へ到達した場合に限り、実際の撤回を`EVIDENCE_REVOKED`として保持し、従来同様にuse=false・終了1とする。取消しreceipt未発行時はcancel markerを分岐選択にだけ使い、既存の全operation停止検査を通す。未停止は`STOP_UNCONFIRMED`・終了2、停止済みでもreceipt未発行なら`NOT_FINALIZED`・終了2を維持する。receipt発行後に終了3へ移り、未精算の`BUDGET_OPEN`を別に残す。通常の未取消しCIに追加の全operation走査を行わない。

### rollback journal容量の追加調査

[SQLite公式形式](https://www.sqlite.org/fileformat.html#rollbackjournal)の同一page重複禁止から、256 MiB主DBのjournalページレコード部分には `floor(256 MiB/page_size)*(page_size+8)` の上界を置ける。ただしheader/sector padding、SAVEPOINTのsecondary journal、物理割当量を含めた上界ではない。[調査記録](evidence/productization-continuation-20260919/capacity-journal-review-v1.json)でWindowsとLinux検証imageのSQLite版を分けて記録した。運用binary/VFSの条件と全filesystemの対応を確認するまで `bound_verified=false` を維持する。`journal_size_limit` のcommit後切詰めをpeak上限にしない。容量不足後の安全な停止・記録という実証済みの性質は、事前容量保証とは別に扱う。

## 保存された予算警告

保存成果物を取得できる通常reportの`budget_warning`は保存Decisionの予算根拠を返す。根拠がない取消し・従来診断ではnullとし、
Markdownには「未取得」と表示する。警告がないことと未取得を混同しない。
取得済みなら閉鎖時の使用量・上限と閾値以上の軸を表示し、現在の資源値から過去の根拠を作り直さない。
WARNING単独はfreshなCI照会で終了0になり得るが、撤回・停止不明・未精算等の現在の拒否を打ち消さない。
算出と保存の契約は[authority仕様](assurance-authority-detail-spec.md)に従う。
