---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 回帰検証

`python -m unittest discover -s tests -v`で部品と文書の回帰試験を実行する。追加パッケージやモデル接続は不要。

| 対象 | 実行する境界 |
|---|---|
| test_registry / test_corpus / test_artifacts | 登録の最大依存鎖、Critical義務、sample/lineage重複、全段階校正、保存前許可、保持・撤回・削除・再配送・原子性 |
| test_decision / test_wire | 全初期閾値と差分、不足・優先順位、厳格な型・文字コード・数値・入力digest |
| test_ledger | 実SQLiteの保存と破損検出、DDL原子性、所有世代、精算矛盾、微小金額、時計巻戻り |
| test_cli | 別プロセス起動・再読込、不正入力、ファイル保全、保存・出力失敗 |
| test_core_integration | 複数プロセスによる全体予算競合、再開・引継ぎ、期限、矛盾留保、中断rollback |
| test_termination / test_ledger_lifecycle / test_lifecycle_integration | 終了優先順の64組合せ×5状態、台帳v2移行/rollback、期限後回収、取消し競合、terminal保存故障と不変性 |
| test_workflow | 文書生成の冪等性、古いsource、壊れた世代、参照欠落、検収欠落・ID重複 |

テストは一時ディレクトリと合成データを使用する。[今回の検収](../docs/acceptance/AC-20260911-03.md)に件数・環境・対象版を記録する。全MVPのE2E、実モデル性能、実環境の管理AI認証・隔離は未検証。
