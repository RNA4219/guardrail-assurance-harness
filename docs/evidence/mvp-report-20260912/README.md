---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 通常runの要約CLIの証跡

[統合記録](verification-v2.json)に、直前の[全体回帰534件](../mvp-cancellation-20260912/verification.json)と、
追加した[専用7テスト](unit-check.json)を区別して記録した。追加CLIは全体回帰の実行後に作成したため、
7件は別の対象検証である。実DBを使った[製品CLIの15項目](runtime-check.json)も成功した。

同じauthorityイメージと保存済みの固定fixture DBを読み、通常runの失効は終了1、取消しは終了3と確認した。
観測時刻・基準時刻・期限・対象・成果物digestを表示し、保存時のHEALTHYだけで現在利用を可にしない。
確認用コンテナは回収し、元の検証ファイルを変更せず、state volumeを保持した。

- [失効した通常runの日本語要約](regression-runtime-report.md) / [同じ根拠のJSON](regression-runtime-stdout.json)
- [取消しrunの日本語要約](cancellation-runtime-report.md) / [同じ根拠のJSON](cancellation-runtime-stdout.json)
- [仕様](../../run-report-detail-spec.md) / [親レビュー](../../reviews/mvp-report-20260912.md)

表形式へ整える前の試験もprior記録として保持する。最終実行はruntime-02である。
この工程も親が担当した。対象は固定UC-CIの読取りと表示に限り、全MVP受入は未完了、
release_gate=no_go、検証packetのci_eligible=falseを維持する。

文書検査で製品出力の管理メタデータ不足を検出し、閲覧用Markdownへ文書情報を追加した。出力原文はraw.txtへbyte単位で保持し、元の統合記録と原文hashの対応をverification-v2.jsonへ記録した。
