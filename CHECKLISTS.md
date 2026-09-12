---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
template_version: 1.0.0
---

# Checklists

## 作業の開始

- [ ] HUB、Guardrails、関連capsを確認した。
- [ ] TaskのObjective / Scope / Requirements / Commandsを記録した。
- [ ] 要件正本と未実装部分を確認した。

## 変更の完了

- [ ] 文書リンクと必要なADRを更新した。
- [ ] `python -m tools.workflow generate` を実行した。
- [ ] `python -m tools.workflow check` と関連テストが成功した。
- [ ] Task、Acceptance、Evidence、[CHANGELOG](CHANGELOG.md) の対応がある。

## 製品を公開する前

- [ ] [要求草案](docs/requirements.md) のMVP受入条件（採択後）を実測した。
- [ ] 本体licenseと依存物の再配布条件を決定した。
- [ ] [Security Review](docs/security/Security_Review_Checklist.md) を実施した。
- [ ] branch protectionの実測exportと必要checkを突合した。
- [ ] [公開記録](docs/releases/README.md) と復旧手順を残した。

