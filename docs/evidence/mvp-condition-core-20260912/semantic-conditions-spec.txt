---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 評価条件の意味的比較と後続契約の構造検査

`gah.semantic_conditions` と `gah.contract_revision_rules` は比較・禁止変更の検査部品である。DB、OS主体、実行、採択、Evidenceの現在有効性は扱わない。[後続契約の接続仕様](contract-revision-detail-spec.md)のうち差分導出を実装したもので、契約3の採択・実行が完成したことを意味しない。[試験記録](evidence/mvp-condition-core-20260912/README.md)へ結ぶ。

## 入力の結合

`signature(source, baseline_context=...)` は、bind済みのmanifest、contract、plan、policy、registry、case_set、selected_controls、ci_eligibleの8項目だけを受け取る。ci_eligibleはfalseを要求する。構造を再検査し、bind結果との完全一致、PolicyProfile・Registry・CaseSetの内容参照、評価器集合、必須カテゴリを照合する。壊れた型や参照を推測で補わない。

比較必須の契約では、保存baselineの完全参照とControlごとの対象参照を持つ明示baseline_contextが必要である。現対象を暗黙にbaselineへ流用しない。参照実体の採択・有効性の検査は接続先authorityの責任である。

## 条件参照

| 軸 | 含めるもの |
|---|---|
| policy | 閾値・予算等のPolicyProfile、実効時間上限、Control定義・必須性・依存、選択範囲、用途、必須出力 |
| corpus | CaseSet・oracle・初期状態・段階順、校正集合参照、trial・variant・必須性を含む予定entry |
| evaluator | obligationと評価器の対応 |
| environment | 固定環境の完全参照 |
| target | Controlと評価対象の対応 |

run ID、plan ID、contract IDと世代、作成時刻の絶対値を実行identityとして分ける。deadlineとcreated_atの差である実効時間上限は残す。比率は約分し、順不同の集合は整列する。段階順、trial数、Control/Case IDを消さない。

measurement_conditions_refはtarget以外の4軸から作る。conditions_refは測定条件に比較mode、保存baseline参照、baseline側の対象対応を加える。baseline参照の文字列が同じでも比較対象が違えば同じ評価条件にはしない。対象の変更は比較結果のchanged_axesに残し、同じ評価条件で異なる対象を測れるようにする。

`compare(previous, following, ...)` は各軸の差、同じ測定条件か、同じ評価条件か、baseline参照とbaseline対象の差を返す。changed_axesの入力申告で比較結果を書き換えない。

## 後続契約の禁止変更

`inspect_revision(previous, following, previous_baseline_context=..., following_baseline_context=...)` は固定した比較実装を使う。呼出側から検査関数を差し替える入力は持たない。

旧契約は世代2以降、新契約は直後世代とする。ID再利用、PolicyProfile系列の変更・世代巻戻し、同じpolicy世代での本文変更、申告軸と導出軸の不一致を拒否する。Criticalの格下げ、必須Control・必須obligationの除去や任意化、必須obligationの種別変更、禁止eventの緩和も拒否する。初期閾値floorと予算ceilingは既存PolicyProfile validatorを通じて適用する。

再検証の条件互換性は、同じ評価条件でtarget軸だけが変わり、実際に対象内容digestが変わった場合に限って返す。改名だけは互換な修復候補に数えない。この値も修復成功ではなく、独立した実行根拠の検査が必要である。

## 出力と残る接続

全てci_eligible=falseで、後続契約検査はauthority_connected、adoption_verified、target_execution_verifiedもfalseとする。旧条件回帰の要求を保持し、新条件だけの成功で旧条件の違反を消さない。

次は保存版ごとの実体化、両条件の別run、独立validator、原子的な採択・世代更新、現在CIと既知DB移行への接続を行う。固定metadataだけから対象版の実行や修復完了を主張しない。[全体の残件](mvp-completion-audit.md)は未完了のまま追跡する。
