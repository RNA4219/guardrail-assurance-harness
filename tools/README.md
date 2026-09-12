---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# ワークフロー補助

`python -m tools.gah_run {run,resume,cancel,status} --runtime <既存deployment> --request <JSON>` で固定UC-CIを監督する。入力と制限は[監督仕様](../docs/supervised-run-detail-spec.md)を参照。`python -m tools.verify_baseline_runtime --supervisor --output <新規repo内directory>` は初回・候補採択から通常監督・中断回収までの独立した実Docker検証経路で、--baseline-refresh等と同時指定しない。

`python -m tools.verify_baseline_runtime --baseline-refresh --output <新規repo内directory>`で、通常runを使ったbaseline 1→2への更新・固定参照・依存失効を確認する。--recovery以下の固定fixture経路を含む。[仕様](../docs/baseline-refresh-detail-spec.md)。

`verify_baseline_runtime --recovery`は既存の取消し検証を含み、実時計のlease切れと根拠撤回後の取消し取得も検査する。[仕様](../docs/run-recovery-detail-spec.md)を参照する。

`python -m tools.gah_report --runtime <既存authority> --request <ci_check JSON> [--format json]` は、保存成果物と最後のCI照会から日本語MarkdownまたはJSONを出力する。[仕様](../docs/run-report-detail-spec.md)。

`python -m tools.verify_baseline_runtime --cancellation --output <repo内の新規ディレクトリ>`で、従来90件に取消し用の固定fixture一件を加えて実Docker検証する。停止未確認、未精算の確定、再起動、後日精算、CI consumer終了3、回収を確認する。[詳細仕様](../docs/run-cancellation-detail-spec.md)。

[workflow.py](workflow.py) が文書索引・Birdseyeの生成と統合検証を提供する。ci配下の8検証器は [固定したCookbook](../docs/UPSTREAM.md) 由来。Python 3.11以上の標準ライブラリで動作する。

実行手順は [RUNBOOK](../RUNBOOK.md)。製品runtimeはsrcへ置く。

固定fixtureの初回baseline接続は、repo rootから `python -m tools.verify_baseline_runtime --output .ga/baseline-check` で明示実行する。
出力先は未存在のrepo内ディレクトリを指定する。既存のauthority/fixtureイメージを検査し、15件の実行、精算、
採択、再起動後の利用と撤回、コンテナ回収を記録する。DB volumeは証跡として保持する。
初回採択後には、validatorの契約移行前検査、候補/manager/operatorの権限拒否、
完全参照・旧runの不変、Evidence撤回後の同一要求の拒否も検査する。
これは[移行前検査](../docs/contract-transition-detail-spec.md)であり、generation 2を採択しない。
事前の固定イメージ作成は `python -m tools.prepare_authority_runtime`。
`--candidate-runs`を加えると、旧条件15件・新条件30件も別runで実行し、初回を含む60件を検証する。
候補の完全参照、再起動後の保存実体、未送信予約に対する撤回後のdispatch拒否と回収も記録する。
例: `python -m tools.verify_baseline_runtime --candidate-runs --output .ga/candidate-check`。
`--candidate-adoption`を加えると同じ60件から専用validation・契約generation 2の採択、
再起動後の現在有効性、根拠撤回後の失効と不変receiptを検査する。
例: `python -m tools.verify_baseline_runtime --candidate-adoption --output .ga/contract-adoption-check`。
このモードは候補のみの未送信予約probeを採択後の検査へ置き換え、baselineはgeneration 1のまま保持する。
全MVPの受入・通常CI成功とは区別する。[接続仕様](../docs/assurance-authority-detail-spec.md)と
[採択仕様](../docs/baseline-adoption-detail-spec.md)を参照する。

## 固定通常runのCI利用

`python -m tools.gah_ci --runtime <既存authorityのディレクトリ> --request <ci_check JSON>`で現在の利用を問い合わせる。固定UC-CIだけを対象とし、保存JSONを直接成功根拠にしない。[仕様](../docs/regression-ci-detail-spec.md)。
`python -m tools.verify_baseline_runtime --regression --output <repo内の新規ディレクトリ>`は初回・候補・通常の計90件の検証用。
