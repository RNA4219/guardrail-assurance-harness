---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
template_version: 1.0.0
---

# Agent Hub

## Purpose

GAHの要求・検討事項・開発運用への入口を示す。現在の段階は要求整理であり、設計・仕様の確定と製品実装には未着手。

## Read Order

1. [README](README.md)
2. [Birdseye Index](docs/birdseye/index.json)と必要なcaps
3. [BLUEPRINT](BLUEPRINT.md) / [GUARDRAILS](GUARDRAILS.md)
4. [要求草案](docs/requirements.md) / [未決定事項](docs/open-questions.md)
5. 作業時は[RUNBOOK](RUNBOOK.md)、完了時は[EVALUATION](EVALUATION.md)

## Task Routing

| 作業 | 入口 | 位置づけ |
|---|---|---|
| 目的・範囲・要求 | [要求明確化案](docs/requirements.md) | coding agentの開発・CIとLLMガードレール評価の共通要求・判定条件 |
| 製品の受入・要求の来歴 | [受入条件](docs/acceptance-criteria.md)、[対応表](docs/requirements-traceability.md) | 未実行の製品受入案と原稿の処遇 |
| 要求の反例と拡張 | [敵対的検証](docs/reviews/requirements-adversarial-20260910.md)、[拡張案](docs/extension-roadmap.md) | 改訂根拠、優先度、後期の着手条件 |
| 判断保留事項 | [未決定事項](docs/open-questions.md) | 未採択・未定義の論点 |
| 設計・仕様・契約 | [設計](docs/design.md)、[仕様](docs/spec.md)、[契約](docs/contracts/README.md) | 今後の作業入口 |
| 次工程 | [開発順序案](orchestration/development-plan.md) | 要件化以降の順序案 |
| 文書検証・CI | [RUNBOOK](RUNBOOK.md)、[CI構成](docs/ci-config.md) | 実在する開発コマンド |
| 作業記録と検収 | [Task一覧](docs/tasks/README.md)、[Acceptance索引](docs/acceptance/INDEX.md) | GAHで実施した作業のみ |
| 来歴・旧案件・出典 | [資料来歴](docs/research/README.md)、[出典台帳](docs/research/citation-register.md) | 保存稿と未検証参照 |
| 開発セキュリティ | [SECURITY](SECURITY.md)、[SAC](docs/security/SAC.md) | 製品安全性の証明とは別 |
| 運用判断・取り込み元 | [ADR索引](docs/ADR/README.md)、[UPSTREAM](docs/UPSTREAM.md) | 文書基盤の来歴 |

## Update Rule

正本文書・Task・検収を変更したら参照を更新し、Birdseyeを再生成する。`index`、`hot`、`caps`の世代とsource hashを揃える。archiveは現行ノード・Task・検収の集計対象に含めない。
