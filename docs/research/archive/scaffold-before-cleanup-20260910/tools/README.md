---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# ワークフロー補助

[workflow.py](workflow.py) が文書索引・Birdseyeの生成と統合検証を提供する。ci配下の8検証器は [固定したCookbook](../docs/UPSTREAM.md) 由来。Python 3.11以上の標準ライブラリで動作する。

実行手順は [RUNBOOK](../RUNBOOK.md)。製品解析runtimeはsrcへ置く。
