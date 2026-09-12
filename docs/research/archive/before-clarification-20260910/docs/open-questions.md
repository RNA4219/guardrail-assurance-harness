---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 未決定事項

現在は要求レベル。[要求草案](requirements.md)の提案を要件・仕様へ具体化する際の論点を記録する。下表は新しい決定や、要求を緩める承認ではない。

| ID | 論点 | 次工程で明確にすること |
|---|---|---|
| GAH-Q01 | MVPと全体構想 | 全体の必須機能表とMVP4機能の対応。署名、権限、root cause、Shadow/Canary等を各段階へ割り当てる |
| GAH-Q02 | Controlと依存関係 | invariant表現、識別子、owner、循環・競合・削除・依存変更の扱い |
| GAH-Q03 | 結果とAssurance状態 | PASS/FAIL等とHEALTHY/HOLD等の対応、優先順位、遷移条件、解除条件。Criticalの不明・未検証・期限切れを安全成功にしない |
| GAH-Q04 | 評価・指標の計算 | 0分母、ERROR、無効・同等mutant、除外理由、観測欠損、control別と集約値の扱い。既存のMutation Score案を基に具体化する |
| GAH-Q05 | 閾値と比較可能性 | 暫定値の校正、標本数、同一suite・budget・evaluator条件、モデル変更時の基準更新、絶対値と差分条件の組合せ |
| GAH-Q06 | 共通coreと外部参照元 | 再利用候補の現物確認、抽出境界、GAH固有契約、依存方向、外部参照元への影響と移行順。今回外部参照元は変更しない |
| GAH-Q07 | Schema・CLI・adapter | 機械可読Schema、互換性、実コマンド、入出力・エラー契約、generic commandとPromptfooのMVP範囲 |
| GAH-Q08 | 証拠と保存 | 正規化・digest・署名の検証条件、鮮度、失効・削除・秘匿化、保持期間、台帳と状態の整合、障害時の回復 |
| GAH-Q09 | 権限と影響範囲 | L0〜L6の実行前判定、承認対象と有効期限、quota、runner分離、HOLD時に停止する操作と復旧操作 |
| GAH-Q10 | MVP評価の設計 | 受入条件案の採択、再現可能な合成ケース、期待結果と独立oracle、機能・性能・費用の測定方法 |
| GAH-Q11 | 調査根拠 | [未復元の引用](research/citation-register.md)の原資料、外部参照元実装主張、競合比較・新規性・制度に関する主張の確認 |
| GAH-Q12 | 配布と運用 | 本体と共通coreのlicense、依存条件、公開方法、CI実環境、保存方式、SLOの測定範囲 |

要求草案に数値や技術名が書かれていても、実測済み・採択済みとは扱わない。判断は対象の[Task](tasks/README.md)で根拠と影響を確認し、必要に応じて[ADR](ADR/README.md)に記録する。
