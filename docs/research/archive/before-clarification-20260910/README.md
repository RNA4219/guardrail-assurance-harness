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

**現在は要求レベルです。** 製品コード、正式Schema、評価CLI、外部ツール連携は未実装です。要求本文の構成図・コマンド例・閾値は提案を含みます。

## MVPの構想

| 機能 | 目的 |
|---|---|
| Control Registry | 守る条件と検査・証拠を対応付ける |
| Mutation CI | 隔離環境で制御を弱め、検査系が気づくか確かめる |
| Decay Detection | 基準値との差や証拠の古さから劣化を検知する |
| Remediation Planner | 根拠・再検証・展開・復旧を含む修復計画を作る |

最終判定と権限管理は決定的なルールで行い、AIは診断と計画の生成を支援する構想です。MVPに本番の自動修復は含めません。

## 読む順序

1. [HUB](HUB.codex.md): 作業別の入口
2. [Blueprint](BLUEPRINT.md): 目的と範囲
3. [要求整理（草案）](docs/requirements.md): 現行要求本文
4. [未決定事項](docs/open-questions.md): 要件化・仕様化の前に決めること
5. [開発順序案](orchestration/development-plan.md): 次工程と開始条件

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
