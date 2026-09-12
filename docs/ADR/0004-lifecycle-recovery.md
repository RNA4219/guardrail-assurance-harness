---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# ADR 0004: 回収専用leaseと不変の終了記録

## 背景

v1ではexecution leaseが元deadlineで切れるため、期限後に判明した費用を既存operationへ精算できなかった。取消し受付と停止確認、終了記録もまだ保存していなかった。[Task](../tasks/TASK.lifecycle-core-09-11-2026.md)でLC03/LC04/LC06の部品実装を進める。

## 判断

同じSQLite上でexecution/recoveryを区別し、切替と引継ぎでowner_epochを更新する。recoveryへ移ったrunは実行へ戻さず、既存費用だけを回収する。取消し、停止確認、terminal、費用は別々の事実として保存し、後日の費用確定で過去terminalを書換えない。

Schema v2への変更は明示コマンドだけで行う。書込みlock取得後の検査とDDL/version更新を一transactionにまとめ、v1の記録を保持する。終了優先順は副作用なしのtermination部品へ分離し、DBから得る取消し/停止/費用をfinalizeで照合する。

## 代替案と影響

元deadlineを延長して再開すると新規評価まで許してしまうため、期限の延長は採らない。料金不明を消して取消しを完了させる方式も採らず、CANCELLED/3とOPENなfinancial closureを併存させる。

移行しないv1を新コードで自動修復しない。既存の利用にはdb-upgradeが必要となる。ownerやconfirm_stoppedはtrusted内部APIの境界であり、OS認証・実停止・外部請求の正しさを保証するものではない。

## 状態

局所的な技術判断。詳細と残件は[状態管理仕様](../lifecycle-detail-spec.md)を正本とする。CI合格の発行と全MVP受入は未接続。
