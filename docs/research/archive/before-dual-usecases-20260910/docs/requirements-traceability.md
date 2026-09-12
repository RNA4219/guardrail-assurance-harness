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
| 初期対象をcoding agentの開発・CIとする | 原稿の中心的な利用場面に絞って読みやすくする | 仮定A01 |
| MVP評価を合成fixture・固定ケースへ絞る | 原稿のsandbox内デモを再現可能な最初の範囲として具体化する | MVP範囲案。実環境への適用は別に判断 |
| 必須/任意とCriticalを実行前に固定 | 実行後の除外や格下げで成功を作らない | 要求案 |
| KILLEDの因果条件・ERRORの分離 | 単なるクラッシュを検査の有効性にしない | 要求案 |
| baselineで意図した変更軸を宣言 | 対象版の変更検証を可能にし、未宣言の条件差と区別する | 要求案 |
| 判定優先順・CIでHEALTHYだけ成功 | 不明を成功にせず、既知のCritical違反を隠さない | 標準CI方針は運用判断待ち |
| HOLDでも診断・再検査を許可 | 改善確認を止めず、GAHの強制力の範囲を正しく示す | 要求案 |
| LLMなしの最小経路 | 外部生成の可用性で判定と根拠が失われない | 要求案 |
| 失効・中断・並行完了・再計算 | 過去のPASSや古いrunで現在を誤判定しない | 要求案 |

技術候補、数値の仮値、外部参照元実装の主張、競合比較・制度の主張は資料来歴へ退避し、検証済み根拠として要求へ持ち込んでいない。GAH-R01〜GAH-R24の各要求は[受入条件](acceptance-criteria.md)へ一対一で接続する。
