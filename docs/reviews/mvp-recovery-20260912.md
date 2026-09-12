---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 所有権期限切れ後の取消し取得の親レビュー

最新の利用者指示に従い、親が実装・レビュー・検証を担当した。追加のモデル委任は行っていない。

## 確認した問題と改修

資源authorityの保存状態で、通常claimが開始前提の失効、recovery claimがRECOVERY_NOT_REQUIRED、元ownerの取消しがOWNER_STALEとなる組合せを確認した。leaseは切れているが資源台帳がまだ取消し・予算違反・deadline超過のいずれにもなっていない場合である。

resource_cancel_claimを追加し、期限切れ所有権の取得と取消しを同じtransactionへ結んだ。開始検査を緩めず、返却を停止・精算用に限定した。稼働中の別owner、epochの上限、未送信予約と送信済み予約、保存失敗時のrollbackを検査する。取消し済みのterminalと正常terminalは変更しない。

明示migrationには直前の取消し版を追加した。保存rootのloadは報告書まで照合するため、その検証結果をreceiptそのものとして二重投入していた初稿を、実行前の親レビューで修正した。旧形式にない取消しrootの拒否は維持する。

## 検証

全体回帰551件と、API一覧の補正後の6件再検証を行い、検証対象全件の成功を確認した。実Docker233項目も成功した。前工程の534テスト・219項目を保持し、通常run要約の7テストと取消し復帰の10テストを今回の検証で確認した。初回全体回帰の一覧期待値1件の失敗と、補正後のログを分けて保持する。実行92件は全て停止・回収済みで、取消し後の精算と再起動も確認した。

## 受入の扱い

この工程は取消し復帰の接続であり、全MVP受入ではない。固定fixtureだけを使い、既存の要求・受入条件・初期方針と4つの決定例ファイルを変更していない。全MVPはin_progress、Acceptanceはdraft、release_gate=no_goを維持する。
