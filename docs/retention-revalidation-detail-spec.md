---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-13
next_review_due: 2026-10-13
---

# authorityの保持・削除と修復確認の接続仕様

元条件と新条件の比較には[意味的条件の部品](semantic-conditions-detail-spec.md)を用いる。対象内容の変更、改名、条件変更を分けるが、比較成立だけではVERIFIEDにしない。

[保存](run-evidence-detail-spec.md)と[修復計画](remediation-detail-spec.md)の部品を、認証されたauthorityの保存実体へ結ぶ設計である。現在のArtifactStoreの削除やFinding部品の候補状態だけで、authority保存の保持運用・VERIFIEDを完了としない。

## 保持期限・撤回・削除

Evidenceのvalid_until、保存本文のretention_until、利用禁止のrevocationを別に扱う。Evidenceが期限切れでも保存監査に必要な本文が存在し得る。本文が保存されていることだけではCIに利用できない。

保持方針はmanagerが既存PolicyProfileと保存要件を満たす版として管理する。operatorが対象の保持状態を照会し、managerが完全参照を固定した削除計画を作る。operatorが有効な計画を適用する。通常のroutine操作は生成AI主体で進めるが、法的保全や明示された保存保留があれば削除対象から外す。

削除は本文を取得不能にする処理と、不変tombstoneの保存を同一transactionへ結ぶ。tombstoneは参照kind/id/digest、理由の固定code、方針参照、実行主体、時刻、影響するEvidence参照を持ち、本文や秘密を複製しない。参照鎖を削除して過去の否定結果がなかったことにしない。

raw本文、添付artifact、DB側本文、作業コピー、キャッシュごとに対象と保存先を確認する。ソースを含むrepo全体や共有runtimeを再帰削除しない。外部保存先の削除確認が得られなければ完了とせず、対象ごとの未確認状態を残す。

## 削除後の現在判定と保存読取り

authorityの参照解決は欠損と破損、意図した削除、撤回、期限切れを固定reasonで区別する。削除された本文から架空の正常artifactを再生成しない。影響するControl、baseline、採択、現在CIへ不足を伝播させる。

保存時のDecision/Assuranceと、現在その根拠を検証できるかは別に表示する。必要な本文が失われた過去runの再現はREPRODUCTION_UNAVAILABLEとし、再計算に成功したとはしない。tombstoneだけで保存時の詳細計算を検証できると主張しない。

## Findingの認証された状態遷移

元runのFinding identityは不変にし、状態更新は別の追記記録へ保存する。記録はfinding_ref、直前状態参照、要求action、OS主体、時刻、理由、関連run/Evidenceと新状態を持つ。current pointerの更新は直前状態参照による条件付き更新とする。

| 操作 | 条件と効果 |
|---|---|
| start | 保存元Findingを照合しOPENからIN_PROGRESSへ |
| request_revalidation | 変更対象と元条件を固定しAWAITING_REVALIDATIONへ。Plan生成だけでは検証済みにしない |
| confirm | 独立validatorが新しい実行根拠を検証し、条件成立時だけVERIFIED記録を作る |
| revise_baseline / retire_target | 承認された採択・廃止の完全参照を要求し、別dispositionを保存。修復成功にはしない |
| recurrence | 同じ意味的条件・対象系統・Controlで再度観測した問題を新Findingにし、前Finding参照を保持 |

候補を作った主体が自分でconfirmすることを拒否する。VERIFIEDの文字列やauthority_confirmation_refの形だけでは認証にならない。公開部品のvalidate_findingは自己申告のVERIFIEDを引き続き拒否する。

## 修復確認に必要な結合

確認には保存元Finding、元のObservationとEvidence、実際の変更対象、元の評価条件、独立validator、新runの完全参照、現在有効な新Evidence、停止・精算を結合する。対象の版変更だけを修正候補として許し、評価条件の緩和や対象除去を同条件再検証へ混入させない。

plan ID・run ID・時刻の変更と、意味的な評価条件の変更を区別する。意味的条件は閾値、必須性、評価器・oracle・データ、entry・stage順、repeat、初期状態・隔離から決定的に生成する。単にreference IDが同じという比較も、異なるという比較も使わない。

新runで元の問題が観測されず、必要範囲が充足し、未知や欠損がなく、根拠が現在有効な場合だけ確認する。既知の別問題は残し、Finding一件の解消でrun全体を健康にしない。確認後の新Evidence失効は現在確認状態へ伝播し、保存時の確認記録は不変に保つ。

## 移行・再起動・受入

新たな状態・tombstoneのschemaは明示migrationで導入し、既知旧版の保存参照を検査する。旧形式にない確認済み記録の混入を拒否する。状態pointerと履歴、確認参照、冪等応答、削除tombstoneは各transactionでrollback可能にする。

受入では期限境界、保留対象、部分削除、保存先不達、削除失敗、依存失効、元Finding欠落、自己確認、条件緩和、別対象、古いEvidence、同時状態更新、再発、再起動と報告書への反映を確認する。未実施の外部削除や修復を完了にしない。


## 実装・接続の確認（2026-09-13）

`evidence_retention_state`/`run_retention_state`は現在の保持状態、`evidence_retention_hold`はmanagerの保存保留、`evidence_retention_plan`はmanagerの削除計画、`evidence_retention_apply`はoperatorの適用である。`python -m tools.gah_retention --runtime <runtime> --request <request.json>`で固定した主体のclientから要求する。本文の役割名でUIDを選択しない。

保持期限は一致時まで保存し、超過時だけRETENTION_EXPIREDを計画できる。EXPLICIT_REMOVALはmanagerの明示計画を要する。計画は3600秒、権限世代、直前の保留状態、Evidenceと元receiptの完全参照に固定する。保留が解除されても古い計画は復活しない。

Evidence本文の削除とtombstoneの保存を同一transactionへ結び、現在CI、再finalize、保存artifactの読取りで削除を検出する。レポートは保存時のAssuranceと現在の拒否を分け、指標・Findingの0件を補わずREPRODUCTION_UNAVAILABLEを表示する。[13統合試験](evidence/mvp-acceptance-20260913/retention-report-summary.json)が成功した。

現在の削除対象はauthority DBの指定Evidence本文である。操作journal・別の作業コピー・外部保存先の削除、WALや物理媒体の消去確認は含めない。これらの未確認対象を削除完了へ含めず、元の判定・Attempt・参照履歴は保持する。

## 正規候補による再検証と別処遇の接続（2026-09-13）

再検証元と新しい評価には、採択済み通常run、またはauthorityが準備して新側へ結合した契約候補runを使う。候補を使う場合も元の意味的評価条件、baseline文脈、Control、対象系統を一致させ、対象内容の変更だけを許す。候補の修復確認は契約採択や通常CIの許可を発行しない。再検証要求時に新runの結果やreceiptが既に保存済みなら拒否する。

`finding_recur` は修復確認時刻より後の新しい観測を要求し、新Findingの完全参照と元Findingの完全参照を別の追記記録で結ぶ。元のFinding本文と保存済み確認を変更しない。

`target_retire` はmanagerが現在採択している契約の対象を完全参照で廃止する。manager/context、要求、契約履歴、理由、時刻、冪等応答を照合し、同じ対象IDの新規開始・予約・dispatchを拒否する。別版のdigestに置き換えても廃止を回避できない。実行中の停止・精算・取消しは引き続き行える。CLIは `python -m tools.gah_target --runtime <runtime> --request <json>`。

`finding_dispose` はmanagerだけが実行し、`BASELINE_REVISED` には同じ基準系列の後続採択の完全ref、`TARGET_RETIRED` には対象廃止記録の完全refを要求する。状態をVERIFIEDに変更せず、理由と処遇根拠を保存する。処遇後の再検証は拒否する。`python -m tools.gah_finding` と通常・候補レポートから管理状態を表示する。

[検証要約](evidence/mvp-acceptance-20260913/finding-management-summary.json)のsource45で処遇・権限・再起動・rollbackを含む19試験、source46でCLI・保存Evidence・純粋cache等の52試験が成功した。新しい修復確認と再発の通し試験はsource47で実施中であり、この段階では受入済みにしない。

現在の再検証元を照合するときは、外側で認証されたactor/contextを内部の候補・baseline照合へ引き継ぐ。役割の欠損を管理者やvalidatorの仮の文字列で補わない。保存時のHEALTHYだけで確認せず、現在の候補・方針・基準・Evidenceの失効と条件不一致を引き続き拒否する。
