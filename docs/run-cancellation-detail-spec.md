---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 固定UC-CIの通常run取消し確定

lease期限切れと根拠失効が重なった場合は、[取消し復帰](run-recovery-detail-spec.md)のresource_cancel_claimを使用する。停止観測・精算・terminalの条件は維持する。

[通常runとCI](regression-ci-detail-spec.md)のgeneration 2、purpose=regressionを対象とする。
対象は自作の固定・無害なfixtureである。取消しの実行状態とAssuranceを別々に保存し、
未完了の試行を成功や完了率の分母から除外しない。通常runと同じDecision・Evidence・
Finding report・Plan report・run receiptを保持する。

## 1. APIと権限

全要求は既存brokerのOS peer credentialを通す。本文の役割や停止済み自己申告を権限として採用しない。

| action | 主体 | 入力・効果 |
|---|---|---|
| resource_cancel | operator | run_id、owner_id、owner_epoch。新規開始を止め、未送信予約を解放する |
| resource_observe | validator | 既存operationの停止・usage。停止と精算を別々に記録できる |
| run_cancel_finalize | operator | run_id。全操作の停止を検査し、取消しterminalと必須成果物を原子的に保存する |
| run_outputs / run_artifact | manager / validator / operator | 取消しrunの保存成果物を照合して取得する |
| ci_check | operator | 通常runと同じ完全参照。毎回保存実体と現在の精算状態を検査する |

全要求にschema_version=1、action、request_idが必要。run_cancel_finalizeはこれらとrun_id以外の
fieldを受け付けない。取消し確定の再配送は同一requestに対する不変応答を返す。現在の利用判定は
ci_checkで取り直す。同じrequest_idを違う内容で使うことはできない。

## 2. 停止と精算

取消し要求だけではterminalを確定しない。全operationについて、未送信なら予約解放、
送信済みなら保存した停止時刻と残存slot=0を確認する。停止観測が未着なら確定を拒否し、
CI終了値は2のままとする。run作成と証拠領域の作成の間で取消しが起きても、保存された
採択条件を使って欠損を含む証拠領域を作成する。任意の新規実行は起動しない。

停止済みでもusageが未確定なら、予約とunsettledを残したまま取消しを確定できる。
receiptのresource_stop_verified=trueとbudget_closure=falseを別々に保持する。
後日の認証されたusage通知は既存operationへ一度だけ精算し、必要ならrecovery claim後に
resource_closeする。精算によって新規開始、成功への変更、元deadlineの延長は認めない。

resource_close後でもterminal未確定なら取消しを受け付ける。terminal確定後の新しい取消し要求は
already_terminal=trueを返し、元のreceiptと取消しflagを変更しない。この応答にも操作履歴を残す。
owner/epochを使うのは未確定runの停止操作であり、確定済み応答は新しい操作権限を与えない。

## 3. 保存の境界

専用のauthority_cancel_receiptとresource_cancellationをauthority_artifactsへ保存する。
通常完了のauthority_run_receiptsへは登録しないため、取消し記録をbaseline採択根拠へ読み替えられない。
closureには確定時の資源集計を保存し、後日精算した現在値で書き換えない。

Decisionは受理済みAttemptと予定全試行から生成する。未観測はUNKNOWN、観測済みの違反は
所定の優先順位で保持し、取消しだけを理由に既知の否定結果を消さない。取消し後の遅延結果・
矛盾は既存のAttempt規則で記録し、現在のHOLDを保持する。元のterminal・報告書は不変である。

terminal、参照実体、Finding/Plan、成功応答を同じtransactionで保存する。途中で失敗した場合は
これらをrollbackし、先に確定した取消し要求・停止・未精算予約を残して再試行可能にする。
取得時には固定factory、manifest、profile、起源主体、保存時permission世代、全参照と本文を照合する。

## 4. CIの終了値

| 状態 | 終了値 | 現在利用 |
|---|---|---|
| 取消し要求のみ、停止未確認、必須成果物なし、保存破損 | 2 | false |
| 保存実体を照合済みの取消しterminal | 3 / CANCELLED | false |
| 別の対象を期待する要求 | 1 | false |
| 既に正常terminalがあり、その後に取消し要求 | 通常のfresh検査結果 | 通常規則に従う |

取消し確定後は期待参照を照合し、CANCEL_REQUESTEDを返す。現在の予算が開いていればBUDGET_OPENを
加える。料金確定後も終了3であり、receiptは変更しない。保存graphが検証できなければ終了3を
推定せず2にする。CI consumerの通信・応答形式・出力障害も2とする。

## 5. 互換性と検証

DB列形式はv4を維持する。直前の通常run実装digestからの明示移行を追加し、通常runのfactory、
資源結合、保存済みreceipt・全成果物、未完了runを照合する。旧版にはない取消しartifactを含むDB、
未知digest、参照不整合は拒否し、更新をrollbackする。通常起動で自動移行しない。

実SQLiteの統合試験と、固定fixture一件を追加する
`python -m tools.verify_baseline_runtime --cancellation --output <repo内の新規証跡先>`で検証する。
前工程90件と198項目を保持し、取消し確定・broker再起動・後日精算・consumer終了3を追加する。
全MVPの残件と受入状態は[完了監査](mvp-completion-audit.md)で管理する。
