---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# baseline更新の工程証跡

[仕様](../../baseline-refresh-detail-spec.md) / [親レビュー](../../reviews/mvp-baseline-refresh-20260912.md)。親が実装・レビュー・検証を担当し、この工程の外部モデル委任は行っていない。

固定UC-CIの通常runからbaseline generation 1→2への更新を接続した。全体回帰560件の後、保存記録の照合を補正して影響する44件を再検証し、対象全件の成功を確認した。固有の検証対象は561件で、従来551件を保持する。補正後の実Docker248項目も成功し、従来233項目の判定を保持した。固定fixtureの実行92件は全て停止・回収済み。世代ごとの撤回と依存失効、旧基準CIの継続、違反runの昇格拒否、保存失敗時のrollbackを確認した。

| 証跡 | 結果と範囲 |
|---|---|
| [統合検証](verification.json) | 現行source、要求正本7ファイル、実行結果とartifact hashを照合 |
| [32受入の関連証拠対応](acceptance-map.json) | 全32件を追跡、関連試験と残件を分離。全条件の製品受入は未完了 |
| [全体回帰](unit-check.json) / [ログ](unittest.log) | 補正前の全体560件が成功。補正後44件を再検証し、旧551件と追加10件の計561件を確認 |
| [補正後の専用試験](focused-test_baseline_refresh.log) | baseline更新10件、current破損拒否・否定結果・依存失効・rollbackを確認 |
| [補正前の確認](pointer-before.json) / [初回Docker](initial-runtime-check.json) | 初稿で3操作がcurrent破損を受け入れた確認と、補正前の実行記録を保持 |
| [実Docker](runtime-check.json) / [照会結果](runtime-observations.json) | 248項目、基準更新・固定参照・失効後の現在CIを確認 |
| [実行receipt](runtime-execution-receipts.json) | 初回15、旧条件15、新条件30、通常30、取消し1、復帰1の92件 |
| [実装lock](authority-runtime.lock.json) | 検証したauthority実装とimageを固定 |
| [非掲載検査](privacy-primary.json) / [読取り補完](privacy-supplement.json) | 非掲載対象の残存0、native読取り補完後の未読0 |

baseline_refreshの追加判定は15項目。従来233項目の判定を保持し、更新後の元基準照会・撤回にはbaseline_resolve / baseline_revoke_refを使う。既存contract 2の比較条件はbaseline 1から変更しない。実Dockerの記録を検証後の現在CIの成功根拠には使わない。

対象は自作の固定・無害なUC-CI、contract generation 2、baseline 1→2に限る。後続契約・条件変更、UC-LLM認証資源、製品監督・保持、Finding修復確認と全32受入は残る。単一の全体実行で補正後561件を実行したとはしない。全MVPは未受入、release_gate=no_go、packetのci_eligible=falseを維持する。
