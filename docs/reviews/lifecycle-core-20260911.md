---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 終了・回収コアの監督レビュー

## 対象と分担

[Task 20260911-04](../tasks/TASK.lifecycle-core-09-11-2026.md)として、[詳細仕様](../lifecycle-detail-spec.md)と0.2.0の台帳Schema v2、明示的移行、回収lease、取消し・停止確認、終了判定、診断CLIを確認した。全MVPの検収ではない。

親が契約・受入境界、CLI、独立の統合試験と最終判断を担当した。Lunaの台帳担当はledgerと部品試験、終了判定担当は副作用のない判定関数と組合せ試験を実装した。別のLunaが読取りレビューを行い、親が指摘を仕様・修正・実行結果へ照合した。共有ファイルの担当範囲を分け、修正後のレビューを依頼した。

## DGX Qwenの照会と処遇

仕様照会はqwen3.8-flash-nextで完了した。入力1,858、出力570、合計2,428 token、24.781秒。以下の提案はいずれも採用しなかった。

| 提案 | 親の判断 |
|---|---|
| 回収取得が期限未満に限定されているとの指摘 | executionの条件との混同。提出時の仕様にも期限後回収を定義済み |
| 停止確認のtrusted根拠をタイマーやIDで補う | それだけでは停止・主体の認証根拠にならない。内部APIの信頼境界と実host未接続を明記する方針を維持 |
| 未精算なら取消しをWAITINGへ戻す | LC06の停止済み取消しと予約保持を両立できなくなる。実行終了とfinancial closureを分ける |
| terminal-showが保存済みexit_codeを返す | 過去記録の表示と新しい実行判定を混同する。正常表示の終了1を維持 |

コード全体の照会は55.032秒で時間切れとなった。回収・lease検査・finalizeの抜粋に絞った一度の再照会も55.062秒で時間切れだった。両方とも応答・usageは取得できておらず、コードレビュー完了とは扱わない。追加再試行は行っていない。

全照会の提出時点のsource bytes/hashを`.ga/lifecycle-core-20260911/qwen-*`に保存した。[モデル証跡](../evidence/lifecycle-core-20260911/model-review.json)で成功・未取得とhash照合を区別する。提出稿のhashは最終コードのhashではない。モデルの指摘数や利用成否を製品性能の証拠に転用しない。

## 指摘・補強と反映

| 確認者 | 確認した問題・境界 | 反映と検証 |
|---|---|---|
| 親・台帳Luna | 移行で必要列・last_clock・部分的な新Schemaを曖昧に扱えない | lock取得後に構造・保存reportを検査し、不正v1を変更せず拒否。DDL途中故障もrollback |
| 親 | 変更中にKeyboardInterruptでrollbackする既存契約が退行 | 既存試験で検出し、BaseException時のrollback/closeを復旧 |
| 親・台帳Luna | terminal後のexecution取得、世代上限、再配送時の不正入力 | 実行再開を禁止。epoch上限とfinalize入力を再配送でも厳格検査 |
| 親 | 停止確認後、期限前に監督が中断すると回収できない | 停止確認済みをrecovery取得条件へ追加。再起動後の精算・終了確定を実行 |
| 独立レビューLuna | 旧DBのexecution leaseがdeadlineを超えていると期限後変更が通り得る | 期限後は有効時間だけでなくrecovery kindを必須化。明示的回収前の精算を拒否 |
| 親・台帳Luna | 不明な操作状態や不完全な精算をCLOSEDへ集計し得る | 操作レコード検査と共通closure計算。未知状態・NULL精算を拒否 |
| 親 | callerがHEALTHYを渡すと保存済みHOLD原因を失う | DBのHOLDを優先しledger_hold_reasonをterminalへ保存 |
| 独立レビューLuna | terminal後の取消し・停止通知に監査記録が不足 | 同run/epoch/kindにつき一度のeventを追加。receiptと元通知時刻を維持 |
| 親 | v1で妥当な、確定額より小さい矛盾費用候補をv2検査が拒否 | 元候補額を維持して移行。会計はmax(確定額, exposure)、OPENを保持 |
| 親・独立レビューLuna | terminalのhash一致だけでは別runのレコード入替を見落とす | 行と本文のrun_idを照合。入替側を拒否し元のrunは読める試験を追加 |
| 親 | CLIの閉じたstderrで例外が再発する | 出力障害を終了2に収束。未存在DBへの新CLIがDBを作らないことも確認 |

修正後、独立レビューLunaは期限後lease・遅延通知・互換性・HOLD原因の該当経路に確定した中程度以上の追加不備を認めなかった。最後のrun_id照合も別途確認した。これは指定範囲のコードレビューであり、網羅的なセキュリティ監査ではない。

最後の限定レビュー対象SHA-256はledger.pyが`4957ff05aefb73f0f0bdd88deab462c1c66e6172b22bb1c9cc8218cddca15408`、test_lifecycle_integration.pyが`e4fa74ca40cb4d68532614d2fae8d51206e11f488a2bcdf2f37cb9623dfbc2bf`。他の対象を含む最終hashは[実行証跡](../evidence/lifecycle-core-20260911/runtime-check.json)に保存する。

## 実行結果

Python 3.12.14 / SQLite 3.53.1 / Windows 11で80テストが成功した。前回55件を全て保持し、新規25件を追加した。終了判定の4テストには6個のboolと5状態の320組合せの照合を含む。これを320個の独立テスト件数とは数えない。

実SQLiteでv1レコード・保存bytesの保持、移行の途中故障と再試行、保存と監査eventの原子性、取消し/確定の別プロセス競合、期限一致、停止未確認、旧owner、再起動、遅延精算を確認した。独立smokeでは未精算のまま停止済み取消しを確定し、元deadline後に別ownerが精算した。financial closureだけがCLOSEDとなり、元のCANCELLED receiptは不変だった。別プロセスterminal-showの出力は保存記録と一致し、表示プロセスは終了1だった。

要求、受入条件、初期方針・数値、100設計例の7ファイルは前回証跡とhashが一致した。対象コード・仕様のhashが試験中に変わっていないことも確認した。文書と非掲載検査の最終結果は[技術検収](../acceptance/AC-20260911-04.md)へ結ぶ。

## 残る境界

外部課金provider、OS/IAM認証、実際の子処理の停止と隔離、全資源監督、採択・dispatch、exposure解除、外部adapterと実評価データは未接続。内部APIの停止・完了引数はtrustedな監督が渡す前提であり、その実在を本部品が証明したものではない。SQLiteと同じhostの管理者による改竄に耐える証跡でもない。

全出力のpurpose=component_validation、ci_eligible=falseを維持する。financial CLOSEDやCOMPLETEDを全MVP受入へ読み替えない。実モデル性能と全32要求のE2E受入はNOT_RUN、全MVP release_gateはno_goである。
