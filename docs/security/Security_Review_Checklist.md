---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Security Review Checklist

- [ ] 変更のデータ入力・出力と権限境界を要求草案と照合した
- [ ] fixture、ログ、Evidenceに秘密や第三者の実データを含めていない
- [ ] Dependabotの更新を確認し、追加依存のlicenseと脆弱性を評価した
- [ ] scanner対象がある場合はSAST / Secrets / 依存 / Containerの実測結果を添付した
- [ ] CI権限、Action固定、外部送信の変更を確認した
- [ ] remote設定をローカルdesired JSONと混同していない

これは変更ごとに用いる雛形であり、チェック済み結果ではない。[SAC](SAC.md) と [Incident雛形](../INCIDENT_TEMPLATE.md) を参照する。
