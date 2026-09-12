---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 固定UC-LLMを認証・資源・Evidenceへ接続する仕様

[評価詳細仕様](evaluation-detail-spec.md)の合成データと評価器を、authorityと資源台帳へ接続する設計である。現行の診断runnerとPromptfoo変換はこの接続の受入証拠ではない。本書の接続は未実装として[完了監査](mvp-completion-audit.md)に残す。

## 許可する入力と実配備の確認

対象は既存の自作合成packの二カテゴリに限定する。受入400ケース、校正18、開発12を別purposeで固定し、参照実体とoracleを検査する。実credential、第三者操作、任意コード、任意送信先を入力として追加しない。

実行前にmanagerの採択記録へprovider、配備識別子、重み版またはproviderが証明する不変版、推論設定、adapter/evaluator/packのdigestを固定する。model表示名や接続URLのhashだけで重み版を証明しない。版確認不能は比較条件不足として拒否する。

送信先はローカル管理が設定するallowlistに限定し、redirect・本文指定URL・モデル出力の送信先は利用しない。provider認証情報は実行主体だけが取得し、候補文、Evidence、Plan、標準出力へ保存しない。ログは固定reason、enum、usage、digestと参照を中心にする。

## 段階ごとの予約と観測

| 順序 | 保存・検査 |
|---|---|
| plan固定 | 全case、trial、stage、variant、初期状態、対象版、評価器、期限・再試行上限を事前確定 |
| reserve | stage呼出前にslot、モデル呼出、input/output token上限、価格表digestに基づく費用上限を同DBで予約 |
| dispatch | owner/epoch、runの現在開始条件、許可送信先、事前bindingを再検査して送信意図を保存 |
| observe | validatorが実行主体のreceipt、binding、停止、usageの各実体を検査して登録 |
| settle | 検査できたusageだけで予約を置換。未確認usageを0にしない |
| close | 全送信の停止と全精算を確認。停止不明はslotと予約を残す |

費用は既存方針のmicro USDによる整数上限を使い、単価の変更は価格表版と条件差として保持する。全体の未精算予約は日次上限へ含める。per-callとrun、日次の上限は直前に再検査する。利用者の初期方針を超える予算は自動で増やさない。

タイムアウトで応答がないことと、provider側が停止したことを区別する。終了・取消しのprovider receiptで停止を確かめられなければ不足を維持する。transport障害の再試行は既存方針の上限以内で別attemptとして予約し、結果が悪かったことを理由に再試行しない。

## 会話と操作の状態分離

caseごとに新しいsessionを作り、初期状態を内容参照で照合する。前caseの会話、workspace、cache、tool状態を持ち込まない。二段階は同じcase内の順序を固定する。前段の禁止違反・欠損を後段の正答で相殺しない。

モデルが選ぶ操作は既存SyntheticEvaluatorの有限enumのみとする。出力をshell、URL、追加tool権限へ変換しない。保存済みの効果記録は実際に行った有限操作へ結び、期待ラベルから実行結果を作らない。

## Promptfooの起源と校正

Promptfoo通常出力はtransport形式として正規化する。外部JSONだけでは実行主体や使用量を証明しない。認証されたrunnerが固定版のPromptfooを開始し、子処理・provider呼出・停止とusageを同じoperation群に結ぶ。

測定系の校正は受入実測より先に、固定応答の既存165vectorと独立した照合ロジックで確認する。測定系の校正失敗ではCIを合格させない。対象モデルの18ケース診断は別の結果として保持し、不一致を測定系そのものの校正失敗と混同しない。対象の誤答・判定不能は評価結果または欠損として追跡する。受入集合の観測は有限集合の結果であり、標本独立性や未知入力への一般化を自己申告しない。

## 出力と受入

通常runは既存Decision、Evidence、Finding、Plan、run_receiptを同一transactionで保存し、現行gah_report/CI consumerへ接続する。manifestへuse_cases=UC-LLM、完全な対象版・dataset・契約・baseline参照と実施範囲を保持する。UC-CIとの複合runでも片方の未実施を全体の成功へ混入させない。

接続完了には400実ケースのstage結果、別用途校正、正常・障害Promptfoo、実provider版確認、認証、予算超過、停止不能、未知usage、会話分離、撤回・期限・再起動とfresh CIの実行証跡が必要である。診断用の集計結果をこの受入へ換算しない。
