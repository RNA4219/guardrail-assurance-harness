---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-14
next_review_due: 2026-10-14
---

# Mutationの除外審査

[要求](requirements.md)GAH-R07/R09/R25と[受入条件](acceptance-criteria.md)へ対応する。元のERROR・予定義務・Decision・receiptを保持したまま、根拠付きの審査を別artifactへ保存する。審査の承認はCI許可や修復成功ではない。

## 対応する根拠

固定方針`invalid-mutant-review-v1`は、変更前検査PASS、実行COMPLETED、停止確認済み、正規化されたMutation ERRORの原因MUTATION_NOT_APPLIED、raw digestと認証Evidenceがそろう単一段階・再試行なしの試行だけを扱う。試行・case・obligation・対象・evaluator・元manifest・契約・Evidence・方針の完全参照を保存する。同等性の自己申告、未到達、未検知、無関係な失敗、タイムアウト、複数段階、再試行の未整理な結果を自動除外しない。対応できない根拠は拒否して元の分類を残す。

## 主体と不変保存

`mutation_review_validate`はvalidator（UID12003）が保存実体から根拠を再構成し、理由と検証者の実actor/contextを記録する。manager（UID12001）の`mutation_review_approve`は完全なvalidation参照、起源、現在のEvidence・条件・権限世代・期限を再検査して承認する。operatorや変更主体、validator自身は承認できない。権限は本文の役割名から作らず、既存のOS peer credentialによる認証へ結ぶ。

同一run・attemptに対する審査IDは決定的に定まり、同じrequestの再配送は元応答を返す。別requestから同じ審査を上書きしない。保存はauthority_artifactsとidempotencyの同じtransaction内で行い、失敗時に片方だけ残さない。現在照会は記録の型・完全参照・本文・元request・実行actor/context・不変応答との対応、ソースの前後一致を検査する。

検証の有効期限は一日以内かつ元Evidenceの期限以内。根拠や管理主体の失効・撤回・権限世代変更により現在有効な承認が成立しなくなれば、保存された承認を変更せずEXCLUSION_PENDINGとする。Evidence削除後は根拠を再取得して復元したことにしない。

## 計数と現在の判定

`mutation_review_current`をoperatorが照会する。現在有効な独立検証と承認がそろうものをEXCLUDED、それ以外の保存審査をEXCLUSION_PENDINGとして版ごとに数える。元のcounts、Mutation ERROR、必須欠損、既存の指標の分子分母とDecisionは変更しない。方針が対象にする未成立Mutationは元からMutation Scoreの分母へ加算されていないため、審査で分母を後から作り替えない。

通常/候補レポートは元のERRORと、審査後に残るMutation ERROR・承認済み除外・除外未確定を別表へ示す。予定数、段階数、再試行、重複を独立標本数へ読み替えない。取得不能を0件で補わず、レポート取得後のfresh CIで元の必須欠損・違反・失効を確認する。

## 製品入口と検証

`python -m tools.gah_mutation_review --runtime ... --request ...`は上記三actionのJSONを受け取る。終了0は管理操作の成立であり、返却ci_eligibleは常にfalse。応答の型、action、完全参照、件数、原判定/義務の不変を照合し、通信・出力・改変・未対応の失敗は終了2とする。

[source75の証拠](evidence/mvp-acceptance-20260913/mutation-review-summary.json)で境界・CLI・レポートと、実SQLiteの失敗run・承認・現在CI・再起動・撤回・削除を確認した。SOURCEの実測版と最終全回帰を区別する。全MVP受入は継続中。
