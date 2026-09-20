---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-19
next_review_due: 2026-10-19
template_version: 1.0.0
---

# Changelog

GAHで実施した変更を記録する。コピー元の履歴は[整理前構成](docs/research/README.md)に保存し、GAHの版・実績として引き継がない。

## [Unreleased]

- 0035: SQLiteのwriting接続と既定のhost文書保存へ容量上限を接続。終了workerの計測をrun/sourceへ束縛し、client回収前のsnapshot・read-only collector・Docker保存先観測を追加。コピー削減の呼出元不一致と過去clientの観測失敗を修正。[継続証跡](docs/evidence/productization-continuation-20260919/README.md)に実行source、途中失敗、検査結果と未受入条件を保持する。

- 0034: Luna・DGXを監督して拡張仕様を実装へ接続。性能計測/CI分割、導入・診断・履歴・保持・bundle・移行、実案件metadata管理を追加し、純粋pack/bind生成の重複、authority時計、setupと通常runのmanifest不一致を修正。関連93試験と固定実Docker90件が成功。容量強制・全資源計数・実案件入力経路・image配布取得は未実装で、全14受入は未完了。
  [実装仕様](docs/productization-implementation-spec.md) / [Task](docs/tasks/TASK.productization-implementation-09-15-2026.md) / [監督レビュー](docs/reviews/productization-implementation-20260915.md)。

- 0033: [拡張仕様 v1](docs/productization-spec.md)と性能/CI・導入運用・実案件評価の3分冊を作成。Lunaの分担執筆を親が監督し、共通入出力・役割・再実行・計測式・失敗条件と設計ケースを整理する。製品コードと既存要求は維持し、新規受入はNOT_RUN。
  [Task](docs/tasks/TASK.productization-spec-09-15-2026.md) / [監督レビュー](docs/reviews/productization-spec-20260915.md) / [文書検収](docs/acceptance/AC-20260915-02.md)。

- 0032: MVP後の[拡張要件 v1](docs/productization-requirements.md)を作成。実案件での有用性、処理速度、初回導入・運用の14要件と14受入条件、提案SLO、実施順、反例レビューを整理した。元MVPの要求・受入証拠を維持し、拡張の製品受入は全NOT_RUN。
  [Task](docs/tasks/TASK.productization-requirements-09-15-2026.md) / [文書検収](docs/acceptance/AC-20260915-01.md)。

- 0031: GitHub初回公開時に判明したWindows/Linux間の文書索引の順序差を補正。repo相対POSIX文字列で生成順を固定し、混在文字種の再現試験と既存の文書運用試験を検証した。

- 0030: MIT Licenseを採用してソース公開を準備。公開範囲・初回取得・現行版の検証限界を[公開記録](docs/oss-publication.md)に明記し、作業用状態を除外する。過去の証跡のbyteをGitで保持し、全unittestのCI上限を60分へ更新。MVPの未受入状態は維持する。

- 0029: 意味的条件比較と後続契約の禁止変更検査を追加。実行ID・対象内容・baseline対象・測定条件を分離し、Critical格下げや必須検査の除去を拒否する。新規30件と既存21件の[試験](docs/evidence/mvp-condition-core-20260912/README.md)が成功。authorityの後続採択・実行は未接続。

- 0028: 監督CLI、fresh操作状態照会、baseline更新版DB移行、不変checkpointを追加。開始前中断の再開漏れと停止不明の表示を補正した。実Docker194項目・91件実行、補正前46件と補正後12件を[証跡](docs/evidence/mvp-supervisor-20260912/README.md)へ保存。全MVPは継続中。

- 0027: 固定UC-CIの通常runからbaseline 1→2の更新、保存世代の現在利用、指定世代の撤回、既知旧版の明示移行を追加。全体560件とレビュー補正後の44件再検証、実Docker248項目・92件実行を確認。[仕様](docs/baseline-refresh-detail-spec.md) / [工程証跡](docs/evidence/mvp-baseline-refresh-20260912/README.md)。

- 0026: 期限切れownerの取得と取消しを原子的に行う操作、直前の取消し版DBの明示migrationを追加。全体551件と一覧補正後の6件再検証、実Docker233項目・92件実行を確認。[仕様](docs/run-recovery-detail-spec.md) / [工程証跡](docs/evidence/mvp-recovery-20260912/README.md)。

- 0025: 通常runの日本語Markdown/JSON要約CLIを追加。成果物参照を照合後、最後のfresh CIで失効・取消しを表示する。専用7テストと実DBの製品CLI15項目が成功。[証跡](docs/evidence/mvp-report-20260912/README.md)。

- 0024: 通常runの停止済み取消し、未精算予約、不変terminal、遅延結果、fresh CI終了3を接続。全体534テストと実Docker219項目・91件実行が成功した。[証跡](docs/evidence/mvp-cancellation-20260912/README.md)。

- 0023: 固定UC-CIの通常generation 2 run、Finding/Planの保存・取得、現在のCI利用を接続した。523テストと実Docker198項目が成功し、前工程の507テスト・150項目を保持した。初回15件・旧条件15件・新条件30件・通常30件の計90件を実行し、停止・精算・cleanupを確認した。 [証跡](docs/evidence/mvp-regression-20260912/README.md) / [親レビュー](docs/reviews/mvp-regression-20260912.md)。通常runの取消しterminal、条件変更を伴う契約・baseline更新、UC-LLMの認証・資源管理、常設orchestrator、Findingの修復確認と全32要求の受入は継続中。

- 0022: 契約候補の専用validation、期待世代検査、generation 2への原子的採択と現在有効性を接続。baselineはgeneration 1を維持し、保存履歴・新旧証拠・失効を再検査する。既知v4の明示upgrade、保存故障時rollback、別runの予算予約、再起動と不変receiptを補正・検証した。507テスト・実Docker150項目（60件実行）が成功。モデル最終レビューと非掲載検査の未確認範囲を記録し、通常run・CI・全MVPは継続中。
  [今回の証跡](docs/evidence/mvp-contract-adoption-20260912/README.md) / [監督レビュー](docs/reviews/mvp-contract-adoption-20260912.md)。

- 0021: 契約候補の作成・専用run開始・資源精算・Evidence保存を接続。旧条件15件と新条件30件を別目的で固定し、ID衝突、撤回、保存版解決、rollback、明示v2/v3→v4移行を検証した。494テスト・実Docker133項目（60件実行）が成功。採択validation・世代更新と全MVP受入は継続中。
  [今回の証跡](docs/evidence/mvp-candidate-20260911/README.md) / [監督レビュー](docs/reviews/mvp-candidate-20260911.md)。

- 0020: 初回baselineと旧契約の実体を照合する純粋preflightと、validatorだけの現在前提検査を接続。条件差なしの空配列、source系列、完全参照、撤回後の再照会、応答契約とreadinessの固定errorを補正した。463テスト・固定Docker52項目が成功。候補run・世代更新と全MVP受入は継続中。
  [今回の証跡](docs/evidence/mvp-transition-20260911/README.md) / [監督レビュー](docs/reviews/mvp-transition-20260911.md)。

- 0019: 既知v2の明示migration、採択世代の不変履歴、同DBのAttempt・Decision・Evidence・資源精算を接続。固定15entryの実入力と36vector校正を用意し、初回baselineの採択・再起動後利用・撤回を実Dockerで検証した。親/Lunaレビューで候補時刻、実体照合、原子的保存、制約のみの判定、起動完了確認を補正。441テストと実接続40/41/22項目が成功。全MVPは継続中。
  [今回の証跡](docs/evidence/mvp-adoption-20260911/README.md) / [監督レビュー](docs/reviews/mvp-adoption-20260911.md)。

- 0018: 実入力400件と別用途の校正18件・開発12件、独立oracle照合、段階/再試行/群別集計、baseline対象変更の照合、固定LLMの無害操作診断、Promptfoo 0.123.0単一行mappingを追加。測定側の165vector校正後、400ケース・600段階で見逃し0・誤検知5・前段不一致2を実測した。保存Attemptからの診断Decision再計算、Finding/定型Plan/再検証候補を追加し、DGX QwenとLunaのレビューを監督した。全MVPの接続・受入は継続中。
  [評価詳細仕様](docs/evaluation-detail-spec.md) / [監督レビュー](docs/reviews/mvp-evaluation-20260911.md)。

- 0017: 初回EvaluationContract/TrialPlan/RunManifest、同DB採択・開始、全資源core、固定fixtureの予約・送信意図・観測・閉鎖を接続。LunaレビューでJSON型、世代表、保存破損、予算矛盾、再送許可、後始末確認を修正。DGX Qwenの部分レビューを記録し、実Docker31項目と旧認証22項目、241テストで検証。全MVPは継続中。
  [Task](docs/tasks/TASK.mvp-completion-09-11-2026.md) / [開始境界仕様](docs/run-contract-detail-spec.md) / [監督レビュー](docs/reviews/mvp-run-contract-20260911.md)。

- 0016: PolicyProfileと初期境界、OS peer認証、原子的な採択・世代/失効・不変receiptを実装。Lunaと親のレビューを反映し、実Docker22項目とDGX Qwenの限定提案・採択7項目を検証した。全MVPの契約・実行・台帳接続は継続中。
  [Task](docs/tasks/TASK.mvp-completion-09-11-2026.md) / [管理詳細仕様](docs/auth-adoption-detail-spec.md) / [監督レビュー](docs/reviews/mvp-authority-20260911.md)。

- 0015: 固定fixtureのDocker実行、generic正規化、送信journal、停止回収とowner排他を実装。10制約・5種の検査系劣化、隔離、不正出力、子処理の取消し・中断回復を実コンテナで検証した。DGX QwenとLunaのレビューを反映し、全MVPは継続中。
  [Task](docs/tasks/TASK.mvp-completion-09-11-2026.md) / [実行詳細仕様](docs/execution-detail-spec.md) / [実行部レビュー](docs/reviews/mvp-execution-20260911.md)。

- 0014: 全MVP完成の継続Taskと32要求の完了監査を追加。Registry・CaseSet・Evidenceを実装し、依存閉包、同一sampleの水増し防止、校正、保存前許可、保持/撤回/削除を検証した。DGX QwenとLunaの相互レビューを反映。全MVP受入は未完了。
  [Task](docs/tasks/TASK.mvp-completion-09-11-2026.md) / [検収draft](docs/acceptance/AC-20260911-05.md) / [監督レビュー](docs/reviews/mvp-completion-20260911.md)。

- 0013: 0.2.0として台帳Schema v2の明示的移行、期限後回収、取消し・停止確認、financial closure、不変の終了記録と監査eventを実装。診断CLIを拡張し、Luna/親のレビュー修正と実DB・競合・故障試験を追加した。DGX Qwenの仕様照会は完了、コード照会の時間切れを記録する。全MVP受入との区別を維持。
  [Task](docs/tasks/TASK.lifecycle-core-09-11-2026.md) / [Acceptance](docs/acceptance/AC-20260911-04.md) / [監督レビュー](docs/reviews/lifecycle-core-20260911.md)。

- 0012: コア詳細仕様v1とadapter接続仕様を作成し、判定・厳格JSON入力・SQLite保存・費用台帳・診断CLIを初期実装。DGX Qwen/Lunaを監督し、丸め・原子性・所有世代・精算矛盾・出力障害等のレビューを反映した。部品診断はci_eligible=falseを固定し、全MVP受入と区別する。
  [Task](docs/tasks/TASK.runtime-core-09-11-2026.md) / [Acceptance](docs/acceptance/AC-20260911-03.md) / [監督レビュー](docs/reviews/runtime-core-20260911.md)。

- 0011: 初回・採択・再開と複合障害を見直し、6件を設計v0.3へ反映。初回契約、採択確定時検査、所有世代とdeadline、微小課金、保存/送信前判定、終了優先順を具体化した。手動確認の32例を追加し、製品試験NOT_RUNを維持。
  [Task](docs/tasks/TASK.state-review-09-11-2026.md) / [Acceptance](docs/acceptance/AC-20260911-02.md) / [レビュー](docs/reviews/state-review-20260911.md)。

- 0010: 設計v0.1を再検討し、6件をv0.2へ修正。確定前後の失効、資源別予算と精算、キャッシュ・段階再試行、セッション集計、UC-CIの対象を具体化し、25件の設計例を追加。要求・初期値は維持。
  [Task](docs/tasks/TASK.design-review-09-11-2026.md) / [Acceptance](docs/acceptance/AC-20260911-01.md) / [レビュー](docs/reviews/design-review-20260911.md)。

- 0009: 契約・評価設計v0.1を作成。32要求と初期値を維持し、結果照合、CI終了コード案、管理AIの採択、予算・保存・時刻の境界を具体化。機械可読な初期値、境界の期待例、11シナリオ群と32要求の対応を追加した。製品runtimeと正式Schemaは未実装。
  [Task](docs/tasks/TASK.contract-design-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-11.md) / [設計](docs/design.md)。

- 0008: 利用者指定により、参照元の名称・略称・URL・版識別子を現行文書、ファイル名、保存稿、作業記録、証跡、生成索引から匿名化。過去の検証は当時の履歴と明示し、保存manifestのhashを匿名化後の版へ更新した。非匿名化版の新しいバックアップは作成していない。
  [Task](docs/tasks/TASK.reference-anonymization-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-10.md) / [非掲載方針](docs/reference-boundary.md)。

- 0007: 利用者訂正により、外部参照元はクローズド資産としてコード・Schema・テスト・文書・データを直接利用しない方針へ変更。0006の移植方針を撤回し、一般的な運用の考え方の参照とGAHの独立設計へ限定。AGENTS/Guardrailsにも反映し、この作業で取得した未変更の調査コピーを削除した。
  [Task](docs/tasks/TASK.reference-boundary-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-09.md) / [参照境界](docs/reference-boundary.md)。

- 0006: 利用者提示のURLから外部参照元のcommit nonpublic-revisionを静的確認し、転用元の確認待ちを解消。評価運用の6範囲とGAH側で補う指標計測・データ・校正を対応付けた。要求v0.4の32要求と初期数値を維持。製品へのコード移植は未実施。
  [Task](docs/tasks/TASK.reference-confirmation-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-08.md) / [転用対応表](docs/reference-boundary.md)。

- 0005: 要求明確化案v0.4。利用者回答に基づきWARNINGのCI成功、生成AIの管理主体、opsのOSSからの評価資産転用方針を反映。委任された閾値・予算を初期運用方針v1へ具体化し、境界と受入例を更新。正確な転用元の特定と実際の転用は次工程。
  [Task](docs/tasks/TASK.operating-decisions-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-07.md) / [初期運用方針](docs/operating-policy.md)。

- 0004: 要求明確化案v0.3。敵対的な反例11観点から、集合指標と個別違反の判定、評価契約の独立性、重複・再試行、依存とCI対象、データ校正、推定の不確かさ、状態分離、実行境界、修復確認を補強。32要求・32受入条件と7件の後期拡張案へ反映。
  [Task](docs/tasks/TASK.requirements-adversarial-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-06.md) / [反例と処遇](docs/reviews/requirements-adversarial-20260910.md)。

- 0003: 要求明確化案v0.2。利用者補足に基づきcoding agentの開発・CIとLLMガードレール評価の両方を初期対象として確認。検出率・見逃し率・FPR、期待ラベル、比較条件、LLMの役割、二つの受入シナリオを明確化。
  [Task](docs/tasks/TASK.dual-usecases-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-05.md) / [要求](docs/requirements.md)。

- 0002: 要求明確化案v0.1。目的・MVP/後期の範囲、24要求と24受入条件、原稿17機能・14受入項目の処遇を整理。判定不能・Mutation結果・baseline比較・HOLDの効力を具体化。
  [Task](docs/tasks/TASK.requirements-clarification-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-04.md) / [要求](docs/requirements.md)。

- 0001: 要求段階に合わせて文書を整理。要求本文を内容保持のまま整形し、未決定事項・出典台帳・資料来歴を追加。コピー元のレビューと検収をarchiveへ分離し、識別子と現行索引を更新。
  [Task](docs/tasks/TASK.docs-cleanup-09-10-2026.md) / [Acceptance](docs/acceptance/AC-20260910-03.md) / [整理記録](docs/reviews/docs-cleanup-20260910.md)。


## 2026-09-16 拡張実装の継続

- offline評価結果の厳密importと冪等CLIを追加。
- 論理容量・新規SQLite上限・failure sink部品とCheckpointの明示予算を追加。
- 固定authority cgroup/host CPU観測をwhole-run副証跡へ接続。空I/Oの欠測をCPUと分離。
- 新規配布先での固定base取得・3image構築とlock照合を追加。
- [検証証跡](docs/evidence/productization-continuation-20260916/README.md): 関連116件中114成功/2skip、実image準備と資源観測。全拡張受入は未完了。
