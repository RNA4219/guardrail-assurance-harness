---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 評価契約 v0.3

[要求](../requirements.md)を実装へ渡す論理契約。以下の全MVPオブジェクトのフィールド名とenumは設計案である。初期実装の部品Schema・API・診断CLIは[詳細仕様](../detail-spec.md)に分け、この全契約を完成済みとは扱わない。[初期値JSON](initial-policy.v1.json)、[算術例](decision-examples.v1.json)、[状態と境界の追加例](review-examples.v2.json)も設計資料である。v0.2の補正を保ち、v0.3では[初回・採択・再開・終了の契約](lifecycle-contract.md)を加えた。複合条件には同補足の確定時検査と優先順を適用する。

## 1. 共通の規則

IDは同じ種類・版内で一意、参照は種類・ID・内容digestを組にする。digestは保存artifactのbytesを対象にし、単なるpathや可変のモデル名で同一性を確定しない。別内容を同じIDへ上書きしない。入力に重複key、不正数値、未対応版、必須field欠落があれば拒否する。未知fieldを黙って権限や既定成功に読み替えない。互換性は正式Schemaの採択時に固定する。

時刻はUTC、期間は整数秒、費用の予算比較はUSDの百万分の一を単位とする非負整数、token・件数は非負整数とする。費用の元の精度と会計用上界を分け、細かな金額は[LC04](lifecycle-contract.md#lc04-費用の精度と精算)により切り上げる。率は整数の分子/正の分母で保持する。0分母・欠損は数値の0に置換しない。表示丸めは判定後に行う。

artifactのdigestは完全性の検査材料であり、作成主体の認証ではない。認証情報は実行境界から取得し、対象が出力した`actor_id`等を信用しない。検査入力・出力・説明内の文字列は実行命令として評価しない。

## 2. オブジェクトと参照

| オブジェクト | 必須の情報 | 作成・確定主体 |
|---|---|---|
| PolicyProfile | policy_ref、目的、閾値、差分、鮮度、予算、CI成功状態、変更可能範囲 | 初期方針を信頼起点に管理境界が検査 |
| Control | control_id、owner、invariant、criticality、target_ref、dependencies、obligations、mutation_applicability | 基準管理AIの提案を管理境界が採択 |
| EvaluationContract | contract_ref、generation、policy_ref、controls、required_categories、case_set_ref、evaluator_refs、comparison、exclusions、required_outputs | 管理境界が不変版として確定 |
| BootstrapContract | 配布物の版、固定初期方針・検査、許可目的、出力・予算・データ境界 | 信頼する配布物。初回専用で回帰CIに使用不可 |
| CaseDefinition | case_id、case_digest、lineage_group、purpose、category、expected_label、oracle_ref、session_steps、scored_stage_id、initial_state_ref | 管理AIが固定oracleの根拠と用途を添えて採択 |
| RunManifest | run_id、contract_ref、purpose、use_cases、target_refs、scope、baseline_ref、plan_ref、profile_ref、environment_ref、actor_context_ref | trustedな呼出情報を実行監督が固定 |
| RunLease / OperationRecord | run_id、owner_ref、owner_epoch、所有期限 / operation_id、dispatch意図、予約・終了・精算の状態 | 実行監督。LC03の所有世代を照合 |
| TrialPlan | obligation_id、case_id、trial_id、variant、stage_ids、required、event_policy、evaluator_ref | 実行前に監督側で確定 |
| AttemptRecord | run/contract/target/obligation/case/trial/stageのbinding、attempt_id、retry_of、開始/終了/停止状態、資源予約と観測、raw_artifact_ref | 実行監督が記録 |
| NormalizedResult | binding、observation、detection、deviation、mutation_outcome、evidence_refs、error_class、adapter_ref | adapter解析後に照合処理が採用 |
| Evidence | evidence_id、subject_ref、observed_at、collected_at、producer_ref、artifact_ref、conditions_ref、revocation_ref、retention_until | 保存処理が確定 |
| Decision | decision_id、run_ref、assessed_at、evidence_state_ref、metrics、comparisons、coverage、各UCと全体のassurance、reason_codes、valid_until | 決定的な判定処理 |
| RunReceipt | run_ref、decision_ref、execution_status、outputs_status、artifact_refs、expected_binding、ci_eligible、exit_code | 必要成果物の保存完了後に確定 |
| AdoptionRecord | request_id、認証済みactor_ref、context_ref、expected_generation、旧/新ref、理由、diff_ref、validation_refs、採否と時刻 | 管理境界 |
| ReuseRecord | source_result_ref、source_observation_id、元のbinding、利用先のobligation/trial、reuse_policy_ref、照合根拠 | 新規観測を作らず監督側が参照を結ぶ |
| UseDecision | receipt_ref、checked_at、呼出側のexpected_binding、最新の失効世代、budget_closure_ref、利用可否と理由 | CI利用時の決定的な再照合 |
| Finding / Plan | finding_id、元の条件・根拠、推定原因と確度、状態、再検証ref / 変更案、保持条件、検証、展開/復旧の前提、権限と不足情報 | 判定/計画処理。採択や実行権限は持たない |

`required`は完了義務、`event_policy`は各事象の禁止条件、指標閾値は集合条件。これらを別に保持する。必須LLMケースでFP/FNを観測しても、全件正答を要求していなければ集合指標により判定する。

AdoptionRecordは[LC02](lifecycle-contract.md#lc02-採択の確定時検査)の提案digestと確定時の検査世代も必要とする。AttemptRecordのraw保存は[LC05](lifecycle-contract.md#lc05-保存入力送信の前のデータ境界)の事前検査を通す。保存拒否時はraw参照を捏造せず、data_dispositionと欠落理由を記録する。

`baseline_ref`は比較を要する目的では必須。初回候補作成では明示的なnullと`comparison_mode=not_applicable`、目的による非適用理由を記録する。通常の回帰目的は`comparison_mode=required`であり、参照が未指定なら入力拒否、指定された根拠が取得不能・失効なら必須比較の不足として扱う。欠損を初回目的に変更して進めない。

## 3. 実行状態と採用規則

実行状態はCREATED → VALIDATED → RUNNING → COLLECTING → FINALIZING → COMPLETED。各段階からFAILEDまたはCANCELLEDへ移れる。停止要求はイベントであり、停止確認まではCANCELLEDにしない。停止確認不能は未完了とFAILEDを記録する。COMPLETEDは処理と必要成果物の完了で、Assuranceの合格を意味しない。

| 入力・事象 | 採用規則 |
|---|---|
| 同一試行の同一内容を再配送 | 内容digestとbindingを照合し一件に畳む。配送回数は保持 |
| 同一attemptに異なる結果 | 整合性不成立としてHOLD。先着または高得点を選ばない |
| 未計画case、別run/対象/契約、未対応段階 | 採用拒否。評価結果に混入した場合は整合性不成立としてHOLD |
| 正当な通信・実行障害 | 元の呼出しに対し一回だけ、同じ論理試行の新attemptで再試行可。費用・call・tokenは双方を数える |
| 初回がPASS/FAIL等の確定観測 | 結果を変えるための再試行は不可 |
| 初回の終了/結果が不明 | 再試行開始を保留。結果が届いていても元の終了と採用可否を確認するまで未確定とする |
| 初回の確定結果が遅延して到着 | 再試行結果との整合を検査する。矛盾はHOLD。都合のよい方を選ばない |
| run取消し確定後の遅延結果 | 履歴として保存可能だがterminal receiptとCI結果を変更しない |
| キャッシュ結果 | 開始前の再利用許可と全条件・鮮度を照合し、ReuseRecordを作る。新規観測・独立試行を要求する義務の代用にしない |

評価ケース/試行予算は実際に開始する論理ケース・試行の各実行を数え、baseline/candidateと再試行も消費する。セッション内の段階は同じケース・試行に属し、各モデルcallとtokenは別途すべて加算する。呼出し予約を親子で二重計上せず、子の実処理を台帳上で識別する。

初期実装では、終了状態が不明な呼出しの再試行開始を保留する。監督側が元の終了（正常終了・実行障害による終了・停止完了）を確認し、再試行可能な実行障害と確定してから、残予算を予約して一回だけ開始する。終了確認と料金確定は別イベント。終了しただけでは料金未確定の予約を解放しない。run全体の取消し後は再試行しない。

二段階caseの再試行では、対象段階の実行前状態を検証済みのsnapshot等から復元できることを追加条件にする。途中状態を残したまま同じ段階を呼び直さない。副作用がないと確認できる単発呼出しも、その根拠を記録する。初期状態を確認できなければ再試行せず、その義務をERRORにする。通信・実行障害でない確定済みの不合格は再試行しない。

再利用は元のrun・trial・観測IDを保持し、別runの結果に利用先のIDを付け直さない。独立した複数試行を要求した場合、一つのsource_observation_idを複数試行の充足に数えない。複数Controlが同じ観測を根拠として参照することと、複数観測として指標へ加算することを分ける。候補とbaselineの差分として宣言した軸を、再利用条件の一致から除外しない。

## 4. 計測・比較・欠損

固定した予定義務を分母として、完了・未実行・ERROR・判定不能・根拠付き除外・未確定除外を別計数する。除外で元の予定数を消さない。対象外の指標は開始前の目的との非適用理由を必要とし、失敗後に対象外へ変えない。

TP/FP/TN/FNは要検知/正常の既知ラベルと検知結果が確定した集合で数える。Recall=TP/(TP+FN)、FNR=FN/(TP+FN)、FPR=FP/(FP+TN)。Mutation Score=KILLED/(KILLED+SURVIVED+NO_COVERAGE)。ASRは独立に観測した逸脱成立数/判定可能な逸脱ケース数とし、検出率の補数にしない。

必要な欠損が一件でもあれば、観測済み部分の率を表示できてもそれだけで合格にしない。根拠付きの除外は当該除外審査の完了と計測分母への影響を分け、必須カテゴリ・最低件数を失えば不足を残す。Critical対象がない場合は対象なしとする。

UC-LLMの既定集計単位はケースの一試行（セッション）。各caseで採点する一つの`scored_stage_id`とその期待ラベルを開始前に固定する。単発はその段階、二段階は前段の状態・観測を保持したうえで指定段階の検知結果をTP/FP/TN/FNへ一件として集計する。別段階の検知で採点段階の見逃しを相殺しない。全必須段階の観測を必要とし、段階の未確定・欠損があれば確定分類に入れず、不足数へ一試行として記録する。段階数で分母や固有case数を増やさない。各段階の期待条件・禁止違反は別に保持し、後段の分類が良くても前段の必須違反を消さない。複数段階を採点する指標やany_stage集約はMVPのこのprofileでは非対応とし、別の集計契約を必要とする。

率の上限n/dとの比較は整数の交差積を使う。baselineからの増減は二つの分数の差で比べ、百分率表示を丸めて判定しない。絶対条件と差分条件を両方検査する。

0分母は分子も0であっても評価不能とし、差分条件も計算不能。NO_COVERAGEが一件以上あればMutation Scoreの分母は正で、その分だけ得点を下げる。全件除外などで分母が0になった場合も100%にはしない。

比較は指標・カテゴリごとに、ケース/ラベル/oracle/反復・集計規則/環境/予算/方針/評価器/初期状態を照合する。変更可能な軸は実行前に固定した評価対象の差分に限る。共通evaluator等の差は影響する全指標を比較不能にする。比較できる部分の差分は保持するが、必須の比較不能を消さない。母集団推定は初期profileで拒否する。

## 5. 予算・時刻・保存

上限は[初期値](initial-policy.v1.json)から固定し、資源の種類で扱いを分ける。ケース/試行、call、token、費用は「確定消費＋未解消予約＋新予約」がrunの上限以下の場合に開始できる。費用はさらに全runの24時間台帳へ同じ予約を一度だけ反映する。run枠と全体枠の判定・予約を原子的に行い、同時runが同じ残枠を使わない。単価・token等の根拠不明時は開始しない。

並列枠は現在稼働中＋確保済み＋新しい稼働数で検査し、終了確認後に解放する。過去のcall数を並列枠の消費として残さない。経過時間はrun開始からの単調時計によるdeadlineで判定し、並行処理の所要時間を足さない。開始前に一呼出しの期限を残り時間以下へ制限し、上限と同時刻では新規処理を開始しない。最終保存までをrun時間に含め、期限後に計画やreceiptができたrunを成功にしない。

管理AIも管理runとして全対象profileを消費する。外部API費用は確定時刻が(T−24時間,T]にある確定額と、年齢を問わない未解消予約の合計にする。確定時は予約を同じ操作の確定額へ置換する。実際額が予約を超えたら超過・見積不成立を記録して新規開始を止め、過去の予約を偽って直さない。並列枠の確保、終了確認、解放も監督側で行う。

成功を確定する前に、全子処理の終了・稼働枠の解放・必要な使用量の計測・費用の確定または非課金の根拠を照合したbudget_closureを必要とする。見積上限を予約済みでも、料金不明をWARNINGだけで済ませない。期限までに必要な精算情報が揃わなければFAILED/終了コード2とし、Assuranceは既知の理由を残しつつ必須不足として少なくともUNKNOWN、Criticalの根拠不足ならHOLDとする。後日料金が判明しても古いreceiptを成功へ書換えず、新しい照合記録を作る。

run・全体の費用や実行境界の上限を実際に超えた場合はHOLDとする。予約額超過で上限遵守の根拠が失われた場合も、HOLDとして再確認する。予算終了後に診断を続ける必要がある場合は、新しい許可済み診断runとして別途予算を確保する。未解消予約を解放せず継承し、評価完了の代用にはしない。

時間はrunの経過測定に単調時計、証拠/費用の期間判定に根拠のあるUTCを使う。再起動時は[LC03](lifecycle-contract.md#lc03-所有世代と再開)に従って停止期間を含む経過・予約・所有世代を復元し、復元できなければ再開して成功にしない。未来の観測、時刻の根拠不足は有効な証拠にしない。

現在証拠は24時間以下、baseline比較証拠は30日以下。`valid_until`は必要証拠それぞれの期限と保持期限の最小値以下にする。撤回、削除、条件変更は期限前でも利用不可。削除後は消した内容を復元せず、許可された最小の参照・撤回記録から影響を表示する。保持期間の初期設定は実装準備で決め、24時間/30日という鮮度と混同しない。

## 6. 判定とCI返却

判定理由はcontrol/obligation/metric/evidenceの参照を持ち、原因をGUARDRAIL、CHECKER、EXECUTION、INTEGRITY、UNKNOWNへ分ける。原因の推測を状態判定の事実として採用しない。

1. 入力・権限・整合性不成立、Criticalの違反/必須不足等 → HOLD。
2. それ以外の必須事象の違反、絶対/差分閾値違反 → DEGRADED。
3. それ以外の必須評価/比較/指標不足 → UNKNOWN。
4. 必須条件が成立し、任意注意または累積予算80%以上 → WARNING。
5. 残り → HEALTHY。

理由は優先順と安定したID順で出し、決定理由以外も保存する。各UC・Controlと全体に同じ優先規則を適用する。対象外はHEALTHYで水増ししない。

CIの成功は、期待する目的・内容・契約・範囲・有効期限に一致し、COMPLETEDかつ必要成果物保存済み、AssuranceがHEALTHY/WARNINGの場合だけ。部分範囲とbaseline候補作成の成功は、その目的のreceiptでしかない。呼出側の期待bindingはcandidateの結果本文から逆算しない。

Assurance算出と保存の間にも証拠は失効し得る。最終保存では、必要artifactとbudget_closureを照合し、取消し状態・失効世代・deadlineを再確認してreceiptを確定する。検査した失効世代が変わった場合はそのまま成功を確定しない。基準時刻と有効期限の一致は境界を含めて許可し、既に期限を過ぎた証拠は使用しない。

確定済みreceiptは当時の観測と判定として不変にする。一方、確定後の撤回・削除・矛盾する遅延結果は別イベントで失効台帳に追加し、依存する現在の利用可否へ反映する。矛盾があれば現在のAssuranceはHOLD。取消しreceiptを後着PASSで成功にしない。CI利用時は最新台帳と現在時刻からUseDecisionを作り、台帳を確認できない場合も成功にしない。以前出力済みの終了コードを遡って変更したとは主張しない。

この設計の保証は各照会時点まで。CIへの結果受渡し後に起きた撤回を外部の保護設定へ即時反映する保証はMVPに含めない。必要な利用側は操作直前に再照会し、期待bindingと失効世代を照合する。

| 終了コード案 | 条件 |
|---|---|
| 0 | 上記のCI成功条件が全て成立 |
| 1 | 完了した判定の不合格、または目的/内容/範囲/期限が呼出側の期待に合わない |
| 2 | 入力拒否、実行・保存・出力・整合性の障害で正常な処理完了が成立しない |
| 3 | 停止が確認された取消し |

条件が重なる場合は[LC06](lifecycle-contract.md#lc06-終了状態が重なる場合)の順序を適用する。2または3でも取得済みのAssuranceと理由は可能な限り返す。Critical違反＋取消しはHOLDを保持し、停止確認と取消し記録の保存/出力が完了した場合は3、保存障害を伴えば2。必須結果欠損が確定し不足報告を保存できたrunはCOMPLETED/UNKNOWN（CriticalならHOLD）/1になり得る。成果物を保存できない失敗は2。プロセス自体の異常終了は実際の非ゼロを保持し、0へ変換しない。

初回候補作成の目的で比較を開始前に非適用とした場合、絶対条件・校正・必要根拠・出力が成立すれば、その目的の終了コードは0になり得る。同じreceiptを通常の回帰CIへ渡した場合は目的不一致で1。通常目的でbaseline参照自体がない入力や循環依存の入力拒否はHOLD/2。参照済みbaselineの根拠不足はUNKNOWN/1（CriticalならHOLD/1）とする。

HOLDは管理下の新たな非許可処理とCI成功を止める。根拠の読取り、診断、元の許可範囲内の再検査は継続できる。対象本番を自動停止する効力はなく、再判定も新しい有効根拠から計算し、statusの書換えで解除しない。

## 7. Findingと計画

FindingはOPEN、IN_PROGRESS、AWAITING_REVALIDATION、VERIFIEDを区別する。計画生成はOPENをVERIFIEDにしない。新しい有効Evidenceで元の対象・条件を再検査し、確認主体を結んだ場合だけVERIFIEDにできる。再発は元のfinding_idを参照する新しい記録にする。

基準改訂・対象廃止は別のdispositionとして記録し、閾値緩和や検査削除だけを修復証明にしない。計画はYAML出力時も必須項目と参照を検査する。任意LLMが使えなければ定型骨子と不足を出す。Findingなしの場合は計画不要の理由を明示する。計画の実行機能はMVPに含めない。
