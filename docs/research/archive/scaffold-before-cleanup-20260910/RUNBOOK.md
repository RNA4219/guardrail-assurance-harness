---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
template_version: 1.0.0
---

# Runbook

## Environments

Python 3.11以上の標準ライブラリを用いる。作業ディレクトリはこのrepoのroot。
ネットワーク・API key・隣接repoへの依存はない。WindowsでもPythonの実体パスを指定して同じコマンドを使える。
CIの定義は [ci-config](docs/ci-config.md) に記載する。

## Execute

```sh
# 文書・Task・Acceptanceを編集した後
python -m tools.workflow generate

# Tier / 文書 / 索引 / Birdseye / CI定義 / Taskと検収の対応
python -m tools.workflow check

# ワークフロー検証ツールの回帰テスト
python -m unittest discover -s tests -v

# 個別のCookbook標準判定
python tools/ci/check_adoption_tier.py --repo . --min-tier 3 --check --check-drift
python -m tools.ci.check_downstream_onboarding --repo . --min-tier 3 --check
python tools/ci/check_acceptance.py --check
python tools/ci/check_task_acceptance_bidirectional.py
python tools/ci/check_birdseye_freshness.py --check
python tools/ci/check_ci_gate_matrix.py
python tools/ci/check_security_posture.py --check
```

`generate` はAcceptance索引とBirdseyeを生成する。`check` は読み取りのみ。
生成物が古い場合にCI側で自動修復せず失敗させる。
`--report .ga/workflow-check.json` をcheckへ追加すると機械可読の検査結果を保存できる。

## Observability

Taskのコマンド・結果を [docs/acceptance](docs/acceptance/README.md) に結ぶ。
随時の出力は `.ga/`、採択した検証記録は [docs/evidence](docs/evidence/README.md) に保存する。
ログには秘密値を含めない。製品の性能・精度を未計測のまま記載しない。

## Confirm

- 検査の失敗が0件である。
- TaskとAcceptance、CHANGELOG、必要なADRが対応する。
- 要件の正本、参照先、生成物の鮮度が揃っている。
- 外部環境の未確認事項をローカル検査成功と区別する。

## Rollback / Retry

生成失敗時は原因を修正し同じコマンドで再生成する。検査結果を通す目的で要件を削らない。
移動前原稿は [資料来歴](docs/research/README.md) を参照する。
文書の差し戻しは対象差分だけに限定し、無関係な利用者変更を戻さない。

