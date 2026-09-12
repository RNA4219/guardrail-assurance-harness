---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 期限後回収と取消し・終了の詳細仕様

[コアv1](detail-spec.md)の台帳をSchema v2へ拡張し、[LC03〜LC06](contracts/lifecycle-contract.md)の一部を部品APIとして具体化する。診断目的とci_eligible=falseを維持する。実hostの認証、外部課金・停止の確認、全資源監督は未接続である。

## 1. 台帳v2と移行

runsにlease_kind（execution/recovery）、cancel_requested_at、stop_confirmed_atを追加する。新たにterminal_records（run_id主キー、canonical_json、payload、sha256）とrun_events（単調なevent_id、run_id、event_kind、observed_at、owner、owner_epoch、details_json）を持つ。イベントは部品の監査履歴で、同一host管理者からの改竄耐性や主体認証ではない。

既存v1は通常openで拒否する。`Ledger.migrate_v1(path)`を明示したときだけ、BEGIN IMMEDIATEで書込みlockを取得してから、既存DBの版・必要表/列・保存reportの完全性を検査し、同じtransaction内でALTER/CREATE/user_version更新を行う。移行専用表が既にある異常なv1は拒否する。新規/空/未知版を移行しない。v2再配送は構造を検査した後に変更なしで返す。途中失敗は全変更をrollbackし、接続を閉じる。元レコードとreport bytesを保持し、未知な列を削除しない。既存runはlease_kind=execution、cancel_requested_at/stop_confirmed_at=NULLで初期化する。返値はschema_version=2とchanged（bool）。

## 2. 実行と回収の所有権

既存claimはexecution lease専用。新規予約は有効なexecution lease、元deadline未満、取消しなし、停止確認なし、terminalなしを同じtransactionで検査する。再配送の予約でも実行禁止境界を越えない。HOLDの既存再配送の扱いはv1を保つ。

`claim_recovery(run_id, owner)`はdeadline到達、取消し受付、停止確認済み、HOLD、terminal確定のいずれかがある場合に限る。停止確認後に監督が中断した場合も、実行を再開せず残りの精算・終了確定を行える。有効leaseを別ownerが横取りせず、同じownerによるkind切替または失効後の引継ぎではepochを増やす。同一owner/kindの有効更新は同epoch。回収leaseはnow+60秒で、元deadlineを変更しない。時刻/epochの上限超過は拒否する。

recoveryへ移ったrunはexecutionへ戻さない。回収ownerは既存予約をsettleできるが、新規予約は不可。旧owner/epochからの操作を拒否する。回収は外部へのdispatch・再送・停止命令を実装するものではない。未精算と未解消exposureを引継ぎや期限だけで解放しない。

## 3. 取消しと停止確認

`request_cancel(run_id, owner, epoch)`と`confirm_stopped(run_id, owner, epoch)`は有効な現在leaseを必要とする。前者は取消し受付時刻、後者はtrustedな監督から受けた停止確認時刻を一度だけ保存し、同じ通知で時刻を更新しない。どちらもeventを同じtransactionへ記録する。取消しは停止確認を兼ねず、どちらの後も新規予約を許さない。

terminal確定後の取消し・停止確認はalready_terminalとして元receiptを保ち、別の監査eventに記録する。同run・owner_epoch・event_kindの再配送は一度にまとめ、同じ通知で時刻を更新しない。取消し受付とfinalizeはBEGIN IMMEDIATEで直列化し、先に確定した事実を後の処理が見る。遅い取消し・停止確認・費用精算でterminalを書換えない。request本文の自己申告を認証済み停止として外部公開するAPIは設けない。

## 4. 費用回収とclosure

`budget_closure(run_id)`はstatus CLOSED/OPEN、pending_operations（未精算件数）、unresolved_exposures（未解消exposure件数）、run_accounted_micros/global_accounted_microsを返す。全操作が精算されexposureがなければfinancial closureはCLOSED。ゼロ操作もfinancialにはCLOSEDだが、作業・子処理・tokenの完了証明にはならない。financial_only=true、purpose=component_validation、ci_eligible=falseを必須にする。

期限後のsettleは回収leaseで行い、v1の同額再配送・矛盾HOLD・会計用切上げを保つ。移行元のlease_untilが元deadlineを超える場合でもexecution leaseによる期限後変更を許さない。監査イベントは操作IDと会計額等の固定metadataを保持し、外部payloadを受け付けない。通常の請求額・非課金の認証根拠は未接続。exposureを解除するAPIは本版では提供しない。

## 5. 終了の決定

`termination.decide_terminal`は副作用なし。keyword引数cancelled、stopped、deadline_reached、work_complete、required_failure、budget_closedを厳密bool、assuranceを5状態の既知enumとして受ける。不正入力は固定ValueErrorとし入力本文を含めない。

| 順序 | 条件 | execution_status / exit_code |
|---|---|---|
| 1 | required_failure | FAILED / 2 |
| 2 | 取消し受付済み、停止確認済み | CANCELLED / 3。未精算でも予約を保持したまま取消し可能 |
| 3 | 取消し受付済み、停止未確認 | 期限内WAITING/null、deadline到達FAILED/2 |
| 4 | 取消しなし、deadline到達 | FAILED / 2。期限後に成功確定しない |
| 5 | 取消しなし、期限内、作業・停止・費用が全て完了 | COMPLETED / 1（部品診断） |
| 6 | 上記以外 | WAITING / null |

返値はschema_version=1、purpose=component_validation、ci_eligible=false、execution_status、exit_code、assurance、reasons（固定codeの配列）。理由はrequired_failure/cancel/停止未確認/deadline/作業未完了/未精算の既知事実を全て一定順で保持する。停止・作業・費用の不足があればassuranceを少なくともUNKNOWNへ上げ、元のDEGRADED/HOLDを下げない。

`Ledger.finalize(run_id, owner, epoch, *, assurance, work_complete, required_failure)`は入力を厳格検査して現在leaseを確認し、取消し、停止、deadline、financial closureをDBから取得して上の関数へ渡す。台帳に保存されたHOLDはcallerのAssuranceより優先する。WAITINGはterminalを作らない。終端状態はrun_idごとに一度だけcanonical bytes/hash付きで保存し、確定eventと原子的にcommitする。返値は判定fieldにrun_id、owner、owner_epoch、finalized_at、deadline、budget_closureを加える。同runの再呼出しは入力と現在leaseを確認して既存receiptを返すだけとし、新しい有効性を保証しない。

`get_terminal(run_id)`はhash/bytesと保存行・本文のrun_idを再照合して過去記録を返す。全子処理の本当の停止や評価完了は未接続の監督責任であり、内部APIへ値を渡したことを実環境検証と呼ばない。

terminalにはledger_hold_reasonも保存し、台帳のHOLD原因を識別できるようにする。HOLDがなければnull。v1のexposureが確定額より小さい場合も元の候補額を保持し、会計ではmax(確定額, exposure)を留保する。妥当な旧記録をv2側の追加検査で不正扱いしない。

## 6. 診断CLIと受入

`budget-show --id --db`と`terminal-show --id --db`は既存DBだけを読み、正常表示の終了値は1。保存済みterminalのexit_codeと表示コマンドの終了値を混同しない。`db-upgrade --db`は既存DBの明示的な移行で、成功表示は終了1、入力・移行・出力障害は2。通常assess/showの意味は維持する。運用状態を任意に書くCLIは追加しない。

受入では、既存v1のbytes保持、移行の途中失敗、未知/破損DB、期限一致、回収epoch競合、旧owner拒否、取消しと確定の競合、停止未確認、未精算取消し、回収後のterminal不変、保存故障を実行する。実行証跡は今回のTaskへ結び、以前の100設計例や検収を書換えない。
