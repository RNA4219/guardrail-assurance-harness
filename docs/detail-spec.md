---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 詳細仕様 v1: 判定と永続化コア

通常runに基づくbaseline更新と保存世代の現在利用は[baseline更新仕様](baseline-refresh-detail-spec.md)へ結ぶ。

所有権期限切れ後の停止入口は[取消し復帰](run-recovery-detail-spec.md)に従う。通常claimと取消し取得を区別し、停止と使用量を独立に観測する。

通常runの人間向け/機械向け表示は[要約仕様](run-report-detail-spec.md)と[検証記録](evidence/mvp-report-20260912/README.md)へ結ぶ。

通常runの停止済み取消しは[取消し仕様](run-cancellation-detail-spec.md)へ接続した。停止確認・未精算・不変成果物・CI終了3の[実証](evidence/mvp-cancellation-20260912/README.md)を参照する。

[要求v0.4](requirements.md)、[基本仕様v0.3](design.md)、[初期方針](operating-policy.md)を具体化する。実装対象はversion 1の部品診断・不変保存・費用台帳。コア後の固定UC-CIでは認証・隔離・採択・通常runと[現在のCI利用](regression-ci-detail-spec.md)を接続した。UC-LLMの認証・モデル資源、常設運用、全MVP受入は残る。部品のHEALTHY/WARNINGを通常CIの成功に使わない。

0.2.0では入力/判定v1を保ち、台帳をv2へ拡張した。[期限後回収・取消し・終了の詳細仕様](lifecycle-detail-spec.md)を合わせて読む。以前の検収は当時のソースhashと結果を保持する。

## 残る接続の詳細設計

以下は実装前の接続仕様であり、現在の製品受入とは区別する。既存要求・初期方針は維持する。

- [条件変更と後続契約](contract-revision-detail-spec.md): 旧・新条件、保存参照の依存関係、一般世代の採択。
- [UC-LLMの認証・資源接続](llm-authority-detail-spec.md): 実配備版、400ケース、Promptfoo、予約・停止・usage。
- [製品監督・再開・対象限定](supervised-run-detail-spec.md): checkpoint、二重実行防止、変更影響、定期全体。
- [保持・削除と修復確認](retention-revalidation-detail-spec.md): tombstone、現在不足、独立再検証と再発。

## 1. 配置と責任

| ファイル | 責任 | 検証対象 |
|---|---|---|
| [decision.py](../src/gah/decision.py) | 入力構造/意味、Fractionによる指標、優先順位、入力digest | public APIを直接呼ぶ境界・不正値試験 |
| [wire.py](../src/gah/wire.py) | JSON bytesのサイズ、文字コード、重複key、深度、小数/指数の拒否 | 型検査前に不正JSONを通さない |
| [ledger.py](../src/gah/ledger.py) | SQLite、不変report、run/所有世代、費用予約と精算 | 再読込、独立connection、矛盾とrollback |
| [cli.py](../src/gah/cli.py) | 入出力・保存・固定エラー・終了コード | 別プロセスの入出力、保存/出力障害 |
| [起動入口](../tools/gah_cli.py) | checkoutのsrc/gahを明示して起動 | 同名のインストール済みsrcとの衝突を避ける |

Python 3.11以上の標準ライブラリを使用する。実際に検証したPython/SQLite版はEvidenceへ固定する。第三者の評価資産を移植せず、公式仕様を参照してGAH内で実装する。

## 2. 部品入力 v1

`assess(request: dict) -> dict` は副作用を持たない。ファイル入力の構文検査はwire、構造/意味検査はassessの一箇所で行い、直接APIでも必須fieldと未知fieldを検査する。[JSON Schema](../schemas/component-assessment.v1.schema.json)は部品入力専用。[入力例](../examples/component-assessment.v1.json)は算術用の集計値で、実モデルの観測証拠ではない。

| field | 型・範囲・意味 |
|---|---|
| schema_version | 整数1のみ。boolや1.0は不可 |
| request_id | `[a-z0-9][a-z0-9_-]{0,63}`。同一IDの別内容を保存しない |
| target_digest / contract_digest | 小文字64桁hex。参照の内容識別用で、発行者の認証ではない |
| purpose | `component_validation`のみ。`regression`等は拒否 |
| observed_at / assessed_at | UTC整数秒、0〜2^53−1。日時文字列やboolは不可 |
| metrics | 1〜100行。metric_idは重複不可 |
| required_missing / critical_missing | 必須観測不足/Critical不足の監督側からの入力 |
| integrity_failure / forbidden_violation / critical_violation | 整合性・必須事象・Critical違反の監督側からの入力 |
| warning | 部品の注意理由。実runの80%注意率の測定はrunner接続時に行う |

各metric行はmetric_id、name、critical、numerator、denominator、baselineを全て持つ。nameはrecall/fnr/fpr/asr/mutation_score。分子・分母は0〜10^12の厳密整数、分子≤分母。baselineはnullまたは同じ数値条件の分子・分母object。fnrにbaselineを指定しない。将来の基準比較では対象・条件・30日鮮度が必要であり、ここでnullを受け付けることは通常CIの比較省略許可ではない。

UTF-8、最大65,536 bytes、深度16以下。BOM、不正UTF-8、重複key、NaN/Infinity、指数/小数表記、末尾の別JSONを拒否する。JSON Schemaの構造制約に加え、JSON整数表記・ID重複・分子と分母・fnrのbaselineをruntimeで検査する。Schemaだけを通した値を検証済みとして採用しない。

## 3. 判定と返値

閾値は初期方針の値をコードに固定し、全5指標のCritical/非Critical、差分を持つ4指標の境界をテストする。外部の自己申告で方針値を上書きするfieldは受け付けない。率はFractionで比較し、出力も既約な`[分子, 分母]`とする。0/0はnull/不足、分子>分母は入力拒否。

優先順位はHOLD > DEGRADED > UNKNOWN > WARNING > HEALTHY。既知の理由を全て残し、state優先・reason code・metric_idで順序を固定する。Critical違反/不足/整合性不成立はHOLD、他の必須違反・閾値違反はDEGRADED、不足はUNKNOWN。観測未来はHOLD、年齢86,400秒以下は鮮度内、超過は不足でCritical対象を含めばHOLD。

返値はschema_version、request_id、request_digest、target_digest、contract_digest、assessed_at、purpose、assurance、ci_eligible、metrics、reasons。ci_eligibleは常にfalse。各metricはmetric_id/name/value/baseline_value/absolute_pass/delta_passを持ち、評価不能や非適用をnullで区別する。reasonはcode/state/metric_id（全体理由ならnull）。

request_digestは検査した入力を`ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False`でUTF-8化したSHA-256。95/100と190/200のように同じ率へ正規化される別入力や、観測時刻だけを変更した入力も識別する。これは保存bytesのhashとは別物で、Unicode正規化や汎用の標準canonicalizationを保証するものではない。

入力の集計値・フラグ・時刻は、まだ接続していない監督側が検証する前提。適合する数値だけで、固有ケース最低件数、カテゴリ完全性、Recall/FNRの同一集合、試行の独立性、実際の検査到達、baselineの有効性を証明したとはみなさない。

## 4. 保存とトランザクション

現在のSQLiteはuser_version=2。新規DBでのみ初期化し、既存の未知版・表/列欠落・異常DBを空の正常DBへ戻さない。初期DDLは一つのtransactionに入れ、途中失敗で部分的な表を残さない。v1からの変更は[明示的な移行](lifecycle-detail-spec.md)だけで行い、通常openでは自動変更しない。

`BEGIN IMMEDIATE`で検査と更新を一つの書込みtransactionへまとめる。接続の待ち時間は最大5秒、foreign_keysを有効にする。SQLiteは同時に一つの書込みtransactionを扱うため、予約残額の照合とINSERTを分離しない。[公式transaction仕様](https://www.sqlite.org/lang_transaction.html)に基づく。schema版は[公式user_version](https://www.sqlite.org/pragma.html#pragma_user_version)の用途に合わせて管理する。

| 表 | 主キー | 保持する状態 |
|---|---|---|
| ledger_meta | key | 最後に受理したUTC clock |
| runs | run_id | profile、開始時刻、元deadline、owner、owner_epoch、lease_until、HOLDと理由 |
| operations | operation_id（全runで一意） | runと予約時owner/epoch、元金額、予約上界、確定額・確定時刻、状態、未解消exposure |
| reports | request_id | canonical JSON文字列、同じUTF-8 bytes、bytesのSHA-256 |

v2のruns追加field、terminal_records、run_eventsは[移行と保存契約](lifecycle-detail-spec.md)に従う。元のreport bytes、runのdeadline、予約・精算の記録は保持する。

`store_report`はtrustedな監督側の定型診断専用で、purpose/ci_eligibleを検査する。同ID・同内容は元の記録、異内容はconflict。`get_report`は文字列・bytes・hashを再照合して返す。読み出し時の型破損・不正JSONも固定LedgerErrorにする。hostが全列とhashを変更する攻撃への認証ではない。

DB内の診断保存と外部ファイル/stdoutへの出力は分散transactionではない。DB保存後に出力が失敗しても、保存済み診断を改変・削除せず終了2にする。部分出力はRunReceiptではなく、通常CIへ流用できない。異常終了時に架空の出力完了を補わない。

## 5. run・所有世代・費用

基本APIは[Ledger](../src/gah/ledger.py)のcreate_run、claim、reserve、settle、snapshot。v2でclaim_recovery、request_cancel、confirm_stopped、budget_closure、finalize/get_terminalを追加した。case/call/token/子処理数の実測と上限強制はrunner接続時に追加する。Python引数のownerはOS認証そのものではなく、今はtrustedな同一管理境界内の識別値である。

create_runはpr=1,200秒/USD2、full=5,400秒/USD10を固定し、重複IDを拒否する。claimは初回またはlease失効時にepochを増やす。同じownerの有効lease更新は同epoch、別ownerは拒否。leaseは60秒を既定とし、元deadlineを超えて延長しない。reserveはowner/epoch/leaseと元deadlineを同じtransactionで照合する。takeover後に旧ownerが新しい予約や精算を行えない。

clockは既定`int(time.time())`、テスト時だけ明示したclockを注入する。bool/負数/2^53−1超過/保存した時刻からの巻戻りを拒否する。停止期間を含むUTC deadlineを保持する。悪意あるホストの時計変更や、起動中の単調時計による監督は未接続であり、UTCだけで製品の時刻信頼性が完成したとはしない。

費用はASCII十進文字列を受け、操作総額を整数演算で百万分の一USDへ切り上げる。元文字列と会計上界を分ける。0は部品APIでは`"0"`に限り受理するが、外部呼出しへの接続時は監督側の非課金根拠を必須にする。長さは128文字、整数会計値はSQLiteの符号付き64bit内。

予約時点の全体額は、(T−86,400,T]で確定した額＋全未精算予約。run額はそのrunの全履歴分。同額再配送は時刻も額も更新しない。未精算のsettled_atはnull。精算は予約を確定額へ置換し、実額が予約上界を超えたら実額とHOLDをcommitした後にエラーを返す。現在のownerは、旧ownerの予約を元のbindingを保持したまま精算できる。

確定額と矛盾する再配送は、最初の確定額を維持したままexposureへ候補最大額を保存する。その操作はmax(元確定額, exposure)を年齢に関わらず全体予算へ計上し、HOLDにする。保守的な留保を理由なく解放しない。v2でdeadline後の回収lease/APIを追加したが、exposure解消、外部課金の認証根拠、管理identityは未接続であり、外部課金の実運用の完成とは扱わない。

## 6. CLIとエラー

repo rootから次を実行する。通常の出力例は終了1であり、コマンド自体の起動失敗ではない。

```sh
python -m tools.gah_cli assess --input examples/component-assessment.v1.json --db .ga/diagnostic.sqlite
python -m tools.gah_cli show --id sample-assessment-1 --db .ga/diagnostic.sqlite
```

assessは入力検査→計算→不変DB保存→再読込照合→任意の`--output`→stdoutの順。出力pathは新規のみ、既存ファイルを上書きしない。途中失敗のpathを自動削除せず終了2とし、別プロセスによる置換済みファイルも保全する。再出力には新しいpathを使う。showは存在しないDBを作らない。

| 終了値 | この部品CLIでの意味 |
|---|---|
| 0 | --help / --versionの補助表示だけ。評価成功の発行機能は未接続 |
| 1 | 部品診断の保存/表示完了。ただしci_eligible=false |
| 2 | 入力・保存・出力・内部障害。固定codeのJSONをstderrへ最善努力で出す |

例外本文、入力payload、任意のパスをエラーJSONへ転記しない。未知引数も固定INVALID_ARGUMENTS。KeyboardInterruptだけで停止完了を認定せず2とする。v2で部品の取消し記録/終了3を保存できるが、terminal-showの正常表示は1。実際の子処理停止と全MVPの取消しは実行監督の接続後に受入する。

## 7. 残る詳細仕様と受入境界

[adapterと実行境界の仕様](adapter-spec.md)へ外部接続と管理AIを分ける。全MVPの次の作業では、以下の状態を受入証跡と結んで閉じる。

| 項目 | 詳細化/実装する内容 | 完了の確認 |
|---|---|---|
| 採択 | 配布BootstrapContract、管理identity/context、提案digest、権限/証拠/世代の確定点再検査 | 初回と更新、権限取消し、証拠失効、競合の実環境拒否 |
| 実行 | 事前TrialPlan、operation_id、送信意図、子処理、単調時計、全資源予約 | 未確認dispatchを再送せず、停止と料金を別に確認 |
| Evidence | 観測元のbinding、失効世代、保存前データ判定、保持/削除 | raw拒否前の漏れなし、撤回後のUseDecision不成功 |
| データ | 400以上の固有ケース、2必須カテゴリ、正負・校正・二段階 | 同文言差替えを独立ケースと数えず、固定oracleで審査 |
| Finding/Plan | 元条件の参照、推定と事実、再検証ref、定型出力 | 計画生成だけでVERIFIEDへ移さない |
| 配布/保守 | v1移行とdeadline後回収の実装済み部分を保ち、保持期間・exposure解消・監督主体保護を追加 | crash/競合/取消し/復旧と削除の実試験 |

この文書はコアの詳細仕様と実装範囲を固定する。全オブジェクトの正式Schema、管理AIの実identity、外部adapterの採用版・最終mappingを確定したとは扱わない。[進捗表](open-questions.md)と[製品受入条件](acceptance-criteria.md)が全体の残件を示す。
