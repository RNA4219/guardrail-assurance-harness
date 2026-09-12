---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# Acceptance運用

[雛形](ACCEPTANCE_TEMPLATE.md) をAC-YYYYMMDD-NN.mdへ複製し、task_idで [Task](../tasks/README.md) に結ぶ。[索引](INDEX.md) は自動生成する。

必須metadataはacceptance_id / task_id / intent_id / owner / status / reviewed_at / reviewed_by。Scope、Acceptance Criteria、Evidence、Verification Resultを記載する。draft → approvedまたはrejected。変更により成立しなくなった検収はsupersededとして後続記録へ接続する。

approvedは記載した範囲の技術的な受入状態。reviewed_byに実際の検査者を記載し、自動検証やCodexの自己検査を利用者の承認と呼ばない。製品公開やremote設定の証跡とは別である。完了Taskはapproved検収を必須とする。

`python -m tools.workflow generate` で索引・Birdseyeを更新し、checkで同期を確認する。
