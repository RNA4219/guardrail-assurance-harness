---
intent_id: INT-GAH-001
owner: RNA4219
status: in_progress
last_reviewed_at: 2026-09-16
next_review_due: 2026-10-16
---

# 拡張実装の継続検証

Lunaが容量、資源probe、offline入力を分担し、親がCLI/Checkpoint/whole-runへの接続、固定image準備、DGXレビューの採否、独立レビューの修正、実Dockerを担当した。現在の範囲は[実装状況](../../productization-status.md)、仕様は[共通実装仕様](../../productization-implementation-spec.md)を参照する。

[最終固定source-v2](source-v2.json)の[関連試験](tests-v2.json)は116件中114件成功、Windowsのlink作成権限による2件スキップ。約10.1秒で完了し、sourceの不変を照合した。全件回帰ではない。[修正前の115件](tests-v1.json)は別sourceの記録として保持する。

[image準備](image-preparation-v1.json)は新規配布先に1,405ファイルを収録し、固定baseからfixture/guardrail/authorityを構築、3つのlockとimage config/source digestを実照合した。約21.7秒、base cacheありのoffline実行で、ネットワーク取得時間や両OSの30分導入受入を測定したものではない。検証済みlockの[現在checkoutへの反映](image-lock-installation.json)は明示した3ファイルだけで、既存deploymentや固定MVPコピーは移行していない。

[実Docker資源観測-v2](resource-live-v2.json)では2snapshotでbroker/clientの固定cgroupとhost CPUを読み、空のI/Oは欠測として保持した。CPU、cgroup memory.current/peakを取得し、RSSと混同していない。専用containerは回収し、state volumeを保持した。workerの全scopeや全計数は未接続で、SLOは無効のまま。[最初の観測](resource-live-v1.json)に残った空I/Oのparser拒否は修正し、過去記録を上書きしていない。

[14件の受入記録-v7](acceptance-records-v7.json)はNOT_RUN4件、INCONCLUSIVE10件、PASS0件。[照合計画](verification-plan-v2.json)は検査索引であり事前SLO採択を示さない。容量の全writer/物理volumeへの強制、短命workerを含む全計数、実target adapter、公開imageの配信、実案件の選定・運用観測・両OS導入受入が残る。setupの容量UNKNOWNは解除していない。
