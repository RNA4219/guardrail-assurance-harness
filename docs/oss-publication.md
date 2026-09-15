---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# MITでのソース公開

2026-09-13の利用者指示に基づき、[Guardrail Assurance Harness](https://github.com/RNA4219/guardrail-assurance-harness)をMITで公開する。正式リリースや製品受入を宣言する工程ではない。

## 2026-09-15 MVP受入後の更新

利用者の反映指示に基づき、全32条件のMVP技術検収結果とCIの12ジョブ並列化を公開更新に含める。[最終証跡](evidence/mvp-acceptance-20260913/mvp-final-20260915-summary.json)と[受入対応表](evidence/mvp-acceptance-20260913/acceptance-map.json)を現在の判定の正本とする。Taskはdone、技術検収はapproved、release_gate=goである。

固定製品ソースの全898試験と追加CI分割9試験、実Docker通常800試行、現在CI・表示・再起動後不変・全精算・回収を確認済み。利用者の指示により、今回のpush後はGitHub Actionsの完了を待たない。CIの起動・必須チェックは維持し、リモート結果を未確認のまま成功扱いしない。

以下の2026-09-13の成熟度・検査記録は初回公開当時の履歴であり、現在のMVP受入状態を表すものではない。

## 公開物とライセンス

本体コード、仕様・文書、自作の固定fixtureと合成データに[MIT License](../LICENSE)を適用する。Workflow-Cookbook由来部分の[既存表示](../third_party/workflow-cookbook.LICENSE)を保持する。第三者パッケージ、モデル重み、コンテナイメージは同梱しない。参照境界と情報非掲載の方針は[従来どおり](reference-boundary.md)とする。

`.ga/`、仮想環境、キャッシュ、実DB、credential、ローカル作業稿はGit対象外。`docs/evidence/`には自作fixture等の履歴を保持し、raw hashとの整合のため改行をGitで変換しない。過去ログにある開発環境のパスは当時の実行場所であり、利用者が設定する接続先ではない。

実行環境に依存していた旧版移行テストは、公開した空v2 schemaから一時DBを生成する。保存稿内のGit属性は本文を変えず`.gitattributes.archived`へ移し、manifestに元の名前を残す。これにより保存された属性が公開ツリーの改行を変換しない。

## 初回公開時の成熟度（2026-09-13）

[全MVP Task](tasks/TASK.mvp-completion-09-11-2026.md)は`in_progress`、[技術検収](acceptance/AC-20260911-05.md)は`draft`、全体の`release_gate=no_go`を維持する。未完了範囲は[32要求の監査](mvp-completion-audit.md)で追跡する。

後続契約と読み取り検査の改修を含む。公開前に残っている直近の記録はfocused 79試験と時刻記録4試験の成功で、統合5試験とそれ以外の全体回帰には完了記録がない。これらを全件成功として扱わない。過去の[工程証跡](evidence/README.md)は、記録されたソース・対象・条件に対する結果のまま保持する。

`config/authority-runtime.lock.json`は改修前の実測値で、現行sourceを固定したDocker imageの構築・実行は未検証。lockを手で書き換えて検査を通さない。Dockerを使用する際は[実行仕様](execution-detail-spec.md)と[管理境界仕様](auth-adoption-detail-spec.md)に従い、fixtureとauthorityを使用環境で構築・検証する。ソース公開によって、LLM評価のCI利用や実運用の保証を追加しない。

## 公開時の検査

初回公開commit `82d6677` はGitHub上でpublic・MITと確認した。初回の文書CIではWindowsとLinuxのPathソート順の差により索引整合が失敗したため、repo相対POSIX文字列で順序を固定した。再現試験の失敗を確認した後、補正後の文書運用11試験が成功。過去のローカル成功記録はその条件のまま保持する。

配布状態の[最終検証](evidence/oss-publication-20260913/portable-validation.json)では、Gitから書き出した全ファイルのbyte一致、文書整合と関連117試験の成功を確認した。先の93件に、作業フォルダへの依存を除いた旧版移行24件を含む。非掲載情報・credential形式・除外対象の残存は0件。全MVPの受入や全suiteの完了は含まない。

[公開準備の検証記録](evidence/oss-publication-20260913/validation.json)では、関連83試験と文書運用10試験の計93件が成功した。文書・CI対応・branch設定の予定値・ローカルsecurity postureも成功し、保護対象7文書は変更していない。全体回帰やリモートCIの成功件数には含めない。

公開するGitツリーに対して、情報非掲載の残存、秘密鍵・token等の形式、除外対象、MIT本文、保護対象7文書の不変を確認する。これは指定パターンとファイル境界の確認であり、一般的な安全性の証明ではない。

文書と構成は`python -m tools.workflow generate`の後に`python -m tools.workflow check`、branch設定の予定値・CI対応・security postureのローカル検査で確認する。全unittestはGitHub Actionsでも実行し、成功・失敗・未完了は各commitの実際のChecksで確認する。branch protectionの実設定と全MVP受入は別に扱う。
