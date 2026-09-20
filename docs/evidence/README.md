---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-19
next_review_due: 2026-10-19
---

# 検証証跡

[9月19日の継続検証](productization-continuation-20260919/README.md)に保存上限、worker計測、コピー削減、実Dockerの失敗と再開を記録する。旧MVPの32条件と拡張14条件の受入を区別する。

[9月20日の通常run 800件の継続試験](productization-continuation-20260920/README.md)は固定in-process workerでの完了結果を記録する。実Docker workerや性能SLOを含まず、拡張14条件の受入状態は変更しない。

[条件比較部品](mvp-condition-core-20260912/README.md)は新規30件と既存21件のfocused試験を記録する。後続契約の採択・実行は未接続である。

[製品監督工程](mvp-supervisor-20260912/README.md)は実Docker194項目・91件実行、補正前46件と開始前中断の修正後12件のfocused試験を記録する。全MVPと全体592試験の成功を意味しない。

[baseline更新工程](mvp-baseline-refresh-20260912/README.md)で全体560件と補正後44件の再検証、実Docker248項目が成功した。過去工程は当時のsource hashと結果を保持する。

[取消し復帰の証跡](mvp-recovery-20260912/README.md)で、期限切れ所有権の取得、根拠撤回後の停止・精算と取消しCIを検査する。

通常runの日本語要約・JSON CLIを追加した。直前の全体回帰534件に加えて専用7テストが成功し、実DBの製品CLI15項目で失効・取消しの表示と回収を確認した。 [通常run要約の証跡](mvp-report-20260912/README.md)を参照する。

前工程の[通常run取消し](mvp-cancellation-20260912/README.md)では固定UC-CIの通常run取消しを接続し、534テストと実Docker219項目が成功した。従来523テスト・198項目を全保持し、既存90件と取消し確認用1件の計91件を実行した。停止未確認はCI終了2、停止済み取消しは3とし、後日精算・再起動・遅延結果で元の記録を変更しない。
条件変更を伴う契約・baseline更新、UC-LLMの認証・資源管理、常設orchestrator、Findingの修復確認と全32要求の受入は継続中。

前工程の[契約gen2採択](mvp-contract-adoption-20260912/README.md)は当時の507テスト・150項目として保持する。

前工程の[契約候補run](mvp-candidate-20260911/README.md)は494テスト、実Docker133項目として保持する。

前工程の[契約移行前検査](mvp-transition-20260911/README.md)は463テスト、固定15件の実Docker52項目として保持する。

固定UC-CIの初回baseline採択と同DBへの証跡接続は[今回の証跡](mvp-adoption-20260911/README.md)へ記録する。441テスト、固定15entryの採択40項目、証跡保存41項目、OS認証22項目が成功した。全MVPと通常CIは未完了である。

管理主体のOS認証とPolicyProfile採択は[管理境界の統合証跡](mvp-authority-20260911/verification.json)へ記録する。実Dockerの22項目、DGX Qwenによる限定提案と採択7項目、Luna/親のレビュー修正を含む。モデルの管理提案であり、検出性能の評価や全MVP受入ではない。

全MVP実装の途中経過として、[Registry・CaseSet・Evidenceの112テスト](mvp-completion-20260911/foundation-check.json)と、[固定fixture実行部追加後の151テスト・実Docker検証](mvp-execution-20260911/verification.json)を保存する。後者は通常fixture30件と6probe、取消し・中断回復を含む。全MVP受入は未完了で、[検収draft](../acceptance/AC-20260911-05.md)を維持する。

期限後回収・取消し・終了と台帳v2は[20260911-04の部品検収](../acceptance/AC-20260911-04.md)へ結ぶ。前回の証跡を保持し、今回の80テスト、独立smoke、照会の成否と対象hashを別の証跡として保存する。

GAHで実施した作業の対象、日時、実行環境、コマンド、終了状態、未実施範囲を記録する。文書整理は[当時の検収](../acceptance/AC-20260910-03.md)、詳細仕様と製品コアは[部品検収](../acceptance/AC-20260911-03.md)から追跡する。実装前のpreflightは当時の履歴として保持する。

単発のモデル下書きや作業用出力は`.ga/`、検収に採用する結果はここへ置く。コピー元の証跡は[archive](../research/README.md)へ退避した。JSON証跡はBirdseyeのsource集合に含めず、自己参照hashを作らない。
