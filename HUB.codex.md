---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
template_version: 1.0.0
---

2026-09-15現在: [32条件のMVP技術検収](docs/mvp-completion-audit.md)は完了し、[最終証跡](docs/evidence/mvp-acceptance-20260913/mvp-final-20260915-summary.json)を正本とする。以下の工程別進捗にある未完了は当時の記録で、現在の判定は完了監査へ従う。設計凍結、人間の追加承認、GitHub上の実行・公開反映を意味しない。


# Agent Hub

[条件比較部品](docs/semantic-conditions-detail-spec.md) / [親レビュー](docs/reviews/mvp-condition-core-20260912.md) / [証跡](docs/evidence/mvp-condition-core-20260912/README.md)。世代更新の採択・実行や修復成功の権限は持たない。

製品監督の[仕様](docs/supervised-run-detail-spec.md)、[親レビュー](docs/reviews/mvp-supervisor-20260912.md)、[工程証跡](docs/evidence/mvp-supervisor-20260912/README.md)。固定全体runを開始・再開・取消し・照会する。対象限定・CIからの定期起動接続は継続中。

[baseline更新仕様](docs/baseline-refresh-detail-spec.md) / [親レビュー](docs/reviews/mvp-baseline-refresh-20260912.md) / [工程証跡](docs/evidence/mvp-baseline-refresh-20260912/README.md)。

[取消し復帰の仕様](docs/run-recovery-detail-spec.md)、[親レビュー](docs/reviews/mvp-recovery-20260912.md)、[証跡](docs/evidence/mvp-recovery-20260912/README.md)を追加した。

通常runの[人間向け要約・JSON](docs/run-report-detail-spec.md)と[製品CLIの証跡](docs/evidence/mvp-report-20260912/README.md)を追加した。

前工程の[取消し確定](docs/run-cancellation-detail-spec.md)は[証跡](docs/evidence/mvp-cancellation-20260912/README.md)と[親レビュー](docs/reviews/mvp-cancellation-20260912.md)へ結ぶ。固定UC-CIの通常run取消しを接続し、534テストと実Docker219項目が成功した。従来523テスト・198項目を全保持し、既存90件と取消し確認用1件の計91件を実行した。停止未確認はCI終了2、停止済み取消しは3とし、後日精算・再起動・遅延結果で元の記録を変更しない。

## Purpose

GAHの要求・詳細仕様・開発運用への入口を示す。契約・評価設計v0.3から、判定・永続化・診断CLIの初期実装へ進んだ。全MVPの設計凍結・受入は未実施。

## Read Order

1. [README](README.md)
2. [Birdseye Index](docs/birdseye/index.json)と必要なcaps
3. [BLUEPRINT](BLUEPRINT.md) / [GUARDRAILS](GUARDRAILS.md)
4. [要求草案](docs/requirements.md) / [未決定事項](docs/open-questions.md)
5. 作業時は[RUNBOOK](RUNBOOK.md)、完了時は[EVALUATION](EVALUATION.md)

## Task Routing

前工程の[通常run接続](docs/reviews/mvp-regression-20260912.md)では固定UC-CIの計90件と、成果物の保存・取得、再起動、現在のCI利用・根拠撤回を確認する。[仕様](docs/regression-ci-detail-spec.md)にコマンドと制限を示す。

| 作業 | 入口 | 位置づけ |
|---|---|---|
| 通常run・成果物・CI利用 | [仕様](docs/regression-ci-detail-spec.md)、[証跡](docs/evidence/mvp-regression-20260912/README.md) | 固定UC-CIの30件と現在の利用検査 |
| 全MVP完成へ継続 | [完了監査](docs/mvp-completion-audit.md)、[登録・ケース・証拠](docs/runtime-contract-spec.md)、[全体Task](docs/tasks/TASK.mvp-completion-09-11-2026.md) | 32要求の不足・必要証拠・実装進捗 |
| 目的・範囲・要求 | [要求明確化案](docs/requirements.md) | coding agentの開発・CIとLLMガードレール評価の共通要求・判定条件 |
| 製品の受入・要求の来歴 | [受入条件](docs/acceptance-criteria.md)、[対応表](docs/requirements-traceability.md) | 未実行の製品受入案と原稿の処遇 |
| 要求の反例と拡張 | [敵対的検証](docs/reviews/requirements-adversarial-20260910.md)、[拡張案](docs/extension-roadmap.md) | 改訂根拠、優先度、後期の着手条件 |
| 初期運用の決定・設計事項 | [運用方針](docs/operating-policy.md)、[判断事項](docs/open-questions.md) | WARNINGのCI成功、初期閾値・予算、管理AI、外部参照元の参照境界と残る設計 |
| 設計・仕様・契約 | [設計](docs/design.md)、[仕様](docs/spec.md)、[契約](docs/contracts/README.md) | 契約・評価設計初版、機械可読な初期値と境界例 |
| コア実装・外部接続の詳細 | [詳細仕様](docs/detail-spec.md)、[adapter仕様](docs/adapter-spec.md)、[監督レビュー](docs/reviews/runtime-core-20260911.md) | 判定・SQLite・診断CLIの実装と未接続の境界 |
| 中断・取消し・費用回収 | [状態管理の詳細仕様](docs/lifecycle-detail-spec.md)、[監督レビュー](docs/reviews/lifecycle-core-20260911.md) | DB v2移行、回収lease、停止確認、不変terminal |
| 固定fixtureの実行・隔離・停止 | [実行監督](docs/execution-detail-spec.md)、[実行部レビュー](docs/reviews/mvp-execution-20260911.md) | Docker、generic正規化、送信journal、取消し・中断回復の部品受入 |
| 管理主体の認証・方針採択 | [管理境界の詳細仕様](docs/auth-adoption-detail-spec.md)、[レビュー](docs/reviews/mvp-authority-20260911.md) | OS peer認証、PolicyProfile、世代/失効、不変receipt、Qwen限定提案の実測 |
| 評価契約・実行計画・全資源 | [開始境界の詳細仕様](docs/run-contract-detail-spec.md)、[レビュー](docs/reviews/mvp-run-contract-20260911.md) | 初回採択と原子的run開始、固定fixtureの予約・実行・停止・精算。baseline/実モデル接続は残る |
| 実評価集合・段階実行・集計 | [評価詳細仕様](docs/evaluation-detail-spec.md)、[配布pack](datasets/synthetic-policy-v1/README.md) | 受入400件と別用途集合、固定LLM、欠損・再試行・比較の部品仕様 |
| 保存Attempt・診断Decision・現在状態 | [保存詳細仕様](docs/run-evidence-detail-spec.md)、[同DBの認証接続](docs/assurance-authority-detail-spec.md)、[今回の証跡](docs/evidence/mvp-adoption-20260911/README.md) | 保存・精算・初回baselineの実Docker接続、履歴と現在状態の再検査 |
| Finding・計画・再検証候補 | [修復計画の詳細仕様](docs/remediation-detail-spec.md) | 原因不明・不足情報・定型骨子、修復完了と候補の区別 |
| baseline採択・契約更新の次工程 | [採択更新の詳細仕様](docs/baseline-adoption-detail-spec.md)、[比較契約への移行](docs/contract-transition-detail-spec.md)、[監督レビュー](docs/reviews/mvp-contract-adoption-20260912.md) | 固定UC-CIの初回baselineと条件差なしの契約gen2採択を接続。通常run・条件変更を伴う更新は継続中 |
| 外部参照元の参照境界 | [参照境界と独立設計](docs/reference-boundary.md) | クローズド資産の複製・移植・依存化を行わず、GAHの要求から設計する |
| 次工程 | [開発順序案](orchestration/development-plan.md) | 要件化以降の順序案 |
| 文書検証・CI | [RUNBOOK](RUNBOOK.md)、[CI構成](docs/ci-config.md) | 実在する開発コマンド |
| 作業記録と検収 | [Task一覧](docs/tasks/README.md)、[Acceptance索引](docs/acceptance/INDEX.md) | GAHで実施した作業のみ |
| 来歴・旧案件・出典 | [資料来歴](docs/research/README.md)、[出典台帳](docs/research/citation-register.md) | 保存稿と未検証参照 |
| 開発セキュリティ | [SECURITY](SECURITY.md)、[SAC](docs/security/SAC.md) | 製品安全性の証明とは別 |
| 運用判断・取り込み元 | [ADR索引](docs/ADR/README.md)、[UPSTREAM](docs/UPSTREAM.md) | 文書基盤の来歴 |

## Update Rule

正本文書・Task・検収を変更したら参照を更新し、Birdseyeを再生成する。`index`、`hot`、`caps`の世代とsource hashを揃える。archiveは現行ノード・Task・検収の集計対象に含めない。
