---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# GAH-R01〜R32 MVP完了監査

固定UC-CIの開始・再開・取消し・状態照会CLIを接続した。実Docker194項目・91件実行が成功し、停止・回収を確認した。追加レビューで準備直後の再開漏れを補正し、実SQLiteと合成runnerの12試験が成功した。補正前46件と合わせて50種類をfocused検証した。Dockerはこの一行補正前の結果で、現行592件の全体試験は再実行していない。 [仕様](supervised-run-detail-spec.md) / [工程証跡](evidence/mvp-supervisor-20260912/README.md)。

全MVP受入は未完了、release_gate=no_goを維持する。[32受入の対応表](evidence/mvp-supervisor-20260912/acceptance-map.json)で過去の証拠と今回のfocused検証を分ける。全条件の製品受入は0件として保持する。

## 要求ごとの接続状況

| 要求 | 現在の実装・確認範囲 | 全MVP受入までに残るもの |
|---|---|---|
| R01 Control登録 | registryのID・必須項目・依存閉包と循環検査、認証されたobject_register、固定15 Controlの契約・判定への結合 | 変更影響を反映した対象限定実行と、依存Evidenceの不足・失効の全利用先への伝播 |
| R02 事前契約 | 初回契約、条件差なしのgen2採択、別目的候補、通常gen2の不変manifest・plan・全30入力 | 条件変更を伴う契約・baseline更新、UC-LLMと両用途runの実行接続 |
| R03 隔離 | 固定Docker image、network none、read-only、UID、capability、資源上限、出力・子処理・cleanupの検査 | モデル評価経路の許可送信先・子処理・保存先境界と、その実環境受入 |
| R04 資源 | 固定監督CLIを予約・dispatch・停止観測・精算・closeへ接続。fresh操作照会で未知usageを保持 | モデル呼出・token・費用、定期起動と全体の運用受入 |
| R05 Evidence | 認証されたAttempt、binding、実行状態、保存Decision/Evidence、停止・精算の実体検査 | UC-LLM/Promptfooの実行・起源・保存接続と、両用途の障害受入 |
| R06 Mutation前提 | 変更前の成立を確認する正規化・集計と固定fixture | 対象限定実行と条件更新を含めた前提失効の伝播 |
| R07 Mutation分類 | 成立・到達・検知、KILLED/SURVIVED/NO_COVERAGE/ERRORの分離と固定fixture | 更新契約・全体監督での分類と障害の保持 |
| R08 adapter | generic実行の固定authority接続。Promptfooの通常/障害出力の変換・binding検査 | Promptfoo実行主体、資源、保存までを結ぶ認証経路 |
| R09 指標 | Mutation Score、LLM混同行列、未確定・除外・0分母、指標ごとの決定的集計 | UC-LLMの認証された通常runで、実入力からCI成果物までの接続 |
| R10 充足 | 予定試行を分母とした充足・Critical・family・欠損判定、通常30件の全成果物 | 対象限定・複数用途runで、未実施範囲を全体合格へ混入させない受入 |
| R11 比較 | 保存baseline 1→2、同条件の旧/新候補、通常contract 2での固定参照照合 | 後続baseline/契約更新、条件差のある候補を旧条件でも評価する経路 |
| R12 Assurance | 固定方針による優先順位、必須違反と集合指標の区別、通常runの否定結果 | 両用途・全体監督を通じた最終受入 |
| R13 鮮度 | 保存時receiptと現在有効性の分離、期限・撤回・採択根拠をfreshに再照合 | UC-LLM、保持/削除、依存変更を含む全利用先への接続 |
| R14 HOLD | 固定監督CLIは根拠撤回後の新規dispatchを拒否し、既存操作を停止・精算・取消しへ進める | 定期起動、両用途と対象限定実行でのHOLD後の処遇 |
| R15 Finding | 通常runのDecisionからControl・範囲・比較・Evidence付きFindingを保存・取得。原因はUNKNOWNを保持 | Findingの変更・再検証・再発を保存された元runへ結ぶ管理経路 |
| R16 Plan | 必要項目・参照を検査する決定的PlanとJSON/YAML互換出力。通常runへ保存しNOT_EXECUTEDを保持 | 再検証に必要な元条件を認証された管理経路へ接続。計画の自動実行はMVP外 |
| R17 補助LLM | 補助モデルなしでDecision/Finding/定型Planを生成。補助出力へ判定権限を与えない | 評価対象モデルの失敗と補助モデル不在を分離した全体受入 |
| R18 不変保存 | 不変checkpoint、OS排他、同DBの保存・冪等応答。準備・開始登録前の中断から同じrunを再開 | 全checkpoint境界、版跨ぎ回収、両用途・一般更新採択・保持処理の接続 |
| R19 出力 | 認証された通常runの日本語Markdown/JSON、指標・差分・欠損・参照・UTC日時の表示。最後のfresh CIで取得中の失効を再照合し、専用7テスト・実DB15項目を確認 | UC-LLM・両用途を含む全体の表示受入 |
| R20 CI接続 | 製品run/resume/cancel/statusとfresh CIを接続。scheduled_fullを受け付け、changeはfullへ拡大 | 対象限定実行、CIからの定期起動接続、両用途・未実施範囲の表示受入 |
| R21 情報取扱い | 自作合成データ、固定送信/保存処理、秘匿処理と非掲載検査 | UC-LLMの認証送信前と保持・計画生成まで一貫した情報取扱いの受入 |
| R22 保持・撤回 | Artifact部品の保持/削除、authority Evidenceの撤回と現在CI拒否 | authority保存実体の保持期限・削除と、影響するControl/判定の不足表示 |
| R23 自己検査 | 合成fixture、評価器校正、adapter・保存・通信・出力障害の部品試験と固定実Docker | 両用途・監督・更新採択・保持処理の障害注入を含む受入 |
| R24 再現 | 固定時点・正規化結果から再計算し、実行ソースと保存成果物のhashを照合 | 両用途・更新後でも同じ根拠から人間向け/機械向け出力を再現する受入 |
| R25 管理AI | 独立UID/context、PolicyProfile採択、初回/条件差なしgen2契約採択、旧根拠保持 | 条件変更を伴う採択・baseline更新、通常の管理ループと主体分離の受入 |
| R26 試行・取消し | 中断回収・取消しCI3、停止不明CI2、開始登録前とACK喪失からの再開・二重実行防止 | 全checkpoint・定期起動・版跨ぎ回収と両用途での運用受入 |
| R27 影響とCI対象 | 依存閉包・影響集合の部品。製品監督ではchangeの未知影響を明示して全体実行へ拡大 | 既知影響から対象限定runへ結び、未実施範囲を全体成功にしない接続 |
| R28 データ・校正 | 400件の実入力を持つ固定合成pack、用途分離、独立165vector校正、固定fixture校正 | 校正・データ改訂とUC-LLMの認証された採択/通常runの接続 |
| R29 統計 | 固定集合の観測と母集団推定を分離する指標・比較条件の部品 | モデルrunで事前規則・標本単位・不確かさを保持する接続 |
| R30 状態分離 | 固定Docker隔離、単発/二段階の合成モデル評価、途中違反を残す集計 | モデルの会話・workspace・cache隔離を認証された監督下で確認する受入 |
| R31 データと権限 | 厳格な要求形、固定実装digest、OS主体、固定fixture、出力から実行権限を追加しない境界 | 両用途、管理ループ、保持と再検証の全体受入 |
| R32 修復確認 | Finding状態・再検証候補・再発の部品。自己申告だけではVERIFIEDにしない | 保存元Finding、変更対象、独立validator、新しい有効Evidenceの原子的な結合と再発追跡 |

## 残件の依存順

1. **完了**: 通常run取消し、停止・精算・不変receipt・CI終了3の実証を保存した。
2. 固定contract 2でbaseline 1→2を接続・検証済み。続けて基準2を使う後続契約と、条件変更を伴う契約/対象とbaseline更新を接続する。旧条件の違反、旧Evidence、採択理由と差分を保持する。
3. 固定UC-LLMの実入力・校正・Promptfoo経路を、認証された実行主体と予約/停止/usageへ接続する。
4. 固定UC-CIの監督入口・再開・全体実行は接続済み。対象限定、CIからの定期起動接続、全checkpoint境界、版跨ぎ回収、保持/削除を同じ保存根拠へ接続する。
5. Findingの処遇・再検証・確認・再発を接続する。閾値緩和や対象廃止は修復成功と区別する。
6. 32受入条件を具体的な実行証拠へ対応付け、全てが成立したときに全体TaskとAcceptanceを完了する。

この順序は実装作業の依存関係であり、元の要求・初期方針を変更しない。固定fixtureの限定成功を
対象モデルの一般的な性能保証へ拡張しない。全MVPの[Task](tasks/TASK.mvp-completion-09-11-2026.md)と
[Acceptance](acceptance/AC-20260911-05.md)は継続中とする。
