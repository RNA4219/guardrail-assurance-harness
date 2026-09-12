---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
template_version: 1.0.0
---

# Runbook

## Environments

Python 3.11以上の標準ライブラリを用いる。作業ディレクトリはこのrepoのroot。
以下の文書検証コマンドにネットワーク・API key・隣接repoへの依存はない。WindowsでもPythonの実体パスを指定して同じコマンドを使える。
CIの定義は [ci-config](docs/ci-config.md) に記載する。

## Execute

```sh
# 文書・Task・Acceptanceを編集した後
python -m tools.workflow generate

# Tier / 文書 / 索引 / Birdseye / CI定義 / Taskと検収の対応
python -m tools.workflow check

# 部品とワークフローの回帰テスト
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

部品診断は次を使用する。集計値の算術検証であり、入力例は実モデルの観測ではない。`assess`はDBへ保存してから返し、`show`は新しいプロセスで同じ記録を読み出す。

```sh
python -m tools.gah_cli assess --input examples/component-assessment.v1.json --db .ga/diagnostic.sqlite
python -m tools.gah_cli show --id sample-assessment-1 --db .ga/diagnostic.sqlite
```

通常の診断完了は終了1、入力・保存・出力障害は終了2。`ci_eligible=false`を固定する。`--help`と`--version`の終了0を評価成功と扱わない。同一request_idに別内容を保存すると拒否する。`--output`は新規pathだけを受け付け、出力に失敗してもDBの診断を消さない。詳細は[CLI契約](docs/detail-spec.md)を参照する。

## Observability

固定ローカルLLMの診断は、[配布pack](datasets/synthetic-policy-v1/README.md)を使う。endpointは127.0.0.1:18000、モデル識別名はqwen3.8-flash-nextで、任意URL・入力本文を受け取らない。開始前に、モデルを呼ばず既知の応答で測定側を独立校正し、`evaluator-calibration.json`へ保存する。評価器校正の不一致や別packなら対象へ送信せずHOLDにする。`calibration`用途は対象の18ケース診断、`acceptance`用途は400ケース測定であり、どちらも同じ事前校正を行う。

summaryの`evaluator_calibration_passed`は測定側の校正、`target_agreement_passed`は対象の全段階一致を表す。互換用の`calibration_passed`はcalibration用途での対象一致度であり、評価器の採択根拠に使わない。対象の誤答・判定不能を測定結果として保持する。

```sh
python -m tools.verify_synthetic_llm --purpose calibration --outdir .ga/llm-calibration-new
python -m tools.verify_synthetic_llm --purpose acceptance --outdir .ga/llm-acceptance-new
```

新規outdirのみ許可し、最大4caseを同時実行する。子処理は120秒、全runの新規送信はmonotonic clockで5400秒以内に制限する。HTTP timeoutやusage不明では未送信を止め、停止不明・予約を保持する。manifest、case別記録、records、summaryを保存する。CLIの終了0は全caseの実行完了であり、校正合格・CI成功・全MVP受入を意味しない。モデル配備版、OS authority、資源台帳、通常CIへの接続は未完了でci_eligible=falseである。

認証brokerと評価契約の接続検証は次を明示実行する。生成される新規管理DBは拡張v2。従来の管理v1 DBは暗黙移行せず拒否する。診断CLIの台帳v2とは別のDBで、既存volumeを破棄・初期化しない。

```sh
python -m tools.prepare_authority_runtime
python -m tools.verify_authority_runtime --output .ga/authority-check-new
python -m tools.verify_evaluation_runtime --output .ga/evaluation-check-new
```

Dockerが必要で、出力先は未存在のrepo内directoryとする。固定fixtureと400個の合成参照を使う部品接続試験で、実LLM評価・baseline採択・通常CI成功を発行しない。検証用state volumeは保持し、所有containerだけを停止・削除・不存在確認する。[詳細仕様](docs/run-contract-detail-spec.md)に未接続範囲を記載する。

固定fixtureの実Docker検証は明示的に実行する。Docker DesktopのLinux Engineと固定した公式Pythonベースが必要で、実行時にimageをpullしない。準備では自作workerとDockerfileの2ファイルだけをbuild contextに入れる。`--output`は未存在のrepo内ディレクトリを指定する。

```sh
docker pull python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
python -m tools.prepare_fixture_runtime
python -m tools.verify_fixture_runtime --suite full --output .ga/fixture-acceptance-new
python -m tools.verify_fixture_lifecycle --output .ga/fixture-lifecycle-new
```

これらの終了0は固定fixtureの検証成功である。通常CIへ渡す評価結果は発行しない。実行部のreceiptは常にci_eligible=false。失敗時は[実行監督仕様](docs/execution-detail-spec.md)のjournalへ未回収状態を残す。監督の再起動後は同じjournalとimage lockで`DockerRunner.recover(run_id, operation_id)`を呼び、同一containerを回収する。生存ownerのロックは奪わず、未知の対象へkill/rmを広げない。

0.2.0では既存v1台帳を明示的に移行する。新規DBはv2で作成され、通常のassess/showで既存v1を自動変更しない。

```sh
python -m tools.gah_cli db-upgrade --db .ga/diagnostic.sqlite
python -m tools.gah_cli budget-show --id example-run --db .ga/diagnostic.sqlite
python -m tools.gah_cli terminal-show --id example-run --db .ga/diagnostic.sqlite
```

example-runは監督用の内部APIで作成したrun_idに置き換える。上記は既存DB専用で、正常な移行・表示は終了1。budget-showは費用だけのclosure、terminal-showは保存済みの終了記録を表示する。保存記録のexit_codeが2/3でも正常表示の終了は1となる。[詳細な状態契約](docs/lifecycle-detail-spec.md)を参照する。

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
