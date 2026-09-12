---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 所有権期限切れ後の取消し取得

[通常run取消し](run-cancellation-detail-spec.md)の停止入口を補完する。既存ownerのleaseが切れ、採択根拠の失効により通常claimも拒否される場合に、取消しと精算へ進めるようにする。対象は自作の固定・無害なfixtureである。

## 入力と権限

`resource_cancel_claim`は既存brokerのoperator専用操作である。入力fieldはschema_version=1、action、request_id、run_id、owner_idだけとする。owner_epoch、停止済みの自己申告、terminal_pendingなどの内部状態は受け付けない。

```json
{"schema_version":1,"action":"resource_cancel_claim","request_id":"cancel-recover-01","run_id":"example-run","owner_id":"recovery-owner"}
```

owner_idは監督のlease識別子であり、OS identityや認証情報ではない。fresh操作として毎回DBと現在のbroker時刻を検査する。過去の応答を再送許可に使用しない。

## 原子的な取得と取消し

| 保存状態 | 動作 |
|---|---|
| 別ownerがlease期限前 | OWNER_ACTIVE。所有権と取消し状態を変更しない |
| 同じownerが期限前 | epochを維持してleaseを更新し、取消しを要求する |
| lease期限に到達した、または期限後 | epochを1増やして取得し、同じtransactionで取消しを要求する |
| generationまたは時計の上限に到達 | GENERATION_EXHAUSTED。部分取得しない |
| resource_close済み、通常runのterminalは未確定 | 取得と取消しを許可する |
| 通常runの正常または取消しterminalが保存済み | 保存実体を照合してalready_terminal=true。履歴・所有権を変更しない |

BEGIN IMMEDIATE内で所有権更新、cancelled=true、未送信予約の解放を確定する。途中失敗は時計更新も含めrollbackする。採択根拠の現在有効性に依存させず、runの保存manifestと既存資源状態を照合する。新規reserve/dispatchには既存の開始検査とowner/epoch照合を維持する。

## 停止と精算の継続

取得成功はrecovery_only=trueを返す。送信済みoperationのslot、停止未確認、未精算額を残す。別のvalidatorが停止とusageを観測し、全停止を確認してからrun_cancel_finalizeする。資源の停止と使用量は別々に観測できる。保存根拠が欠損した場合も資源停止・精算は進められるが、不完全な根拠からterminalやCI成功を作らない。

取消し済みでleaseが再び切れた場合は、既存のresource_claim(recovery=true)で精算用の所有権を得る。停止確認までCI終了2、保存された取消し実体を照合できれば3となり、後日精算で0へ変更しない。

## 既知DBの明示移行

直前の取消し版のextension digestを既知の移行元へ追加した。通常起動で自動移行せず、既存の明示migrationを使う。保存した取消しroot、元run、binding、資源、terminal、参照実体、報告書を検査し、既知の正常・未完了runと共に保持する。取消し形式を持たない旧版への偽装や、根拠の欠落・破損は拒否する。判定結果や過去の採択を移行時に作り直さない。

[親レビュー](reviews/mvp-recovery-20260912.md)と[証跡](evidence/mvp-recovery-20260912/README.md)で確認範囲を示す。常設監督全体の復帰、両用途の運用受入は[完了監査](mvp-completion-audit.md)の残件として維持する。
