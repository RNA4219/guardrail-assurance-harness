---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# 検証証跡

コマンド、対象、日時、実行環境、終了状態、未実施範囲を記録する。単発出力は.gitignore対象の.gaに置き、検収に採択する結果だけここへ保存する。

今回の構成検証はworkflow-adoption-20260909.jsonに保存し、[Acceptance](../acceptance/AC-20260909-01.md) から参照する。JSONにはローカル検査と製品・remoteの未実施状態を明示する。証跡JSONはBirdseyeのsource集合に含めず、自己参照hashを作らない。
