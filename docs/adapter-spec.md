---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# Adapter接続仕様 v0.1

本書は、固定した合成fixtureを実行し、外部評価器の観測をGAHの正規化結果へ渡す境界を定める。対象はgeneric command adapterとPromptfoo adapter、およびそれらを起動する実行監督・管理AI・隔離providerである。[実行監督の詳細仕様](execution-detail-spec.md)の範囲でDocker固定fixtureとgeneric正規化、§4でPromptfoo通常evalの単一行mappingを実装した。管理主体のOS認証は[管理境界](auth-adoption-detail-spec.md)を参照する。adapter registryの採択とモデル送信・全体の管理接続は未完了で、部品受入を全MVP受入や実運用性能の証明とは扱わない。

## 1. 境界と責任

adapterは「採択済みの入力を指定された評価器へ渡し、容量を制限して出力を収集し、正規化候補を返す」部品である。契約の採択、基準値の変更、CI合否、Findingの検証、任意のshell実行は行わない。GAHの判定部はadapterのPASSや終了コードを無検査で合格へ変換せず、`NormalizedResult`とEvidenceのbinding・鮮度・失効・整合性を別に確認する。

実行監督は、RunManifest、TrialPlan、fixture、予算、deadline、隔離、子処理、停止確認を開始前に固定する。adapterは監督が渡した不変のoperation_idと所有世代にだけ応答し、別run・別契約・別試行の結果を受け付けない。再配送は内容digestとbindingが一致する場合だけ一件に畳み、異なる内容は矛盾としてHOLDにする。

管理AIは、Control、EvaluationContract、baseline、PolicyProfileの提案と更新を担当し得るが、説明文だけで採択・権限・評価結果を変更しない。採択要求には実行境界で認証した`actor_ref`、`context_ref`、`expected_generation`、`proposal_digest`を結び、LC02の原子的な確定時検査を通す。認証済み主体を取得できない場合、またはcandidate・adapterの出力から主体を逆算する場合は採択も実行も拒否する。OS/IAMによる認証と権限発行は本書の未実装境界である。

### 共通のbinding

すべてのadapterの入力・出力・監査metadataは、少なくとも次の参照を同じ値で持つ。

| 参照 | 用途 |
|---|---|
| `run_id`、`operation_id`、`owner_epoch` | 所有世代、予約、再配送、再開の照合 |
| `contract_ref`、`contract_digest`、`target_digest` | 契約・対象内容の版と比較軸 |
| `case_id`、`trial_id`、`stage_id`、`fixture_digest` | 予定義務、試行、段階、入力集合の照合 |
| `adapter_id`、`adapter_digest`、`mapping_profile_ref` | 実行部品・正規化規則の固定 |
| `policy_ref`、`evaluator_ref`、`isolation_ref` | 閾値・評価器・隔離条件の固定 |

path、可変の表示名、対象が出力した自己申告の版、終了コードだけでは同一性を確定しない。版またはdigestを取得できない場合は、結果を成功へ変換せず、比較不能または実行不成立として記録する。

## 2. 実行段階

段階は直列化し、各段階の入力・出力・検査点をEvidenceまたは許可された最小metadataへ結ぶ。raw payloadを通常ログへ流さず、受信前に容量検査とデータ方針検査を行う。以下の上限は初期実装で固定する候補値であり、正式Schema・運用profileを採択する前に受入条件へ反映する。

| 段階 | 入力 | 出力 | 検査点と失敗時 |
|---|---|---|---|
| 0. 計画検査 | RunManifest、TrialPlan、adapter registry、fixture参照、予算、deadline | 不変のDispatchPlan | 必須参照、対象版、用途、予定義務、adapterの対応範囲、予算、認証主体、隔離providerを検査。欠落・未知版・世代競合・権限不足は開始前に拒否しHOLD。 |
| 1. 隔離確保 | DispatchPlan、許可fixture、isolation policy | `isolation_ref`、子処理枠、送受信境界 | 検証済みproviderの識別・版digest・能力・期限・入力/出力先を照合。検証済みproviderがない場合は評価器を起動せず、`ISOLATION_UNAVAILABLE`としてFAILED/HOLD。 |
| 2. 起動・送信 | adapter registryが解決した実行ファイル、固定引数配列、fixture、operation lease | 起動記録、送信記録、終了状態 | `shell=false`、固定cwd、許可した環境変数、許可した入出力先、deadline、子処理を強制。任意shell、宣言外path、ネットワーク、期限超過、停止確認不能は開始拒否またはERROR。 |
| 3. bounded収集 | stdout/stderrまたはadapterが許可した構造化出力 | 制限済みraw artifactと収集metadata | stdout/stderr各256 KiB、合計512 KiB、正規化入力1 MiBを上限とする。超過は`OUTPUT_TOO_LARGE`として保存・送信せず、必須試行の欠損を残す。timeoutは部分出力をPASSへしない。 |
| 4. 正規化 | 制限済み出力、binding、採択済みmapping、対象版情報 | `NormalizedResult`候補 | 構造、型、enum、重複、binding、adapter/mapping版、fixture digest、期待labelとの対応を検査。内容矛盾、余剰・重複、版不一致はHOLD、単なるmapping不在・必要欄欠損はUNKNOWN（Critical義務ならHOLD）として保持し、成功へ補完しない。 |
| 5. 照合・採用 | 正規化候補、予定義務、既存試行、Evidence参照 | 採用済みNormalizedResultまたは拒否理由 | 同一試行の再配送はdigest一致時だけ採用。異なる結果、未計画case、別run・別契約・未対応段階は整合性不成立としてHOLD。adapter自身の結果で期待labelや契約を変更しない。 |
| 6. 保存・判定入力 | 採用済み結果、最小metadata、費用・停止・鮮度情報 | Evidence、Decision入力、RunReceipt候補 | LC05の保存前検査、raw/artifact digest、予算精算、owner_epoch、deadline、失効世代、必要成果物を再確認。保存失敗は成功receiptを返さずFAILED/2。最終assuranceは決定部が全理由と優先順を計算する。 |

各段階のtimeoutはrunの元deadlineを延長しない。初期profileのper-call上限は120秒（起動した子処理の待機と外部評価器の一回の呼出しを含む）とし、PR runのdeadlineは1,200秒（20分）、full runのdeadlineは5,400秒（90分）とする。収集・正規化・保存もrun deadline内に含め、個別の呼出しがdeadlineをまたいで継続すること、timeout後に新しい呼出しを開始すること、期限を30秒または300秒の別枠で延長することを認めない。per-call 120秒またはrun deadlineを超えた場合は`TIMEOUT`とし、停止確認・精算・保存を完了できない部分出力は成功へ変換しない。実際のprofileがこの既存の上限と一致しない場合は開始せず、料金・token・最大出力が不明な場合もWARNINGへ格下げせず実行不成立として扱う。

## 3. Generic command adapter

generic commandは任意のshell文字列を受け取るAPIではない。監督側が不変の`adapter_id`をregistryから解決し、そのentryに結び付いた許可済み実行ファイル、固定fixture、固定引数配列、許可環境、出力mappingだけを使用する。呼出し入力にcommand文字列、自由な実行ファイルpath、任意引数、shell展開を含めてはならない。

registry entryの最低条件は次のとおりである。

- `adapter_id`、entry内容digest、対応する実行ファイルdigest、許可fixture digest、mapping profile、対応target/contract範囲、timeout、入出力上限、隔離policyを不変版として保存する。
- OS起動APIへ渡すのは実行ファイルと事前採択した引数の配列だけにし、`shell=false`を固定する。引数へfixture本文や未検査payloadを文字列連結しない。
- fixtureはGAHが作成し用途・期待label・lineageを固定した合成集合に限る。自己申告の「合成」「安全」や実行器の終了コードは許可根拠にしない。
- stdout/stderrはbounded収集し、正規化に必要な最小構造だけをmappingする。拒否された文字列・抜粋・低エントロピー値を推測できるdigestを既定の監査証跡へ残さない。

実行ファイル・fixture・mapping・targetのいずれかがregistryと一致しない場合、`VERSION_MISMATCH`または`FIXTURE_NOT_ALLOWED`で開始を拒否する。起動失敗、クラッシュ、timeout、出力欠損はKILLEDや正常な拒否観測へ読み替えず、ERRORと必須不足へ分けて記録する。generic commandで任意コードの変異、攻撃、宣言外のhost操作を自動化する機能は対象外である。

## 4. Promptfoo adapterの境界

対応版は0.123.0。公式タグの型定義・CLI・出力資料とnpm配布物を取得し、内容hashを記録した。通常evalを固定した無害provider一つ・一行で実行し、実際のJSON wrapperを照合する。[公式固定型定義](https://github.com/promptfoo/promptfoo/blob/0.123.0/src/types/index.ts)、[公式CLI処理](https://github.com/promptfoo/promptfoo/blob/0.123.0/src/node/doEval.ts)、[固定版の配布情報](https://registry.npmjs.org/promptfoo/0.123.0)を根拠とする。配布物はMIT、実測環境はNode 24.11.0。導入時のinstall scriptsは無効にした。

`normalize_promptfoo(raw, expected_binding=..., expected_identity=...)`はOutputFileのevalId/results/config/shareableUrlと、results.version=3のsummaryを読む。V3行はresults.results[]のEvaluateResultであり、表形式DTOやdocsの省略例を行schemaとして流用しない。初期mappingは一つの出力ファイルに一行だけを許可し、欠損・余剰・重複行を拒否する。複数行の採択・分割はまだ実装していない。

expected_identityはevalId、testIdx、promptIdx、providerのid/label、promptId、versionを持ち、実行監督が保持する期待値と照合する。promptIdは開始前に固定したpromptの内容hash。evalIdは通常CLIが実行時に生成するため、監督が実行receiptへ記録する必要がある。出力から取り出して自己照合しただけでは実行同一性や認証の証明にならない。モデル・設定・実行器・fixtureの実体版は、別途registryとdispatch receiptで照合する。

### 伏せ字とbindingの対応

0.123.0の実exporterはmetadata.gah内のdigest欄を伏せ字へ置換する。初回の実出力はBINDING_MISMATCHで拒否された。この場合に伏せ字を元の値とみなしたり、redaction設定を弱めたりしない。

対応表を使うprofileでは、metadata.gahをbinding_id一項目に絞り、開始前expected_bindingのcanonical JSONから作る`gah-binding:`付きhashへ照合する。colonは型付きIDと裸の秘密値候補を区別する区切りであり、redaction設定は維持する。このIDは不透明な対応キーであり、認証や権限を与えない。全bindingを輸送できる入力も全fieldの型・値を検査する。整数と等しい小数、別case・run・epoch、未知field、伏せ字は一致扱いにしない。

### 観測の正規化

Promptfooのsuccess/score/gradingResultはassertion採点である。ガードレールのdetect/allow、独立したdeviation、MutationのKILLEDへ直接読み替えない。response.outputには固定したmode/observations、またはGAH generic envelopeだけを許可し、既存generic normalizerで再検査する。自由文から値を推測しない。errorを伴う行は固定EXECUTION_FAILUREへ写像し、error本文を返さない。

JSONは1MiB、構造化outputは64KiBを上限とする。重複key、BOM、非finite、複雑度超過、未知field、不正型、binding違い、未知版を拒否する。statsと一行の状態も照合する。返すのはNormalizedResultと許可されたdigestだけで、config、prompt、variables、trace、任意metadata、自由文をDecisionへ含めない。対応package版がmetadataにある場合は固定版と矛盾していないことを検査する。

これは通常eval出力の部品mappingであり、OS認証済みrunへの接続・mapping profileの採択・外部モデル資源台帳・全MVP受入は継続中。未対応版・mapping不足をgeneric成功へフォールバックしない。実行・隔離・版・現在の採択が揃うまでは通常CI成功に使わない。

## 5. 管理AIと認証境界

管理AIからの提案と採択操作を分離する。提案本文はデータであり、shell、SQL、権限変更、契約更新命令として解釈しない。採択・更新を実行できるのは、実行境界が認証して当該対象・操作へ権限を持つ主体だけである。

採択処理は次の順で行う。

1. 認証層から`actor_ref`、主体の許可範囲、`context_ref`、認証時刻を取得する。request本文やadapter出力のactor_idは無視する。
2. 管理AIの提案をcanonical bytesへ固定し`proposal_digest`を作る。提案に含まれるcontract、policy、fixture、evaluator、adapter、isolationの参照を解決し、許可範囲と対象版を照合する。
3. `expected_generation`、最新失効世代、必要な旧条件評価、新条件の受入証拠、校正、予算、保存先を原子的な確定点で再検査する。世代競合・期限切れ・権限取消し・証拠不明・保存障害なら現行採択を維持し、更新成功を返さない。
4. 採択後のRunManifestへactor/contextと全bindingを結び、adapterへは許可済み参照だけを渡す。管理AI、candidate、adapterが採択済みcontractやbaselineを自己変更できる権限を与えない。

認証・権限・世代検査を実装できないhostでは、管理AIの提案を保存することはできても採択やCI成功に使用しない。GAHのローカルdigestは完全性の材料であり、主体認証や同一host管理者からの改竄耐性を証明しない。

## 6. 隔離provider

隔離providerは、許可したfixture、実行ファイル、入力・出力先、network、子処理、期限を実際に強制できることを事前検証したものだけを指す。provider recordにはproviderの識別子・版digest、検査時刻、能力、policy digest、許可mount/egress、資源上限、監査参照、失効状態を結ぶ。

実行開始前に次をすべて満たす。

- provider recordが現行policyとcontractに対応し、失効していない。
- 実行ファイル、fixture、設定、出力先、adapter mappingが許可範囲に一致する。
- providerがネットワーク・host filesystem・credential・宣言外子処理を遮断または検査できる。
- timeout、容量、CPU/メモリ、同時実行数、費用、停止確認をrun予算とdeadline内で監査できる。
- raw payloadの収集・送信・一時保存がLC05の検査を通り、通常ログへ漏れない。

一つでも確認できない場合は、評価器を起動せず`ISOLATION_UNAVAILABLE`として実行拒否する。既存hostにプロセスを置いただけでは安全な隔離とは扱わず、現在の実装が隔離を完成させたとも主張しない。providerから遅れて届いた結果は履歴として受け付け得るが、binding・停止・所有世代・取消し・期限を再検査するまで正規結果やCI成功を変更しない。

## 7. エラー、timeout、状態、受入境界

adapter層のエラーは原因を混同しない。入力・権限・版・隔離・整合性の不成立はHOLD、実行器の起動・収集・保存障害はFAILED/2と必要評価の不足、評価結果の既知の閾値違反は判定部のDEGRADEDまたはCriticalならHOLD、判定不能・mapping不足はUNKNOWNとして記録する。WARNINGは必須条件を満たした任意注意に限る。

最低限のreason codeは次の固定候補とする。

| code | 条件 | 扱い |
|---|---|---|
| `ADAPTER_UNKNOWN` / `VERSION_MISMATCH` | registryにないadapter、未対応版、digest不一致 | 開始拒否、HOLD |
| `AUTHORITY_MISSING` / `GENERATION_CONFLICT` | 認証主体・権限・expected_generation不成立 | 採択/開始拒否、HOLD |
| `ISOLATION_UNAVAILABLE` / `FIXTURE_NOT_ALLOWED` | 検証済み隔離または許可fixtureがない | 起動前拒否、HOLD |
| `TIMEOUT` / `OUTPUT_TOO_LARGE` | deadline超過またはbounded上限超過 | PASSへ変換せず、ERROR/不足 |
| `OUTPUT_INVALID` / `VERSION_MISMATCH` | 構造不正、未知形式、余剰・重複、版不一致 | 内容不整合、HOLD |
| `RESULT_CONFLICT` / `BINDING_MISMATCH` | 同一試行の異内容、別run/契約/段階、対応内容の矛盾 | 整合性不成立、HOLD |
| `MAPPING_UNAVAILABLE` | mapping未採択、対応表・必要欄の不足・欠損 | UNKNOWN、Critical義務ならHOLD |
| `PERSISTENCE_FAILED` | Evidence、receipt、budget closureを保存できない | FAILED/2、成功返却なし |

reasonはrun・operation・metric・evidenceの参照を許可されたmetadataだけで保持し、拒否payload本文を含めない。複数原因は捨てず、契約の優先順で安定化する。timeout後の部分出力、外部評価器の終了コード、管理AIの説明、隔離providerの自己申告だけで必須評価の完了を作らない。

製品受入では、少なくとも未知adapter、版不一致、任意shell引数、fixture差替え、隔離なし、認証主体偽装、mapping未登録、出力上限超過、timeout、同一試行の矛盾、遅延結果、取消し、保存障害、再起動後のowner_epoch不一致を、実装した入力・期待結果・実結果・対象版digestとともに検査する。現在これらのシナリオは設計上の受入観点であり、製品runnerの実行済み証跡ではない。

## 8. 実装前に固定する事項

次の項目を、実装開始前に将来の製品registry採択として管理境界で確定し、registry・Schema・受入fixtureへ反映する。ここでいう「採択」は製品側の登録・審査を指し、今回の文書作業で利用者へ再許可を求める意味ではない。

- adapter registryの保存形式、entryの不変性、adapter/target/fixture/mappingのdigest生成対象と対応版規則。
- Promptfoo 0.123.0の原典・実出力を確認した単一行mappingを、実体digest、OS認証、dispatch receiptと結び、製品registryで採択する。現行main docsだけで未確認フィールドを推測して追加しない。
- generic commandの実行ファイル、固定引数、環境変数、cwd、入出力先、`shell=false`を強制する実装と監査。
- per-call 120秒、PR run 20分、full run 90分のtimeout、256 KiB/512 KiB/1 MiBの容量上限、resource・費用上限が各profileで成立するかの根拠。
- 検証済み隔離providerのprovider record、能力検査、失効手順、停止確認、rawデータ境界。安全な隔離を提供できなければ実行拒否する。
- 管理AIの認証主体、actor/contextの取得元、採択権限、世代競合の原子的な確定方式。未実装のOS/IAMを完成済みと扱わない。
- adapter errorからEvidence、Decision、RunReceipt、終了コードへの変換規則と、全理由を保持する保存方式。

上記事項が未確定の間、generic commandとPromptfooを本番対象・任意コード・実データへ拡張しない。診断コアの`component_validation`結果は常に`ci_eligible=false`であり、adapter接続の存在だけでMVP受入、CI保護、現実の検知性能を主張しない。
