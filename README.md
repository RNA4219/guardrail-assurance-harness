---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-19
next_review_due: 2026-10-19
template_version: 1.0.0
---

# Guardrail Assurance Harness

ガードレールと、その検査系の劣化を追跡するPythonプロジェクトです。coding agentの開発・CIでは制約違反と検査漏れを、LLMガードレール評価では検出率・見逃し率・誤検知率の変化を扱います。

**MIT License / 固定版のMVP技術検収完了。** 受入済み固定版では全32条件を満たし、`release_gate=go`です。開発中の拡張版は全回帰・製品受入を継続しています。[最終証跡](docs/evidence/mvp-acceptance-20260913/mvp-final-20260915-summary.json)と[要件別の監査](docs/mvp-completion-audit.md)に検証範囲を記録しています。

## 実装と検証の範囲

| 経路 | 確認済みの範囲 |
|---|---|
| UC-CI | 制約と検査系の劣化、契約・基準の世代更新、通常CI、対象限定、取消し・復帰・回収 |
| UC-LLM | 自作合成400ケース・対象2版、独立165vector校正、段階評価、検出率・見逃し率・誤検知率の比較 |
| 管理と証拠 | OS認証による役割分離、独立採択、Finding/Plan、修復確認・再発、保持・撤回・削除後の現在CI判定 |
| 全MVP | GAH-AC01〜32の技術検収完了。GitHubでの定期実行と新しい12ジョブCIの実行は未確認 |

固定source80の全898試験と、追加したCI分割の9試験が成功しました。source81との差分は実Docker検証ツール一つで、製品コアは同一です。実Dockerでは初回400ケース・600段階、旧400/新800の比較と独立採択、通常CLI800試行、freshなCI、JSON/Markdown、再起動後の出力・receipt不変、全精算・回収を確認しました。MVP検収時の907件は重い統合6ジョブと残り6分割へ割り当てました。拡張後もdiscoveryした全テストから12laneの計画を生成し、同じsource・計画・全件の成功を必須チェックで集約します。[CI構成](docs/ci-config.md)を参照してください。

製品入口は `python -m tools.gah_run`、現在のCI判定は `python -m tools.gah_ci`、表示は `python -m tools.gah_report` です。基準管理AIが契約とbaselineを管理し、最終判定・認証・予算の照合は決定的なルールで行います。変更を作るAI、基準管理AI、評価器の役割を分離します。

合成ガードレールの受入は学習済みモデル一般の性能保証を含みません。実行環境には安定した時計が必要です。今回のWSL検証では時刻同期を一時調整し、検証後に元の設定へ復元しました。過去の失敗や使用量不明の取消しrunは証跡へ保持しています。ソース公開・技術検収・GitHub上のCI結果は別に扱います。[公開時の状態](docs/oss-publication.md)を参照してください。

[拡張14要件](docs/productization-requirements.md)を[4仕様](docs/productization-spec.md)へ具体化し、Lunaの分担実装を親がレビュー、DGX Qwenへ局所レビューを依頼しました。計測/CI分割、setup・診断・履歴・保持・bundle・移行、実案件評価の計画/metadata管理を実装しています。[実装と残件の対応表](docs/productization-status.md)に検証範囲を記録しました。固定source-v5の関連93試験と実Docker90件に加え、[9月19日の継続検証](docs/evidence/productization-continuation-20260919/README.md)で保存上限・worker計測・Docker容量観測を確認しています。拡張14条件の製品受入は未完了です。簡易setupは容量上限未実証のため開始前に停止します。[導入手順と前提](docs/productization-quickstart.md)を確認してください。

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

- [拡張実装・受入対応](docs/productization-status.md)


2026-09-16の[継続実装](docs/evidence/productization-continuation-20260916/README.md)で、offline結果import、容量保存部品、資源観測、固定imageの新規取得・構築を追加しました。関連116件は114成功・2スキップ、実Dockerのimage構築・資源観測も確認済みです。全writer/全子scope/実target接続と拡張受入は継続中です。
