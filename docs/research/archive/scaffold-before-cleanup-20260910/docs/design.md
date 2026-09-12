---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# 設計の入口

詳細な設計制約の正本は [要件定義](requirements.md)。ここは実装に伴う設計判断への入口とする。現時点で製品実装はない。

## 責務と依存方向

Source-firstの静的解析結果と、明示的に許可された実行証跡を別々に取得し、共通のEvidence契約へ正規化する。再構成処理は根拠と推論を区別して仕様候補を出力する。Explorerは保存済み結果を読む表示層とする。

入力snapshot → adapter → evidence正規化 → 仕様候補 → review/exportの順。解析対象リポジトリのscriptや依存インストールは既定で実行しない。

## M0設計作業

[契約](contracts/README.md) からSchemaとfixtureを先に定め、要件中の対象バージョン・対応構文・対象外をadapterごとに固定する。機能追加は [Task](TASKS.md) を起こし、設計変更は [ADR](ADR/README.md) に記録する。

永続化、CLIの具体的コマンド体系、エラー型、incremental更新は実装Task内で決定し、要件のsnapshot・再現性・互換性契約と突合する。未決定事項を既存機能として扱わない。
