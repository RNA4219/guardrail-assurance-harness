---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 契約候補runの接続・監督レビュー

[移行詳細仕様](../contract-transition-detail-spec.md)に沿って、旧条件15件と新条件30件を
別runとして保存・実行・精算し、別Evidenceへ結ぶ経路を追加した。
全494テストと実Docker133項目・60件が成功し、前工程463テスト・52項目を保持した。[統合証跡](../evidence/mvp-candidate-20260911/verification.json)へ
最終ソース、実Docker、補助レビューと検証範囲を記録する。

## 分担と親レビュー

| 担当 | 範囲 | 親による確認・修正 |
|---|---|---|
| Luna | 純粋な候補factoryとpurpose検査 | preparedの全field・metadata・時刻・IDを厳格化。一時的な初回runへの偽装を除き、元pack参照を保持する生成へ修正 |
| Luna | 明示v2/v3→v4移行、移行テスト | 新規/移行のDDLを一本化。schema4だけを移行先として許可し、保存参照の点検を追加。旧版のimage sources_digestとExtension.digestの混同を訂正 |
| Luna | 実SQLiteの候補run統合テスト | APIのキー・lease照合を修正。旧新runで時計を単調に進め、未送信操作を実行済み観測へ数えない試験に修正 |
| 親 | 認証・保存・実行/Evidenceの接続、Docker検証、文書・証跡 | 独立レビューの指摘を照合し、ID衝突、保存版解決、完全参照、rollbackを確認 |

candidate作成はvalidator、開始はoperatorに限定した。候補purposeだけでなく、予約行の存在でも
専用resolverへ進むため、保存manifestのpurposeを書き換えて通常runへ読み替えられない。
初回fixtureと候補runのIDを両方向で衝突検査する。prepareの衝突検査はfresh照合より先に行い、
既存IDの再利用には固定のRUN_CONFLICTを返す。新規runの実行には別途現在前提を必要とする。

Lunaが指摘したbegin再配送の古いsnapshotは、既存の不変receipt仕様として保持する。
再配送は開始許可ではなく履歴の返却であり、予約・dispatch・Evidence開始・現在利用で
権限、旧契約、baselineと根拠を再検査する。この区別を詳細仕様へ明記した。

## DGX Qwenの扱い

自己作成した候補保存コードと仕様だけを、利用者が指定したローカル接続へ1回提出した。
応答は完了し、使用量は入力4,175・出力326・計4,501tokenだった。主体認証、モデル版の
証明、製品性能や採択成功の証拠には使用しない。

指摘1のside順序は、既にnew→oldで一致していた。指摘2のcallback構造についても、
checkedのtransitionを明示的に取り出す実装と実DBの正規経路を確認した。
この2件は追加修正の根拠とせず、提出稿・生応答・使用量・hashをそのまま保存した。

## 実行検証と失敗の処遇

初回Docker検証は初回15件と旧条件15件の実行・精算・Evidence保存まで成功した。
その後、検証側が応答のaction/request_idを含む値と保存receipt本体を直接比較して停止した。
差がこの2つの封筒fieldだけで、保存本文は完全一致していることを確認し、比較対象を修正した。
初回の失敗記録を上書きせず、同じ製品イメージで再検証し、133項目が成功した。

実Dockerの検証は、beginのrun/side/世代/lease、manifestとbundleの完全参照、保存された
5 artifactのfresh照合、再起動後receiptまで確認する。別の候補を予約まで作ってから
source Evidenceを撤回し、未開始sideのbeginと未送信予約のdispatchを拒否、取消し・回収を許す。
完了済み候補のreceiptを変えず、現在利用を拒否することも検査する。

## 残る接続

候補の2本が完了しても、採択validation、世代2へのCASと原子的更新、更新後の通常runは
未接続である。条件変更を含むbaseline更新、UC-LLMの認証・モデル資源、Finding/Plan、
常設運用と通常CIも続ける。[全体Task](../tasks/TASK.mvp-completion-09-11-2026.md)と
[全MVP検収](../acceptance/AC-20260911-05.md)は未完了を維持する。
