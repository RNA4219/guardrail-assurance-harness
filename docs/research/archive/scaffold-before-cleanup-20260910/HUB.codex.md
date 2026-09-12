---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
template_version: 1.0.0
---

# Agent Hub

## Purpose

Spec Reconstructorの作業入口を集約する。共通様式の導入元は [UPSTREAM](docs/UPSTREAM.md) に固定し、製品の責務はこのrepo内で管理する。

## Read Order

1. [README](README.md)
2. [Birdseye Index](docs/birdseye/index.json) と必要なcaps
3. [BLUEPRINT](BLUEPRINT.md) / [GUARDRAILS](GUARDRAILS.md)
4. 作業対象の正本と [RUNBOOK](RUNBOOK.md)
5. 完了前に [EVALUATION](EVALUATION.md)

## Task Routing

| 依頼 | 入口 | ノードの役割 |
|---|---|---|
| 要件・範囲 | [要件定義](docs/requirements.md) | requirements |
| 設計・データ契約 | [設計](docs/design.md)、[契約](docs/contracts/README.md) | design / contract |
| 仕様 | [仕様](docs/spec.md) | specification |
| ワークフロー変更 | [RUNBOOK](RUNBOOK.md)、[CI構成](docs/ci-config.md) | operations / ci |
| 新規作業 | [Taskガイド](docs/TASKS.md)、[Task一覧](docs/tasks/README.md) | task-routing |
| 検収 | [EVALUATION](EVALUATION.md)、[Acceptance](docs/acceptance/INDEX.md) | evaluation / acceptance |
| 安全・インシデント | [SECURITY](SECURITY.md)、[SAC](docs/security/SAC.md)、[Incident雛形](docs/INCIDENT_TEMPLATE.md) | security |
| 導入元更新 | [UPSTREAM](docs/UPSTREAM.md) | upstream |
| 開発順序 | [M0導入順](orchestration/m0-development.md) | orchestration |

## Update Rule

新しい正本文書・Task・契約を追加したら参照を接続し、Birdseyeを再生成する。
`index.generated_at` とhot/capsの世代番号を同一にする。

