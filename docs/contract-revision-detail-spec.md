---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 条件変更と後続契約の接続仕様

差分導出と禁止変更の[比較部品](semantic-conditions-detail-spec.md)に続き、同じ測定条件を使う後続契約とbaselineの次世代更新を接続した。契約3・基準3の統合検証と実Docker結果の範囲は[継続レビュー](reviews/mvp-acceptance-20260913.md)に記録する。条件差・対象差を伴う一般更新は継続中である。

この仕様は[完了監査](mvp-completion-audit.md)の後続契約・条件変更を実装するための設計である。現在の製品は同条件での後続契約と[baselineの次世代更新](baseline-refresh-detail-spec.md)を扱う。本書の条件差を伴う全経路の接続を実装済みとはしない。要求・初期閾値・予算の正本は変更しない。

## 契約・基準・元runの依存関係

契約は同一系列の直前世代から1だけ進む。baselineも同様とし、世代の飛越・巻戻し・整数上限超過は拒否する。契約とbaselineの世代数は同じ値を要求しない。契約は比較に用いる保存baselineを完全参照で固定する。

| 例 | 根拠と比較対象 |
|---|---|
| contract 2 | baseline 1を固定した既存契約 |
| baseline 2 | contract 2の完全な通常runから採択 |
| contract 3 | baseline 2を固定する後続契約。旧条件の候補runはcontract 2 / baseline 1を保持 |
| 対象変更の候補 | 新対象は新契約へ固定し、旧対象を旧条件runから取り除かない |

保存履歴の解決ではcurrentへの一致を要求しない。新規開始ではcurrentの契約、固定baselineと全依存根拠の現在有効性を確認する。依存は保存参照を辿り、visited集合と深さ128・総参照1024の上限で循環・過大な入力を拒否する。上限超過から部分的な合格を返さない。

## 差分の決定

差分は旧・新契約と参照実体をcanonical化して導出する。管理AIのchanged_axes申告だけでは決めない。比較対象は対象、Control定義・必須性・依存、データとoracle、evaluator・adapter、繰返しとstage、判定条件、閾値、予算、baselineである。

単なるrun ID、plan ID、生成時刻の差は条件差に数えない。planはIDだけを除いたentry集合・variant・段階順・再試行規則から意味的な条件参照を作る。対象の変更は修正候補の差として保持し、他の評価条件と区別する。対象名だけの改名から改善を主張しない。

閾値floor、予算ceiling、Criticalの格下げ禁止、必須義務の除去禁止を既存PolicyProfileで検査する。理由の自由文は監査用データとして保存し、規則を無効化しない。条件緩和・対象廃止の処遇を修復成功へ変換しない。

## 提案から採択まで

既存contract_propose、contract_preflight、contract_candidate_prepare、contract_candidate_validate、contract_candidate_adoptを一般世代へ接続する。managerによる提案と採択、validatorによる実体化・独立検証、operatorによる実行を維持する。新しい役割文字列は認証にならない。

1. 旧契約・旧baselineと新契約・新baselineの完全参照、導出差分、採択理由を保存する。
2. 旧条件用と新条件用のmanifest・plan・run IDを別々に予約する。source runや既存runのIDは使わない。
3. 旧条件での結果と新条件での結果を別Evidence・closureとして保存する。旧条件の違反や不足を新条件の成功で消さない。
4. validatorが充足・比較可能性・停止・精算・元の根拠と両側Evidenceの現在有効性を再照合する。比較不能は改善なしではなく不足とする。
5. managerが提案者一致、権限世代、validation期限、期待する旧契約とbaselineの完全参照を再照合する。
6. 同一transactionで履歴を追加し、currentを期待世代と旧digestによる条件付き更新にする。競合・保存失敗は全rollbackする。

採択validationの寿命はその全依存Evidenceの最短期限以下とする。再起動や現在照会は保存時の判断を上書きせず、現在利用できるかを別に返す。

## 保存と移行

採択記録は前契約参照、使用baseline参照、両候補runの完全参照、差分・理由、独立validator、権限世代、時刻、専用validation参照を持つ。各ノードを同一DBで不変保存する。

既知旧版からの明示migrationだけを許可する。旧世代に存在しない後続形式を含むDB、履歴の飛越、未来の根拠、前世代欠損を拒否する。migrationで新しい採択や健康な結果を作らない。

## 接続完了の証拠

少なくとも基準2を参照するcontract 3、旧・新条件の別実行、採択後の通常run、元根拠の撤回伝播、世代競合、参照循環・欠損、期限境界、旧Finding保持、保存失敗rollback、再起動と既知DB移行を確認する。条件を変えた対象実体の実行証拠がない場合、形だけの契約差分を条件変更の受入に数えない。

## 固定LLMの後続世代

通常のUC-LLM全体runを元に基準を更新した場合、後続契約の旧条件回帰は元のbaseline_contextと800試行を維持する。新条件候補は元のcandidate側400試行からbaseline/candidate各400試行を組み立て、新たに採択されたbaseline参照へ結ぶ。実入力400ケース、合計1200段階、元の実効時間上限を保持する。旧基準の比較条件を新基準で上書きせず、契約・基準世代と元runの完全参照を照合する。

materializationの全資料とソース実装が同じ場合のみ純粋な構造生成を再利用し、入力は2MiB以下、結果は2件までの不変JSONで保持する。現在の採択・権限・期限・Evidence照合と独立validatorの採択は上位が毎回行う。構造生成の結果はauthority_connected=false、ci_eligible=falseであり、実行や採択の成功を意味しない。source69の構造・境界検証と統合の状況は[継続レビュー](reviews/mvp-acceptance-20260913.md)へ記録する。
