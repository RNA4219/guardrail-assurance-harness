---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 通常run取消しの親レビュー

利用者の指示により、親が実装・コードレビュー・試験を担当した。この工程では外部モデルへ委任していない。
対象は固定UC-CIの通常generation 2 run、取消し確定、保存成果物、後日精算、現在のCI判定と既知DB移行である。
[詳細仕様](../run-cancellation-detail-spec.md)と[全体監査](../mvp-completion-audit.md)へ結ぶ。

## 指摘と処遇

| 観点 | 確認した問題 | 処遇 |
|---|---|---|
| 停止と料金 | 精算完了を必須にすると、停止済みでも料金未確定のrunを取消し確定できない | slot=0と全operation停止を検査し、unsettled予約を保持した取消しreceiptを保存する |
| 作成直後 | run_beginとevidence_openの間の取消しで証拠領域が存在しない | 取消し確定時だけ、保存された束縛から証拠領域を同じtransactionで作る |
| 資源閉鎖とterminal | resource_close後・terminal前の取消しがRUN_CLOSEDで止まる | 通常runのterminal未確定時に限る内部flagで取消しを受理する。入力fieldとしては公開しない |
| 停止経路の可用性 | 停止時に採択の全履歴を要求すると、根拠が利用不能なときに回収まで止まる | 未確定runの停止は保存manifestと資源ownerで扱い、全履歴の検査は成果物確定・取得側で行う |
| 確定後の変更 | 遅延結果や再取消しで正常/取消しterminalを置換する余地がある | terminal後の取消しはalready_terminal。遅延観測は既存規則で記録し、現在HOLDを保持する |
| 成果物参照 | run_outputsが通常receiptのkindに固定されていた | 検証したreceipt種別へ完全参照を結び、全成果物を保存実体へ再照合する |
| 原子的保存 | Finding/Planの保存失敗でterminalだけ残ると誤った確定に見える | terminal・全artifact・成功応答をrollbackし、取消し要求・停止・未精算予約を保持する |
| 互換性 | 直前の通常run実装を既知旧版として扱えず、完了/未完了runを保持した移行ができない | 既知digestを追加し、factory・資源・receipt・成果物を検査する。旧版を装う取消しartifactは拒否する |
| CI判定 | 取消し要求と保存済み取消しの区別、料金確定後の扱いが未接続 | 停止/保存未完了は2、照合済み取消しは3、対象違いは1。全て現在利用false |

## 検証

初期の統合8テストは396.602秒で成功した。その後、作成直後の取消し、遅延成功、採択履歴が
解決不能でも停止・精算できることの3テストを追加した。最終の全体回帰は534件、実Dockerは219項目が成功した。
[証跡](../evidence/mvp-cancellation-20260912/README.md)に、実行ソースhash、旧テスト保持、91件の実行と回収を記録した。途中の試験結果は、その時点の確認範囲として保持する。

要求・受入条件・運用方針・初期policyと設計例の7ファイルを変更しない。計画の自動実行、
第三者対象、任意コマンドの実行機能は追加していない。全MVPの残件は全体監査で管理し、
取消し工程の成功を全MVP完成へ換算しない。
