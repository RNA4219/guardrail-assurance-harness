---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
template_version: 1.0.0
---

# Evaluation

baseline generation 1→2の工程は[仕様](docs/baseline-refresh-detail-spec.md)と[証跡](docs/evidence/mvp-baseline-refresh-20260912/README.md)へ結ぶ。全MVP受入とは区別する。

所有権期限切れ後の取消し取得は、`python -m tools.verify_baseline_runtime --recovery --output <repo内の新規出力先>` で既存経路と共に実Docker検証する。[仕様](docs/run-recovery-detail-spec.md)と[証跡](docs/evidence/mvp-recovery-20260912/README.md)を参照する。

通常runの日本語要約・JSON CLIを追加した。直前の全体回帰534件に加えて専用7テストが成功し、実DBの製品CLI15項目で失効・取消しの表示と回収を確認した。 [追加検証](docs/evidence/mvp-report-20260912/README.md)は、全体回帰534件と後から追加した専用7件を分けて記録する。

## Acceptance Criteria

現在の[全MVP Task](docs/tasks/TASK.mvp-completion-09-11-2026.md)はUC-CI/UC-LLMとGAH-R01〜R32の実装・接続・受入を対象とする。[最新の証跡](docs/evidence/mvp-cancellation-20260912/README.md)では固定UC-CIの通常run・Finding/Plan・現在CI・停止済み取消しの限定経路を確認した。各部品と接続の技術検収、設計凍結、全MVPの安全性・性能の受入を区別する。

- 現行入口が実装範囲と未接続を明示し、設計例を正式Schema・runtime設定・製品試験の成功と書かない。
- 元原稿・整理前文書を保存し、要求の改訂範囲と来歴を追跡できる。
- 要求IDが一意で、受入条件と原稿機能の処遇に対応する。仮定と実施済み事項を区別する。
- 旧案件の完了Task・承認・証跡を現行の実績へ流用しない。
- 文書参照、metadata、TaskとAcceptance、索引、Birdseyeのhashと世代が整合する。
- 5標準文書の必須見出しとtemplate versionを保ち、固定したCookbookの検証を通す。
- DGX Qwen/Lunaの出力と親の監督・実行検査を区別し、指摘の採否を記録する。出典未復元、製品の未実装部分、remote未検証を記録する。
- 診断CLIを別プロセスで実行し、実SQLiteの保存・復元・競合・故障を検査する。HEALTHY/WARNINGでもci_eligible=false、正常診断は終了1とする。

## KPIs

| 指標 | 目的 | 文書検収の目標 |
|---|---|---|
| 原稿と保存構成のhash | 来歴の保全 | 現在の保存版と一致。利用者指定の匿名化後は、その版と非匿名化原本を区別 |
| 要求の追跡 | 改訂による無言の欠落防止 | 要求ID・受入・原稿の処遇が対応 |
| 参照切れ・古いcaps | 読む導線の整合 | 0件 |
| 現行Taskの対応 | 実施作業の追跡 | 対応する技術検収あり |
| adoption tier | 既存の運用構造 | 3 / Full |

製品の計測・判定要求は[要求明確化案](docs/requirements.md)、期待結果は[受入条件案](docs/acceptance-criteria.md)を参照する。元の性能目標と仮値は保存稿の参考情報であり、本番閾値の採択・製品測定は未実施。

## Test Outline

既存unittestで文書生成の冪等性、source変更、参照欠落、壊れた世代、検収の欠落とID重複を確認する。コアのテストは、厳格な入力、指標と優先順位、SQLiteの不変保存・原子性・予算・所有世代、別プロセスCLIと出力障害を実行する。実行環境・件数・対象hash・結果は[部品検収](docs/acceptance/AC-20260911-03.md)へ結ぶ。

台帳v2では明示的移行の保持・rollback、取消し/確定の競合、停止と費用の分離、回収lease、遅延精算後のterminal不変と監査eventを実行する。最新結果は[終了・回収の部品検収](docs/acceptance/AC-20260911-04.md)へ結ぶ。

過去の設計例100件は当時のNOT_RUN記録を保つ。固定fixtureの隔離・停止・回収、OS peer認証、契約採択、有限集合のモデル診断は各証跡の条件と版で検証する。これらを32要求全てのE2E受入へ換算しない。常設管理AIとモデル資源の全体接続、通常取消し、条件変更を伴う更新、Findingの修復確認、全体CI運用は[完了監査](docs/mvp-completion-audit.md)に従って継続する。

## Verification Checklist

- [RUNBOOK](RUNBOOK.md)のgenerate/check/unittestが成功する。
- [Task](docs/tasks/README.md)、[Acceptance](docs/acceptance/INDEX.md)、[CHANGELOG](CHANGELOG.md)、Evidenceが対応する。
- 保存稿を現在の要求や承認と誤認させず、未実施範囲を明記する。
