---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# 固定合成ガードレールの認証付き実行

[LLM接続仕様](llm-authority-detail-spec.md)のうち、自作の有限ガードレールによる実入力・正規化・資源・保存の接続を定める。対象は学習済みLLMではない。今回のMVP受入には固定合成400ケースと対象2版を用いる。学習済みモデル一般の性能保証や任意providerの自動起動を追加の必須条件にしない。generic command/Promptfooの取込み・正規化・raw追跡はGAH-AC08で扱い、[受入範囲](mvp-completion-audit.md)と区別する。

## 対象・評価器・実行器

二カテゴリの既存400ケースを使い、targetにはinput本文だけを渡す。期待ラベル・oracle・用途名・採択権限を渡さない。targetはdetect/allow/indeterminateとapply/block/deferだけを返す。workerが独立したcounterへ有限操作を適用し、その変化と各段階の観測を記録する。

baseline-v1とdegraded-v2は事前固定した自作実装の二版で、受入集合ではそれぞれTP196/FN4/FP4/TN196とTP184/FN16/FP2/TN198を生成する。これは合成の有限集合を実行した結果であり、神経モデルや未知入力の推定性能ではない。

対象参照はソース内容・behavior版・不変image IDを含む。評価器参照は対象版から分離し、正規化・測定校正・packを固定する。execution profile v2はtarget/evaluatorの組からworker/adapterを一意に選ぶ。余分な組、未知の組、異なる環境、別workerの結果を拒否する。

## 初回候補と校正

managerだけがguardrail_prepareを呼べる。現在採択されたPolicyProfileとpermission世代を使い、400ケース、600stage、固定plan・manifest・target・evaluatorを生成する。応答へ大きいpackを重複同梱せず、実体は固定factoryと参照で再照合する。保存は既存fixture_admissionsの明示kindを使い、読み取り側も同じfactoryで一致を確認する。

測定系は受入実行より先に独立した165vectorで校正する。対象18ケースの一致率を測定系の校正と読み替えない。固定factoryのcacheはソースdigest・完全な入力・lockをkeyとし、保存DBの値を正解としてcacheしない。校正失敗・入力改変・権限世代不一致では採択しない。

初回契約は既存の提案、独立validatorの検証、managerの採択を通す。run_begin、evidence_openも既存の採択・現在条件検査を使う。候補HEALTHYと通常CIの成功は区別する。

## 資源と隔離

固定合成workerは外部モデルを呼ばず、network noneで実行する。case_trial_executionsを1ケース分予約し、1段階または2段階を同じケース操作へ結ぶ。model_calls/input_tokens/output_tokens/従量費はこの非ML処理では0である。この予約を実モデルのstage単位予約に流用しない。

runnerはread-only root、固定UID65532、capability除去、no-new-privileges、CPU・memory・PID上限とtmpfsを実containerから検査する。imageのOS・CPU種別・entrypoint・source labelも検査する。各caseは別containerとcounter初期値0で開始する。入力・出力は64 KiBで制限する。

journalの操作digestには全stageの入力とbindingを含める。同一操作の再配送は保存receiptを返し、変更した二段階目を新しい成功として受け入れない。未解決の送信は再送せず、同じ所有containerだけを停止・回収する。worker出力から起動コマンド、URL、保存先、権限を追加しない。

## 観測と保存

validatorが停止・usageを登録した後に各stageのAttemptを保存する。実行ID、owner epoch、契約、対象、評価器、adapter、入力、段階順、観測効果を照合する。未知usageや停止不明を完了として埋めない。全操作の停止・精算と全予定stageの根拠が揃った時だけEvidenceをfinalizeし、既存の初回baseline採択へ渡す。

runtimeのclient再利用とclientの常駐は、それぞれ明示オプションとする。固定UID・固定mountと所有実体・設定を要求ごとに確認し、各要求を新しいプロセスで処理する。応答cacheを現在の認証や採択の代用にしない。失敗したclientは所有実体を確認して除去する。常駐clientを使う場合も、評価caseごとのworker分離は維持する。

## 検証と残件

[以前の追加証跡](evidence/mvp-acceptance-20260913/llm-integration-summary.json)は、初回採択の3統合試験、新旧runnerの33試験、実Dockerの計2ケースを記録する。固定source81では[実Docker400ケース・600段階と16項目](evidence/mvp-acceptance-20260913/runtime-restart-20260915-summary.json)が成功し、初回baseline採択・精算・再起動・回収を確認した。

通常LLM・後続契約3/基準3・両用途run・独立した修復確認・保持/削除の統合試験は成功した。実Dockerの旧400/新800比較・独立採択と通常CLI800、freshなCI・表示・再起動後不変・全精算・回収が成功した。時計同期設定は検証後に復元し、全32条件を技術検収済みとした。判定は[対応表](evidence/mvp-acceptance-20260913/acceptance-map.json)を正本とする。要求・受入条件・初期閾値・予算は変更しない。


## 実時刻の記録と分割候補（2026-09-13）

最初の400ケース実行では、二段階へケース全体の時間帯を記録し、段階の重なりを集計が拒否した。修正版workerは段階ごとの開始・終了を実測し、後退・重なりを拒否する。authorityは同じ実行環境の配送・worker段階・停止時刻を照合する。ホストの計測時刻はsupervisor_host_utc、worker/authorityはauthority_runtime_utcとして分け、異なる時計領域の値を直接比較しない。時刻の補正や順序検査の緩和は行わない。

受入前には使用するimageで時計の逆行・時刻飛びを確認する。時刻不整合による失敗は保存し、停止・精算を確認してから新規runを開始する。今回のWSLでは再起動だけでは解消せず、時刻同期経路の一時調整後に120秒の測定が成功した。この調整を製品による時計操作や恒久的な環境修正とは扱わず、実行条件と復元結果を証跡へ残す。

400ケースの初回baselineは合成oracleのkindを保持して完全参照を照合する。旧400・新800の比較候補はtransition/old/newを別artifactへ保存する。候補refは各節の完全refを持つ小さい保存rootを指し、取得時には全節を再導出して照合する。1MiB上限は維持する。旧800・新800となる後続世代で一つの節がこの上限を超える場合は、正規化した本文を256KiBごとのbase64断片へ分け、v2の節に順序付き完全参照・本文byte数・本文digestを保存する。一つの節の復元上限は4MiB・16断片とする。各artifactは引き続き1MiB以下で、通常の小さい節のv1参照は変えない。復元時は所有候補、節名、断片IDと順序、型、byte数、正規化表現、全参照と本文digestを照合する。欠損、未知版、余分なfield、改変は候補不正として拒否し、保存失敗は呼出元transactionで全体をrollbackする。片側読取りはrun開始と分離する。[修正検証](evidence/mvp-acceptance-20260913/llm-corrections-summary.json)に対象版を記録する。

## 既知v4 validatorの移行後照合

既知の初回DBの明示移行では保存済みvalidator値と採択履歴を保持し、extension digestだけを現行へ更新する。方針検証が同一の既知旧v4 validatorはschema4の既存採択照会でのみ互換対象とする。未採択の旧提案は現行で提案・検証をやり直す。現在の期限・権限・撤回・出力hashの検査を保持する。移行の行保持だけで起動可能とは判定せず、既定factoryでの起動、現在利用、再起動後のreceipt不変を確認する。
