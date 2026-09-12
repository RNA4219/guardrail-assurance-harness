---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
template_version: 1.0.0
---

# Spec Reconstructor

既存システムの構造と振る舞いを、根拠付きの仕様へ再構成するOSS。識別子は `spec-reconstructor`。

現在は要件定義・設計準備段階です。解析CLI・Explorerは未実装で、下記コマンドは開発ワークフローと文書の検証に使用します。

## 読み順

1. [HUB.codex.md](HUB.codex.md) — 作業別の入口
2. [BLUEPRINT.md](BLUEPRINT.md) — 目的、範囲、入出力
3. [要件定義](docs/requirements.md) — 改訂済みの詳細要件の正本
4. [RUNBOOK.md](RUNBOOK.md) — 生成・検証コマンド
5. [EVALUATION.md](EVALUATION.md) — 構成受入と製品受入の区別

<!-- LLM-BOOTSTRAP v1 -->
エージェントは [Birdseye Index](docs/birdseye/index.json) から対象ノードの±2 hopを絞り、
対応する `docs/birdseye/caps/<path>.json` を読む。JSONを利用できない場合は
[Birdseyeの読み方](docs/BIRDSEYE.md) を使用する。
<!-- /LLM-BOOTSTRAP -->

## ローカル検証

Python 3.11以上。第三者Pythonパッケージのインストールは不要です。

```sh
python -m tools.workflow generate
python -m tools.workflow check
python -m unittest discover -s tests -v
```

Windowsで `python` がStore stubの場合は、導入済みPythonの実体パスを使用する。
Pythonはワークフロー補助用であり、製品runtimeの実装言語を決定するものではない。

## フォルダ構成

```text
spec-reconstructor/
├── README.md / AGENTS.md / HUB.codex.md
├── BLUEPRINT.md / RUNBOOK.md / GUARDRAILS.md / EVALUATION.md
├── CHECKLISTS.md / CHANGELOG.md / TASK.codex.md
├── CONTRIBUTING.md / SECURITY.md / CODEOWNERS
├── .github/             CI・PR/Issueテンプレート・Dependabot
├── governance/          ポリシー・必要check・導入元の固定情報
├── docs/
│   ├── requirements.md  詳細要件の正本
│   ├── design.md / spec.md
│   ├── ADR/             決定記録
│   ├── tasks/           Task Seed
│   ├── acceptance/      検収記録と自動生成索引
│   ├── birdseye/        index / hot / caps
│   ├── evidence/        検証証跡
│   ├── research/        資料来歴・改訂前の原稿
│   ├── security/        開発セキュリティの基準
│   ├── contracts/       契約仕様の入口
│   └── releases/        将来の公開記録
├── orchestration/       M0→M1→M2の依存順
├── templates/           Cookbookの固定した基本文書雛形
├── tools/               生成・検証ツール
├── tests/               ワークフロー検証の回帰テスト
├── src/                 製品実装予定
├── schemas/             製品Schema実装予定
├── fixtures/            合成fixture予定
├── datasets/            評価データ管理
├── examples/            将来の利用例
└── third_party/         取り込み元のlicense
```

## ワークフロー

[Workflow-Cookbook Tier 3: Full導入記録](docs/ADR/0001-workflow-cookbook-adoption.md)、
[Task一覧](docs/tasks/README.md)、[Acceptance索引](docs/acceptance/INDEX.md)、
[CI構成](docs/ci-config.md)、[Upstream管理](docs/UPSTREAM.md) を参照する。

## Securityと公開状態

[Security方針](SECURITY.md) と [SAC](docs/security/SAC.md) を参照。
GitHub repoの作成、外部公開、branch protectionの実設定は未実施。
`governance/branch-protection.expected.json` は適用予定の設定であり実測exportではない。
本体のOSS licenseは要件定義に従い公開前に決定する。
取り込んだWorkflow-Cookbookのツール・雛形には [MIT license](third_party/workflow-cookbook.LICENSE) を維持する。

