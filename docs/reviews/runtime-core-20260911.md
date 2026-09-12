---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 詳細仕様と製品コアの監督レビュー

[Task 20260911-03](../tasks/TASK.runtime-core-09-11-2026.md)で、[コア詳細仕様v1](../detail-spec.md)、[adapter接続仕様](../adapter-spec.md)、判定・SQLite保存・費用台帳・診断CLIを作成した。以下はこの部品範囲のレビューであり、全MVPの製品受入ではない。

## 委任と監督

利用者がDGX QwenとLunaの利用を明示した。親が最初にAPI、ファイル所有権、合成入力だけを使う範囲、正常診断でもCI合格を発行しない条件を固定した。Lunaには判定/テストと台帳/テストを分担させ、別のLunaへCLIの読み取りレビューを依頼した。親はwire・CLI・統合試験・仕様を担当し、実装を読んで指摘を返した。共有ファイルの他者変更を戻さないよう各担当へ指示した。

| 担当 | 成果物・確認 | 親の処遇 |
|---|---|---|
| Luna 判定 | decision.py、test_decision.py | 入力digest、全初期閾値/差分の境界、理由の優先順位を追補して統合 |
| Luna 台帳 | ledger.py、test_ledger.py | DDL・丸め・引継ぎ・矛盾精算を修正依頼し、親の中断/並行試験で確認 |
| Luna CLIレビュー | wire・CLI・保存/出力失敗の読み取りレビュー | 指数表記、破損DBの型、出力失敗時削除、起動経路の指摘を修正。最終限定レビューで重大・中程度の確定した残存不備なし |
| Luna 接続仕様 | adapter-spec.md | 公開版の根拠とschemaの根拠を分離、HOLD/UNKNOWNの矛盾とtimeout規則を修正 |
| DGX Qwen | 仕様とコードの局所レビュー2回 | 下表の採否を親が判断。製品試験・独立承認には数えない |

## 指摘と修正

| ID | 指摘・実害 | 修正と確認 |
|---|---|---|
| RC01 | SQLite executescriptによる暗黙commitで初期化途中の表が残り得る | DDLを個別executeで一transactionに統合。途中失敗後の全DDL rollbackを実DBで確認 |
| RC02 | Decimalの既定精度に依存した微小USDの切上げ、非ASCII数字の受理 | ASCII十進文字列を整数の桁演算へ変換。通常精度を超える桁・百万分の一未満・全角数字を検査 |
| RC03 | 所有者引継ぎ後に旧予約を精算できない、旧所有者を許す危険 | 現在のowner/epochで旧予約を精算し、元bindingを保持。旧ownerの予約/精算拒否を再起動を含め確認 |
| RC04 | 矛盾した確定費用を捨てると他runが予算を過小に見る | 元確定額と最大候補exposureを保持。未解消は24時間外でも計上し、他runの予約を拒否 |
| RC05 | 同じ率へ正規化される別入力、時刻変更が同じ診断と誤認される | 検査後の元入力digestを出力へ追加。同ID・異内容の再保存を拒否 |
| RC06 | JSONの小数/指数がfloat変換でInfinityになる | wireで小数/指数表記を拒否。NaN/Infinity、重複key、深度・容量・文字コードも負例検査 |
| RC07 | 破損したSQLite値が型例外を漏らす、未知障害が未整理 | 固定LedgerErrorとCLI終了2へ統合。例外本文・入力・任意pathを返さない |
| RC08 | 出力失敗時の自動削除が別プロセスの置換済みファイルを消し得る | exclusive create後の失敗pathを保全。fsync・stdout/stderr失敗でも成功を返さない |
| RC09 | インストール済みsrcとの名前衝突、WindowsでSQLite handleが残る | tools.gah_cliからrepo/srcを指定。テスト接続は明示closeし、別プロセスの起動と再読込を確認 |
| RC10 | KeyboardInterruptがtransactionを残すと次操作が正常に進まない | 初期化失敗時close、transaction内のBaseExceptionでrollbackして再送出。中断後の次操作を確認 |
| RC11 | adapter仕様で現行docsを固定releaseのschemaとして扱い、矛盾をUNKNOWNへ下げる | 版根拠とmapping候補を区別し、内容矛盾はHOLD、単なる不足はUNKNOWN/Critical HOLDへ統一。未接続のmappingは採択済みにしない |

## DGX Qwenの利用と処遇

モデルはqwen3.8-flash-next。仕様照会は入力1,592/出力530 token、コード照会は入力7,374/出力536 tokenで完了した。入力はGAHの局所仕様・ledger・CLIだけ。応答とreceiptは`.ga/runtime-core-20260911/`へ保持し、採用したmetadataは[照会証跡](../evidence/runtime-core-20260911/model-review.json)へ記録する。

| 照会 | 応答の主張 | 処遇 |
|---|---|---|
| 仕様1/2/4 | fnr baseline、deadline優先、HOLD優先が不明 | 既に明示していたため変更理由にしない。対応する境界試験で確認 |
| 仕様3/5 | reserve時settled_at、既定clockの明記 | 未精算nullとint(time.time())を明確化し台帳担当へ反映 |
| コード1 | builtinのValueErrorが未定義 | 誤り。提示された同名置換も修正にならず不採用 |
| コード2 | exposure更新とreserveが競合する可能性 | transactionとMAXの確認観点として扱い、実プロセス競合・他runへの留保試験で確認。回答だけで欠陥と断定しない |
| コード3 | 初期leaseが0なのでNone判定が機能しない | 実コードの初期値はNoneであり不採用 |
| コード4 | deferred_errorをraiseしてからassertへ落ちる | raise後には進まないため不採用。Noneを正常返却する提案は適用しない |
| コード5 | KeyboardInterruptが捕捉されない | CLIには専用exceptが既にあるため不採用。親が別途発見した台帳transaction中断はRC10で修正 |

照会receiptのsource hashは照会時点の版である。照会後に修正したため最終コードのhashとは異なる。照会時のソースbytesの独立snapshotは保存しておらず、receiptを最終版全体のレビュー証明にしない。最終検証対象のhashはruntime-checkへ別に記録する。

## 実行検証と限界

[実行証跡](../evidence/runtime-core-20260911/runtime-check.json)、[unittestログ](../evidence/runtime-core-20260911/unittest.log)、[文書検査](../evidence/runtime-core-20260911/workflow-check.json)、[非掲載検査](../evidence/runtime-core-20260911/privacy-check.json)を[技術検収](../acceptance/AC-20260911-03.md)へ結ぶ。

部品試験では全指標の閾値と差分、入力負例、別プロセスCLI、不変保存、破損、並行予約、引継ぎ、期限、留保、中断を確認する。仮の終了コードを置いた設計例の再集計ではなく、実装を呼ぶ試験である。一方、集計値の観測元は未認証で、全資源監督・deadline後精算回収・失効/採択/UseDecision・保存前データ分類・管理AI認証・OS隔離・受入集合・外部adapterは未接続である。

過去の100設計例、実装前preflight、過去の文書検収は当時の記録を保持する。今回の部品試験を全32要求のE2E・実モデル性能・通常CIの成功へ換算しない。全MVPのrelease判定はno_go、部品診断は常にci_eligible=falseである。
