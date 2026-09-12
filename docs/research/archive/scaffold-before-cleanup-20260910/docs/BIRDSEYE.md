---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# Birdseyeの読み方

[index.json](birdseye/index.json) のnodesから対象pathを見つける。edgesを±2 hopまで辿り、各nodeのcapsで要約・依存・source hashを確認する。[hot.json](birdseye/hot.json) は作業入口の集合。

生成規則は [Birdseyeガイド](birdseye/README.md)。JSONを利用できない場合は [HUB](../HUB.codex.md) のTask Routingと [Task一覧](tasks/README.md) を使う。
