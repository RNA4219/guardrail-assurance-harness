---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 要求の来歴と処遇

[明確化前の要求本文](research/archive/before-clarification-20260910/docs/requirements.md)と、[現行要求](requirements.md)の対応を記録する。継承は原稿の意図を明文化したもの、補強は今回の要求案、後期は削除ではなくMVP対象外への分離を意味する。外部出典の正しさや全面採択を示す表ではない。

## 原稿の機能要求17件

| 原稿ID | 処遇 | 現行要求・位置 |
|---|---|---|
| FR-MAN | MVPへ継承 | GAH-R02、GAH-R18 |
| FR-REG | MVPへ継承 | GAH-R01 |
| FR-MUT | MVPへ継承・結果条件を補強 | GAH-R06、GAH-R07 |
| FR-DRIFT | MVPへ継承・比較不能を明確化 | GAH-R11、GAH-R12、GAH-R13 |
| FR-RCA | 根拠とUNKNOWNの最小分類はMVP、深い診断は後期 | GAH-R15、GAH-R17 |
| FR-PLAN | MVPへ継承・計画成立を明確化 | GAH-R16、GAH-R17 |
| FR-SHD | 運用対象のShadow実行は後期 | 要求§2。MVPの隔離fixture評価はGAH-R03 |
| FR-CAN | 本番Canary実行は後期 | 要求§2 |
| FR-PRO | 本番Promote実行は後期 | 要求§2。計画の前提記録はGAH-R16 |
| FR-RBK | 外部環境Rollback実行は後期 | 要求§2。計画の不足表示はGAH-R16 |
| FR-COV | MVPへ継承・分母と充足を補強 | GAH-R10 |
| FR-MSC | 基本指標はMVP、重み付き高度集計は後期 | GAH-R09、GAH-R12 |
| FR-EVD | 識別・digest・保存・整合はMVP、署名・外部不変storeは後期 | GAH-R05、GAH-R13、GAH-R18、GAH-R21、GAH-R22 |
| FR-PERM | MVPの範囲制限は必須、実環境のL4〜L6制御は後期 | GAH-R03、GAH-R04、GAH-R14 |
| FR-BLAST | 宣言scopeと予算の確認はMVP、実環境の影響予測は後期 | GAH-R03、GAH-R04 |
| FR-GC | 必要証拠の失効反映はMVP、週次の横断GCは後期 | GAH-R13、GAH-R22 |
| FR-KILL | GAHの処理とCI結果へ限定して明確化 | GAH-R14、GAH-R19、GAH-R20 |

## 原稿のMVP受入14項目

| 原稿項目 | 処遇 | 受入条件 |
|---|---|---|
| Registry | 10 Control以上を規模案として維持 | GAH-AC01 |
| Schema | cross-field不正拒否を要求、具体Schemaは設計へ | GAH-AC01、GAH-AC02 |
| Mutation | 5 family以上を規模案として維持 | GAH-AC07 |
| Adapter | generic command + Promptfooを維持 | GAH-AC08 |
| Result | 意味と根拠を明確化 | GAH-AC05、GAH-AC06、GAH-AC07 |
| Decay | 比較可能性と判定優先順を補強 | GAH-AC11、GAH-AC12 |
| Metrics | 分子分母・評価不能・固定coverage分母を明確化 | GAH-AC09、GAH-AC10 |
| Plan | 構文だけでなく項目・参照・不足表示も要求 | GAH-AC15、GAH-AC16、GAH-AC17 |
| Evidence | 識別・鮮度・整合・秘匿化を補強 | GAH-AC05、GAH-AC13、GAH-AC21、GAH-AC22 |
| Ledger | 巻戻し禁止と古い完了による上書き防止 | GAH-AC18 |
| Permissions | L0〜L3という名前だけでなくMVPで許す操作を限定 | GAH-AC03、GAH-AC04、GAH-AC14 |
| CI | 対象限定と全体の区別を明確化 | GAH-AC19、GAH-AC20 |
| Failure | Critical unknown/failureのHOLDを維持 | GAH-AC12、GAH-AC13、GAH-AC23 |
| Demo | 合成データで制御・検査系・実行失敗を区別 | 受入条件の最小シナリオ、GAH-AC06、GAH-AC07、GAH-AC23 |

## 今回の主な補強案

| 補強点 | 理由 | 状態 |
|---|---|---|
| 初期対象をcoding agentの開発・CIとLLMガードレール評価の両方とする | 2026-09-10の利用者補足を反映 | GAH-D01確認済み。v0.1の仮定A01を置換 |
| MVP評価を合成fixture・固定ケースへ絞る | 原稿のsandbox内デモを再現可能な最初の範囲として具体化する | MVP範囲案。実環境への適用は別に判断 |
| 必須/任意とCriticalを実行前に固定 | 実行後の除外や格下げで成功を作らない | 要求案 |
| KILLEDの因果条件・ERRORの分離 | 単なるクラッシュを検査の有効性にしない | 要求案 |
| baselineで意図した変更軸を宣言 | 対象版の変更検証を可能にし、未宣言の条件差と区別する | 要求案 |
| 判定優先順・CIでHEALTHYとWARNINGを成功にする | 必須条件が成立した任意の注意をCIで許し、不明やCritical違反は成功にしない | GAH-D02の利用者決定。v0.3のHEALTHY限定案を更新 |
| HOLDでも診断・再検査を許可 | 改善確認を止めず、GAHの強制力の範囲を正しく示す | 要求案 |
| 任意の計画補助LLMなしの最小経路 | 取得済み証拠から判定と計画骨子を出せる。対象LLM・必須評価器の欠損は別に扱う | 要求案 |
| 失効・中断・並行完了・再計算 | 過去のPASSや古いrunで現在を誤判定しない | 要求案 |

技術候補、数値の仮値、外部参照元実装の主張、競合比較・制度の主張は資料来歴へ退避し、検証済み根拠として要求へ持ち込んでいない。GAH-R01〜GAH-R32の各要求は[受入条件](acceptance-criteria.md)へ一対一で接続する。

## v0.2で明確化した二つの利用場面

| 内容 | 要求と受入条件 | 来歴 |
|---|---|---|
| UC-CI: 制約と検査系の劣化 | GAH-R05〜GAH-R07、GAH-R09〜GAH-R13、GAH-R20 / 同番号のGAH-AC、UC-CI最小シナリオ | 利用目的は確認済み。通常の違反とMutationへの期待反応を分離 |
| UC-LLM: 検出性能・誤検知の変化 | GAH-R02、GAH-R09、GAH-R11、GAH-R24 / GAH-AC02、GAH-AC09、GAH-AC11、GAH-AC24、UC-LLM最小シナリオ | 利用目的は確認済み。期待ラベル、検出率・見逃し率・FPR、比較条件は今回の明確化案 |
| 利用場面別の表示と全体判定 | GAH-R12、GAH-R19 / GAH-AC12、GAH-AC19 | 一方の合格やFPR改善で他方の必須違反を相殺しない要求案 |
| 評価対象・必須評価器と任意の計画補助の区別 | GAH-R17、GAH-R23 / GAH-AC17、GAH-AC23 | v0.1の「LLMなし」が評価欠損の成功扱いに読めないよう明確化 |

改訂前の[v0.1保存稿](research/archive/before-dual-usecases-20260910/docs/requirements.md)と[今回の改訂記録](reviews/dual-usecases-20260910.md)を保持する。利用場面の確認から、数値閾値や全要求の承認を推定しない。

## v0.3の敵対的検証からの追加・改訂

反例の出発点は[v0.2保存稿](research/archive/before-adversarial-20260910/docs/requirements.md)。AR番号は[検討記録](reviews/requirements-adversarial-20260910.md)の文書上の観点であり、製品の再現済み不具合ではない。すべて要求案としての反映。

| 反例ID | 改訂内容 | 要求 | 受入条件 |
|---|---|---|---|
| GAH-AR01 | 必須ケースの完了と全件正答を分け、集合の許容率と事象の禁止条件を区別 | GAH-R12、§5・§7 | GAH-AC12 |
| GAH-AR02 | candidateから独立した評価契約、基準変更と採択の記録 | GAH-R25 | GAH-AC25 |
| GAH-AR03 | 予定と結果の照合、重複・矛盾・再試行・取消しの扱い | GAH-R26 | GAH-AC26 |
| GAH-AR04 | 必須依存・共有資産への変更波及、CIの対象内容と有効性 | GAH-R27 | GAH-AC27 |
| GAH-AR05 | 有限集合と推定を分け、不確かさでの誤合格を防ぐ | GAH-R29 | GAH-AC29 |
| GAH-AR06 | データの用途・重複・派生と評価器校正の成立 | GAH-R28 | GAH-AC28 |
| GAH-AR07 | ケース・版間の状態分離と途中段階の違反保持 | GAH-R30 | GAH-AC30 |
| GAH-AR08 | 開始時だけでなく実行中の境界、予約予算、停止確認 | GAH-R03、GAH-R04 | GAH-AC03、GAH-AC04 |
| GAH-AR09 | 評価データと判定権限の分離、自己申告だけで成功にしない | GAH-R31 | GAH-AC31 |
| GAH-AR10 | 計画生成・基準緩和と修復確認を分け、再発を追跡 | GAH-R32 | GAH-AC32 |
| GAH-AR11 | baseline初回作成、部分比較、再取込みと再観測の違い | GAH-R02、GAH-R11、GAH-R13、GAH-R29 | GAH-AC02、GAH-AC11、GAH-AC13、GAH-AC29 |

8件の追加要求と既存要求の補強をMVPへ反映し、後期に残す範囲は[拡張案](extension-roadmap.md)で追跡する。文書の技術検収で、製品試験の成功や運用判断の承認を代替しない。

## v0.4の運用方針の決定

出発点は[v0.3保存稿](research/archive/before-operating-decisions-20260910/docs/requirements.md)。利用者指定と数値選定の委任を[初期運用方針](operating-policy.md)へ具体化した。32要求/32受入条件のIDは維持する。

| 決定 | 反映先 | 来歴 |
|---|---|---|
| D02: WARNINGをCIで許す | R19・§7 / AC19・組合せ条件 | 利用者決定。実行完了・必要証拠等の条件は維持 |
| D03: 閾値・予算を委任 | R04/R09/R11〜R13・運用方針§2〜§4 / 受入の境界例 | Codexが初期値を選定。原稿の仮値や本番実測の転記ではない |
| D04: 評価資産をopsのOSSから転用 | R08/R26/R28/R29・運用方針§6 / データと契約の入口 | 方針は決定、正確な元repoと固定版は確認後に対応付ける |
| D05: 生成AIが管理 | R25・§1・§10 / AC25 | 利用者決定。管理identityと候補生成を分離し、許可範囲内の採択を決定的に検査 |

[改訂記録](reviews/operating-decisions-20260910.md)に選定理由、Qwenの指摘の処遇、未実施範囲を記録する。
