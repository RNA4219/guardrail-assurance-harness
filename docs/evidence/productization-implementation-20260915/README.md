---
intent_id: INT-GAH-001
owner: RNA4219
status: in_progress
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# 拡張実装の検証証跡

Lunaが性能・運用・pilotを分担し、親が接続・反例レビュー・DGXレビューの採否と検証を担当した。対象は[拡張14要件](../../productization-requirements.md)。現在の実装と残件は[対応表](../../productization-status.md)で追跡する。

[固定source](source-v4.json)、[要求snapshot](requirements-v4.json)、[全件分割計画](test-plan-v4.json)、[照合用計画](verification-plan-v4.json)を保持する。照合用計画は既に固定して実行中の全件計画を参照する索引であり、新たな事前SLO採択や実案件評価の認証を与えない。sourceのpathはリポジトリ相対、内容はSHA-256で固定した。

[最終差分source-v5](source-v5.json)に対する[関連93件](final-delta-v5.json)と[実Docker90件](docker-integration-v5.json)は成功。実Dockerは初回/比較gen2採択から通常run・現在CI・reportまでを別clientへの切替えを含めて検査した。元の容量doctorを通した導入受入ではない。

[局所性能診断](profile-comparison-v5.json)はdeepcopy呼出しの削減とwallの未改善を別に保存する。[14件のPAC記録](acceptance-records-v6.json)はNOT_RUN4件/INCONCLUSIVE10件でPASSは0件。

1,110件を12lane・最大6並列で実行し、[集約結果](regression-v4.json)を保存した。元の完走結果は、入力4ファイルの収録漏れによる2laneの失敗を保持する。同一source bytesへ必要な入力を加え、[12件](fixture-recovery-v4b.json)と[19件](fixture-recovery-v4c.json)を再検査して成功した。24の失敗eventは23のtest methodに対応し、補完後の未解決は0件。追加31試行は重複を含む。局所集約のPASS_WITH_FIXTURE_RECOVERYを、元実行やGitHub全jobの成功へ書き換えない。

[最終版のdiscovery記録](test-inventory-v5.json)は1,117件。source-v4からの追加は性能反復の3件と小集合cacheの4件で、1,110件の回帰と同じ実行として数えない。最終差分93件は別の検査結果であり、重複を含む。

[修正前のDocker失敗](docker-integration-v3.json)も残し、修正版の成功と区別する。固定コピーの収録をGit inventoryへ改め、必須4入力・原本/コピーのhash・既存先拒否を[実確認](freeze-helper-v1.json)した。旧MVPの証跡と不成功の元ログは変更していない。
