---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-13
next_review_due: 2026-10-13
---

# 両用途を事前固定する複合run

[要求](requirements.md) §2、GAH-R02/R04/R19/R20と[受入条件](acceptance-criteria.md)の共通シナリオに対応する。製品の複合runは、同じauthority DB上のUC-CIとUC-LLMの通常runを一つずつ持つ。親manifestと二つの子manifestを、実行前に完全参照で固定する。実装の接続中で、全MVP受入を完了としない。

## 事前固定

operatorの`combined_prepare`へ親run IDと、用途順UC-CI / UC-LLMの子run ID・契約系列・採択済み契約の完全参照を渡す。用途欠落、ID重複、用途順の入替え、設定外のstatusや予算入力を拒否する。二つの通常runは同じPolicyProfileとfull profileを使う。親は子の対象・plan・baseline・契約・作成時刻と期限を保持し、根拠のない集約成功を受け取らない。

親子IDの予約は同じtransactionで保存する。子の通常開始は親に固定したmanifest・plan・契約系列と一致する必要がある。親・子の後からの対象限定化や、別の複合runとのID共有を拒否する。

## 予算・停止・取消し

親の予算は既存full profileの上限。子ごとの実行制限に加え、予約・dispatch時に二つの子の実行中slot、試行、model call、token、費用を合算する。不明usageを0へ置き換えず、未解消予約を残す。期限は開始前に固定し、子の開始が遅れても親期限を延長しない。日次費用の共通台帳は既存どおり適用する。

一方の取消し・予算違反があれば、他方の新しいdispatchも止める。観測保存・停止精算は可能なままにする。`combined_cancel`は新規処理を止める要求を記録する。実行中の操作が停止観測されていなければ終了2、全て停止済みなら終了3とし、未開始の子を成功にしない。完了済みreceiptを取消しで書き換えない。

## 現在判定と出力

`combined_child_read`は親の完全参照と用途に一致する固定入力だけを返す。`combined_finalize`は必要な子成果物が揃った場合に親receiptを不変保存する。`combined_current`は毎回、子の完全対象と目的に対するfresh CIを再照合する。一方のHEALTHYで他方のHOLD・DEGRADED・UNKNOWN・必要出力欠損を相殺しない。親receiptの欠損や、子のEvidence撤回・削除・期限切れもCI成功にしない。

製品入口は`python -m tools.gah_combined run|resume|cancel|status --runtime ... --request ...`。既存監督を用途ごとの固定実行器で使い、親と子のcheckpoint、authority輸送、runの排他を保持する。JSONとMarkdownには用途別の範囲・結果を残し、子レポートを読み取った後にも親の現在CIを再照合する。CLIの終了0は現在利用可能な複合runだけに返す。

## 受入の範囲

MVP最小シナリオは自作の固定合成fixtureで確認する。学習済みモデルの呼出しや本番性能を必要条件へ追加しない。GAH-R08はgeneric commandとPromptfooの固定結果の取込み・正規化を要求するため、任意providerの自動起動を受入の前提にしない。宣言していない外部送信や実環境への侵入を追加しない。

単一用途の実行・保存の成功は、その用途の証拠として保持する。複合runの製品受入には親の開始前固定、合算予算、両用途の実行と用途別/全体表示、欠損・失効・中断時のCI拒否の接続証拠を別に必要とする。

## 現在CIの組込み境界

`combined_finalize`は不変receiptの保存であり、応答も常に`ci_eligible=false`とする。現在のCI利用は`combined_current`で改めて照合する。AdoptionStoreは通常の`ci_check`と同様に、固定EvaluationExtensionの厳密な型・実装digest・fresh action登録を検査して組込み関数を呼ぶ。任意extensionの成功宣言から利用権限を作らない。

現在照会の前後でソースを照合し、時刻・保存元のoperator/context・権限世代・子の現在状態・出力完全参照を検査する。失効後も元receiptと確定応答は書き換えない。source71の全件複合試験は保存応答の権限境界で失敗したため、実行が終わった件数を複合CI成功へ数えない。
