---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 採択と実行証跡の接続レビュー

この工程は実装中であり、全MVP受入を完了した記録ではない。前工程の357テストと
実モデル実測は[当時の検証](../evidence/mvp-evaluation-20260911/README.md)として保持する。

## Lunaの実装と親レビュー

共有transaction版RunEvidenceBook、既知v2からの明示migration、採択履歴、baselineの
構造照合を分担した。親側で共有DB・migration・baselineを含む36テストの成功を確認した。
その後、finalize後半の保存失敗による全rollback、失効後の過去receipt配送、方針の次世代
採択後も旧runの固定条件を維持する3テストを追加した。最終件数と対象hashは工程の確定時に
証跡化するため、この途中経過を最新版全体の合格証明に使わない。

親はbaseline照合からEvidence本文のdigest照合が欠けている点を修正した。
また反復設定を保存Planから一意に生成し、比較文脈のcontract generationを実体と照合した。
別系列proposal・validation列・固定historyの結合検査をLunaの独立レビュー後に追加した。

期限切れ後の停止済み観測の保存と、新規dispatchを区別する。新規dispatchは現在の権限・
採択条件を再検査する。停止・精算済みの観測は履歴へ残せるが、現在の利用判定で失効を
反映する。freshnessを過去receipt再配送の前提にして、監査記録を読めなくする案は採用しない。
closureの構造・時刻・カウンタと撤回eventを厳格化し、可変の全体費用windowで過去closureを
書き換えない。

## 実行検証で見つかった補正

固定packの初稿には一つのentryへ複数scenarioを結ぶ曖昧さと、実体bindingをmockした試験があった。
親レビュー後、15個のentryと実入力・oracle・初期状態を一対一にし、実DBの採択・実行・採択後利用を
coreのmockなしで検査した。候補のcreated_atを検証時に再生成する不具合と、materializationの
補助metadataがcore bundle照合へ混入する不具合を修正した。採択の後半INSERT失敗ではhistory/currentの
両行をrollbackし、撤回行のgeneration・主体・context・時刻の破損を明示エラーにする。

430テストが成功した後の実Docker接続で、constraintのみのrunが空metricsを理由に最終化できない点を
確認した。保存Decisionに制約事象だけの経路を追加し、非critical制約FAILもDEGRADEDへ反映する。
修正と起動確認の追加後は441テストが成功し、前工程357件を全て保持した。途中の集計スクリプトが
docstring付きテストを数え漏らした記録も保持し、保存logの再集計と最終の全試験を分けて記録する。

受入ツールも親がレビューし、begin request IDとlease ownerの不一致、撤回応答の形、mutationの
期待値、複数fixtureのcleanup結果を最初の一件だけで判定する誤りを補正した。実Dockerの停止・
隔離・精算・現在利用を検査し、保存DBは後の確認のため保持する。

固定15entryの実Docker実行から精算、証跡最終化、baseline採択、再起動後の利用、Evidenceとbaselineの
撤回まで40項目が成功した。起動直後の失敗では製品runを開始せず、専用の起動診断を実施した。
監督側のprepare/restartは、固定operatorのcurrent読み取りで起動完了を確認してから呼出し元へ返す。
読み取りだけを最大3回まで再試行し、未採択のcurrentでも通信の成立を確認できる。変更加算を伴う
要求を起動確認のために再送しない。新しい監督実装と同じイメージで証跡接続41項目・認証22項目の
回帰も成功した。[統合証跡](../evidence/mvp-adoption-20260911/README.md)へ固定ソースhashと失敗履歴を保存した。

## DGX Qwenへの照会記録

自己作成の採択仕様・証跡保存仕様のみを送信した。固定モデルの1要求が16.625秒で完了し、
入力6,570・出力443 token、finish_reason=stopだった。送信bytes、応答envelope、応答本文、
各SHA256を作業証跡へ保存した。モデルの提案を認証・受入証拠には数えない。

1. kindの不統一の指摘は、既に予定していた`baseline`への統一として反映した。
2. 初回contract参照の記述を明確化し、previous/new contract refが同一であると追記した。
   ただし「new_baseline_refをIDだけにする」という提案は、内容参照の要求に反するため
   不採用。初回baselineの旧参照はnull、契約の旧・新参照は同じ既存採択版であり、混同しない。

## 残る接続

固定UC-CIの初回採択に続き、UC-LLMの実入力pack採択、baseline更新と旧条件の回帰、
モデル送信の資源管理、現在CI判定とFinding/Planの保存を続ける。
保存・採択操作の成功を全MVPのCI成功に読み替えない。

R29は要求が「推定を合格根拠に使う場合」と条件を付け、初期方針では推定を無効にしている。
Lunaの読み取りレビューで、区間推定の未実装を無条件のMVP不足に数える監査の誤読を補正した。
要求・AC・初期方針は変更しない。現行では有限計数、推定の入力拒否、欠損・途中停止と反復計数を
受入し、区間推定の試験例は将来の別方針のgateに残す。
