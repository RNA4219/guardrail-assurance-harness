---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 製品監督の親レビュー

親が実装・レビュー・検証を担当した。この工程で外部モデル・subagentへ委任していない。操作状態照会を現在の送信許可と分け、保存manifest/plan/予約/usageのdigestと元epochへ結合した。元Evidence撤回後も停止処理に必要な状態を読める。既知DB移行では世代1・2と元runの保存根拠を検査し、期限・撤回を変更しない。

監督は送信前の意図と実ExecutionJournalを照合する。終了時刻を失った操作は再実行せず取消しへ進める。停止unknownとusage unknownを残し、最後のfresh CIから終了コードを取得する。

初回監督8試験ではCI終了2を維持していたが、停止不明の表示理由が不足していた。表示補正後の8試験は成功した。追加レビューで、準備直後のresumeがrun_beginを省略してRUN_MISSINGとなる分岐を実DBで再現した。同じrun_beginを再送する一行補正後、準備送信前・準備ACK後・開始登録送信前・開始ACK喪失の4試験と既存8試験が成功した。失敗・再現・差分・補正前ソースを保存する。

実Docker194項目・91件実行は一行補正前のソースに結ぶ。通常30件の完了、再開時の実行数不変、実CLI status、1件実行後の中断取消し、再起動、元根拠撤回後のCI1と過去成果物不変、全停止・回収を確認した。補正後にDockerを再実行したとはしない。開始前の境界は実SQLiteと合成runnerで確認し、Docker開始後の分岐を変更していないことを差分で照合した。

補正前46件と補正後12件の固有対象は50件。全体592件は再実行していない。前工程は当時のsource snapshotを保持する。非掲載検査は既知未読ディレクトリを補完し、残存0・未読0とした。

[仕様](../supervised-run-detail-spec.md)、[証跡](../evidence/mvp-supervisor-20260912/README.md)、[残件](../mvp-completion-audit.md)へ結ぶ。対象限定・定期起動サービス・全checkpoint境界・版跨ぎ回収・保持・後続契約・UC-LLM・Finding修復確認は未完了。Task=in_progress、Acceptance=draft、release_gate=no_goを維持する。
