---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 評価契約・実行開始・資源管理の監督記録

[全MVP Task](../tasks/TASK.mvp-completion-09-11-2026.md)の一工程として、[詳細仕様](../run-contract-detail-spec.md)から初回評価契約の採択、実行計画、同DBでの開始・全資源予約、固定fixtureの実行と停止/精算を接続した。全MVPの合格を出すレビューではない。

## 分担とモデル利用

Luna decisionがrun_contractsとEvaluationExtension、その対応試験を担当。Luna cli reviewがAdoptionStore拡張を実装し、別担当の評価契約と実Docker検証を読み取りレビューした。Luna ledgerが資源・認証接続の境界試験と閉鎖処理を担当した。親が仕様、資源core、固定fixtureの認証API、実Docker接続、統合検証を実装し、変更をレビューした。所有ファイルを分け、他者の変更を戻していない。

DGX Qwenへ仕様の同DB確定点、停止とusage、所有世代の部分を照会した。入力2254token、出力650token、24.141秒でfinish_reason=lengthの部分応答となった。提出稿と応答hashを[照会記録](../evidence/mvp-run-contract-20260911/qwen-review-receipt.json)へ保存する。途中で切れたレビューを承認や完了した独立検証に数えない。親とLunaが該当箇所を確認した。

## 指摘と処遇

| 指摘 | 処遇・根拠 |
|---|---|
| 直接Python値のcanonical化が非文字列key、巨大整数、深い構造等を受ける | JSON型、深さ16、100000 nodes、整数範囲、surrogateを事前検査。境界値と拒否を試験 |
| 評価契約の世代を方針tableから取得 | eval_currentへ分離。同名の方針系列と評価系列で成功経路を試験 |
| 既存校正/validationの壊れたpayloadを再配送し得る | 保存canonical bytes/digestを再検査し、current/contract/proposalの世代と内容も照合 |
| 予算矛盾を例外rollbackだけで終えると旧closureが有効 | 矛盾・最大費用exposureを保存し、両runにまたがるevent ID衝突も現在のclosureを無効化 |
| 閉鎖後のrunを再claim・再予約できる | closed_atとcloseを追加。停止/精算完了が閉鎖条件。後から届く矛盾は保存し、新規開始は拒否 |
| 過去のdispatch/reserve応答が再度許可になる | 認証resource操作をfresh処理し、同一requestでも現在状態を照合。同じ予定entryを別operationに再予約できない |
| 観測でplanとoperationのbindingを検査しない | resource_bindingsを照合し、未binding操作の観測を拒否する試験を追加 |
| cleanup再確認が例外なしだけで成功になる | recoverのstop_confirmed/cleanup_confirmedも検査。隔離検証値も明示確認し、runtime-02で再実行 |
| owner_idが認証actor名ではない | 偽装の指摘としては不採用。owner_idは監督インスタンスのlease ID、認証はOS operatorを毎回別に検査する。意味を仕様へ明記 |
| baseline/candidateが同一targetしか扱わない | 既存コードの一致だけで要求充足とは判断しない。比較/更新採択を拒否し、旧target比較文脈を未実装として明記 |

## 実測と限界

[統合記録](../evidence/mvp-run-contract-20260911/verification.json)で241テスト、以前の203件の保持、要求/初期値/100設計例のhash不変を照合する。固定イメージの実Docker接続31項目と、同イメージで既存のOS認証・方針採択22項目を検証した。実行したコードと現在ソースのhashを一致確認する。

runtime-01は後始末再確認の条件を強める前の記録で、今回の最終証拠には使わない。runtime-02を採用する。両記録と以前の完成済み部品証跡は上書きしない。所有するcontainerの停止/削除を確認し、合成データのstate volumeを調査用に保持する。

400参照と3ラベルの校正観測は、保存と認証APIの合成fixtureである。校正観測を実評価器の独立性能測定には数えない。実行したのは1件の固定制約fixture。全資源coreの有料/上限/並行予約は部品試験で、外部modelとの実接続ではない。baseline/旧条件回帰、実400データ/Promptfoo、常設orchestrator、モデル資源、Evidence/CI、Finding/Planは残る。全MVPのrelease_gate=no_go、ci_eligible=falseを維持する。
