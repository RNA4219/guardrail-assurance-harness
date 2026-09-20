---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# 拡張仕様の監督レビュー

[統合仕様](../productization-spec.md)と3分冊、[設計ケース](../contracts/productization-spec-cases.v1.json)を対象にする。利用者の指示でLunaを使用し、親Codexが既存コード・要求への対応を検査する。製品試験や外部監査の実績へ換算しない。

## 分担と監督

| 担当 | 所有した成果物 | 役割 |
|---|---|---|
| Luna performance | 性能・CI仕様 | PR05〜08/13の執筆 |
| Luna operations | 導入・運用仕様 | PR09〜12/14の執筆 |
| Luna pilot | 実案件評価仕様 | PR01〜04/14の執筆 |
| 親Codex | 統合仕様、設計ケース、Task/検収/導線 | 現行CLI・wire・役割の確認、境界の固定、仕様統合と検査 |

担当ごとに編集範囲を一つの分冊へ限定し、実装・外部通信・Docker実行・Git変更・文書一括生成を委任しなかった。Lunaの出力は仕様下書きであり、採択・判定権限や実装事実を付与しない。

## 親の指摘と反映

| ID | 論点 | 反映・確認 |
|---|---|---|
| S01 | 補助CLI終了0を製品CI成功と誤認する | 別kind/operation_status、ci_eligible=false、既存CLIの終了値は維持 |
| S02 | 現行CI応答はfield集合も厳密なのに項目を足す | tools/gah_ci.pyを確認し、既存応答へ新fieldを追加しない共通契約 |
| S03 | 独自JSON正規化や無制限の埋込入力 | 現行contracts/wireの参照/canonical/1MiB上限を再利用、分割artifact |
| S04 | 計画本文のroleや採択済みフラグが権限になる | 現在の認証・validator・原子的採択・開始直前再照合を共通化 |
| S05 | 別管理journalの障害後に二重採択・二重送信する | actor/command/request_idとdigestを固定し、authorityの現在状態から回復 |
| S06 | CPU/RSS/queue/runner分を混ぜて高速化を過大表示する | 計測単位と区間、子処理・同時peak、費用とcritical pathを分離するよう差戻し |
| S07 | 工数0分母や異種資源の合算で有用性を合格にする | 対応観測の中央値、ゼロ/不明と資源各系列の非増加を指定 |
| S08 | 文書検査から新製品受入をPASSへ昇格する | 14条件と全設計caseをNOT_RUN、文書/技術/有用性/公開を分離 |
| S09 | 新規workspaceに既存runtime/採択を要求して初回手順が止まる | bootstrap/readyを分離し、setup内にimage/authority/初回baselineとready検査を配置 |
| S10 | run用と現在CI用のrequestを同じfileにする | run-request.jsonとci-request.jsonを別生成し、LLMはguardrail runnerを明示 |
| S11 | 同一repo bindingの1revisionしか扱えず履歴pairを評価できない | repo identityと許可済み凍結revision集合を分け、2版を集合から選ぶ |
| S12 | 受入集合とは別の未定holdoutを必須化する | 受入集合自身をholdoutとし、追加集合は任意診断 |
| S13 | 人間介入の中央値だけで一部の大量介入を隠せる | 同じ観測集合の合計、資源の合計/最大を単位ごとに判定 |
| S14 | 予約と確定実額の二重計上・曖昧な通貨単位 | 未解消予約+確定実額、精算で置換、既存整数micro-USDを採用 |
| S15 | CI推定のcountとns混在、setup費用欠落、fallbackだと実測SLOまで不明 | module setup/teardown込み時間、nsの決定的fallback、推定の不確かさと実測を分離 |
| S16 | 各CI jobが別の履歴で分割を再生成する | 同一timing historyとplan lockの完全refを配布、既存plan_digestと両方を照合 |
| S17 | 運用の入力型やbundle/clock上限が仕様作成後も候補のまま | setup payload、doctor型、64 MiB/256 entry、観測120秒・全体300秒を仕様値として固定 |

## 相互レビュー

| 担当 → 対象 | 結果 | 親の処遇 |
|---|---|---|
| Luna operations → 性能分冊 | 共通外枠、観測状態、初回計時、取消しと精算時点の4点 | 採用。分冊と共通契約へ反映。`--lane`が実在するという確認も受領し、引数は維持 |
| Luna performance → pilot分冊 | benchmark以外のCLI禁止との指摘 | 不採用。benchmark限定は性能担当の編集範囲であり、共通仕様はops/pilotを別に定義している |
| Luna performance → pilot分冊 | REJECTEDとPAC状態の混同、created_atの外枠/payload重複、費用単位の不明瞭さ | 状態と時刻の指摘を採用。費用はns/bytes/countへ無理に変換せず、既存整数micro-USDを明記 |
| Luna pilot → 統合仕様と36設計ケース | 計画/操作/PAC状態、参照、採択・冪等性、境界値を確認して追加指摘なし | 親の静的・算術照合と合わせて採用 |

相互レビューは各稿に対して行い、親が指摘処遇と最終統合を担当した。Lunaの案をそのまま採択せず、既存コードや要求に合わない指摘は理由付きで退けた。新機能・SLOの検証を実施したことにはしない。

## 検査と残る入力

仕様の静的照合14項目、設計例の算術8項目、既存の文書ワークフロー11テストが成功した。生成/文書検査12項目も成功。保護対象320ファイルと元の要求・受入証拠を維持した。具体のpilot対象・実利用集合・model版・基準機・営業日カレンダーはM0で採択する。ここでは要件と仕様の整合を確認し、性能や有用性の結果は捏造しない。
