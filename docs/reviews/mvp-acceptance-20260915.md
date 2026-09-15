---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# MVP受入の親レビュー

全32条件の技術検収が完了し、release_gate=goとする。[最終証跡](../evidence/mvp-acceptance-20260913/mvp-final-20260915-summary.json)で通常CLI800件、freshなCI、出力、再起動後不変、全精算・回収と環境復元を確認した。以下の失敗は履歴として保持する。

固定source80全898試験、source73の契約3・基準3・通常800試行が完了した。複合run、修復確認・再発、保持・削除、独立除外審査を[要件別の試験ID](../evidence/mvp-acceptance-20260913/acceptance-map.json)へ対応付けた。source81の差分はtools/verify_guardrail_runtime.pyだけで、製品コア・試験・設定・imageは同一。

検証ツールを通常製品と同じresource_start/evidence_completeへ接続し、hostとworkerの時計を別に記録した。変更後の実Dockerは184ケース・276段階後、OPERATION_TIME_MISMATCHで停止した。これは成功として数えない。

二つの固定imageでWSLの壁時計逆行を独立に実測した。時計源変更、別のNTP同期停止、両方の組合せでは解消せず、設定を元に戻した。[時計診断と失敗履歴](../evidence/mvp-acceptance-20260913/runtime-clock-20260915-summary.json)にhash・対象・範囲を残す。逆行そのものは確認したが、基盤側の原因は未特定。時刻を補正したり、予算・判定を緩めたりして受入しない。

今回の失敗初回run三つは停止・精算・取消し・closeと所有container回収を完了した。前回の通常runも全操作を停止確認しCANCELLEDを保存した。1件の使用量不明を0へ変換せず保持し、budget_closure=falseとする。

利用者の再起動許可後、Docker/WSLを再起動した。再起動だけでは解消せず、時刻同期専用デバイスとNTPの一時停止後に両imageの120秒・各12000点で逆行・時刻飛び・API不一致が0となった。固定source81の初回400ケース・600段階、旧400/新800比較と独立採択、通常CLI800試行の受入が成功した。時刻の補正や予算変更はせず、通常実行後と後処理検証後の双方で元の同期設定へ復元した。[再開証跡](../evidence/mvp-acceptance-20260913/runtime-restart-20260915-summary.json)を参照する。今回の親レビューを外部モデルの独立レビューとはしない。

## CIの分割と親レビュー（2026-09-15）

利用者の並行化指示を受け、名前hashによる4分割から、重い統合6ジョブと残り6分割へ変更した。親が計画の全件保持、モジュール準備の境界、計画digest照合、失敗・収集エラー・未実行の不成功伝搬をレビューした。新規9件と既存分割2件の11試験が成功し、既存898件をすべて保持した計907件が12ジョブへ重複・欠落なく割り当てられた。最初の分割試験は分散数の期待値順序で失敗し、最大差の条件へ修正後に成功した。製品コードと稼働中source81は変更せず、GitHub実行・短縮時間は未確認。証拠は [CI分割要約](../evidence/mvp-acceptance-20260913/ci-parallelization-20260915-summary.json) を参照する。
