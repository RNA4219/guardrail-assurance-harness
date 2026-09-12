---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
template_version: 1.0.0
---

# Guardrail Assurance Harness

Guardrail Assurance Harness（GAH）は、ガードレールとその検査系が今も有効かを継続検証するための制御基盤を目指すプロジェクトです。識別子は `guardrail-assurance-harness`。

**現在は要求レベルです。** 製品コード、正式Schema、評価CLI、外部ツール連携は未実装です。要求明確化案v0.4で32の必須要求と受入条件を整理しました。初期対象は次の両方で確認済みです。WARNINGをCIで許し、生成AIが基準を管理する方針と初期閾値・予算を定めました。外部参照元はクローズド資産のため、一般的な運用の考え方だけを参考にします。コード・Schema・テスト・文書等は移植せず、[参照境界](docs/reference-boundary.md)に従いGAHの要求から独立に設計・実装します。

| 初期対象 | 追跡する変化 |
|---|---|
| coding agentの開発・CI | 制約の違反と、それを見つける検査系の劣化 |
| LLMガードレール評価 | 検出率・見逃し率・誤検知率の変化 |

## MVPの構想

| 機能 | 目的 |
|---|---|
| Control Registry | 守る条件と検査・証拠を対応付ける |
| Mutation CI | 隔離環境で制御を弱め、検査系が気づくか確かめる |
| Decay Detection | 基準値との差や証拠の古さから劣化を検知する |
| Remediation Planner | 根拠・再検証・展開・復旧を含む修復計画を作る |

基準管理AIが評価契約とbaselineを管理し、最終判定と権限の検査は決定的なルールで行います。変更を作るAI、基準管理AI、評価器、任意の計画補助AIの権限を区別します。通常の基準更新に毎回の人間承認を前提としません。MVPに本番の自動修復は含めません。

## 読む順序

1. [HUB](HUB.codex.md): 作業別の入口
2. [Blueprint](BLUEPRINT.md): 目的と範囲
3. [要求明確化案](docs/requirements.md): 目的・MVP・32の必須要求・判定条件
4. [受入条件](docs/acceptance-criteria.md) / [原稿との対応](docs/requirements-traceability.md)
5. [初期運用方針](docs/operating-policy.md) / [判断事項](docs/open-questions.md): CI・数値・管理AIの決定と残る設計事項
6. [開発順序案](orchestration/development-plan.md): 次工程と開始条件
7. [敵対的検証の記録](docs/reviews/requirements-adversarial-20260910.md) / [拡張案](docs/extension-roadmap.md): 反例・修正理由・後期の着手条件

<!-- LLM-BOOTSTRAP v1 -->
エージェントは[Birdseye Index](docs/birdseye/index.json)から対象の周辺ノードとcapsを読む。JSONが使えない場合は[Birdseyeの読み方](docs/BIRDSEYE.md)を参照する。
<!-- /LLM-BOOTSTRAP -->

## 今使えるコマンド

Python 3.11以上、第三者パッケージ不要。文書と開発ワークフローを検証します。

```sh
python -m tools.workflow generate
python -m tools.workflow check
python -m unittest discover -s tests -v
```

[RUNBOOK](RUNBOOK.md)に実行手順、[EVALUATION](EVALUATION.md)に文書の技術検収と将来の製品受入の区別を記載しています。

## 構成と来歴

`docs/requirements.md` が現行要求の正本、`docs/design.md` と `docs/spec.md` は未着手の設計・仕様への入口です。`src/`、`schemas/`、`fixtures/`、`datasets/`、`examples/` は今後の配置先です。`tools/`、`tests/`、`governance/` は文書・開発運用の基盤です。

コピー元の文書・レビュー・検収記録は[資料来歴](docs/research/README.md)から参照できます。GAHの実績や承認には含めません。[出典台帳](docs/research/citation-register.md)の外部引用は未復元です。

GitHub公開・実環境CI・branch protection設定は未実施です。本体licenseは未決定で、コピーしたWorkflow-Cookbook由来のツールと雛形には[MIT表示](third_party/workflow-cookbook.LICENSE)を保持しています。[Security方針](SECURITY.md)も参照してください。
