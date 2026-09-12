---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 固定fixture実行部の監督・レビュー

[全MVP Task](../tasks/TASK.mvp-completion-09-11-2026.md)の実行部分を進めた記録である。[詳細仕様](../execution-detail-spec.md)と実装を照合し、全MVP受入の不足は[監査](../mvp-completion-audit.md)へ残す。

## 分担と指摘の処遇

Lunaが固定worker、generic正規化、実行journalを分担し、別担当によるworker・runnerの読取りレビューと境界テストを実施した。親はDocker runner、ビルド固定、実コンテナの検証器を実装し、API接続と停止・保存順序をレビューした。Lunaの結果は親が実行・コードと照合して採用した。

| 指摘・実測 | 処遇 |
|---|---|
| journalのreceipt省略、finishのrecord返却、状態更新のcontainer_id必須がrunnerと不一致 | 接続を修正。初回smokeは失敗として保持し、作成containerの停止回収を確認 |
| workerのUID/GID、IPv6経路、root mount、無制限cgroupの検査が不足 | 固定ID・両IP版・mountと書込み拒否・正の資源上限を検査 |
| 実Dockerのlo上のIPv6経路を外向きと誤判定 | 外向き経路とloopbackを区別。親がprefix長/flagsの桁数も補正し、実形式の純粋parser試験と再実行で確認 |
| 既知containerの消失だけで停止済みになる | 実停止観測をSTOPPEDへ保存してからremove。停止記録なしの消失は未確認を維持 |
| recoverが生存ownerを停止できる | OSのoperation lockをrun/recoverで共有。Windowsで別handle・別スレッドの回復拒否と、監督死亡後の回復を実測 |
| journalが不完全receipt、停止未確認、二種類の出力の共存を許す | 全18field、状態/reason、scenario/mode、probe全true、停止・除去・結果排他を厳格化 |
| finishの状態検査で既知containerの再配送を拒否 | FINISHEDの同一receipt確認を先に行い、同一再配送と変更拒否を試験 |
| create前の取消しが伝播しない | image検査/createへeventを渡し、各開始段階で再確認。取消し後にcontainerを作らない回帰試験を追加 |
| 非isolation probeに通常validatorが適用される | 故障注入probeは正常結果へ採用しないと明記し、timeout/不正/実行失敗を実測 |
| markerを実行していないlifecycle試験が非保存を主張する | 当該検査を削除し、markerを実行するfull suiteのSQLite・WAL・receiptへ非保存検査を移動 |
| base_refの実行時来歴確認がない | ビルド時FROM固定の来歴と、runtimeの完成image ID照合を区別。署名認証を主張しない |

実行完了のStartedAtは短命な通常fixture30件で確認した。Docker設定・停止・除去の確認に失敗した結果は採用しない。網羅的なセキュリティ監査やhost管理者からの保護を主張するレビューではない。

## DGX Qwen

自作仕様の限定レビューをqwen3.8-flash-nextへ1回照会し、入力1,876、出力689、計2,565 token、finish_reason=stopで完了した。提出仕様のexact bytesと回答hashを[検証証跡](../evidence/mvp-execution-20260911/verification.json)へ結ぶ。実行性能や判定の証拠として利用しない。

BOMの扱いは「前後とも拒否」を明記して試験した。超過出力のdigest対象の提案は、もともと拒否出力のdigestを作らないため、その挙動を維持し4 KiB読取り・超過chunk破棄を具体化した。未知scenarioは開始前INVALID_SCENARIOで拒否し、観測UNKNOWNへ写像する提案は採用しなかった。

## 実行結果

Python 3.12.14 / SQLite 3.53.1 / Windows 11、Docker Linux Engine 29.0.1で実行した。公式Pythonをdigest固定し、自作workerだけのimageを作成した。最終実行のimageとソースhashは[runtime-check](../evidence/mvp-execution-20260911/runtime-check.json)と[lifecycle-check](../evidence/mvp-execution-20260911/lifecycle-check.json)に保存する。

通常fixture30件と隔離・容量超過・不正出力・拒否marker・異常終了・timeoutの6件、計36件が期待どおりとなった。別のlifecycle検証では固定した子処理の起動、取消し、生存ownerの回復拒否、監督プロセス中断、同一containerの停止・除去、再配送不変、期限切れ/事前取消し時の未作成を確認した。全体テストと以前の112件の保持、要求・初期値・100設計例のhashは[統合検証](../evidence/mvp-execution-20260911/verification.json)へ記録する。

管理AIの認証・採択、全契約と全資源監督、Promptfoo、400件の実評価集合、Finding/Plan、通常CIへの接続は残る。今回の固定fixture受入を全MVP完成や実LLMの検出性能へ読み替えず、Taskはin_progress、全MVP gateはno_goを維持する。
