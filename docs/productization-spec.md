---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# MVP後の拡張仕様 v1: 共通契約と統合

[拡張14要件](productization-requirements.md)を実装へ渡すための仕様。2026-09-15、Lunaの分担執筆を親Codexが監督・統合した。**本書と分冊は仕様上の設計であり、新しいCLI・接続・計測・受入は未実装/NOT_RUN。** MVPの32/32受入、既存の要求・方針・契約・証跡は維持する。実際の製品状態は[MVP監査](mvp-completion-audit.md)を参照する。

## 1. 構成と正本

| 仕様 | 主に具体化する要件 | 定義すること |
|---|---|---|
| 本書 | PR01〜14、特にPR13〜14 | 共通wire、責任境界、操作結果、採択、互換、受入対応 |
| [性能・CI仕様](productization-performance-spec.md) | PR05〜08、PR13 | 計測plan/観測、時間・仕事量・cache・page、CI分割とgate |
| [導入・運用仕様](productization-operations-spec.md) | PR09〜12、PR14 | 初回手順、設定、doctor、説明、保持・復旧 |
| [実案件評価仕様](productization-pilot-spec.md) | PR01〜04、PR14 | 接続manifest、データとoracle、比較、工数、pilot判定 |
| [設計ケース](contracts/productization-spec-cases.v1.json) | PR01〜14 | 入力断片と期待値。製品試験の成功例ではない |
| [監督レビュー](reviews/productization-spec-20260915.md) | 仕様全体 | Lunaの担当、親の指摘と反映、相互レビュー、未確認範囲 |

要求が仕様に優先し、共通wire・状態・権限は本書、操作別の詳細は各分冊を正本とする。受入条件を緩めて仕様間の矛盾を解消しない。新規の正式JSON Schema・migration・実装テストは実装工程でこの仕様に対応させる。設計ケースを既存validatorへ投入可能な全体requestと誤認しない。

## 2. 現行実装との境界

確認した既存入口は `tools.gah_run`（run/resume/cancel/status、runnerはfixture/guardrail）、`tools.gah_ci`（現在のCI利用）、`tools.gah_report`（JSON/Markdown）。既存 `response_exit_code` はfieldの集合も厳密に検査する。本仕様の新fieldを既存応答へ無言で追加しない。

| 層 | 拡張での責務 | 委譲先・制約 |
|---|---|---|
| 操作入口 | 設定preview、前提診断、計測/pilotの計画・表示 | 新補助CLIの名前はgah_ops/gah_benchmark/gah_pilot。全て計画上の名称 |
| 計画・接続 | 対象/入力/oracle/測定条件を内容参照で固定する | 本文からshell・実行権限・採択権限を生成しない |
| 既存authority | 実行identity、許可、採択世代、失効、現在利用を決定 | 既存認証とvalidator経由。補助CLI内のフラグで代替しない |
| 既存監督・runner | 予約・隔離・送信・停止・usage・Evidence | 初期拡張も許可された固定recipe/adapter。任意の攻撃処理を実行する機能は追加しない |
| 観測・集計 | 同一条件比較と欠測・不明・不確かさを計算 | 既存決定的算術・予定照合・独立校正を再利用 |
| 現在CI | 対象・契約・Evidence・権限をfreshに照合 | 成功を発行するのは従来の製品CI経路のみ |

新しい実案件bindingを現行の固定fixture設定へ名前だけ変更して渡してはならない。runner/adapterはcapabilityと入力版を検査し、未対応は拒否する。既存fixture/guardrailの契約と再現用CLIを維持して、新しい接続を明示的に追加する。

## 3. 共通wireと参照

- UTF-8のobject。既存[contracts.py](../src/gah/contracts.py)の上限1,048,576 bytes、深度16、node数100,000と厳密JSON検査を再利用する。大きなケース本文や観測列は分割artifact参照で受け渡す。
- `schema_version` は厳密整数1。未知field・欠落field・重複key・NaN/Infinity・小数/指数・孤立surrogateを拒否。boolは数値fieldで不可。nullableは各表で明示した箇所だけ。
- 識別子は既存 `require_id`、digestは `require_digest` に従う。参照は厳密に `{kind,id,digest}` の3field。kind別allowlistと参照先の内容を検証する。digestは署名や実行権限ではない。
- `canonical_bytes`（UTF-8、sort_keys、空白なし、ensure_ascii=false）とSHA-256を再利用する。保存したcanonical bytesと参照digestを毎回整合確認する。改行や独自のUnicode正規化を後付けしない。
- UTCは0〜2^53−1の整数秒。区間時間は差分の整数ns、容量はbytes、仕事量は要素数/呼出数をfield名で区別する。raw monotonic時計の機器間比較は禁止。比率は整数の分子/分母で保持し、表示の丸めを判定へ戻さない。
- 計測不能値は `null` と理由を組にする。0は観測済みのゼロに限定する。計測予定件数と必須の受入義務は別fieldとし、観測なしで合格へ進めない。

### 3.1 計画artifactの共通外枠

| field | 型・意味 |
|---|---|
| schema_version / kind / id | 1 / 操作別の固定kind / 既存ID形式 |
| requirement_ids | PR IDの非空・重複なし配列。計画対象外は対象外の理由を別記 |
| source_ref | 対象実装snapshotの完全参照。branch名やlatestだけは不可 |
| requirements_ref | この拡張要求の内容を指す完全参照 |
| created_at / expires_at | UTC秒。後者は前者より大きく、開始時に期限内 |
| payload | 分冊の定義する閉じたobject。共通外枠を除く固有fieldはこの中に置く |

source_ref/requirements_refは、fileのraw SHA-256とpathを内容に持つsnapshot manifestの参照とする。manifest自体のcanonical digestと参照先fileのraw digestを区別する。

大きい集合は順序付きsegment参照・件数・全体digestで固定する。segmentの入替え・重複・欠損を照合し、上限超過時に末尾を切り捨てない。plan自身のdigestを自身のpayloadへ含めない。外枠に採択済みフラグや自己申告のroleを入れず、採択receiptと状態はauthorityから取得する。

例に現れる未生成のartifact名は設計上のkindである。既存Registry/ArtifactStoreのallowlistに登録済みだとは扱わない。実装時はkindごとの専用validatorと採択操作を同時に追加し、汎用の任意JSON登録を開放しない。

## 4. 補助操作の結果と終了値

新補助CLIは、既存 `ci_gate_result` と別の `extension_operation_result` を返す。

| field | 型・必須条件 |
|---|---|
| schema_version / kind | 1 / `extension_operation_result` |
| command | 実際に受け付けた操作名。分冊で定義する固定集合 |
| request_id | 入力ID。構文破損で取得不能の場合だけnull |
| operation_status | COMPLETED / REJECTED / INCOMPLETE / CANCELLED |
| checked_at | この操作を照会したUTC秒 |
| result_ref | 保存できた結果artifactの完全参照、なければnull |
| reasons | 固定reason codeの重複なし配列。非完了では1件以上 |
| ci_eligible | 常にfalse |
| exit_code | 次の表と一致する整数 |

| operation_status | 終了 | 意味 |
|---|---:|---|
| COMPLETED | 0 | 計画生成・診断・表示等、その補助操作が完了。製品CIの合格を表さない |
| REJECTED | 1 | 妥当な入力に対して既知の必須条件不成立、SLO不合格、無権限・入力意味違反 |
| INCOMPLETE | 2 | 入出力不能、観測不足、通信/時計不成立等で必要な確認が終わらない |
| CANCELLED | 3 | 操作の取消しが確定。実行済み製品runの停止・精算は別に表示 |

`command`はドメインを含む固定名とする。ops.doctor、ops.setup.preview/apply、ops.bundle.create、ops.retention.plan/apply、ops.migrate.preview/apply、benchmark.plan/measure/compare、pilot.register/plan/execute/status/reportを用い、同名のplanでもドメインを省略しない。全補助CLIに任意の`--request-id`を設け、未指定IDの生成規則は分冊に従う。

syntax/型/未知field等は `INVALID_INPUT` によりREJECTED/1。入力fileを読めない、結果を保存/出力できない場合はINCOMPLETE/2。保存後のstdout障害で結果を消さず、同一request照会で回復する。既存CLIsの終了値・出力形はそのまま維持する。wrapperが既存CLIのJSONを加工して0へ変換することを禁止する。

`assess/compare` は、必要な全観測があり目標に届かない場合REJECTED/1、判定に必要な観測が不足する場合INCOMPLETE/2、全対象条件を満たせばCOMPLETED/0。`show/report` はFAILの結果を正常表示できればCOMPLETED/0。両操作とも製品CIはfalseで、判定対象の結果をresult_ref先から読める。

## 5. 管理AI・採択・冪等性

候補作成AIはデータと修正案を出せるが、期待ラベル・閾値・対象範囲・計画採択を変更できない。基準管理AIは既存方針内で対象と計画を選び、別identityのvalidatorが決定的に検査する。JSONに書かれた役割は認証として使わない。人間への追加承認を通常の実行条件にしない。

1. 計画draftを内容固定し、対象許可・能力・依存・上限をvalidatorへ渡す。
2. validatorが同じ内容の参照に対して検査receiptを返す。違反・不明は保持する。
3. 基準管理AIが現在権限・expected generation・receipt・期限を照合し、原子的に採択する。
4. 実行開始直前に、採択版・現在権限・失効・時計・残予算・対象snapshotを再照合し、予約と開始の既存境界へ接続する。
5. 採択後の変更は新id/digestと新計画版にする。元の受入失敗、取消し、未精算を保存し続ける。

更新操作の冪等キーは `(認証されたprincipal, command, request_id)`。同じ入力digestは保存済み操作へ戻し、別digestは `IDEMPOTENCY_CONFLICT` / REJECTED、別principalのIDで結果や権限を取得させない。再開前・結果の現在利用時にも認証を確認する。request再配送だけを新たな評価件数・新たな送信許可にしない。

計画の状態はDRAFT→VALIDATED→ADOPTEDを基本とし、採択後もREVOKED/EXPIREDへ変わり得る。状態とreceipt本文を分離し、過去receiptは不変。実行runのCOMPLETED/CANCELLED等を計画の状態に混ぜない。取消し/期限切れによって未解消資源予約を消去しない。

## 6. 互換性・永続化・失敗境界

既存の厳密JSONに新fieldを足す場合は、既存版の受入を維持した別version/別kindを追加し、呼出側と受取側で双方を検査する。新しいplanning artifactの保存が必要でも、既存authorityの採択tableを外部CLIから直接更新しない。

新管理操作のjournalは、runtime専用管理領域のSQLiteへ `schema_version=1` として保存する設計とする。既存authority DBとは別ファイルで、権限・実予算・Evidenceの正本にはしない。最小キーはprincipal/command/request_id、入力digest、操作状態、result_ref、created_at、updated_at。排他的な唯一制約とtransactionで二重作成を防ぐ。秘密値は保存しない。実行済みか不明な外部処理について、journalだけの推測で再送しない。 更新は認証済みの管理入口だけが行う。journalとauthorityを一つのtransactionと偽装せず、authority側commit後にjournal保存が失敗した場合はINCOMPLETEで止める。同じrequest_idに対するauthorityのreceipt/現在状態を照会し、同一digestを確認してjournalを復元する。照会不能なら再送せず不明を保持する。

既存DBの変更が必要な機能は専用migrationに分離する。現行DBの対応versionはその実装Taskの対象sourceから固定し、旧版の無条件読込・暗黙初期化・全開発snapshot互換を要求しない。migration計画には書込停止、事前snapshot、変換前後の件数/参照/未精算照合、失敗時の元形式への復旧を含める。新規snapshotは復旧目的に限定し、Evidenceの保持・削除方針を回避する永久コピーにしない。

失敗の優先順位は、権限/整合性の拒否→停止未確認・未精算等の未完了→既知の目標未達→正常補助完了の順で表示する。`operation_status`は各操作が完了可能かを表し、`assurance`や製品gateを置き換えない。元製品の決定優先順位は変更しない。

## 7. 14受入条件への対応

各分冊の具体caseと下記を満たす製品試験を実装時に対応させる。現時点では全行NOT_RUN。

| 要件 / 受入 | 仕様 | 主な受入結果 |
|---|---|---|
| PR01 / PAC01 | 実案件評価、§2〜5 | 2repo/1対象の登録〜結果、許可/版/能力不一致拒否 |
| PR02 / PAC02 | 実案件評価 | 実履歴20組、正常100、校正、誤通知、実劣化と修復確認 |
| PR03 / PAC03 | 実案件評価 | 実利用正負200、必須カテゴリ各100、独立集計と不明 |
| PR04 / PAC04 | 実案件評価 | 20対応観測、時間30%削減、資源/介入非増加 |
| PR05 / PAC05 | 性能・CI、§3 | 固定manifest、6観測、単位・区間・失敗の分離 |
| PR06 / PAC06 | 性能・CI | 通常800試行、中央値1800秒/最大2700秒、基準比60% |
| PR07 / PAC07 | 性能・CI | 100warm照会、cold3、仕事量、cache/失効、page上限 |
| PR08 / PAC08 | 性能・CI | 早期3分、全件30分、総runner120分/60%、完全なgate |
| PR09 / PAC09 | 導入・運用 | Linux/Windows×両UCの4導入、5command/30分 |
| PR10 / PAC10 | 導入・運用、§5 | 7環境不成立、非破壊診断、実行開始時の再照合 |
| PR11 / PAC11 | 導入・運用、§4 | JSON/要約、8状態、理由と次操作、既存exit維持 |
| PR12 / PAC12 | 導入・運用、§6 | 回収/未精算/保持/移行/復旧、容量上限、秘匿 |
| PR13 / PAC13 | 全分冊、§2〜6 | 既存32条件への影響、意味等価、変更経路の故障試験 |
| PR14 / PAC14 | 全分冊、§4〜6 | 計画版、独立採択、権限、NOT_RUN/PASS/FAIL/INCONCLUSIVE |

`ProductizationAcceptanceRecord` は要件ID、受入ID、plan_ref、source_ref、evidence_refs、status、reasons、checked_atを持つ。statusはNOT_RUN/PASS/FAIL/INCONCLUSIVE。plan_refは未選定の場合に限りnull、source_refは対象sourceの完全参照、evidence_refsはNOT_RUNの場合は空配列とする。各種結果は専用validatorで閉じたfield集合を検査する。未開始はNOT_RUN、実施したが必須の根拠を欠く場合はINCONCLUSIVE、確定した未達はFAIL。FAILと不明が同居すればFAILを維持し不足理由も残す。 その場合、assess操作自体は必要観測未完了としてINCOMPLETE/2、受入recordは確定未達を示すFAILとなる。合成校正のPASSを実案件の未実施条件へ引き継がない。

文書検収、P0の技術受入、P1の有用性確認、全14条件の製品受入、GitHub反映を別に記録する。MVPの32条件へ番号を足して過去の受入数を付け替えない。

## 8. 実装に渡す順序と未確定の入力

M0で測定・pilot計画と専用validatorを具体化し、既存と新規の能力照合を接続する。M1は計測/照会/CI、M2はsetup/doctor/運用を所有ファイルと共通契約の境界で分割できる。M3で実案件データを評価し有用性を判定する。各改修で親の差分レビュー、関連する境界試験、要件と証拠の対応を完成させる。

未確定は、許可を確認した具体repo/実利用集合/modelの不変版、性能基準機の実値、観測期間の営業日カレンダー。これらは仕様上の任意文字列で置換して受入せず、M0の採択planで固定する。本ターンでは取得・配備・測定を実施していない。数値SLOと必要標本は[要件](productization-requirements.md)から維持しており、達成可能性を測定済みとはしない。
