---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# 決定事項と残る設計事項

2026-09-15現在: MVPの32条件は[技術検収済み](mvp-completion-audit.md)。以下の旧工程の設計・実装待ちは当時の記録として読む。次の改修は[拡張要件 v1](productization-requirements.md)に分離した。

拡張M0で固定するのは、利用許可のある2repo・1評価対象、実利用集合と独立した期待結果、機材・版・測定manifestである。未選定の対象を架空の実績で埋めない。14件の製品受入は全NOT_RUN。CI成功方針、数値選定の委任、生成AIによる管理、非掲載指定は維持し、再承認待ちへ戻さない。新しい性能数値は目標案であり、既存契約の上限や検出閾値の変更ではない。

[baseline更新](baseline-refresh-detail-spec.md)は固定contract 2・同条件で1→2まで実装した。基準2を参照する後続契約と対象・条件を変える更新は引き続き未完了である。

所有権切れと採択根拠の失効が重なる取消し復帰は[仕様](run-recovery-detail-spec.md)へ接続した。常設監督全体の再開・対象限定・モデル資源管理は[完了監査](mvp-completion-audit.md)の残件として維持する。

通常runの日本語要約・JSON CLIを追加した。直前の全体回帰534件に加えて専用7テストが成功し、実DBの製品CLI15項目で失効・取消しの表示と回収を確認した。 [仕様](run-report-detail-spec.md) / [証跡](evidence/mvp-report-20260912/README.md)。両用途の統合・運用受入は継続中。

最新の取消し工程: 固定UC-CIの通常run取消しを接続し、534テストと実Docker219項目が成功した。従来523テスト・198項目を全保持し、既存90件と取消し確認用1件の計91件を実行した。停止未確認はCI終了2、停止済み取消しは3とし、後日精算・再起動・遅延結果で元の記録を変更しない。 [証跡](evidence/mvp-cancellation-20260912/README.md)。条件変更を伴う契約・baseline更新、UC-LLMの認証・資源管理、常設orchestrator、Findingの修復確認と全32要求の受入は継続中。 外部モデルの独立レビューは未実施だが、利用者の指示で親が担当した。非掲載検査の読取り不能箇所は補完済み。要求・初期数値は変更していない。

## 確認済みの利用目的と運用方針

| ID | 確認内容 | 根拠・扱い |
|---|---|---|
| GAH-D01 | coding agentの開発・CIで制約と検査系の劣化を検知すること、LLMガードレール評価で検出性能・誤検知の変化を追うことの両方 | 2026-09-10の利用者補足で確認。UC-CIとUC-LLMをMVPの対象とし、v0.1の仮定A01を置き換える |
| GAH-D02 | HEALTHYとWARNINGをCI成功にする | 利用者指定「CIで許す」。必要な目的・対象・証拠・実行完了が成立する場合に適用し、WARNING理由は残す |
| GAH-D03 | 閾値・予算の選定をCodexへ委任 | 利用者指定「閾値と予算はおまかせ」。委任に基づく初期値を運用方針v1で選定済み。有限集合の回帰確認に適用する |
| GAH-D04 | 外部参照元はクローズド資産として直接利用せず、一般的な運用の考え方のみ参考にする | 利用者の訂正により旧移植方針を撤回。[参照境界](reference-boundary.md)に従い、コード・Schema・テスト・文書・データを持ち込まずGAHの要求から独立に設計する |
| GAH-D05 | 管理主体は生成AIとする | 利用者指定「管理主体は生成AI」。基準管理AIとcandidateの権限を分け、通常の採択・更新は決定的な検査後に都度の人間承認なしで行う |

## 契約・評価設計へ残す具体化

| 対応 | 具体化する内容 | 完了条件 |
|---|---|---|
| GAH-D02 | CIへの結果・終了コード・対象内容の結び付け | HEALTHY/WARNING成功と、それ以外の不成功を同じ契約で検査できる |
| GAH-D03 | 初期値の機械可読な設定と境界fixture | 丸め・予約・再試行・run間合算・期限で条件を回避できない |
| GAH-D04 | GAHの契約・ケース・期待ラベル・指標計測・校正・初回baselineの独立設計 | GAHの要求と、利用する公開OSSの原典を根拠にする。外部参照元の関数・Schema・fixtureを移植せず、元のPASSもGAHの証拠へ継承しない |
| GAH-D05 | 常設の基準管理AIの実行identity、別context/権限、構造化採択と保存 | 候補と管理の権限を分離し、既定の範囲内で採択・更新を行える |

D01〜D05の方針確認は完了。[設計初版](design.md)と[評価契約](contracts/README.md)で論理構成・結果照合・CI返却・管理AI採択を具体化した。[v0.2の見直し](reviews/design-review-20260911.md)で、確定時の再照合、精算、再利用、二段階の集計とUC-CIの対象も補正した。母集団推定は既定では無効で、手法の選定・受入後に別契約として有効化する。

## 設計初版の後に残る実装事項

評価工程の追加: [実評価集合と集計仕様](evaluation-detail-spec.md)でI06の実入力400件・別用途18/12件と独立oracle、I05のPromptfoo単一行mappingを実装した。対象Qwenの診断と測定側の165vectorによる独立校正を分離し、補正後の400件実測では見逃し0・誤検知5・前段不一致2を保存した。[保存仕様](run-evidence-detail-spec.md)と[修復計画仕様](remediation-detail-spec.md)で診断DecisionとFinding/Planの部品を追加した。[レビュー](reviews/mvp-evaluation-20260911.md)を最新の結果とする。モデルの重み版確認、OS authority/resource ledger、baseline採択と旧条件回帰、全オブジェクト/CIへの接続は残る。以下の工程別の過去記録を現在の未実装一覧に読み替えない。

最新の接続進捗: [開始境界の詳細仕様](run-contract-detail-spec.md)でI01のEvaluationContract/TrialPlan/RunManifest、I02の同DB採択・資源予約/停止/精算/閉鎖、I03の初回契約・実行操作のOS認証を追加した。固定fixtureの接続31項目と既存認証22項目を検証。下記の未完了事項は、個別部品の有無ではなく全経路の完了条件として扱う。baseline/更新契約、実校正・400ケース、model資源・Promptfoo・常設orchestrator・Evidence/CIは残る。

2026-09-11の進捗: I01は部品入力v1・厳格検査・request digest、I02はSQLite診断保存・run lease・費用予約/精算に加え、[v2の回収lease・取消し/停止確認・不変terminal](lifecycle-detail-spec.md)を実装した。I05は[adapter仕様](adapter-spec.md)を追加したが、実adapterの接続試験は未実施。I01の全オブジェクト・Bootstrap、I02の採択世代・不明dispatch・外部課金の認証・exposure解消、I03/I04/I06/I07は未完了。以下の完了条件を部品試験だけで閉じない。

| ID | 次工程で決める/確認すること | 完了の根拠 |
|---|---|---|
| GAH-I01 | 正式Schema、正規化・digest、追加field・版互換性、配布BootstrapContract | 正常/不正/未知版と初回の契約試験。設計例の算術検査だけでは完了しない |
| GAH-I02 | 不変artifact、世代付き採択参照、RunLease、予約/精算・時刻継続の保存方式 | LC02〜LC04/LC06の並行完了・確定直前の失効・不明dispatch・微小課金・停止期間・複合障害を確認 |
| GAH-I03 | 常設管理AIのidentityとcandidateの権限・context分離、配布物の認証 | JSONの役割名で偽装できず、初回自己承認と確定前の権限取消しを拒否する実環境検証 |
| GAH-I04 | OS上の隔離、固定fixtureの実行監督、LC05の保存/送信前検査 | 子処理・入出力・停止・telemetry/一時ログ/モデルpromptへの事前判定を確認 |
| GAH-I05 | adapterの公開原典・固定版・正規化mapping | generic command/Promptfooの成功・不正・未知版・欠損を同じ契約で照合 |
| GAH-I06 | 独立に作るケース・校正集合・oracleの実データ、初回の固定受入検査 | 必須2カテゴリ案と正負各最低件数、用途・派生関係・自己採点に依存しない初期校正を検査 |
| GAH-I07 | 証拠保持の初期値と削除・撤回手順 | 鮮度と保持を区別し、削除で旧成功を再利用できない |

これらは実装準備の技術判断で、利用者へ同じ運用方針の再承認を求めるものではない。v0.3の[補足契約](contracts/lifecycle-contract.md)と[複合条件のレビュー](reviews/state-review-20260911.md)を完了条件へ反映した。外部adapterの版やSchemaを未確認のまま実装済みと表示しない。

## 前回の12論点の処遇

管理境界の追加後: I03の固定UID/GIDとsocket peer認証、PolicyProfileの提案・独立検証・原子的採択・失効/再配送、候補の自己承認拒否を実装した。DGX Qwenの固定選択肢による実提案も採択まで確認した。[管理境界の証跡](evidence/mvp-authority-20260911/verification.json)へ結ぶ。I01/I02のEvaluationContract/baseline、I03の常設運用と全体の権限失効連鎖、モデル送信の全資源台帳接続は残る。

実行部の進捗: I04の固定fixture・Docker隔離・子処理停止・取消し/中断回収、I05のgeneric結果正規化を[実行監督仕様](execution-detail-spec.md)へ具体化し、[実コンテナと回帰の証跡](evidence/mvp-execution-20260911/verification.json)を追加した。I03の評価契約/baselineへの認証・採択接続、I04の全資源・権限・モデル送信との接続、I05のPromptfoo、I06の400実評価ケースは引き続き未完了である。

| 元ID | 要求段階で明確化した内容 | 残る工程 |
|---|---|---|
| GAH-Q01 | MVP4機能と必須の支援機能、本番段階展開などの後期対象を分離 | この範囲を契約・評価設計へ接続 |
| GAH-Q02 | ID、owner、必要検査、依存の妥当性をGAH-R01で要求 | invariantの具体記法・Schemaは設計 |
| GAH-Q03 | 個別事象と集合指標、Assuranceの優先順、HOLD解除、CI対象の照合を明確化 | D02/D05の決定を状態API・権限方式へ具体化 |
| GAH-Q04 | 計数、0分母、ERROR、除外、重複・再試行、coverage分母を明確化 | 合成fixture・oracle・結果採用契約の具体化 |
| GAH-Q05 | 比較単位、意図した変更軸、有限集合と推定、不確かさの扱いを明確化 | D03の初期値で境界を設計し、D04の実データを対応付ける |
| GAH-Q06 | 外部参照元の直接利用・共通core抽出案を撤回し、一般的な考え方の参照に限定 | GAHの要求から独立に設計する。外部参照元本体はクローズドのまま変更しない |
| GAH-Q07 | 必要な情報とgeneric command/Promptfooの役割を明確化 | Schema、CLI、API、adapter詳細は設計 |
| GAH-Q08 | Evidenceの識別・鮮度・失効・保持・保存失敗を明確化 | 保存方式・時刻処理・互換性・後期の署名方式は設計 |
| GAH-Q09 | 合成fixture内の範囲、予算、HOLDの効力を明確化 | 具体の隔離方式は設計。運用対象のL4〜L6は後期 |
| GAH-Q10 | 32要求と受入条件、UC-CI/UC-LLMと反例のシナリオへ整理 | 実データ校正と製品試験は未実施 |
| GAH-Q11 | 外部出典・新規性・制度の主張を要求根拠から分離 | 出典52件の復元と再確認は別の調査 |
| GAH-Q12 | MVPに既定の性能保証を設けず条件付き実測を要求 | license・公開条件・本番SLOは配布/運用判断 |

技術選定は[ADR](ADR/README.md)で追跡する。コアはPython標準ライブラリ・SQLite・部品JSON Schemaを採用した。別の評価器・保存製品等を追加する場合も根拠と影響を記録する。

## 敵対的検証から設計へ渡す事項

R25〜R32の振る舞いは要求案へ反映した。生成AIによる評価契約の管理・変更方式、対象内容とCI結果の結び付け、結果の重複・矛盾と再試行、依存先への影響、評価器の校正、セッションの初期状態、実行中の境界強制、Findingの再検証を契約・評価設計で具体化する。実行方式やSchemaをこの文書で確定しない。

実運用データ、多様な出力形式、評価集合更新時の比較等は[拡張案](extension-roadmap.md)に着手条件を置く。未確定のoracleが必要な製品試験は実行済みにせず、条件が固定されるまで設計・探索項目として扱う。
