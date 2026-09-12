---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 要件レビュー用の反例モデル

[requirements_adversarial.py](requirements_adversarial.py) は要件レビューの計算・集合・イベント順序の反例を再現する。外部通信、対象システム操作、負荷投入、実データの解析は行わない。

```sh
python tools/review/requirements_adversarial.py --output docs/evidence/requirements-adversarial-20260910/models.json
```

出力のcounterexamples_reproducedはモデルの反例再現を示す。製品テストの合格や脆弱性の実証を意味しない。[レビュー記録](../../docs/reviews/requirements-adversarial-2026-09-10.md) と併読する。
