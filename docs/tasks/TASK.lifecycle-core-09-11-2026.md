---
task_id: 20260911-04
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# Task: 期限後回収と取消し・終了の永続化

## Objective

DGX QwenとLunaを監督し、元deadlineを延長せず費用を回収し、取消しと不変の終了記録を保存する部品を実装する。

## Scope

In: 台帳Schema v2と明示的v1移行、回収専用lease、取消し/停止確認、financial closure、終了優先順、診断読取りCLI、実DB/並行/故障試験とレビュー。
Out: 課金providerとOS認証/隔離の接続、実際の子処理の停止、未解消exposureの解除、全MVP受入、公開。内部APIはtrustedな監督を前提とする部品で、認証済みの外部イベントを実装したとは扱わない。

## Requirements

Behavior: GAH-R04/R18/R23/R26とLC03/LC04/LC06。取消しだけで停止済みにせず、費用回収で実行・terminalを復活させない。
I/O Contract: [詳細仕様](../lifecycle-detail-spec.md)。epoch、元deadline、厳格な型、financial closureと実行完了の区別。
Constraints: [参照境界](../reference-boundary.md)。既存の要求・初期値・設計例と以前の検収証跡は保持する。
Acceptance Criteria: v1のレコード保持、移行のrollback、並行取消し/確定、期限境界、旧owner拒否、停止未確認、未精算、再起動と遅延精算を実行する。

## Affected Paths

src/gah/ledger.py、src/gah/termination.py、CLI、tests、詳細仕様、入口、Task/Acceptance/Review/Evidence。

## Local Commands

```sh
python -m unittest discover -s tests -v
python -m tools.workflow generate
python -m tools.workflow check
```

## Deliverables

詳細仕様、部品実装、v1移行、回帰・統合試験、監督レビュー、技術検収。

## Plan

1. 親が契約と受入境界を固定し、Lunaへ台帳と終了判定を分担する。
2. DGX Qwenへ仕様レビューを依頼し、親が処遇を決める。
3. 親が読取りCLI・移行入口と独立の競合/回復試験を接続する。
4. Lunaの独立レビューと修正を反映し、証跡・入口を確定する。

## Tests

80テストが成功（以前の55を保持、新規25）。実SQLiteの移行保持・rollback、原子的保存、別プロセス競合、期限後回収、旧owner拒否、停止未確認、未精算取消し、遅延通知・精算後のterminal不変と改変検出を確認した。文書ワークフロー12項目成功、非掲載の本文・配置残存0件。全MVP受入と実モデル性能はNOT_RUN。

## Commands

`python -X utf8 .ga/lifecycle-core-20260911/verify_lifecycle.py`が成功し、unittestと独立smoke、既存55試験・要求等のhash保持、モデル照会記録を確認した。`python -m tools.workflow generate`と`python -m tools.workflow check --report docs/evidence/lifecycle-core-20260911/workflow-check.json`が成功。別途、作業稿と生成索引を含む非掲載検査を実行した。[技術検収](../acceptance/AC-20260911-04.md)と[監督レビュー](../reviews/lifecycle-core-20260911.md)へ結ぶ。

## Notes

利用者の継続指示と、DGX Qwen/Lunaによる委任・監督・レビューの既存指示を適用する。

DGX Qwenの仕様照会は完了した。コード照会は全体・一度の限定再試行とも時間切れで、コードレビュー完了に数えない。Lunaの実装・独立レビューと親の修正・実行確認を採用した。外部認証・停止・課金・評価データ等の未接続は次工程であり、本Taskの完了は全MVPの完了を意味しない。
