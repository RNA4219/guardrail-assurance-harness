---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# 実案件pilot接続・有用性・採択追跡の詳細仕様

本書は [拡張要件 v1](productization-requirements.md) の GAH-PR01〜04 と GAH-PR14 を、実装前に再現可能なpilot計画へ落とす。対象は利用者が所有または明示的な利用許可を持つ二つの開発repoと、一つのLLM評価対象だけである。攻撃、任意コード、第三者対象の操作、危険な入力は扱わず、repoの無害な履歴と固定した検査変更・秘匿済み評価入力に限定する。

本書の実案件接続と製品受入は未完了である。現行コードには、固定sampleを用いた厳密なbinding/plan登録、canonical ref/digest検査、算術検査、独立validator/managerによるmetadata採択の入口と局所試験がある。一方、実利用の二repo・一LLMを固定runnerへimport/executeする接続、実connector、全資源計測、実案件データ取得は未実装である。対象の版・許可・oracle・20観測・10営業日の入力も未確定で、固定sampleを実案件の代替にしない。GAH-PAC01〜04は `NOT_RUN` のままとし、14要件の現在状況は [productization-status.md](productization-status.md) で追跡する。MVP32、既存の受入証拠、既存の終了値、固定400ケースの意味は変更しない。新14条件はMVPへ加算せず、別のpilot結果として報告する。

## 1. 境界と再利用する不変条件

登録入口は、許可済みの固定recipeと採択済みadapterだけを解決する計画とする。利用者入力から任意shell文字列、任意実行ファイル、任意引数、攻撃手順、外部宛先を生成しない。実行が必要になった時点でも、既存 `gah_run`、`gah_ci`、`gah_report` のauthority・停止・精算・保存境界へ接続し、pilot側で判定権限を複製しない。

既存 `src/gah/run_contracts.py` の `content_ref` と同じ canonical UTF-8 JSON（sorted key、区切り文字固定、非finite拒否、深さ・1 MiB上限）を参照生成に再利用する。共通参照は常に次の3項目だけを持つ。

| field | 型・制約 | 用途 |
|---|---|---|
| `kind` | `id` | 参照対象の型。表示名で代用しない |
| `id` | `id` | 不変内容を識別する値。改訂時に再利用しない |
| `digest` | 小文字SHA-256 64桁 | canonical bytesの完全性 |

`bool` は `int` として受け付けない。時刻はUTC Unix秒、経過時間はns、容量はbytes、件数はcountとしてfieldと単位を分け、数値の単位を表示文から推測しない。未知field、重複key、整数範囲超過、refのkind/id/digest不一致、直接渡されたpayloadの再canonical化によるdigest差は登録・実行・採用の各境界で拒否する。

pilotのplanning metadataは、現行authorityが採択したControl、EvaluationContract、baseline、PolicyProfileの代替ではない。`project_binding` 等が存在するだけで権限、CI成功、Evidenceの有効性、モデル版の実在性を付与しない。pilotの計画・結果は `ci_eligible=false` とし、既存CI利用の可否は毎回 `gah_ci` の現在照会だけで決める。

## 2. 登録契約（新Schemaではなく計画上のfield表）

以下はこの文書で固定するfield表であり、現行のpilot validator/authority実装と実案件接続の受入を同一視しない。親仕様の共通契約へ統合する際も、既存refの意味、unknown field拒否、canonical digest、再現可能な状態遷移を維持する。

### 2.1 Binding artifactの共通外枠

`project_binding` と `evaluation_target_binding` は、親仕様の計画artifact共通外枠（`schema_version`、`kind`、`id`、`requirement_ids`、`source_ref`、`requirements_ref`、`created_at`、`expires_at`、`payload`）を持つ専用artifactとして登録する計画である。共通refの `id` は外枠の `id` と一致し、payload内に別の識別子を作らない。以下の表で `schema_version`、`kind`、`binding_id`、`created_at` と記した行は外枠の値またはその読み替えであり、payloadへ二重保存しない。それ以外はpayloadのfieldである。固定sampleの登録検査とmetadata authorityの実装はあるが、任意repoの実入力接続・実行許可までを提供しない。

### 2.2 `project_binding`（UC-CIの一repo）

`kind=project_binding` の外枠 `id` を `binding_id` として参照し、payloadは次の閉じたfieldだけを持つ。

| field | 型 | 必須 | 固定条件 |
|---|---|---:|---|
| `schema_version` / `kind` / `binding_id` | int / enum / id | ○ | 外枠の `1` / `project_binding` / `id`。payloadには複製しない |
| `owner_ref` | ref | ○ | 許可主体の台帳参照。候補出力から作らない |
| `repository_ref` | ref | ○ | `kind=repository_identity`。同一repoを表す内容digest |
| `revision_refs` | ref配列、2以上 | ○ | `kind=repository_snapshot`。許可済みbefore/after/clean revisionの集合 |
| `revision_set_digest` | digest | ○ | 順序付きrevision_refs集合の不変照合 |
| `permission_ref` | ref | ○ | `kind=permission_grant`。有効期限・範囲は別artifactで照合 |
| `permission_scope` | enum配列 | ○ | `history_read`、`fixed_inspection_read` の部分集合 |
| `recipe_ref` | ref | ○ | 採択済み固定接続recipe |
| `adapter_ref` | ref | ○ | 採択済みadapterと対応版 |
| `capabilities` | enum配列 | ○ | 下記のproject用allowlistの部分集合 |
| `unsupported_capabilities` | enum配列 | ○ | 下記のunsupported集合から選び、空でも省略しない |
| `secret_ref` | refまたはnull | ○ | `kind=secret_handle`。秘密値はpayloadへ置かない |
| `resource_profile_ref` | ref | ○ | CPU/RAM/storage/時間/費用上限 |
| `retention_ref` | ref | ○ | 保持・削除・撤回条件 |
| `immutable` | bool | ○ | 登録時は常にtrue。falseへの更新を許さない |
| `created_at` | int | ○ | UTC Unix秒 |

project用 `capabilities` の閉じたallowlistは `history_read`、`diff_metadata_read`、`fixed_inspection_read`、`result_write` である。`unsupported_capabilities` の閉じた拒否集合は `shell_exec`、`host_write`、`credential_read`、`network_egress`、`unbounded_process`、`raw_input_export` とする。allowlist外の能力名は未知fieldとして拒否し、unsupportedを理由にfixtureへ置換しない。二つのbindingは異なる外枠 `id` を持ち、各repoのowner、許可範囲、凍結したrevision_refs集合、source digestを個別に照合する。pairのbefore/afterは同じrepo identity binding内の別revision_refとして選び、bindingをrevisionごとに上書きしない。少なくとも一つは本製品以外の所有または利用許可済みrepoとするが、M0で対象を選ぶまでは名称や証拠を作らない。

### 2.3 `evaluation_target_binding`（UC-LLMの一論理対象・二版）

一つの `target_ref` は一つの論理評価対象を表し、比較に使うのはその対象に属する二つの不変binding（baseline版とcandidate版）である。したがって「1評価対象」は「1版だけ」を意味せず、登録時にbaseline/candidateの2版を別digestで確定できない場合はPAC03を開始しない。

| field | 型 | 必須 | 固定条件 |
|---|---|---:|---|
| `schema_version` / `kind` / `binding_id` | int / enum / id | ○ | 外枠の `1` / `evaluation_target_binding` / `id`。payloadには複製しない |
| `target_ref` | ref | ○ | `kind=target`。同じ論理対象familyのdigest |
| `model_revision_ref` | ref | ○ | 実在する版・設定の台帳参照 |
| `permission_ref` | ref | ○ | 利用・評価・秘匿処理の許可 |
| `secret_ref` | refまたはnull | ○ | token等のopaque handleのみ |
| `recipe_ref` / `adapter_ref` / `evaluator_ref` | ref | ○ | 採択済みrecipe・版・mapping |
| `capabilities` | enum配列 | ○ | 下記のLLM用allowlistの部分集合 |
| `unsupported_capabilities` | enum配列 | ○ | project bindingと同じ拒否集合から選ぶ |
| `resource_profile_ref` | ref | ○ | token、呼出、時間、費用上限 |
| `redaction_profile_ref` | ref | ○ | 入力・出力の秘匿処理 |
| `immutable` / `created_at` | bool / int | ○ | true / UTC Unix秒 |

LLM用 `capabilities` の閉じたallowlistは `redacted_case_read`、`bounded_model_eval`、`result_write` である。対象版、endpoint、adapter出力、設定hashだけを重み版・認証・許可の証明にしない。対象版を取得できない、二版比較の同一条件を確定できない、許可の期限が切れている、またはcapabilityが不足する場合、既知の許可/能力不成立は操作REJECTED/1、版や比較条件の観測不足は操作INCOMPLETE/2とする。対応するPACは未開始ならNOT_RUN、実施したが根拠不足ならINCONCLUSIVEとし、低い性能を捨てたり固定fixtureへ差し替えたりしない。

### 2.4 `pilot_plan`/`pilot_manifest` と不変revision

`pilot_manifest` は、親仕様の計画artifact共通外枠で `kind=pilot_plan` としたartifactの `payload` として定義する。外枠fieldは共通契約に委譲し、pilot固有fieldを外枠へ重複させない。`pilot_id` は外枠 `id` であり、payloadには複製しない。固定sampleのpilot plan validator/metadata authorityの局所実装はあるが、ここで定義する実案件の全bindingを登録済みとは扱わない。

| payload field | 型・件数 | 固定条件 |
|---|---|---|
| `plan_revision` | id | 同じpilotの改訂ごとに新規 |
| `project_binding_refs` | ref配列、長さ2 | 全要素kind=`project_binding`、id重複なし |
| `evaluation_target_ref` | ref、長さ1 | kind=`target`。一論理対象を表す |
| `baseline_target_binding_ref` / `candidate_target_binding_ref` | ref各1 | kind=`evaluation_target_binding`、id/digestが相互に異なる |
| `selection_ref` / `baseline_ref` | ref各1 | kind=`pilot_selection` / `pilot_baseline` |
| `contract_ref` / `registry_ref` / `case_set_ref` | ref各1 | 現行EvaluationContract、ControlRegistry、CaseSetへ解決 |
| `adapter_refs` / `evaluator_refs` | ref配列、各1以上 | baseline/candidateで同じdigestを共有 |
| `resource_profile_ref` / `retention_ref` | ref各1 | 既存PolicyProfileと保持境界へ解決 |
| `owner_ref` | ref | 認証済み主体の参照。payloadのrole自己申告は認証にしない |
| `permission_refs` | ref配列、1以上 | 二repoとtarget版の許可を全て覆う |
| `design_evidence_refs` | ref配列、1以上 | 計画・許可・oracle・baseline固定の根拠 |

`registry_ref` は現行 `control_registry` の `registry_id`、Controlの `target_ref`、依存閉包、obligation、`mutation_applicability` を解決し、`case_set_ref` は現行CaseSetの `case_set_id`、purpose、required_categories、case/lineage、oracle_ref、initial_state_ref、session_steps、scored_stage_idを解決する。`run_manifest` の既存 `contract_ref`、`purpose`、`use_cases`、`target_refs`、`control_ids`、`baseline_ref`、`plan_ref`、`policy_ref`、`profile`、`environment_ref`、`actor_context_ref`、`created_at`、`deadline` は変更せず、pilot manifestはそのrefを計画payloadとして束ねる。`bind_run_manifest` の `ci_eligible=false` と既存の厳密検査を、実案件pilotが完了したという理由で緩めない。

計画artifactの内容はcanonical bytesで一度固定し、同じ外枠 `id` で別内容を再登録しない。revision変更、許可範囲変更、対象版変更、oracle変更、評価器変更、資源profile変更、保持条件変更は新しい `plan_revision` と新しいdigestを要求する。開始後のfield上書き、期限の延長、秘密値の差替え、対象の差替えは旧結果を更新せず、旧計画を保持した新計画として扱う。外枠の `expires_at` を越えた計画は、延長して継続せず新計画へ進む。
## 3. GAH-PR01 / GAH-PAC01: 実案件をCLIへ接続する計画と現行runnerとの差

実装時の安全な接続順は次のとおりとする。

1. 利用者が既存の許可と管理PolicyProfileを参照として選び、管理AIが評価範囲、秘匿方法、保持期限、費用上限を `permission_ref` として束ねる。validatorが有効期限・scope・ownerを独立照合し、新しい権限が必要ならそこで依存作業を止める。
2. 固定recipeが読み取り可能なrevisionだけをsnapshot化し、canonical digest、revision_id、取得範囲、非対応capabilityを記録する。曖昧な履歴・欠損revision・権限不足は登録拒否とする。
3. 二つの `project_binding` と一つの `evaluation_target_binding` を検証し、Control/CaseSet/adapter/evaluatorのrefを既存契約へbindする。未対応入力は `unsupported` として残す。
4. 基準管理AIがpilot manifest、selection、baseline、予算、停止条件を計画版として固定する。候補AIはoracle、閾値、採択権限、結果の書換え権限を持たない。
5. 実行段階では固定adapterを通じて既存 `gah_run` の監督・停止・回収・精算・保存へ渡し、結果は `gah_ci` でfreshに照会し、`gah_report` でJSON/Markdownを表示する。pilotの補助結果だけでCI成功を発行しない。
6. design evidence、execution observation、acceptance resultを別artifactとして保存し、未完了・不明・矛盾・未精算を0件へ補完せず、PAC状態を確定する。

現行固定runnerとの違いを次に固定する。

| 観点 | 現行実装 | pilotで必要な接続 |
|---|---|---|
| 入口 | 既存deploymentと固定request | 許可済みbindingと不変manifestの登録 |
| 対象 | 固定fixtureまたは固定guardrail runner | 2repoの許可済み履歴と1評価対象 |
| revision | request/contractの既存ref | repo snapshot・対象版・recipeを個別に固定 |
| 実行器 | `gah_run` の `fixture` / `guardrail` | 新しい任意runnerを作らず、採択adapterから既存境界へ委譲 |
| 状態照会 | `gah_ci` が現在authorityを照会 | pilot結果をci_eligible=falseで別照会し、既存判定を再利用 |
| 表示 | `gah_report` の保存artifactとfresh CI | pilotの選定・不明・費用・保守観測を追加refで表示 |
| 許可・秘密 | 固定runtimeの既存authority境界 | permission_refとsecret_handleを分離し、payload/ログへ秘密を置かない |
| 非対応入力 | 固定契約外は開始不成立 | `unsupported_capabilities` と理由を保存し、fixture代替しない |

`python -m tools.gah_pilot` は、`register`、`plan`、`status`、`report`、`execute` の固定補助入口を実装している。登録・計画・metadata current/adoption照会・決定的算術結果の保存は既存validator/authority境界へ接続するが、`execute` は製品run authority未接続を `AUTHORITY_REQUIRED` / `INCOMPLETE` として保持し、実repo/LLMの入力import・実行へ進まない。実connector、全資源計測、任意shellや攻撃手順を受け付ける引数は設けない。

`python -m tools.gah_pilot` の返却は親共通契約の `extension_operation_result` を使い、`schema_version`、`kind`、`command`、`request_id`、`operation_status`、`checked_at`、`result_ref`、`reasons`、`ci_eligible`、`exit_code` の10個を固定fieldとして返す。`operation_status` は `COMPLETED`、`REJECTED`、`INCOMPLETE`、`CANCELLED` のいずれか、`ci_eligible=false` とする。`exit_code` は補助処理完了=0、既知拒否=1、不成立・不明=2、取消し=3。`result_ref` がない不成立では理由を保持し、FAILの結果を表示できた `report` は操作としてCOMPLETEDでもPACはFAILのままとする。これは新入口だけの補助契約であり、既存 `gah_run`、`gah_ci`、`gah_report` のexitの意味を変更しない。

計画上の入力は `register --binding <binding> --output <新規result>`、`plan --input <manifest候補> --output <新規plan>`、`execute --plan <採択plan> --runtime <既存runtime>`、`status/report --plan <plan> --runtime <runtime>` とする。全操作で任意の`--request-id`を受け、register/plan/executeの既定はbindingまたはplanのid、status/reportの未指定IDは新規生成して応答に返す。全pathは明示workspace内で解決し、既存出力へ別内容を上書きしない。executeは同一planの同じ実行を再送せず、既存run状態を照会する。before/afterとbaseline/candidateの子run IDは凍結plan内に列挙する。

## 4. GAH-PR02 / GAH-PAC02: UC-CIの選定と判定

### 4.1 履歴20組、clean100、独立校正

M0で許可確認後、二つのrepoから変更前後のpairを合計20組以上選ぶ。各repoは5組以上を含め、Controlまたは検査系の挙動に関係する変更を優先する。単なる版番号、表示名、検査挙動・制約に無関係な依存lockの更新だけで件数を満たさない。依存lockが検査挙動・制約を変える場合は、その差分とoracleを記録して対象にできる。pairには `before_ref`、`after_ref`、`project_binding_ref`、変更範囲digest、独立oracle_ref、選定理由を付け、同一履歴の派生を別pairにしない。

履歴pairとは別に、正常な変更100件をclean集合へ固定する。各件は許可済みsnapshot、期待する非劣化label、独立oracle、Control別の適用範囲を持つ。不明label、適用範囲が未確定の変更、重複sampleはclean分母へ入れず、必要件数不足ならPAC02を `NOT_RUN` または `INCONCLUSIVE` とする。

校正集合は実履歴・clean集合・評価対象候補から独立に作り、既存10 Controlと5 Mutation familyの適用する全義務を覆う。期待判定は選定後に候補出力から作らず、別のレビュー可能な固定oracleへcanonical refで束縛する。校正の不一致は0件でなければ失敗とし、校正件数を履歴20組・clean100件へ足さない。

### 4.2 履歴の実行とbaseline

各pairは同じrepo identityの `project_binding` 内で凍結した別revision_refをbefore/afterに選び、同じregistryの依存閉包、同じadapter/evaluator、同じPolicyProfile、同じresource/retention条件で評価する。baselineはbefore、candidateはafterとし、実行範囲、予定義務、stage順、停止条件、Evidence保存条件を一致させる。Control、evaluator、policy、corpus、environmentのいずれかを変える場合は `changed_axes` を明示した新しい計画へ分け、同じpairの改善率へ混ぜない。

必須違反の判定は全件Findingへ結び、Critical見逃し0件をPAC02条件とする。repo別・Control別の適用範囲と非被覆を結果へ残す。実劣化が一件も得られない場合、検査が正しく動作しても「実案件への効果」は未実証とする。原因・修復・修復後再検証は同じFindingのref連鎖で保持し、修復前の結果を上書きしない。

### 4.3 CIで扱う不明・混同

同じattemptの同じ内容digestの再配送は一件に畳み、配送回数を別countにする。異なる内容、別binding、別revision、未予定case、カテゴリ混同は勝ち結果を選ばず、矛盾と不足を保存する。timeout、権限不足、adapter未対応、停止・精算不明は正常・違反・0件へ読み替えない。

cleanの誤った要対応通知は5件以下、誤ったCriticalは0件とする。不明labelはclean分母へ入れず、必要数不足を成功へしない。候補AIの説明、終了値、自己申告revisionでoracleやFindingを変更しない。

## 5. GAH-PR03 / GAH-PAC03: UC-LLMの受入集合と比較

### 5.1 受入、holdout、校正の分離

利用許可と秘匿処理を確認した実利用由来の受入集合を、開発・調整・校正から分ける。受入は要検知label 200件以上、正常label 200件以上とし、各必須categoryで正負各100件以上を満たす。同一scenario、同一lineage、入力だけの表記差、ref/ID/段階名の改名で独立件数を水増ししない。既存合成packの400件を実利用集合の代用にしない。

この受入集合そのものをholdoutとして計画凍結時に件数、category、用途、oracle責任者、digestを固定し、開発・調整・校正から分離する。受入200/200の分母へ含め、結果を閲覧してから集合やラベルを補正しない。追加のholdout集合は任意の診断用であり、PAC03の必須件数やgateへ加えない。受入集合の許可・秘匿・固定が確認できない場合はPAC03を `NOT_RUN` とする。

校正は対象モデルとは独立したcontrolled responseと独立normalizerで実施する。検知の `detect` / `allow` / `indeterminate` と操作の `apply` / `block` / `defer` を別々に組み合わせ、正常、違反、判定不能、無関係条件のfalse、費用不明、重複配送、前段失敗を含める。測定器校正不合格時は対象へ送信しない。

### 5.2 2版比較とbaseline条件

同一acceptance集合を二つの実在target版または設定へ渡し、target以外の条件を固定する。baseline/candidateの各manifestは同一 `case_set_ref`、oracle、category、session初期状態、stage順、adapter/evaluator、resource/permission/retention条件を参照する。baselineは別の実行で確定し、候補の応答や低性能を見て書き換えない。

対象版、evaluator、policy、corpus、environmentの差はmanifestの `changed_axes` として事前宣言する。oracle、pack、stage、条件のdigestが違う比較は性能差へ集計せず、別計画または `INCONCLUSIVE` とする。既存 `baseline_context` のbaseline_ref、target集合、依存閉包の照合を再利用し、contextを渡しただけで採択済みとは扱わない。

### 5.3 独立oracleと混同・不明の扱い

oracleは対象応答を見る前に固定し、対象の自己申告、自由文、理由説明から期待labelを作らない。期待labelが独立レビューで割れた入力は `indeterminate` として保持し、正負の分母へ入れない。oracle参照、入力、initial state、scored stageは全て `{kind,id,digest}` で照合する。

正負のscored stageでは、検出結果を次のように扱う。

| 状態 | TP/FP/TN/FNへの扱い | 追加記録 |
|---|---|---|
| positive + detect / negative + allow | 対応する正解count | category、variant、義務scope |
| positive + allow / negative + detect | FN / FP | Findingまたは誤警報として独立記録 |
| 正負 + indeterminate | binary混同行列へ入れない | `prediction_indeterminate` として不足扱い |
| oracle indeterminate | binary分母へ入れない | `oracle_indeterminate` として別集計 |
| missing、ERROR、timeout、未対応 | 0件・正常・違反にしない | `missing`、reason、未精算を保持 |
| duplicate同内容 | 一件＋配送回数 | digest一致を再照合 |
| duplicate異内容・category混同 | 採用結果を選ばない | `CONFLICT`、PACはINCONCLUSIVE |

全必須stageが揃うcaseだけを完了と数え、前段の欠損を後段の正答で相殺しない。TP/FN/TN/FP、indeterminate、missing、duplicate、errorはcandidate/baseline、category、Control、義務のscope別に返す。0分母の率はnullとし、実利用集合から母集団性能を推定する主張は別の確かさ条件なしに行わない。

### 5.4 正常タスクと対象低性能

正常タスクの期待結果もtarget応答と独立に固定し、達成率を別metricとして測る。targetが低性能、誤検知、判定不能でも、それを測定できたことは評価器の失敗ではない。対象をHEALTHYへ誘導するためのlabel除外、retryによる回答差替え、閾値引下げは受入変更として扱い、元の結果を保持する。

## 6. GAH-PR04 / GAH-PAC04: 保守負担の比較

### 6.1 20件の観測単位

合計20件以上の対応観測を、UC-CIとUC-LLMから各5件以上含める。観測単位は、許可済み変更またはcaseの開始から、原因特定、判定、証拠確認、必要な再確認までを一つの結果へ結んだものとする。既存手順とGAH手順で同程度の難度・同じ正解・同じEvidence水準を用い、担当、順序、習熟、再試行、欠測理由を記録する。人間が通常作業していない工程の人件費削減は主張しない。

各観測は `observation_id`、`use_case`、`subject_ref`、`legacy_protocol_ref`、`gah_plan_ref`、`correctness_ref`、`operator_role_ref`、`order`、`started_at`、`closed_at`、両手順の計測、欠測・不明理由を持つ。秘密、raw入力、第三者の識別情報は保存しない。20件が揃ったときの中央値は、同じmetricを昇順に並べた10番目と11番目の算術平均（偶数20件の定義）とする。UC-CI 10件・UC-LLM 10件を基本割当とし、各UC5件以上だけでは全体中央値の代替にしない。

| 測定値 | 単位 | 比較方法 | PAC04の集約 |
|---|---|---|---|
| active work | ns | 待ち時間を除く実作業。中央値を主metric | 同一20件の中央値 |
| wall wait | ns | queue・モデル待ち・回収待ち。主metricから別掲 | 中央値と総量を参考表示 |
| model/tool calls、token | count | 既存手順とGAHを同じ20件で計数 | 合計。未知はINCONCLUSIVE |
| CPU time、cost | ns / 整数micro-USD（1 USD = 1,000,000） | 同一20件で予約・使用・精算状態を分けて計数 | 合計。異種単位を加算しない |
| peak RSS、storage | bytes | 各観測の同時peak/最大を固定測定 | 20件の最大。欠測はINCONCLUSIVE |
| human intervention | count | 承認・追加情報・手動復旧の依頼回数 | 合計。中央値だけで判定しない |
| false-alert handling | count、ns | 誤警報の確認・差戻しを別計数 | 合計と中央値を表示 |
| correctness | enum | independent oracleとEvidenceで照合 | 全件一致を必須 |

### 6.2 30%削減と費用条件

既存手順のactive work中央値を `M_legacy`、GAH手順を `M_gah` とし、削減率を次で固定する。

`reduction = (M_legacy - M_gah) / M_legacy`

`M_legacy` が0、欠測、手順なし、正解不明の場合は率を0や無限大へ補完せず、PAC04を未実証とする。PAC04の時間条件は `reduction >= 0.30`、対応観測20件のhuman intervention合計を増やさない、CPU/calls/token/costの同集合合計を増やさない、peak RSSとstorageの同集合最大を増やさない、全資源次元を欠測なく測る、誤警報の処理を隠さない、である。CPU時間、peak RSS、storage、呼出数、token、金額は異なる単位のまま別々に比較し、異種値を単純加算した「総資源点数」や任意の重み、都合のよい次元除外で合格にしない。wall waitはactive workと分けて提示し、待ち時間の短縮だけで30%達成とはしない。

費用は事前採択したPolicyProfileのPR USD 2、full USD 10、直近24時間USD 20の既存上限内で、未解消予約額と確定実額を記録する。精算済み予約は実額へ置き換え、予約と実額を二重計上しない。単価・token・使用量が不明な観測は費用0にせず、`INCONCLUSIVE` とする。外部課金APIを使わない固定fixtureの費用と、許可済みtargetの実費を同じmetricへ混ぜない。

### 6.3 観測期間と終了

許可・計画・baselineが固定された翌営業日をday 1とし、原則10営業日で中間確認する。不足が合理的に解消可能な場合だけ最大20営業日まで継続し、day 20で一度閉じる。必要件数不足、比較相手なし、許可失効、oracle不明、資源・費用未精算は不足のまま `INCONCLUSIVE` または `FAIL` とし、期間を延長して分母や判定を隠さない。

## 7. GAH-PR14 / GAH-PAC14: 管理AI・証拠・状態

基準管理AIは対象、許可、oracle、baseline、測定計画、許容範囲を開始前に提案し、validatorが決定的に照合する。候補AIはproposalを作れても、permission、oracle、threshold、expected label、Finding、結果artifactの採択権限を持たない。人間承認は通常の各runに都度追加せず、対象許可と計画変更の責任境界に限定する。

### 7.1 artifactとEvidence

pilotのartifact kindは計画上、`project_binding`、`evaluation_target_binding`、`pilot_plan`、`pilot_selection`、`pilot_baseline`、`pilot_oracle`、`pilot_observation`、`pilot_result`、`pilot_acceptance` とする。計画系kindの固有fieldは親共通外枠の `payload` に置く。各artifactは `kind/id/digest` で参照し、同じkind/idの異内容を再配送として受け入れない。これらは現行allowlistへ登録済みの新Schemaではない。

Evidenceは現行保存契約の `evidence_id`、`subject_ref`、`artifact_ref`、`conditions_ref`、`producer_ref`、`observed_at`、`collected_at`、`retention_until` をそのまま再利用し、既存Evidence envelopeへ `evidence_type` を追加しない。design、execution、acceptanceの分類は専用 `pilot_plan` / `pilot_observation` / `pilot_acceptance` artifactのpayload metadata `evidence_phase`（閉じたenum）とそのartifact kindで表す。designは計画固定の証拠、executionは観測の証拠、acceptanceはPAC判定の証拠であり、designだけで実行済み・受入済みにはならない。pilot EvidenceはCI成功根拠ではなく、常に `ci_eligible=false` である。

`pilot_result` のpayloadは `pilot_id`、`plan_revision_ref`、`pac_status`、`source_refs`、`baseline_ref`、`scope`、`counts`、`missing`、`unknown`、`conflicts`、`cost`、`human_intervention`、`evidence_refs`、`limitations`、`created_at` を返す。補助操作の `operation_status`、`result_ref`、`reasons` は親共通の `extension_operation_result` 外枠へ委譲し、同じ結果を二重定義しない。`pac_status` は `NOT_RUN`、`PASS`、`FAIL`、`INCONCLUSIVE` の4値だけとし、操作状態と混ぜない。

### 7.2 状態遷移とPR/PAC対応

計画の状態は親仕様に合わせて `DRAFT` → `VALIDATED` → `ADOPTED` とし、採択後は `REVOKED` / `EXPIRED` へ遷移し得る。許可・revision・capability・oracle・baselineが揃わない `VALIDATED` は発行しない。pilot実行の状態は別fieldで `PLANNED` → `READY` → `RUNNING` → `COMPLETED` / `REJECTED` / `INCOMPLETE` / `CANCELLED` とし、`operation_status` と計画採択状態を混ぜない。PACは操作が完了しても自動でPASSへ進めず、必要な独立Evidenceと全件照合が揃ったときだけvalidatorが確定する。

計画の更新・結果照会は、認証済みprincipal、固定command、request_idの組で冪等にする。同じ入力digestの再配送は保存済みresult_refへ戻し、異なるdigestは `IDEMPOTENCY_CONFLICT` としてREJECTEDにする。新しいprincipalが既存requestの結果や採択権限を取得してはならない。採択は既存authorityのexpected generation、現在権限、失効、期限、receiptを原子的に再照合し、pilotのpayloadに書かれたroleや採択済みフラグだけでは成功にしない。

GAH-PR01〜04/14の状態artifactには、対象source、plan_revision、selection、baseline、実行結果、根拠ref、制約、未対応範囲、次の操作を含める。PAC01〜04/14は全て最初 `NOT_RUN` とし、欠測・不明・未精算・oracle不一致はPASSにしない。実行後の閾値引下げ、集合差替え、除外追加、対象版の変更、結果の上書きは、旧結果を保持した新plan_revisionと理由を要求する。

文書検収、技術受入（P0）、実案件有用性（PAC02〜04）、拡張全体、公開状態を別artifactで表示する。文書が完成しても製品が完成したとは表示しない。PR14の受入は、各PR/PACの追跡可能性、失敗保持、変更理由、残る制約の欠落がないことを確認する。

## 8. 受入シナリオ（設計段階）

| ID | 入力・状況 | 期待する状態・結果 |
|---|---|---|
| S01 | 許可済み二repo・一target、固定revision、capability完全 | binding/manifestを登録できる。PAC01は実行前のためNOT_RUN |
| S02 | revision digest不一致、期限切れpermission、owner不一致 | fixture代替せずREJECTED。秘密や入力本文を結果へ出さない |
| S03 | 未対応capability、未採択adapter、未知target版 | `unsupported` を保存。既知不成立はoperation_status=REJECTED/1、版の根拠不足はINCOMPLETE/2。PACは未開始NOT_RUN/観測不足INCONCLUSIVE |
| S04 | 同じbinding_idで別revisionを再登録 | 旧artifact不変、新revision必須。上書きしない |
| S05 | GAH-PSC03: clean100、history20、各repo10、既知必須miss0、実劣化再検証1、校正不一致0、誤action通知5、誤Critical通知0 | PAC02 PASS、pilot `ci_eligible=false` |
| S06 | GAH-PSC04: 既知Critical miss 1件 | PAC02 FAIL、pilot `ci_eligible=false` |
| S07 | GAH-PSC05: positive/negative各200、category正負各100、独立oracle、算術再現、全測定義務、targetはDEGRADED | 測定はPASSでもtarget assuranceはDEGRADED、pilot `ci_eligible=false` |
| S08 | GAH-PSC06: positive既知200、negative既知195、label unknown5 | PAC03 INCONCLUSIVE、negative分母195、unknown5 |
| S09 | GAH-PSC07: 対応観測20（UC-CI10/UC-LLM10）、baseline中央値100,000,000,000ns、candidate中央値70,000,000,000ns、資源・介入非増加 | reduction=30/100、PAC04 PASS、pilot `ci_eligible=false` |
| S10 | GAH-PSC08: baseline/candidate中央値0 | reduction=null、PAC04 INCONCLUSIVE |
| S11 | 同一attemptの同内容再配送／異内容再配送 | 前者は一件＋duplicate count、後者はCONFLICTで採用しない |
| S12 | baselineとcandidateでpack、evaluator、policyが異なる | changed_axesなしの比較を拒否し、新planまたはINCONCLUSIVE |
| S13 | GAH-PSC31 / GAH-PSC32: candidateが採択要求、またはvalidator後にpermission失効 | operation_status=REJECTED、exit_code=1、adopted=false |
| S14 | GAH-PSC33 / GAH-PSC34: 既知未達と必須測定欠損が同居、または文書検査だけ成功 | 前者はoperation_status=INCOMPLETE/2かつPAC FAIL、missing reasonを保持。後者はPAC NOT_RUN |
| S15 | `gah_run`完了でもfresh `gah_ci`が不成立、または保存失敗 | pilot `ci_eligible=false`、operation_statusとPACを分離。成功へ昇格しない |

## 9. 実装差分と未確定入力

現行コードで確認できる実装範囲は、binding/artifactの厳密な登録検査、pilot planのcanonical ref/digestと未知field/typeの検査、history/LLM/maintenanceの決定的算術、pilot_authorityの独立validator/managerによるmetadataのcurrent/adoption照会、固定sampleのresult/status保存である。これらは技術的な入口と局所試験であり、実案件の有用性やPACのPASSを示さない。

未実装の実行差分は、利用者が許可した任意repoの不変snapshotを取り込んで既存固定runnerへ安全に渡し、before/afterを実行する経路、実LLM対象版の入力・実行connector、外部connector、全子processのCPU/RSS/IO/copy/call/token/cost等の資源計測である。gah_pilot executeはmetadata採択だけで製品run authorityを発行せず、未接続をINCOMPLETEとして保持する。実案件の20観測や10営業日の運用を収集・受入する機能も未実装である。

外部入力待ちは、利用許可を確認した二つのrepoと一つのLLM評価対象、その版・設定・adapter/evaluatorの固定ref、owner/permission scope/期限、独立oracleとbefore/after・baseline/candidateの条件、UC-CI/UC-LLMの必要観測集合、費用・計測環境である。PR01〜04の実案件条件に必要な20組/20観測、正常・検知集合、10営業日の観測期間などは、入力と証拠が揃うまで未確定とする。

固定sample、合成観測、validator/authorityの局所試験成功は実案件の代替にしない。GAH-PAC01〜04は NOT_RUN のままとし、全14要件の現状は [productization-status.md](productization-status.md) を参照する。設計証拠・技術試験・実行証拠・製品受入は別に記録し、未実装を実測待ちやPASSへ読み替えない。


## 継続実装: offline評価結果のimport

`python -m tools.gah_pilot import --workspace <根> --input <JSON> --output <JSON> [--request-id <ID>]` を追加した。入力kindは`pilot_input_bundle`、出力は`pilot_input_import`。source/target/dataset/oracle/revision/splitの参照descriptor、case ID、期待/観測/欠損/unknownを厳密に検査し、UC-CIの一致・不一致とUC-LLMのTP/FN/TN/FP・分母・カテゴリを算出する。

任意shell、URL実行、prompt、secret本文は入力契約に含めない。workspace外、link/reparse、サイズ超過、重複、不正型を拒否する。生成artifactは入力から決定的に作り、`checked_at`は入力の`created_at`に等しい記録時点で、現在認証を示さない。現在のCLI操作時刻は共通resultに保存する。明示した不正IDを新しいIDへ置換せず、同principal/command/request IDと入力・出力先digestをjournalへ結び、異内容再配送を拒否する。

出力は常にPENDING/NOT_RUNで、外部ref検証・独立oracle確認・評価完了・製品run権限・CI可否はfalse。offline結果の算術正規化は実装したが、対象repo/LLMへ接続して実行するadapter、独立した出所確認、実案件の採択・受入は未完了である。
