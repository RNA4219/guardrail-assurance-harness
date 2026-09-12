---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
template_version: 1.0.0
---

# Guardrail Assurance Harness

ガードレールと、その検査系の劣化を追跡するPythonプロジェクトです。coding agentの開発・CIでは制約違反と検査漏れを、LLMガードレール評価では検出率・見逃し率・誤検知率の変化を扱います。

**MIT License / 開発中。** ソース公開とMVPの製品受入は別です。全32要求の受入は未完了で、`release_gate=no_go`を維持します。[公開時の状態](docs/oss-publication.md)と[残件の監査](docs/mvp-completion-audit.md)を参照してください。

直近の後続契約・読み取り検査の改修はfocused 83試験を確認していますが、統合試験と全体回帰には完了記録がありません。同梱のauthority runtime lockは改修前の実測を保持し、現行ソースとの一致は未検証です。過去のDocker証跡を現行版の動作保証には用いません。

## 実装と検証の記録

条件比較と後続契約の禁止変更を[部品として実装](docs/semantic-conditions-detail-spec.md)した。新規30件・既存21件の[試験が成功](docs/evidence/mvp-condition-core-20260912/README.md)。契約3の採択・実行への接続は継続中。

固定UC-CIの開始・再開・取消し・状態照会CLIを接続した。実Docker194項目・91件実行が成功し、停止・回収を確認した。追加レビューで準備直後の再開漏れを補正し、実SQLiteと合成runnerの12試験が成功した。補正前46件と合わせて50種類をfocused検証した。Dockerはこの一行補正前の結果で、現行592件の全体試験は再実行していない。 [工程証跡](docs/evidence/mvp-supervisor-20260912/README.md)。全MVP受入は未完了。

固定UC-CIの通常runからbaseline generation 1→2への更新を接続した。全体回帰560件の後、保存記録の照合を補正して影響する44件を再検証し、対象全件の成功を確認した。固有の検証対象は561件で、従来551件を保持する。補正後の実Docker248項目も成功し、従来233項目の判定を保持した。固定fixtureの実行92件は全て停止・回収済み。世代ごとの撤回と依存失効、旧基準CIの継続、違反runの昇格拒否、保存失敗時のrollbackを確認した。 [仕様](docs/baseline-refresh-detail-spec.md) / [親レビュー](docs/reviews/mvp-baseline-refresh-20260912.md) / [工程証跡](docs/evidence/mvp-baseline-refresh-20260912/README.md)。

全MVPは未受入。固定UC-CIの通常run・現在CI・要約・取消し復帰は接続済みで、過去の検証は[証跡一覧](docs/evidence/README.md)に保持する。条件変更と後続契約、UC-LLMの認証資源、製品監督・保持処理、Finding修復確認、全32要求の受入は[完了監査](docs/mvp-completion-audit.md)で追跡する。

Guardrail Assurance Harness（GAH）は、ガードレールとその検査系が今も有効かを継続検証するための制御基盤を目指すプロジェクトです。識別子は `guardrail-assurance-harness`。

前工程では実入力400件の評価packと、段階実行・集計・Promptfooの正規化を追加しました。[評価詳細仕様](docs/evaluation-detail-spec.md)と[監督レビュー](docs/reviews/mvp-evaluation-20260911.md)に、集合の盲点の補正、DGX Qwenを対象にした実測、測定側の独立校正を記録します。現行部品の結果は常に`ci_eligible=false`です。

0.2.0で[期限後回収と取消し・終了の保存](docs/lifecycle-detail-spec.md)を追加しました。回収用の所有権で既存費用を精算し、実行の再開と過去の終了記録の書換えを防ぎます。既存v1 DBは`db-upgrade`による明示的な移行を使用します。

現在は[全MVP完成のTask](docs/tasks/TASK.mvp-completion-09-11-2026.md)を進行中です。[Registry・CaseSet・Evidence](docs/runtime-contract-spec.md)の構造検査・依存解決・校正照合・不変保存・撤回を追加しました。[32要求の監査](docs/mvp-completion-audit.md)に実装済み部分と残る接続を記録し、部品試験を全MVP完成へ読み替えません。

自作の10制約・5種類の検査系劣化を、digest固定したイメージで実行します。ネットワーク遮断、非root、資源上限、拒否出力の破棄、取消し、監督中断からの回収を[実行証跡](docs/evidence/mvp-execution-20260911/verification.json)へ記録しました。これは固定合成fixtureの検証で、実モデル性能の評価ではありません。

管理境界は4主体のUID/GIDを固定し、本文の役割名では認証しません。候補の自己承認拒否、失効後の現在状態、再起動後の不変receiptなど実Dockerの22項目と、Qwen提案の採択7項目を[管理境界の証跡](docs/evidence/mvp-authority-20260911/verification.json)へ記録します。[監督・レビュー記録](docs/reviews/mvp-authority-20260911.md)に修正と未接続範囲を残しています。

要求明確化案v0.4で32の必須要求と受入条件を整理しました。初期対象は次の両方で確認済みです。WARNINGをCIで許し、生成AIが基準を管理する方針と初期閾値・予算を定めました。外部参照元はクローズド資産のため、一般的な運用の考え方だけを参考にします。コード・Schema・テスト・文書等は移植せず、[参照境界](docs/reference-boundary.md)に従いGAHの要求から独立に設計・実装します。

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
7. [設計初版](docs/design.md) / [評価契約](docs/contracts/README.md): CI・AI管理・結果照合と境界例
8. [コア詳細仕様](docs/detail-spec.md) / [adapter接続仕様](docs/adapter-spec.md): 実装済み部品と外部接続の境界
9. [敵対的検証の記録](docs/reviews/requirements-adversarial-20260910.md) / [拡張案](docs/extension-roadmap.md): 反例・修正理由・後期の着手条件

<!-- LLM-BOOTSTRAP v1 -->
エージェントは[Birdseye Index](docs/birdseye/index.json)から対象の周辺ノードとcapsを読む。JSONが使えない場合は[Birdseyeの読み方](docs/BIRDSEYE.md)を参照する。
<!-- /LLM-BOOTSTRAP -->

## 今使えるコマンド

Python 3.11以上、診断・文書検証は第三者パッケージ不要。取得後、repo rootで実行します。

```sh
git clone https://github.com/RNA4219/guardrail-assurance-harness.git
cd guardrail-assurance-harness
```

```sh
python -m tools.workflow generate
python -m tools.workflow check
python -m unittest discover -s tests -v
python -m tools.gah_cli assess --input examples/component-assessment.v1.json --db .ga/diagnostic.sqlite
python -m tools.gah_cli show --id sample-assessment-1 --db .ga/diagnostic.sqlite
```

診断CLIの正常な保存・表示は終了1、入力・保存・出力障害は終了2です。[RUNBOOK](RUNBOOK.md)に手順、[EVALUATION](EVALUATION.md)に文書・部品・全MVPの検収範囲を記載しています。

## 構成と来歴

`docs/requirements.md` が現行要求の正本、`docs/spec.md` が基本仕様と詳細仕様の入口です。`src/gah/`に部品コア、`schemas/`に部品入力Schema、`examples/`に合成集計値、`tests/`に部品と文書のテストを置いています。`fixtures/runtime/`には自作の固定fixtureがあります。[LLM評価pack](datasets/synthetic-policy-v1/README.md)は受入400件、別用途の校正18件・開発12件を実入力とoracle付きで生成しました。[評価詳細仕様](docs/evaluation-detail-spec.md)に集計と固定LLMの段階実行、[adapter仕様](docs/adapter-spec.md)にPromptfoo通常出力の対応を記録します。データ生成と全MVP受入は区別します。

コピー元の文書・レビュー・検収記録は[資料来歴](docs/research/README.md)から参照できます。GAHの実績や承認には含めません。[出典台帳](docs/research/citation-register.md)の外部引用は未復元です。

## ライセンスと公開

本体コード・文書・自作fixture/合成データは[MIT License](LICENSE)です。Workflow-Cookbook由来のツールと雛形には既存の[MIT表示](third_party/workflow-cookbook.LICENSE)を保持します。外部サービス・モデル・第三者依存物には各提供元の条件が適用されます。

[GitHubリポジトリ](https://github.com/RNA4219/guardrail-assurance-harness)でソースを公開します。配布パッケージ・コンテナ・正式リリースは未発行です。GitHub Actionsの結果は各commitのChecksで確認してください。branch protectionの実設定は未検証です。[Security方針](SECURITY.md)も参照してください。
