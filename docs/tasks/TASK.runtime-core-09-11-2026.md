---
task_id: 20260911-03
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# Task: 詳細仕様と製品コアの初期実装

## Objective

DGX QwenとLunaを監督し、詳細仕様、判定・保存・予算の実装、コードレビュー、実動作の証跡を作る。

## Scope

In: version 1の部品API/JSON入出力、Python標準ライブラリによる判定、SQLiteの原子的保存・世代・費用予約、診断CLI、部品・プロセス・並行試験、レビューと修正。
Out: 本番CIへの接続、外部コマンド/モデルを実行する製品runner、実モデルの性能受入、署名配布、公開。管理AI・OS隔離・adapter等の詳細仕様は作るが、未接続を実装済みにしない。

## Requirements

Behavior: 32要求・初期方針を保ち、部品検証とMVP全体の受入を区別する。
I/O Contract: 厳格なJSON、決定的な指標と理由、内容識別、変更不可の診断記録、世代を照合した費用操作。
Constraints: [参照境界](../reference-boundary.md)。外部資産は取得・掲載しない。モデル出力は親がレビューし、実行結果で確認する。
Acceptance Criteria: 仕様と実装・テストの対応、負例・境界・再起動・並行予約・保存失敗を実際に確認。未接続の信頼境界を成功で埋めず、診断CLIを通常CIの合格に使えない。

## Affected Paths

src/gah、tests、schemas、examples、docs/detail-spec.md、docs/ADR、入口、Task/Acceptance/Review/Evidence。

## Local Commands

```sh
python -m unittest discover -s tests -v
python -m tools.workflow generate
python -m tools.workflow check
```

## Deliverables

[コア詳細仕様](../detail-spec.md)、[adapter接続仕様](../adapter-spec.md)、部品実装と診断CLI、実動作試験、[DGX Qwen/Lunaのレビュー処遇](../reviews/runtime-core-20260911.md)、[技術検収](../acceptance/AC-20260911-03.md)。全MVP完成とは区別する。

## Plan

1. 親がインターフェースと受入範囲を固定し、Lunaへファイル所有権を割り当てる。
2. DGX Qwenへ仕様の反例確認、Lunaへ判定と台帳の独立実装を委任する。
3. 親が厳格な入力・CLI・仕様を実装し、各成果物をレビューして統合する。
4. 実プロセス・SQLite・負例を検証し、指摘修正後に再検証する。

## Tests

Python 3.12.14 / SQLite 3.53.1 / Windows 11でunittest 55件が成功（新規部品45、既存文書10）。別プロセスのassess→showは同一出力、正常診断の終了1・ci_eligible=falseを確認した。実SQLiteのDDL rollback、微小金額、予算の並行競合、所有者引継ぎ、矛盾留保、期限、中断、保存/出力障害を検査した。

32要求・32受入条件・初期方針・初期値JSON・100設計例のhashを前回証跡と照合し、変更なし。文書ワークフロー12項目が成功、非掲載情報の残存は本文・配置とも0件。過去の設計例は当時のNOT_RUNを保ち、全MVPのrelease判定はno_go。

## Commands

```sh
python -X utf8 .ga/runtime-core-20260911/verify_runtime.py
python -X utf8 -m tools.workflow generate
python -X utf8 -m tools.workflow check --report docs/evidence/runtime-core-20260911/workflow-check.json
```

verify_runtimeは実際の`python -E -X utf8 -m unittest discover -s tests -v`と別プロセスCLIを起動する。[実行記録](../evidence/runtime-core-20260911/runtime-check.json)、[テストlog](../evidence/runtime-core-20260911/unittest.log)、[文書検査](../evidence/runtime-core-20260911/workflow-check.json)、[非掲載検査](../evidence/runtime-core-20260911/privacy-check.json)へ環境・終了値・対象hashを保存した。

## Notes

利用者が詳細仕様以降の作成とDGX Qwen/Lunaによる委任・監督・コードレビューを明示した。通常の実装判断はこの範囲で進める。

管理AIの実identity、OS隔離、全資源の実行監督、外部adapter、deadline後の精算回収、実評価データは未接続。Promptfooは公開版を確認したが版固有出力型が未確認で、mapping採択を完了していない。部品診断を通常CIの合格や実モデル性能の証明へ流用しない。Git記録・公開・本番CI設定は未実施。
