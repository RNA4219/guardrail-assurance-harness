---
task_id: 20260911-05
intent_id: INT-GAH-001
owner: RNA4219
status: in_progress
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# Task: 全MVPの実装・接続・受入

[条件比較部品](../semantic-conditions-detail-spec.md)の新規30件・既存21件を[検証](../evidence/mvp-condition-core-20260912/README.md)した。後続契約の実体化・旧新別実行・採択を継続し、全MVPのin_progressを維持する。

固定UC-CIの開始・再開・取消し・状態照会CLIを接続した。実Docker194項目・91件実行が成功し、停止・回収を確認した。追加レビューで準備直後の再開漏れを補正し、実SQLiteと合成runnerの12試験が成功した。補正前46件と合わせて50種類をfocused検証した。Dockerはこの一行補正前の結果で、現行592件の全体試験は再実行していない。 [工程証跡](../evidence/mvp-supervisor-20260912/README.md)。全MVP受入は未完了。

固定UC-CIの通常runからbaseline generation 1→2への更新を接続した。全体回帰560件の後、保存記録の照合を補正して影響する44件を再検証し、対象全件の成功を確認した。固有の検証対象は561件で、従来551件を保持する。補正後の実Docker248項目も成功し、従来233項目の判定を保持した。固定fixtureの実行92件は全て停止・回収済み。世代ごとの撤回と依存失効、旧基準CIの継続、違反runの昇格拒否、保存失敗時のrollbackを確認した。 [仕様](../baseline-refresh-detail-spec.md)と[証跡](../evidence/mvp-baseline-refresh-20260912/README.md)へ結ぶ。親が担当し、全MVPのObjectiveとin_progressを維持する。

[取消し復帰](../run-recovery-detail-spec.md)を追加した。親が実装・レビュー・検証を担当し、[証跡](../evidence/mvp-recovery-20260912/README.md)へ結ぶ。全MVPの目標と未完了の接続範囲を維持する。

## Objective

利用者の「全MVP完成まで進めて」に従い、二つの利用場面とGAH-R01〜R32を実装・接続し、要件別の実行証拠で完成を確認する。部品の成功へ目標を縮めない。

## Scope

In: Registry、固定fixtureのMutation CI、Decay、Finding/Plan、全契約と採択、生成AI管理の主体分離、generic command/Promptfoo、実行監督・隔離・予算・停止・復旧、400件の評価集合と校正、Evidence保持・撤回、CI利用時照合と変更/定期接続、32要求の受入。
Out: 本番の自動修復、第三者への攻撃、任意コードの攻撃実行、公開/push/repository設定変更、後期拡張。非掲載指定と独立設計を守る。

## Requirements

Behavior: [要求](../requirements.md)と[受入条件](../acceptance-criteria.md)を全て対象とする。
I/O Contract: [評価契約](../contracts/evaluation-contract.md)、[初回・採択等](../contracts/lifecycle-contract.md)、[登録・ケース・証拠](../runtime-contract-spec.md)。
Constraints: 初期方針の閾値・予算を維持する。認証や隔離を役割文字列で代用せず、実行失敗や欠損を成功にしない。
Acceptance Criteria: [完了監査](../mvp-completion-audit.md)で各要求の必要証拠を特定し、実際の実装・実行と照合する。未検証が一つでも残れば全MVP完了としない。

採択・証跡接続の工程: [同DBの認証接続仕様](../assurance-authority-detail-spec.md)と
[監督レビュー](../reviews/mvp-adoption-20260911.md)に、Lunaの分担・親レビュー・DGX Qwen照会を記録する。
既知v2の明示migration、固定世代の採択履歴、精算済み観測と不変receiptの原子的保存を追加した。
停止済みの記録保存と新規dispatchを区別し、現在の利用判定で期限・権限・撤回を再照合する。
固定UC-CIの15entryから初回baseline採択・現在利用・撤回まで実Dockerで確認した。
UC-LLMのpack採択、baseline更新、常設run・モデル資源・通常CIの残る接続を継続する。

次の工程では[比較契約への移行](../contract-transition-detail-spec.md)に沿って、
保存済み旧条件と次契約を照合する事前検査を追加した。[最新証跡](../evidence/mvp-transition-20260911/README.md)で
全463テストと固定Docker52項目が成功した。preflightから候補run、
旧条件回帰、採択validation、原子的な世代更新への接続を段階的に検証し、
事前検査だけで全MVPの受入を完了にしない。

## 候補runの進捗

[移行仕様](../contract-transition-detail-spec.md)の手順4まで接続し、
旧条件15件・新条件30件を別run・Evidence・closureとして保存した。
[最新証跡](../evidence/mvp-candidate-20260911/verification.json)で494テスト（前回463件を保持）と
実Docker133項目・60件が成功。新規/移行DB v4の同一DDL、ID予約の排他、保存失敗時のrollback、
撤回後の開始拒否と停止済み観測保存を確認した。[監督レビュー](../reviews/mvp-candidate-20260911.md)に
Luna・DGX Qwenの処遇と検証側のreceipt比較修正を記録する。
手順5は[契約採択の監督レビュー](../reviews/mvp-contract-adoption-20260912.md)と
[専用証跡](../evidence/mvp-contract-adoption-20260912/README.md)へ続く。

## 契約世代2の採択と次工程

専用validation、旧契約・baselineの期待世代検査、契約currentと履歴の原子的更新を接続した。
採択後の現在有効性は旧currentへの復帰を要求せず、不変履歴・校正・初回source・候補Evidenceから再検査する。
DBの列形式はv4を保ち、既知の候補実行版からの明示upgradeでfactory結合と保存履歴を確認する。
Lunaの利用上限とQwenの2回の時間切れにより最終独立レビューは未完了。親が修正・コードレビュー・検証を引き取った。

採択工程で次とした通常gen2 runの比較contextと入力実体化は、下記の通常run工程で接続した。候補purposeの再利用を拒否し、新しいrun IDと
採択済みの完全参照から通常評価を作る。再起動、根拠撤回、精算、保存Evidenceの現在判定までを確認したうえで
通常CIへ進む。条件変更を伴う契約・baseline更新、認証されたモデル資源、Finding/Plan、全32要求の受入も継続する。
採択時点の読取り不能記録は保持する。今回の通常run工程で補完検査を実施し、全MVP Taskはin_progressを維持する。

## 通常runとCI利用の進捗

固定UC-CIの通常generation 2 run、Finding/Planの保存・取得、現在のCI利用を接続した。523テストと実Docker198項目が成功し、前工程の507テスト・150項目を保持した。初回15件・旧条件15件・新条件30件・通常30件の計90件を実行し、停止・精算・cleanupを確認した。
[証跡](../evidence/mvp-regression-20260912/README.md)、[仕様](../regression-ci-detail-spec.md)、[親レビュー](../reviews/mvp-regression-20260912.md)へ結ぶ。最新の利用者指示により親が担当し、外部モデルレビューを完了したとはしない。非掲載検査の読取り不能箇所は31ファイルのnative読取りで解消した。

条件変更を伴う契約・baseline更新、UC-LLMの認証・資源管理、常設orchestrator、Findingの修復確認と全32要求の受入は継続中。 全MVP Taskはin_progressを維持する。

## 通常run取消しの進捗

固定UC-CIの通常run取消しを接続し、534テストと実Docker219項目が成功した。従来523テスト・198項目を全保持し、既存90件と取消し確認用1件の計91件を実行した。停止未確認はCI終了2、停止済み取消しは3とし、後日精算・再起動・遅延結果で元の記録を変更しない。
[証跡](../evidence/mvp-cancellation-20260912/README.md)、[仕様](../run-cancellation-detail-spec.md)、[親レビュー](../reviews/mvp-cancellation-20260912.md)へ結ぶ。全MVPの残件は[32要求の監査](../mvp-completion-audit.md)で管理し、Taskはin_progressを維持する。

## 通常run要約の進捗

通常runの日本語要約・JSON CLIを追加した。直前の全体回帰534件に加えて専用7テストが成功し、実DBの製品CLI15項目で失効・取消しの表示と回収を確認した。
[仕様](../run-report-detail-spec.md)、[親レビュー](../reviews/mvp-report-20260912.md)、[証跡](../evidence/mvp-report-20260912/README.md)。全MVP Taskはin_progressを維持する。

## Affected Paths

src/gah、schemas、fixtures、datasets、tests、tools、現行仕様・導線・Task/Acceptance/Evidence。

## Local Commands

```sh
python -m unittest discover -s tests -v
python -m tools.workflow generate
python -m tools.workflow check
```

## Deliverables

全MVPの実行可能な製品、要件別の受入証拠、詳細仕様、運用手順、監督・独立レビューと指摘の処遇。

## Plan

1. 要件別に実装と証拠の不足を監査し、登録・ケース・証拠の契約を実装する。
2. 全オブジェクトと保存・採択を接続し、管理AIの主体・権限・context分離を実環境で検証する。
3. 固定fixture・実評価データ・oracle・校正と二つのadapterを接続する。
4. 隔離・資源監督・dispatch・停止・復旧・保存前境界を実行で検証する。
5. Decision、Finding/Plan、再検証、現在のCI利用判定を接続する。
6. 正常・劣化・違反・欠損・故障・競合を含め32要求全てを監査し、最終受入する。

## Tests

契約gen2採択時点では[507テスト・実Docker150項目](../evidence/mvp-contract-adoption-20260912/verification.json)が成功した。前回494件を全保持し、新規13件を追加。固定60件の実行と採択後の現在有効性、再起動、撤回、不変receipt、回収を確認した。実測ソースと要求・初期値・設計例7ファイルのhashも一致する。モデルによる最終独立レビューと非掲載検査の読取り不能箇所を未完了として記録する。全MVP Taskは継続中。

初回採択工程は441テスト成功（前工程357件を保持、新規84件）。[当時の統合検証](../evidence/mvp-adoption-20260911/verification.json)に、実Dockerの採択40項目・証跡接続41項目・認証22項目を記録した。

前工程の評価は357テスト成功。[当時の統合検証](../evidence/mvp-evaluation-20260911/verification.json)として保持する。

途中経過: 登録・ケース・証拠の追加後112テスト成功（以前の80を保持、新規32）。[対象hashと結果](../evidence/mvp-completion-20260911/foundation-check.json)。全MVPの要件別受入は未完了で、[技術検収](../acceptance/AC-20260911-05.md)はdraftを維持する。

実行部を追加し、固定30fixtureと6probeの実Docker検証が成功した。子処理の取消し・監督中断からの回復・owner排他・再配送不変も確認した。[実行部の統合検証](../evidence/mvp-execution-20260911/verification.json)に全テスト、前回112件の保持、最終ソースhashを記録する。

最終回帰は151テスト成功（前回112件を保持、新規39件）。元の要求・受入・初期方針/数値・100設計例のhashも一致した。

管理境界追加後の回帰は203テスト成功（前回151件を保持、新規52件）。実DockerとQwen接続で試験したコードのhashも一致した。詳細仕様の検証範囲を明確化した後の確認は[最終確認](../evidence/mvp-authority-20260911/final-check.json)へ記録する。

## Commands

`python -X utf8 .ga/mvp-completion-20260911/verify_foundation.py`が成功。全unittest、前回80件の保持、要求・初期値・設計例のhash、DGX Qwen提出稿と回答hashを検査した。[監督レビュー](../reviews/mvp-completion-20260911.md)へ処遇を記録する。Docker Engine版の読取りにも成功し、次の隔離実装の実基盤を確認した。

続いて`python -m tools.verify_fixture_runtime --suite full`、`python -m tools.verify_fixture_lifecycle`、`.ga/mvp-execution-20260911/verify_execution.py`を実行した。出力先を新規指定し、以前の実行証跡は上書きしない。[実行詳細仕様](../execution-detail-spec.md)と[実行部レビュー](../reviews/mvp-execution-20260911.md)を追加した。次は採択/認証と実行契約・全資源台帳の接続を進める。

## Notes

2026-09-13: 利用者の指示に従い、MITでの[ソース公開](../oss-publication.md)を優先する。後続契約の接続改修は作業中のソースとして含み、統合・全体回帰・現行Dockerの完了を主張しない。全MVP Taskはin_progress、Acceptanceはdraftを維持する。

評価工程の進捗: [評価仕様](../evaluation-detail-spec.md)、[保存仕様](../run-evidence-detail-spec.md)、[修復計画仕様](../remediation-detail-spec.md)を追加した。実入力400件の集合の盲点と、対象の一致度を評価器校正としていた誤りを親・Lunaレビューで補正した。独立校正165vectorを通した後の実測は400ケース・600段階、TP200/FN0/TN195/FP5、前段不一致2、173,309token、398秒。Promptfoo通常出力と障害出力、固定fixture31項目、同じイメージのOS認証22項目も確認した。[レビューと処遇](../reviews/mvp-evaluation-20260911.md)および[今回の証跡](../evidence/mvp-evaluation-20260911/README.md)へ結ぶ。baseline/更新契約、モデルの資源・認証、常設実行、全オブジェクトと現在CI判定の接続を続ける。

評価契約接続の進捗: [詳細仕様](../run-contract-detail-spec.md)と[レビュー処遇](../reviews/mvp-run-contract-20260911.md)を追加。初回契約・RunManifest・全資源coreを同DBへ接続し、実Dockerで採択→予約→固定fixture実行→停止/usage→精算・閉鎖の31項目、既存認証の22項目が成功した。`python -m tools.verify_evaluation_runtime`のruntime-02を最終証拠とし、cleanup再確認を強化する前のruntime-01は上書きせず保留する。[統合証跡](../evidence/mvp-run-contract-20260911/verification.json)で241テスト、前回203件の保持、要求・初期値・設計例の不変を確認する。DGX Qwenの仕様照会は650token上限で部分応答となり、完了レビューには数えない。baseline/旧条件回帰、実評価データ・Promptfoo、常設実行とモデル資源、Evidence/CI、Finding/Planの接続を続ける。

管理境界の進捗: `PolicyProfile`、固定UIDとSO_PEERCRED、原子的採択・現在状態・不変receiptを実装した。実Dockerの22項目、DGX Qwenの提案から採択まで7項目が成功した。[管理境界の統合証跡](../evidence/mvp-authority-20260911/verification.json)と[レビュー処遇](../reviews/mvp-authority-20260911.md)へ結ぶ。Qwenの仕様レビュー時間切れと、モデル接続初回の受理失敗も履歴として保持する。全MVPのTaskはin_progressを維持し、次は全評価契約と実行・台帳の接続を進める。

利用者の最新指示に従い、残件は親が実装・レビュー・検証を担当する。過去のモデル出力を採択・製品成功の証拠に直接使わない。全MVPという目標を保持する。
