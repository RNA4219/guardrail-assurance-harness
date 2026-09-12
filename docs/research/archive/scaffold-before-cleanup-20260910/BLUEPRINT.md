---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
template_version: 1.0.0
---

# Blueprint

## 1. Problem Statement

既存システムのコード・設定・運用資料が分散し、振る舞いとその根拠を追いにくい。
決定論的に抽出したFactとEvidenceからBehavior仕様を作り、人間が確定・推論・矛盾・不明を区別できるようにする。

## 2. Scope

- In: 複数repoのinventory、機密除去、Fact/Evidence、link、Behavior、snapshot、探索・export。
- Out: 対象製品の品質判定、要求生成、設計改善の自動決定、ソースの自動変更。
- 初回M0はReact/Expressを中心とする限定subset。M1/M2と詳細は [要件定義](docs/requirements.md) を正本とする。

## 3. Constraints / Assumptions

static extractionをruntime観測と混同しない。LLMはoptional。根拠のない確定を表示しない。
現在は要件とワークフローを整えた段階で、製品runtime・正式Schemaは未実装。
本体license、保存方式等の未決定事項は [ADR索引](docs/ADR/README.md) から管理する。

## 4. I/O Contract

- Input: 版を固定したローカルrepo、設定・schema・docs、optionalな範囲付き観測。
- Output: Sanitized Input Manifest、Fact Snapshot、Behavior Revision、Run Receipt、Evidence付きexport。
- この導入作業のInput: 改訂済み要件定義とWorkflow-Cookbookの固定版。
- この導入作業のOutput: Tier 3構成、生成可能な索引、TaskとAcceptance、実行できる検査。

## 5. Interfaces

製品CLI/APIは [仕様](docs/spec.md) の設計対象。現時点で利用可能なのは以下の開発コマンドだけ。

```sh
python -m tools.workflow generate
python -m tools.workflow check
```

## 6. Verification

[RUNBOOK](RUNBOOK.md) の生成・検査とunittestを実行する。
Tier 3の構成合格はM0製品の受入合格を意味しない。

